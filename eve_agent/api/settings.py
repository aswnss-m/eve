import frappe

from eve_agent.sync.schema_sync import get_graph_client


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
