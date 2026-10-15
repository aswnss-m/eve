"""Read records from Frappe for the data graph. The only Frappe-touching part of the data sync."""

import frappe

from eve_agent.falkor.importer.data_rows import DocTypePlan


def _existing_fields(plan: DocTypePlan) -> list[str]:
	# Virtual fields and stale schema entries have no column, so ask the table.
	columns = set(frappe.db.get_table_columns(plan.name))
	return [f for f in plan.fetch_fields if f in columns]


def fetch_records(plan: DocTypePlan, limit: int) -> list[dict] | None:
	if not frappe.db.table_exists(plan.name):
		return None
	return frappe.get_all(
		plan.name,
		filters=plan.filters or None,
		fields=_existing_fields(plan),
		order_by="modified desc",
		limit=limit,
	)


def fetch_children(plan: DocTypePlan, parent_doctype: str, parentfield: str) -> list[dict]:
	if not frappe.db.table_exists(plan.name):
		return []
	return frappe.get_all(
		plan.name,
		filters={"parenttype": parent_doctype, "parentfield": parentfield},
		fields=_existing_fields(plan),
		order_by="parent, idx",
		limit=0,
	)
