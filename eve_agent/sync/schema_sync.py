"""Run a schema sync using Eve Agent Settings. Frappe-aware glue code."""

import frappe
from frappe.utils import now_datetime

from eve_agent.falkor.connection import GraphClient, build_url
from eve_agent.falkor.importer.schema_import import import_schema
from eve_agent.sync.meta_reader import read_doctype_metas

SETTINGS_DOCTYPE = "Eve Agent Settings"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6500"


SYNC_DONE_EVENT = "eve_agent_sync_done"

# Set (to a fresh token) when a field moves to another permlevel; deleted by the next schema sync that
# started after it. Kept by `bench clear-cache` (hooks.persistent_cache_keys).
LAYOUT_CHANGED_KEY = "eve_agent:field_layout_changed"
# Deletes KEYS[1] only if it still holds ARGV[1], the token read when the sync started.
DELETE_IF_UNCHANGED = 'if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) end'


def _layout_key() -> bytes:
	return frappe.cache.make_key(LAYOUT_CHANGED_KEY)


def mark_schema_outdated() -> None:
	"""A field moved permlevel: the schema graph's field levels are out of date until the next schema
	sync. Access profile plans take their levels from it, so profile builds wait (schema_outdated)."""
	frappe.cache.set(_layout_key(), frappe.generate_hash(length=12))


def schema_outdated() -> bool:
	return frappe.cache.get(_layout_key()) is not None


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
	layout_change = None
	try:
		layout_change = frappe.cache.get(_layout_key())  # read before the metas: this sync includes it
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
		_rebuild_access_profiles(layout_change)
	return text


def _rebuild_access_profiles(layout_change: bytes | None) -> None:
	"""Profile plans take field levels from the schema graph: every profile is rebuilt with the new ones.
	Never turns the sync into a failure."""
	try:
		if layout_change is not None:
			frappe.cache.eval(DELETE_IF_UNCHANGED, 1, _layout_key(), layout_change)
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
