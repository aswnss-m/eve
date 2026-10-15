"""FalkorDB access for the chat: read-only ERP queries, and writes to the memory graph only.

Reads go through GraphClient.ro_rows, which checks for writes locally and runs
GRAPH.RO_QUERY, so FalkorDB refuses writes as well.
"""

import time
from functools import lru_cache
from typing import Any

import redis

from eve_agent.chat.common import QueryRun
from eve_agent.falkor.connection import GraphClient

QUERY_TIMEOUT_SECONDS = 15


@lru_cache(maxsize=8)
def _connection(url: str) -> redis.Redis:
	return redis.Redis.from_url(
		url, decode_responses=True, socket_timeout=QUERY_TIMEOUT_SECONDS, socket_connect_timeout=5
	)


def plain(value: Any) -> Any:
	"""Turn decoded nodes and edges into plain dicts the model and the widget can read."""
	if isinstance(value, list):
		return [plain(v) for v in value]
	if isinstance(value, dict):
		out = {}
		for key, item in value.items():
			if key == "_labels":
				continue
			out["type" if key == "_type" else key] = plain(item)
		return out
	return value


class Graph:
	def __init__(self, url: str, name: str):
		self.name = name
		self._client = GraphClient(_connection(url), name)

	def ro(self, title: str, cypher: str, params: dict | None = None) -> QueryRun:
		started = time.monotonic()
		rows = [plain(row) for row in self._client.ro_rows(cypher, params)]
		return QueryRun(
			title=title,
			cypher=cypher.strip(),
			params=params or {},
			rows=rows,
			ms=round((time.monotonic() - started) * 1000),
		)

	def write(self, cypher: str, params: dict | None = None) -> list[dict]:
		"""Writes, for the memory graph only."""
		return [plain(row) for row in self._client.rows(cypher, params)]
