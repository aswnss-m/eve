"""Resolve a Frappe user's AccessScope over the DocTypes in Eve's data plan.

Every rule asks Frappe's own permission APIs. When in doubt a DocType is left out:
Eve shows less than Frappe would, never more.
"""

import frappe
import frappe.permissions
from frappe.model import db_query
from frappe.utils import cint

from eve_agent.access.scope import FULL_SCOPE, SPECIAL_DROP, SPECIAL_KEEP, AccessScope, make_key
from eve_agent.falkor.importer.data_rows import DataPlan

SPECIAL = (*SPECIAL_KEEP, *SPECIAL_DROP)


def resolve_scope(user: str, plan: DataPlan) -> AccessScope:
	if user == "Administrator":
		return FULL_SCOPE
	readable: dict[str, tuple[int, ...]] = {}
	for p in plan.parents:
		if p.name not in SPECIAL and _can_read(p.name, user):
			readable[p.name] = tuple(sorted(frappe.get_meta(p.name).get_permlevel_access("read", user=user)))
	parents = tuple(sorted(readable))
	for child in plan.children:
		meta = frappe.get_meta(child)
		levels = None  # intersection over the readable parents whose tables use this child
		for p in plan.parents:
			if p.name in readable and child in p.tables.values():
				access = set(meta.get_permlevel_access("read", parenttype=p.name, user=user))
				levels = access if levels is None else levels & access
		if levels:
			readable[child] = tuple(sorted(levels))
	user_permissions, own_record_doctypes = _user_permissions(user)
	strict = bool(cint(frappe.get_system_settings("apply_strict_user_permissions")))
	planned = (*(p.name for p in plan.parents), *plan.children)
	if not user_permissions and not own_record_doctypes and all(_fully_read(dt, readable) for dt in planned):
		# User, Comment and ToDo never enter `readable`: a plan with them is full only for Administrator.
		return FULL_SCOPE
	return AccessScope(
		key=make_key(readable, user_permissions, own_record_doctypes, strict),
		full_access=False,
		readable=readable,
		user_permissions=user_permissions,
		own_record_doctypes=own_record_doctypes,
		strict=strict,
		parents=parents,
	)


def is_own_record(allow: str, doc: str, user: str) -> bool:
	"""True when a Link-to-User field of `allow` holds `user` on record `doc` (e.g. Employee.user_id)."""
	user_links = frappe.get_meta(allow).get("fields", {"fieldtype": "Link", "options": "User"})
	fields = [df.fieldname for df in user_links]
	if not fields:
		return False
	values = frappe.db.get_value(allow, doc, fields, as_dict=True)
	return bool(values) and user in values.values()


def hook_restricted(doctype: str, user: str) -> bool:
	"""True when custom permission code (a hook or a Server Script) may narrow what `user` reads."""
	if _hooks("has_permission", doctype):
		return True
	for method in _hooks("permission_query_conditions", doctype):
		if frappe.call(frappe.get_attr(method), user, doctype=doctype):
			return True
	return bool(db_query.get_server_script_map().get("permission_query", {}).get(doctype))


def _hooks(name: str, doctype: str) -> list[str]:
	hooks = frappe.get_hooks(name, {})
	return hooks.get(doctype, []) + hooks.get("*", [])  # Frappe applies "*" hooks to every DocType


def _can_read(doctype: str, user: str) -> bool:
	"""Read through a role permission that is not "if owner", with no custom permission code."""
	perms = frappe.permissions.get_role_permissions(frappe.get_meta(doctype), user=user)
	return bool(perms.read) and "read" not in perms.get("if_owner", {}) and not hook_restricted(doctype, user)


def _fully_read(doctype: str, readable: dict[str, tuple[int, ...]]) -> bool:
	"""Readable at every permlevel the DocType's fields use."""
	used = {0} | {cint(df.permlevel) for df in frappe.get_meta(doctype).fields}
	return used <= set(readable.get(doctype, ()))


def _user_permissions(user: str) -> tuple[dict[str, tuple[tuple[str, str], ...]], tuple[str, ...]]:
	"""(allow -> sorted (doc, applicable_for) pairs, own-record DocTypes). Own-record entries are left out."""
	out, own = {}, set()
	for allow, entries in frappe.permissions.get_user_permissions(user).items():
		pairs = set()
		for entry in entries:
			if is_own_record(allow, entry["doc"], user):
				own.add(allow)
			else:
				pairs.add((entry["doc"], entry.get("applicable_for") or ""))
		if pairs:
			out[allow] = tuple(sorted(pairs))
	return out, tuple(sorted(own))
