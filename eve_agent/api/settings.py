import frappe

from eve_agent.sync.schema_sync import SETTINGS_DOCTYPE, get_graph_client

SYNC_STATUS_FIELDS = (
	"last_synced_at",
	"last_sync_status",
	"last_sync_summary",
	"active_data_graph",
	"last_data_synced_at",
	"last_data_sync_status",
	"last_data_sync_summary",
	"last_incremental_sync_at",
	"last_incremental_summary",
)


def module_names(client) -> set[str]:
	"""MODULE LIST replies with a list of flat [key, value, ...] lists."""
	names = set()
	for module in client.redis.execute_command("MODULE", "LIST"):
		fields = dict(zip(module[::2], module[1::2]))
		names.add(str(fields.get("name", "")).lower())
	return names


@frappe.whitelist()
def test_connection() -> dict:
	"""Ping the FalkorDB Redis configured in Eve Agent Settings."""
	frappe.only_for("System Manager")
	client = get_graph_client()
	try:
		client.ping()
		info = client.redis.info("server").get("redis_version", "unknown")
		has_graph = "graph" in module_names(client)
		return {"ok": True, "redis_version": info, "graph_module": has_graph, "graph": client.graph_name}
	except Exception as exc:
		return {"ok": False, "error": str(exc)}


@frappe.whitelist()
def sync_status() -> dict | None:
	"""The read-only sync fields of Eve Agent Settings, for the Sync Status block on the Eve workspace."""
	if not frappe.has_permission(SETTINGS_DOCTYPE):
		return None
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	return {field: settings.get(field) for field in SYNC_STATUS_FIELDS}
