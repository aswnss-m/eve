"""Read the records a profile may see: what Frappe's list view returns to a member of the profile,
minus records visible only through shares or owner-only access, minus the own-record rule.

The conditions come from Frappe's own `DatabaseQuery.add_user_permissions`; this module only adds
the own-record narrowing and the Comment rule. When in doubt a record is left out.
"""

import frappe
from frappe.model.db_query import DatabaseQuery
from frappe.utils import cint

from eve_agent.access.scope import AccessScope
from eve_agent.falkor.importer.data_rows import DocTypePlan
from eve_agent.sync import record_reader


def match_condition(doctype: str, scope: AccessScope) -> str:
	"""SQL condition on `tab{doctype}` for the records `scope` may read. "" means no restriction."""
	if scope.full_access:
		return ""
	query = DatabaseQuery(doctype)
	query.match_conditions = []
	query.match_filters = []
	query.add_user_permissions(_frappe_user_permissions(scope))
	parts = [*query.match_conditions, *_own_record_conditions(doctype, scope)]
	if doctype == "Comment":
		parts.append(_comment_condition(scope))
	return " and ".join(f"({part})" for part in parts)


def _frappe_user_permissions(scope: AccessScope) -> dict[str, list[dict]]:
	"""`scope.user_permissions` in the shape of `frappe.permissions.get_user_permissions`."""
	return {
		allow: [{"doc": doc, "applicable_for": applicable_for or None} for doc, applicable_for in entries]
		for allow, entries in scope.user_permissions.items()
	}


def _own_record_conditions(doctype: str, scope: AccessScope) -> list[str]:
	"""Frappe lets a link to the user's own record (e.g. Sales Person.employee) through; a profile
	is shared, so only empty links pass, and in strict mode not even those.

	The fields are those `add_user_permissions` checks, the record's own `name` included, so an
	own-record DocType itself yields nothing. Strict mode is read from the scope and from System
	Settings (which `add_user_permissions` uses), and the stricter one wins.
	"""
	own = set(scope.own_record_doctypes)
	if not own:
		return []
	strict = scope.strict or bool(cint(frappe.get_system_settings("apply_strict_user_permissions")))
	fields = [*frappe.get_meta(doctype).get_link_fields(), {"options": doctype, "fieldname": "name"}]
	return [
		"1=0" if strict else f"ifnull(`tab{doctype}`.`{df.get('fieldname')}`, '')=''"
		for df in fields
		if df.get("options") in own and not df.get("ignore_user_permissions")
	]


def _comment_condition(scope: AccessScope) -> str:
	"""A comment is readable when the record it is on is."""
	clauses = []
	for doctype in scope.parents:
		if not frappe.db.table_exists(doctype):
			continue
		where = match_condition(doctype, scope) or "1=1"
		clauses.append(
			f"(`tabComment`.`reference_doctype` = {frappe.db.escape(doctype, percent=False)}"
			f" and `tabComment`.`reference_name` in (select `name` from `tab{doctype}` where {where}))"
		)
	return " or ".join(clauses) or "1=0"


class ProfileQuery(DatabaseQuery):
	"""A list query that runs as Administrator, so the DocType-level check passes, with `condition`
	as its only row restriction: no user permissions of the session, no shares, no owner rule."""

	def __init__(self, doctype: str, condition: str):
		super().__init__(doctype, user="Administrator")
		self.condition = condition

	def build_match_conditions(self, as_condition=True):
		return self.condition

	def apply_fieldlevel_read_permissions(self):
		"""Frappe filters fields by the session user, who may not belong to the profile (a build can be
		queued by anyone). Administrator reads every field, and the profile's plan already holds only
		the fields at its permlevels (`filter_plan`)."""


class ProfileReader:
	"""Duck-types `eve_agent.sync.record_reader` for one profile's graph.

	Child rows are read unfiltered: the importer keeps only those whose parent was fetched.
	"""

	fetch_children = staticmethod(record_reader.fetch_children)
	fetch_children_by_parents = staticmethod(record_reader.fetch_children_by_parents)

	def __init__(self, scope: AccessScope):
		self.scope = scope

	def fetch_records(self, plan: DocTypePlan, limit: int) -> list[dict] | None:
		if not frappe.db.table_exists(plan.name):
			return None
		if not self.scope.can_see(plan.name):
			return []
		return self._execute(
			plan, filters=plan.filters or None, order_by="modified desc", limit_page_length=limit
		)

	def fetch_records_by_names(self, plan: DocTypePlan, names: list[str]) -> list[dict]:
		"""The records in `names` that still exist and the profile may read."""
		if not names or not frappe.db.table_exists(plan.name) or not self.scope.can_see(plan.name):
			return []
		return self._execute(plan, filters={"name": ("in", names)})

	def _execute(self, plan: DocTypePlan, **kwargs) -> list[dict]:
		query = ProfileQuery(plan.name, match_condition(plan.name, self.scope))
		# `execute` resets the query's user from its `user` argument.
		return query.execute(
			fields=record_reader._existing_fields(plan),
			ignore_permissions=False,
			user="Administrator",
			**kwargs,
		)
