"""Ask Eve: one chat turn, streamed from the Frappe web process (so `bench start` serves it).

POST /api/method/eve_agent.api.chat.stream   (Desk session and CSRF token)
  body: {"id": "<chat id>", "messages": [the widget's messages, ending with the user's question]}
  reply: text/event-stream of JSON events (see eve_agent/chat/loop.py), ending with "finish" or "error"

The request reads everything it needs from Frappe (user, roles, Settings) before
it returns. Frappe's request context stays alive while the response streams (it is
released by a ClosingIterator callback), which the Cypher log and the permission
check for record proposals rely on; anything they write commits itself.
"""

import json
import logging
from collections.abc import Iterator

import frappe
import litellm
from frappe import _
from werkzeug.wrappers import Response

from eve_agent.api.agent import chat_allowed, get_settings, write_doctypes
from eve_agent.chat import cypher_log
from eve_agent.chat.common import ChatConfig, ChatUser, ToolContext
from eve_agent.chat.graph import Graph
from eve_agent.chat.llm import DEFAULT_MODEL, model_arguments
from eve_agent.chat.loop import run_turn
from eve_agent.chat.memory import memory_context, save_turn
from eve_agent.chat.prompt import system_prompt
from eve_agent.chat.tools import build_tools
from eve_agent.sync.data_sync import active_data_graph
from eve_agent.sync.schema_sync import connection_url, default_graph_name

logger = logging.getLogger(__name__)


@frappe.whitelist(methods=["POST"])
def stream(id: str = "", messages: list | None = None) -> Response:
	settings = get_settings()
	if not chat_allowed(settings):
		frappe.throw(_("You are not allowed to chat with Eve"), frappe.PermissionError)

	history = messages or []
	question = last_user_text(history)
	if not question:
		frappe.throw(_("Ask Eve a question first."))

	ctx = build_context(settings, question=question, chat_id=str(id or "default"))
	return Response(
		_events(ctx, history, str(id or "default"), question),
		mimetype="text/event-stream",
		headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
	)


def last_user_text(messages: list[dict]) -> str:
	for message in reversed(messages):
		if message.get("role") == "user":
			return " ".join(
				p.get("text", "") for p in message.get("parts") or [] if p.get("type") == "text"
			).strip()
	return ""


def build_context(settings, question: str = "", chat_id: str = "") -> ToolContext:
	user = frappe.session.user
	config = ChatConfig(
		site=frappe.local.site,
		model=settings.llm_model or DEFAULT_MODEL,
		api_key=settings.get_password("llm_api_key", raise_exception=False) or "",
		base_url=settings.llm_base_url or "",
		redis_url=connection_url(settings),
		data_graph=active_data_graph(settings),
		schema_graph=settings.graph_name or default_graph_name("schema"),
		memory_graph=settings.memory_graph_name or default_graph_name("memory"),
		data_as_of=str(settings.last_data_synced_at or ""),
		currency=frappe.defaults.get_global_default("currency") or "",
		company=frappe.defaults.get_global_default("company") or "",
		allow_create=bool(settings.get("allow_create")),
		allow_update=bool(settings.get("allow_update")),
		write_doctypes=write_doctypes(settings),
	)
	chat_user = ChatUser(user=user, name=frappe.utils.get_fullname(user), roles=frappe.get_roles(user))
	return ToolContext(
		config=config,
		user=chat_user,
		data=Graph(config.redis_url, config.data_graph),
		schema=Graph(config.redis_url, config.schema_graph),
		memory=Graph(config.redis_url, config.memory_graph),
		question=question,
		chat_id=chat_id,
		can_write=lambda doctype, ptype: bool(frappe.has_permission(doctype, ptype, user=user)),
		log_cypher=cypher_log.record,
	)


LOCATE_LIMIT = 200


@frappe.whitelist(methods=["POST"])
def locate(names: list | str | None = None) -> dict:
	"""Map record names from an answer to their DocType, so the widget can link them.

	Only the DocType comes back, never record contents. Opening a link still goes through
	the Desk, which checks the user's own permission on the document.
	"""
	settings = get_settings()
	if not chat_allowed(settings):
		frappe.throw(_("You are not allowed to chat with Eve"), frappe.PermissionError)

	names = frappe.parse_json(names) if isinstance(names, str) else names
	names = [str(n) for n in (names or []) if n][:LOCATE_LIMIT]
	if not names:
		return {}

	ctx = build_context(settings)
	run = ctx.data.ro(
		"Locate records",
		"UNWIND $names AS name MATCH (r:Record) WHERE r.name = name RETURN r.name AS name, r.doctype AS doctype",
		{"names": names},
	)
	found = {}
	for row in run.rows:
		if row.get("name") and row.get("doctype") and row["name"] not in found:
			found[row["name"]] = row["doctype"]
	return found


def _events(ctx: ToolContext, history: list[dict], chat_id: str, question: str) -> Iterator[str]:
	config = ctx.config
	answer: list[str] = []
	try:
		system = system_prompt(config, ctx.user, memory_context(ctx))
		events = run_turn(
			system=system,
			ui_messages=history,
			tools=build_tools(ctx),
			completion=litellm.completion,
			model_kwargs=model_arguments(config.model, config.api_key, config.base_url),
		)
		for event in events:
			if event["type"] == "text-delta":
				answer.append(event["delta"])
			yield _sse(event)
		try:
			save_turn(ctx, chat_id, question, "".join(answer))
		except Exception:
			logger.exception("memory: could not save turn")
		yield _sse({"type": "finish"})
	except Exception as exc:
		logger.exception("chat error")
		yield _sse({"type": "error", "errorText": str(exc)})


def _sse(event: dict) -> str:
	return f"data: {json.dumps(event, default=str)}\n\n"
