"""Connection to the FalkorDB Redis instance and raw query execution.

The FalkorDB Redis is a separate server from the bench's own Redis (cache and
queue). Its URL comes from Eve Agent Settings in the Desk.

We use Frappe's redis-py (4.5.x) and call GRAPH.QUERY directly, because the
published `falkordb` Python client needs redis 5+ or 8+, and Frappe pins 4.5.
"""

from dataclasses import dataclass, field

import redis

from eve_agent.falkor.literals import with_params
from eve_agent.falkor.query.readonly import assert_read_only

DEFAULT_TIMEOUT_SECONDS = 30


@dataclass
class QueryResult:
	header: list[str]
	rows: list[list]
	stats: list[str] = field(default_factory=list)


def _column_name(item) -> str:
	# Newer FalkorDB versions send [type, name] pairs in the header.
	return item[-1] if isinstance(item, list) else item


def parse_reply(raw) -> QueryResult:
	if not raw:
		return QueryResult(header=[], rows=[])
	header = [_column_name(h) for h in raw[0]] if len(raw) > 0 else []
	rows = raw[1] if len(raw) > 1 else []
	stats = raw[2] if len(raw) > 2 else []
	return QueryResult(header=header, rows=rows, stats=stats)


class GraphClient:
	def __init__(self, redis_conn: redis.Redis, graph_name: str):
		self.redis = redis_conn
		self.graph_name = graph_name

	@classmethod
	def from_url(cls, url: str, graph_name: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> "GraphClient":
		conn = redis.Redis.from_url(url, decode_responses=True, socket_timeout=timeout, socket_connect_timeout=5)
		return cls(conn, graph_name)

	def ping(self) -> bool:
		return bool(self.redis.ping())

	def query(self, cypher: str, params: dict | None = None) -> QueryResult:
		"""Read or write query. Use ro_query for anything the agent generates."""
		raw = self.redis.execute_command("GRAPH.QUERY", self.graph_name, with_params(cypher, params))
		return parse_reply(raw)

	def ro_query(self, cypher: str, params: dict | None = None) -> QueryResult:
		"""Read-only query, checked locally and enforced by GRAPH.RO_QUERY."""
		assert_read_only(cypher)
		raw = self.redis.execute_command("GRAPH.RO_QUERY", self.graph_name, with_params(cypher, params))
		return parse_reply(raw)

	def delete_graph(self) -> None:
		try:
			self.redis.execute_command("GRAPH.DELETE", self.graph_name)
		except redis.ResponseError as exc:
			# Deleting a graph that does not exist is not an error for us.
			if "not found" not in str(exc).lower() and "empty key" not in str(exc).lower():
				raise
