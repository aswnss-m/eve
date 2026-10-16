"""Apply a record proposal the user confirmed in Ask Eve.

POST /api/method/eve_agent.api.actions.apply
  {"mode": "create" | "update", "doctype": ..., "values": {...}, "name": ...}

Runs as the Desk user with Frappe's own permission checks. Creates drafts only:
docstatus is never set and nothing is ever submitted.
"""

import frappe
from frappe import _

from eve_agent.api.agent import chat_allowed, get_settings, write_allowed

RESERVED = {"doctype", "docstatus", "amended_from", "name", "owner", "creation", "modified", "modified_by"}
TABLE_TYPES = {"Table", "Table MultiSelect"}


@frappe.whitelist(methods=["POST"])
def apply(mode: str, doctype: str, values: dict | str | None = None, name: str | None = None) -> dict:
	settings = get_settings()
	if not chat_allowed(settings):
		frappe.throw(_("You are not allowed to chat with Eve"), frappe.PermissionError)
	mode = "update" if mode == "update" else "create"
	if not write_allowed(settings, mode, doctype):
		frappe.throw(_("Eve is not allowed to {0} {1} records. Check Eve Agent Settings → Eve Actions.").format(mode, doctype))

	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	meta = frappe.get_meta(doctype)
	scalars, tables = _split_values(meta, values)

	if mode == "create":
		doc = frappe.new_doc(doctype)
		doc.update(scalars)
		for fieldname, rows in tables.items():
			for row in rows:
				doc.append(fieldname, row)
		doc.insert()  # Checks the user's create permission. Never submit.
	else:
		if not name:
			frappe.throw(_("An update needs the record name."))
		doc = frappe.get_doc(doctype, name)
		if doc.docstatus != 0:
			frappe.throw(_("{0} {1} is already submitted. Amend it in the Desk instead.").format(doctype, name))
		doc.update(scalars)
		for fieldname, rows in tables.items():
			doc.set(fieldname, [])
			for row in rows:
				doc.append(fieldname, row)
		doc.save()  # Checks the user's write permission.

	frappe.db.commit()
	title_field = meta.get_title_field()
	return {
		"doctype": doc.doctype,
		"name": doc.name,
		"title": str(doc.get(title_field) or doc.name) if title_field else doc.name,
		"docstatus": doc.docstatus,
		"is_submittable": bool(meta.is_submittable),
		"url": doc.get_url(),
	}


def _split_values(meta, values: dict) -> tuple[dict, dict[str, list[dict]]]:
	"""Keep only real fields: scalars (incl. Links) and child-table rows filtered against the child meta."""
	scalars, tables = {}, {}
	for key, value in values.items():
		if key in RESERVED:
			continue
		field = meta.get_field(key)
		if field is None:
			continue
		if field.fieldtype in TABLE_TYPES:
			child_meta = frappe.get_meta(field.options)
			rows = []
			for row in value or []:
				if not isinstance(row, dict):
					continue
				clean = {k: v for k, v in row.items() if k not in RESERVED and child_meta.get_field(k) is not None}
				if clean:
					rows.append(clean)
			tables[key] = rows
		else:
			scalars[key] = value
	return scalars, tables
