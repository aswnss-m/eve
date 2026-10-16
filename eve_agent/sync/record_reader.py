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


def fetch_records_by_names(plan: DocTypePlan, names: list[str]) -> list[dict]:
	"""The records in `names` that still exist. Missing ones were deleted or renamed."""
	if not names or not frappe.db.table_exists(plan.name):
		return []
	return frappe.get_all(plan.name, filters={"name": ("in", names)}, fields=_existing_fields(plan), limit=0)


def fetch_children_by_parents(plan: DocTypePlan, parent_doctype: str, parentfield: str, parents: list[str]) -> list[dict]:
	if not parents or not frappe.db.table_exists(plan.name):
		return []
	return frappe.get_all(
		plan.name,
		filters={"parenttype": parent_doctype, "parentfield": parentfield, "parent": ("in", parents)},
		fields=_existing_fields(plan),
		order_by="parent, idx",
		limit=0,
	)


def names_modified_since(plan: DocTypePlan, children: dict[str, DocTypePlan], since) -> set[str]:
	"""Parents changed since `since`, including those whose child rows changed (status updaters
	and stock updates write child rows and bins with SQL that bumps `modified` but fires no hook)."""
	if not frappe.db.table_exists(plan.name):
		return set()
	names = set(frappe.get_all(plan.name, filters={"modified": (">=", since)}, pluck="name", limit=0))
	for child_name in plan.tables.values():
		if child_name not in children or not frappe.db.table_exists(child_name):
			continue
		names.update(
			frappe.get_all(
				child_name,
				filters={"parenttype": plan.name, "modified": (">=", since)},
				pluck="parent",
				distinct=True,
				limit=0,
			)
		)
	return {str(n) for n in names if n}
