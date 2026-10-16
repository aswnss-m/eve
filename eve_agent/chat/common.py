"""What every chat tool shares: the request's context, the tool shape and the result format.

The tools are a Python port of the agent's TypeScript tools. Their Cypher and
result shape match, so the Desk widget renders them the same way.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

from eve_agent.falkor.cypher.data_queries import quote_label

MAX_SOURCES = 25
LABEL_TTL_SECONDS = 300

_label_cache: dict[str, tuple[float, set[str]]] = {}


@dataclass
class ChatUser:
	user: str
	name: str
	roles: list[str]


@dataclass
class ChatConfig:
	site: str
	model: str
	api_key: str
	base_url: str
	redis_url: str
	data_graph: str
	schema_graph: str
	memory_graph: str
	data_as_of: str
	currency: str
	company: str
	# Record writes (eve_agent/chat/tools/actions.py). Off unless Settings say otherwise.
	allow_create: bool = False
	allow_update: bool = False
	write_doctypes: list[str] = field(default_factory=list)


@dataclass
class QueryRun:
	title: str
	cypher: str
	params: dict
	rows: list[dict]
	ms: int

	def as_dict(self) -> dict:
		return {
			"title": self.title,
			"cypher": self.cypher,
			"params": self.params,
			"rows": self.rows,
			"ms": self.ms,
		}


@dataclass
class ToolContext:
	config: ChatConfig
	user: ChatUser
	data: Any  # chat.graph.Graph for the ERP data graph
	schema: Any  # chat.graph.Graph for the schema graph
	memory: Any  # chat.graph.Graph for the memory graph
	# Every source cited this turn; memory links the conversation to these.
	cited: list[dict] = field(default_factory=list)
	# The user's question and chat, for the Cypher log.
	question: str = ""
	chat_id: str = ""
	# Frappe-side behaviour, injected by api/chat.py so the chat package stays importable without Frappe:
	# can_write(doctype, "create" | "write") -> bool, and log_cypher(entry) for run_cypher.
	can_write: Callable[[str, str], bool] | None = None
	log_cypher: Callable[[dict], None] | None = None


@dataclass
class Tool:
	name: str
	description: str
	params: type[BaseModel]
	run: Callable[[Any], dict]

	def spec(self) -> dict:
		"""The OpenAI-style function definition LiteLLM sends to every provider."""
		return {
			"type": "function",
			"function": {
				"name": self.name,
				"description": self.description,
				"parameters": self.params.model_json_schema(),
			},
		}


def refs(rows: list[dict], doctype: str, name_key: str, title_key: str | None = None) -> list[dict]:
	out = []
	for row in rows:
		if row.get(name_key) in (None, ""):
			continue
		source = {"doctype": doctype, "name": str(row[name_key])}
		if title_key and row.get(title_key):
			source["title"] = str(row[title_key])
		out.append(source)
	return out


def result(ctx: ToolContext, queries: list[QueryRun], sources: list[dict], note: str | None = None) -> dict:
	seen: set[str] = set()
	unique = []
	for source in sources:
		key = f"{source['doctype']}|{source['name']}"
		if key in seen:
			continue
		seen.add(key)
		unique.append(source)
	ctx.cited.extend(unique)
	out = {
		"queries": [q.as_dict() for q in queries],
		"sources": unique[:MAX_SOURCES],
		"as_of": ctx.config.data_as_of,
	}
	if note:
		out["note"] = note
	return out


def known_labels(graph: Any) -> set[str]:
	cached = _label_cache.get(graph.name)
	if cached and time.monotonic() - cached[0] < LABEL_TTL_SECONDS:
		return cached[1]
	run = graph.ro("labels", "CALL db.labels() YIELD label RETURN label")
	labels = {str(row["label"]) for row in run.rows}
	_label_cache[graph.name] = (time.monotonic(), labels)
	return labels


def label(ctx: ToolContext, doctype: str) -> str:
	"""A DocType name checked against the graph's labels, so it is safe to splice into Cypher."""
	if doctype not in known_labels(ctx.data) or doctype == "Record":
		raise ValueError(
			f"DocType '{doctype}' has no records in the graph. Use graph_schema to see what exists."
		)
	return quote_label(doctype)


def iso_days_ago(days: int) -> str:
	return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
