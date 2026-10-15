import frappe

from eve_agent.sync.data_sync import enqueue_data_sync
from eve_agent.sync.schema_sync import enqueue_schema_sync


@frappe.whitelist()
def sync_schema() -> dict:
	"""Queue a full DocType schema sync in the background."""
	frappe.only_for("System Manager")
	enqueue_schema_sync()
	return {"queued": True}


@frappe.whitelist()
def sync_data_graph() -> dict:
	"""Queue a rebuild of the data graph from the current schema graph."""
	frappe.only_for("System Manager")
	enqueue_data_sync()
	return {"queued": True}


@frappe.whitelist()
def sync_all() -> dict:
	"""Queue a schema sync followed by a data graph rebuild."""
	frappe.only_for("System Manager")
	enqueue_data_sync(full=True)
	return {"queued": True}
