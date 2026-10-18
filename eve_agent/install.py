from pathlib import Path

import frappe

from eve_agent.sync.schema_sync import enqueue_schema_sync

SYNC_STATUS_BLOCK = "Eve Sync Status"
BLOCK_DIR = Path(__file__).parent / "eve_agent" / "custom_html_block" / "eve_sync_status"


def after_install():
	ensure_sync_status_block()
	enqueue_schema_sync()


def after_migrate():
	ensure_sync_status_block()
	mark_access_profiles_pending()
	enqueue_schema_sync()


def mark_access_profiles_pending():
	"""A migrate may move fields to other permlevels: no profile graph is used until the schema sync queued
	next rebuilds them (run_schema_sync). Never breaks the migrate."""
	try:
		from eve_agent.access.profiles import mark_all_pending

		mark_all_pending()
	except Exception:
		frappe.log_error(title="Eve Agent: could not mark access profiles for rebuild")


def ensure_sync_status_block():
	"""Custom HTML Block is not a standard (exportable) DocType, so the Eve workspace's block is written here."""
	if frappe.db.exists("Custom HTML Block", SYNC_STATUS_BLOCK):
		doc = frappe.get_doc("Custom HTML Block", SYNC_STATUS_BLOCK)
	else:
		doc = frappe.new_doc("Custom HTML Block")
		doc.name = SYNC_STATUS_BLOCK
	doc.update(
		{
			"html": (BLOCK_DIR / "eve_sync_status.html").read_text(),
			"script": (BLOCK_DIR / "eve_sync_status.js").read_text(),
			"style": (BLOCK_DIR / "eve_sync_status.css").read_text(),
			"private": 0,
			"roles": [{"role": "System Manager"}],
		}
	)
	doc.save(ignore_permissions=True)
