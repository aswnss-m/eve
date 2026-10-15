"""Run a schema sync using Eve Agent Settings. Frappe-aware glue code."""

import frappe
from frappe.utils import now_datetime

from eve_agent.falkor.connection import GraphClient
from eve_agent.falkor.importer.schema_import import import_schema
from eve_agent.sync.meta_reader import read_doctype_metas

SETTINGS_DOCTYPE = "Eve Agent Settings"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6500"


def get_graph_client(settings=None) -> GraphClient:
	settings = settings or frappe.get_single(SETTINGS_DOCTYPE)
	graph_name = settings.graph_name or f"{frappe.local.site.replace('.', '_')}_schema"
	return GraphClient.from_url(settings.redis_url or DEFAULT_REDIS_URL, graph_name)


def run_schema_sync() -> str:
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	try:
		client = get_graph_client(settings)
		summary = import_schema(client, read_doctype_metas())
		status, text = "Success", summary.as_text()
	except Exception as exc:
		status, text = "Failed", f"{type(exc).__name__}: {exc}"
		frappe.log_error(title="Eve Agent schema sync failed")

	frappe.db.set_single_value(
		SETTINGS_DOCTYPE,
		{"last_synced_at": now_datetime(), "last_sync_status": status, "last_sync_summary": text},
	)
	frappe.db.commit()
	return text


def enqueue_schema_sync() -> None:
	frappe.enqueue(
		"eve_agent.sync.schema_sync.run_schema_sync",
		queue="long",
		timeout=3600,
		enqueue_after_commit=True,
	)
