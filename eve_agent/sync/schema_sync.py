"""Run a schema sync using Eve Agent Settings. Frappe-aware glue code."""

import frappe
from frappe.utils import now_datetime

from eve_agent.falkor.connection import GraphClient, build_url
from eve_agent.falkor.importer.schema_import import import_schema
from eve_agent.sync.meta_reader import read_doctype_metas

SETTINGS_DOCTYPE = "Eve Agent Settings"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6500"


SYNC_DONE_EVENT = "eve_agent_sync_done"


def default_graph_name(suffix: str) -> str:
	return f"{frappe.local.site.replace('.', '_')}_{suffix}"


def connection_url(settings) -> str:
	"""Host set: build from the Host/Port/Username/Password/TLS fields. Otherwise the URL field."""
	if settings.get("falkordb_host"):
		return build_url(
			settings.falkordb_host,
			settings.falkordb_port,
			settings.falkordb_username,
			settings.get_password("falkordb_password", raise_exception=False),
			settings.falkordb_tls,
		)
	return settings.redis_url or DEFAULT_REDIS_URL


def get_graph_client(settings=None, graph_name: str | None = None) -> GraphClient:
	settings = settings or frappe.get_single(SETTINGS_DOCTYPE)
	graph_name = graph_name or settings.graph_name or default_graph_name("schema")
	return GraphClient.from_url(connection_url(settings), graph_name)


def publish_sync_done(kind: str, status: str, text: str) -> None:
	frappe.publish_realtime(SYNC_DONE_EVENT, {"kind": kind, "status": status, "summary": text})


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
	publish_sync_done("schema", status, text)
	if status == "Success":
		_rebuild_access_profiles()
	return text


def _rebuild_access_profiles() -> None:
	"""Profile plans come from the schema graph, which may now have other fields: every profile is
	rebuilt. Never turns the sync into a failure."""
	try:
		from eve_agent.access.profiles import mark_all_pending_and_rebuild

		mark_all_pending_and_rebuild()
		frappe.db.commit()  # Sends the builds (queued on commit); `bench sync-schema` won't commit later.
	except Exception:
		frappe.log_error(title="Eve Agent: could not queue access profile rebuilds")


def enqueue_schema_sync() -> None:
	frappe.enqueue(
		"eve_agent.sync.schema_sync.run_schema_sync",
		queue="long",
		timeout=3600,
		enqueue_after_commit=True,
	)
