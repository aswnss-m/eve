import frappe

from eve_agent.sync.schema_sync import enqueue_schema_sync


@frappe.whitelist()
def sync_schema() -> dict:
	"""Queue a full DocType schema sync in the background."""
	frappe.only_for("System Manager")
	enqueue_schema_sync()
	return {"queued": True}
