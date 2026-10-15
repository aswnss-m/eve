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

	# --- Typed results (--compact) for the agent -------------------------------
	#
	# Plain GRAPH.QUERY replies turn doubles, lists and maps into strings. The
	# agent needs real values, so it reads in --compact mode and decodes the
	# [type, value] pairs here, the way the official client does.

	_T_NULL, _T_STRING, _T_INT, _T_BOOL, _T_DOUBLE, _T_ARRAY, _T_EDGE, _T_NODE, _T_PATH, _T_MAP, _T_POINT = range(
		1, 12
	)

	def ro_rows(self, cypher: str, params: dict | None = None) -> list[dict]:
		"""Read-only query returning one dict per row, with typed values."""
		assert_read_only(cypher)
		raw = self.redis.execute_command("GRAPH.RO_QUERY", self.graph_name, with_params(cypher, params), "--compact")
		return self._decode_rows(raw)

	def rows(self, cypher: str, params: dict | None = None) -> list[dict]:
		"""Read or write query returning typed rows. Only the memory graph is written this way."""
		raw = self.redis.execute_command("GRAPH.QUERY", self.graph_name, with_params(cypher, params), "--compact")
		return self._decode_rows(raw)

	def _decode_rows(self, raw) -> list[dict]:
		if not raw or len(raw) < 2:
			return []
		header = [h[1] for h in raw[0]]
		return [dict(zip(header, (self._decode(v) for v in row))) for row in raw[1]]

	def _decode(self, item):
		kind, value = item
		if kind in (self._T_NULL, self._T_STRING, self._T_INT):
			return value
		if kind == self._T_BOOL:
			return value == "true"
		if kind == self._T_DOUBLE:
			return float(value)
		if kind == self._T_ARRAY:
			return [self._decode(v) for v in value]
		if kind == self._T_MAP:
			return {value[i]: self._decode(value[i + 1]) for i in range(0, len(value), 2)}
		if kind == self._T_NODE:
			_id, label_ids, props = value
			node = self._properties(props)
			node["_labels"] = [self._name("labels", i) for i in label_ids]
			return node
		if kind == self._T_EDGE:
			_id, type_id, _src, _dest, props = value
			edge = self._properties(props)
			edge["_type"] = self._name("relationshipTypes", type_id)
			return edge
		if kind == self._T_PATH:
			nodes, edges = value
			return {"nodes": self._decode(nodes), "edges": self._decode(edges)}
		if kind == self._T_POINT:
			return {"latitude": float(value[0]), "longitude": float(value[1])}
		return value

	def _properties(self, props) -> dict:
		return {self._name("propertyKeys", key_id): self._decode((kind, value)) for key_id, kind, value in props}

	def _name(self, procedure: str, index: int) -> str:
		"""Labels, relationship types and property keys are sent as ids; look the names up (cached)."""
		cache = self.__dict__.setdefault("_names", {}).setdefault(procedure, [])
		if index >= len(cache):
			raw = self.redis.execute_command("GRAPH.RO_QUERY", self.graph_name, f"CALL db.{procedure}()", "--compact")
			cache[:] = [row[0][1] for row in raw[1]]
		return cache[index] if index < len(cache) else str(index)

	def delete_graph(self) -> None:
		try:
			self.redis.execute_command("GRAPH.DELETE", self.graph_name)
		except redis.ResponseError as exc:
			# Deleting a graph that does not exist is not an error for us.
			if "not found" not in str(exc).lower() and "empty key" not in str(exc).lower():
				raise
