"""Tests for the Ask Eve chat core: model mapping, the turn loop, the tools and the memory format.

No Frappe and no LLM are needed. The model is a scripted completion function, and
the graph is a fake that records the Cypher it is given. Run with:
  env/bin/python -m unittest eve_agent.tests.test_chat   (from apps/eve_agent)

The live class runs against FalkorDB when it is reachable (EVE_TEST_REDIS,
default redis://127.0.0.1:6500; EVE_TEST_GRAPH, default eve_local_data) and skips otherwise.
"""

import json
import os
import re
import unittest
from types import SimpleNamespace

from eve_agent.access.scope import AccessScope
from eve_agent.chat.common import ChatConfig, ChatUser, QueryRun, Tool, ToolContext, result
from eve_agent.chat.graph import Graph, plain
from eve_agent.chat.llm import model_arguments
from eve_agent.chat.loop import run_turn, to_model_messages
from eve_agent.chat.memory import entity_refs, memory_context, save_turn
from eve_agent.chat.prompt import system_prompt
from eve_agent.chat.tools import TOOL_NEEDS, build_tools
from eve_agent.chat.tools.entities import fulltext_query
from eve_agent.chat.tools.sales import (
	OUTSTANDING_NOTE,
	PURCHASE_ORDERS_NOTE,
	SALES_ORDERS_NOTE,
	TopCustomersArgs,
	top_customers_cypher,
)
from eve_agent.falkor.query.readonly import WriteQueryError, assert_read_only

EXPECTED_TOOLS = {
	"find_entity",
	"customer_profile",
	"item_profile",
	"explain_connection",
	"top_selling_items",
	"top_customers",
	"top_suppliers",
	"also_bought",
	"dormant_customers",
	"supplier_risk",
	"who_knows",
	"decision_trail",
	"graph_schema",
	"describe_doctype",
	"run_cypher",
	"record_form",
	"propose_record",
	"recall",
	"remember_insight",
}


class FakeGraph:
	"""Stands in for chat.graph.Graph: records the Cypher it is asked to run and returns no rows."""

	def __init__(self, name="data"):
		self.name = name
		self.calls = []

	def ro(self, title, cypher, params=None):
		assert_read_only(cypher)
		self.calls.append((title, cypher, params or {}))
		return QueryRun(title=title, cypher=cypher.strip(), params=params or {}, rows=[], ms=0)

	def write(self, cypher, params=None):
		self.calls.append(("write", cypher, params or {}))
		return []


def make_ctx(data=None) -> ToolContext:
	config = ChatConfig(
		site="test",
		model="anthropic:claude-sonnet-5-5",
		api_key="",
		base_url="",
		redis_url="redis://127.0.0.1:6500",
		data_graph="eve_test_data",
		schema_graph="eve_test_schema",
		memory_graph="eve_test_memory",
		data_as_of="2026-10-15 19:00:00",
		currency="USD",
		company="Test Co",
	)
	user = ChatUser(user="tester@example.com", name="Tester", roles=["Sales Manager"])
	return ToolContext(
		config=config,
		user=user,
		data=data or FakeGraph(),
		schema=FakeGraph("schema"),
		memory=FakeGraph("memory"),
	)


def chunk(content=None, tool_calls=None, finish=None):
	"""A LiteLLM-shaped streaming chunk."""
	delta = SimpleNamespace(content=content, tool_calls=tool_calls)
	return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def call_piece(index, id=None, name=None, arguments=None):
	function = SimpleNamespace(name=name, arguments=arguments)
	return SimpleNamespace(index=index, id=id, function=function)


class ScriptedCompletion:
	"""Plays one scripted reply per call and remembers the messages it was sent."""

	def __init__(self, *replies):
		self.replies = list(replies)
		self.sent = []

	def __call__(self, **kwargs):
		self.sent.append(kwargs)
		return iter(self.replies.pop(0))


class ModelArgumentsTests(unittest.TestCase):
	def test_settings_provider_prefixes_map_to_litellm(self):
		self.assertEqual(
			model_arguments("anthropic:claude-sonnet-5-5", "key")["model"], "anthropic/claude-sonnet-5-5"
		)
		self.assertEqual(model_arguments("openai:gpt-4o")["model"], "openai/gpt-4o")
		self.assertEqual(model_arguments("google:gemini-2.5-pro")["model"], "gemini/gemini-2.5-pro")

	def test_api_key_passes_through_and_defaults_to_none(self):
		self.assertEqual(model_arguments("openai:gpt-4o", "sk-x")["api_key"], "sk-x")
		self.assertIsNone(model_arguments("openai:gpt-4o")["api_key"])

	def test_compatible_uses_openai_protocol_at_the_base_url(self):
		args = model_arguments("compatible:llama3.1", "", "http://10.0.0.5:11434/v1")
		self.assertEqual(args["model"], "openai/llama3.1")
		self.assertEqual(args["api_base"], "http://10.0.0.5:11434/v1")
		self.assertTrue(args["api_key"])  # OpenAI-compatible servers still want a non-empty key

	def test_bare_and_litellm_native_names(self):
		self.assertEqual(model_arguments("claude-sonnet-5-5")["model"], "anthropic/claude-sonnet-5-5")
		self.assertEqual(model_arguments("ollama/llama3.1")["model"], "ollama/llama3.1")
		self.assertEqual(model_arguments("ollama:llama3.1", "", "http://x")["model"], "ollama/llama3.1")


class HistoryTests(unittest.TestCase):
	def test_completed_tool_steps_become_call_and_result_messages(self):
		history = [
			{"id": "u1", "role": "user", "parts": [{"type": "text", "text": "Best customers?"}]},
			{
				"id": "a1",
				"role": "assistant",
				"parts": [
					{
						"type": "tool-top_customers",
						"toolName": "top_customers",
						"toolCallId": "call-1",
						"state": "output-available",
						"input": {"limit": 3},
						"output": {"sources": [{"doctype": "Customer", "name": "ACME"}]},
					},
					{"type": "text", "text": "ACME leads."},
				],
			},
			{"id": "u2", "role": "user", "parts": [{"type": "text", "text": "And last year?"}]},
		]
		messages = to_model_messages("SYSTEM", history)
		self.assertEqual(messages[0], {"role": "system", "content": "SYSTEM"})
		self.assertEqual(messages[1], {"role": "user", "content": "Best customers?"})
		call = messages[2]["tool_calls"][0]
		self.assertEqual(call["id"], "call-1")
		self.assertEqual(call["function"]["name"], "top_customers")
		self.assertEqual(messages[3]["role"], "tool")
		self.assertIn("ACME", messages[3]["content"])
		self.assertEqual(messages[4], {"role": "assistant", "content": "ACME leads."})
		self.assertEqual(messages[-1], {"role": "user", "content": "And last year?"})

	def test_older_tool_outputs_lose_their_rows(self):
		def reply(call_id, rows):
			return {
				"role": "assistant",
				"parts": [
					{
						"type": "tool-run_cypher",
						"toolCallId": call_id,
						"state": "output-available",
						"input": {},
						"output": {"queries": [{"title": "t", "cypher": "MATCH", "rows": rows}], "sources": [{"doctype": "Item", "name": "A"}]},
					}
				],
			}

		history = [reply("old", [{"a": 1}, {"a": 2}]), {"role": "user", "parts": [{"type": "text", "text": "more"}]}, reply("new", [{"a": 3}])]
		old, new = (m for m in to_model_messages("S", history) if m["role"] == "tool")
		self.assertIn('"rows": 2', old["content"])
		self.assertNotIn('"a": 1', old["content"])
		self.assertIn('"name": "A"', old["content"])
		self.assertIn('"a": 3', new["content"])

	def test_unfinished_tool_calls_are_left_out(self):
		history = [
			{
				"role": "assistant",
				"parts": [
					{"type": "tool-find_entity", "toolCallId": "c", "state": "input-available", "input": {}}
				],
			}
		]
		self.assertEqual(len(to_model_messages("S", history)), 1)


class TurnLoopTests(unittest.TestCase):
	def setUp(self):
		self.tool = Tool(
			"top_customers",
			"rank",
			TopCustomersArgs,
			lambda args: {"queries": [], "sources": [{"doctype": "Customer", "name": f"limit-{args.limit}"}]},
		)

	def run_turn_events(self, completion, tools=None):
		return list(
			run_turn(
				system="S",
				ui_messages=[{"role": "user", "parts": [{"type": "text", "text": "q"}]}],
				tools=tools or [self.tool],
				completion=completion,
				model_kwargs={"model": "anthropic/x"},
			)
		)

	def test_tool_call_then_answer(self):
		completion = ScriptedCompletion(
			[
				chunk(tool_calls=[call_piece(0, id="call-1", name="top_customers", arguments='{"li')]),
				chunk(tool_calls=[call_piece(0, arguments='mit": 3}')]),
				chunk(finish="tool_calls"),
			],
			[chunk(content="ACME "), chunk(content="leads."), chunk(finish="stop")],
		)
		events = self.run_turn_events(completion)

		self.assertEqual(events[0]["type"], "tool-start")
		self.assertEqual(events[0]["toolName"], "top_customers")
		self.assertEqual(events[0]["input"], {"limit": 3})
		self.assertEqual(events[1]["type"], "tool-output")
		self.assertEqual(events[1]["output"]["sources"][0]["name"], "limit-3")
		self.assertEqual([e["delta"] for e in events[2:]], ["ACME ", "leads."])

		# The second call carries the assistant's tool call and the tool's result.
		second = completion.sent[1]["messages"]
		self.assertEqual(second[-2]["tool_calls"][0]["id"], "call-1")
		self.assertEqual(second[-1]["role"], "tool")
		self.assertTrue(completion.sent[0]["stream"])
		self.assertTrue(completion.sent[0]["drop_params"])

	def test_failed_tool_is_reported_to_the_model_and_the_loop_continues(self):
		completion = ScriptedCompletion(
			[chunk(tool_calls=[call_piece(0, id="c", name="top_customers", arguments='{"limit": 999}')])],
			[chunk(content="Sorry.")],
		)
		events = self.run_turn_events(completion)
		self.assertEqual([e["type"] for e in events], ["tool-start", "tool-error", "text-delta"])
		self.assertIn("limit", events[1]["errorText"])
		self.assertIn("error", completion.sent[1]["messages"][-1]["content"])

	def test_unknown_tool_is_an_error_event(self):
		completion = ScriptedCompletion(
			[chunk(tool_calls=[call_piece(0, id="c", name="drop_everything", arguments="{}")])],
			[chunk(content="ok")],
		)
		events = self.run_turn_events(completion)
		self.assertEqual(events[1]["type"], "tool-error")
		self.assertIn("Unknown tool", events[1]["errorText"])

	def test_stops_after_max_steps_when_the_model_keeps_calling_tools(self):
		def always_tool(**kwargs):
			return iter([chunk(tool_calls=[call_piece(0, id="c", name="top_customers", arguments="{}")])])

		events = self.run_turn_events(always_tool)
		self.assertEqual(sum(1 for e in events if e["type"] == "tool-output"), 10)


class ToolTests(unittest.TestCase):
	def test_every_tool_is_registered_once_with_a_schema(self):
		tools = build_tools(make_ctx())
		names = [tool.name for tool in tools]
		self.assertEqual(len(names), len(set(names)))
		self.assertEqual(set(names), EXPECTED_TOOLS)
		for tool in tools:
			spec = tool.spec()
			self.assertEqual(spec["type"], "function")
			self.assertEqual(spec["function"]["name"], tool.name)
			self.assertEqual(spec["function"]["parameters"]["type"], "object")

	def test_run_cypher_refuses_writes_before_querying(self):
		data = FakeGraph()
		tools = {t.name: t for t in build_tools(make_ctx(data))}
		with self.assertRaises(WriteQueryError):
			tools["run_cypher"].run(
				tools["run_cypher"].params.model_validate(
					{"cypher": "MATCH (n) DETACH DELETE n", "purpose": "x"}
				)
			)
		self.assertEqual(data.calls, [])

	def test_run_cypher_adds_a_limit_and_keeps_named_sources(self):
		data = FakeGraph()
		data.ro = lambda title, cypher, params=None: QueryRun(
			title=title,
			cypher=cypher,
			params={},
			rows=[{"doctype": "Item", "name": "SDR-1"}, {"doctype": "Item", "name": None}],
			ms=0,
		)
		tool = next(t for t in build_tools(make_ctx(data)) if t.name == "run_cypher")
		out = tool.run(
			tool.params.model_validate(
				{"cypher": "MATCH (i:Item) RETURN i.doctype AS doctype, i.name AS name", "purpose": "items"}
			)
		)
		self.assertEqual(out["sources"], [{"doctype": "Item", "name": "SDR-1"}])
		self.assertEqual(out["queries"][0]["rows"][1]["name"], None)

	def test_run_cypher_logs_success_and_failure(self):
		data = FakeGraph()
		entries = []
		ctx = make_ctx(data)
		ctx.question, ctx.chat_id, ctx.log_cypher = "how many items?", "chat-1", entries.append
		tool = next(t for t in build_tools(ctx) if t.name == "run_cypher")
		tool.run(tool.params.model_validate({"cypher": "MATCH (i:Item) RETURN count(i) AS n", "purpose": "count"}))
		with self.assertRaises(WriteQueryError):
			tool.run(tool.params.model_validate({"cypher": "MATCH (n) DELETE n", "purpose": "bad"}))
		self.assertEqual([e["status"] for e in entries], ["Success", "Failed"])
		self.assertEqual(entries[0]["question"], "how many items?")
		self.assertEqual(entries[0]["chat_id"], "chat-1")
		self.assertTrue(entries[0]["cypher"].endswith("LIMIT 200"))
		self.assertIn("Write clause", entries[1]["error"])

	def test_date_filters_are_validated(self):
		with self.assertRaises(ValueError):
			TopCustomersArgs.model_validate({"date_from": "last month"})
		self.assertEqual(TopCustomersArgs.model_validate({"date_from": "2026-01-01"}).date_from, "2026-01-01")

	def test_fulltext_query_strips_operators(self):
		self.assertEqual(fulltext_query("Acme (Ltd) & Co_1"), "acme* ltd* co* 1*")
		self.assertEqual(fulltext_query("   "), "")


class ActionToolTests(unittest.TestCase):
	def propose(self, ctx, **values):
		tool = next(t for t in build_tools(ctx) if t.name == "propose_record")
		args = {"doctype": "Customer", "values": {"customer_name": "Ashish"}, "summary": "New customer Ashish", **values}
		return tool.run(tool.params.model_validate(args))

	def writable_ctx(self, **overrides):
		ctx = make_ctx()
		ctx.config.allow_create = True
		ctx.config.write_doctypes = ["Customer", "Sales Invoice"]
		ctx.can_write = lambda doctype, ptype: True
		for key, value in overrides.items():
			setattr(ctx.config, key, value)
		return ctx

	def test_refused_when_disabled_unlisted_or_forbidden(self):
		with self.assertRaisesRegex(ValueError, "not allowed to create"):
			self.propose(make_ctx())
		with self.assertRaisesRegex(ValueError, "may not write Customer"):
			self.propose(self.writable_ctx(write_doctypes=["Item"]))
		ctx = self.writable_ctx()
		ctx.can_write = lambda doctype, ptype: False
		with self.assertRaisesRegex(ValueError, "permission"):
			self.propose(ctx)
		with self.assertRaisesRegex(ValueError, "drafts"):
			self.propose(self.writable_ctx(), values={"customer_name": "A", "docstatus": 1})
		with self.assertRaisesRegex(ValueError, "exact record name"):
			self.propose(self.writable_ctx(allow_update=True), mode="update")

	def test_pending_action_keeps_child_rows(self):
		ctx = self.writable_ctx()
		out = self.propose(
			ctx,
			doctype="Sales Invoice",
			values={"customer": "Ashish", "items": [{"item_code": "SDR-1", "qty": 2}]},
			summary="Invoice for Ashish",
		)
		action = out["action"]
		self.assertEqual(action["status"], "pending")
		self.assertEqual(action["kind"], "create")
		self.assertEqual(action["values"]["items"], [{"item_code": "SDR-1", "qty": 2}])
		self.assertIn("confirm", out["note"])

	def test_record_form_splits_required_optional_and_tables(self):
		import json

		schema = FakeGraph("schema")
		fields = {
			"Sales Invoice": [
				{"fieldname": "customer", "fieldtype": "Link", "options": "Customer", "label": "Customer", "reqd": 1},
				{"fieldname": "naming_series", "fieldtype": "Select", "options": "ACC-", "reqd": 1},
				{"fieldname": "sb", "fieldtype": "Section Break"},
				{"fieldname": "status", "fieldtype": "Select", "options": "Draft\nPaid", "label": "Status"},
				{"fieldname": "items", "fieldtype": "Table", "options": "Sales Invoice Item", "label": "Items"},
			],
			"Sales Invoice Item": [
				{"fieldname": "item_code", "fieldtype": "Link", "options": "Item", "label": "Item", "reqd": 1},
				{"fieldname": "qty", "fieldtype": "Float", "label": "Qty", "reqd": 1},
			],
		}
		schema.ro = lambda title, cypher, params=None: QueryRun(
			title=title,
			cypher=cypher,
			params=params or {},
			rows=[{"doctype": params["doctype"], "is_submittable": True, "fields": json.dumps(fields[params["doctype"]])}]
			if params["doctype"] in fields
			else [],
			ms=0,
		)
		ctx = make_ctx()
		ctx.schema = schema
		tool = next(t for t in build_tools(ctx) if t.name == "record_form")
		form = tool.run(tool.params.model_validate({"doctype": "Sales Invoice"}))["form"]
		self.assertTrue(form["is_submittable"])
		self.assertEqual([f["fieldname"] for f in form["required"]], ["customer"])
		self.assertEqual(form["required"][0]["options"], "Customer")
		self.assertEqual(form["optional"][0]["choices"], ["Draft", "Paid"])
		self.assertEqual(form["tables"][0]["child_doctype"], "Sales Invoice Item")
		self.assertEqual([f["fieldname"] for f in form["tables"][0]["required"]], ["item_code", "qty"])
		self.assertFalse(form["writable"])


def make_scope(readable, own=()):
	parents = tuple(sorted(d for d in readable if d not in ("Sales Invoice Item",)))
	return AccessScope(
		key="k",
		full_access=False,
		readable=readable,
		user_permissions={},
		own_record_doctypes=tuple(own),
		parents=parents,
	)


class ScopeTests(unittest.TestCase):
	def scoped(self, readable, **kw):
		ctx = make_ctx()
		ctx.scope = make_scope(readable)
		for k, v in kw.items():
			setattr(ctx, k, v)
		return ctx

	def test_tools_hidden_without_visible_doctypes(self):
		ctx = self.scoped({"Customer": (0,), "Sales Order": (0,), "Item": (0,)})
		names = {t.name for t in build_tools(ctx)}
		self.assertNotIn("top_suppliers", names)
		self.assertNotIn("supplier_risk", names)
		self.assertIn("top_customers", names)

	def test_empty_scope_hides_data_tools(self):
		names = {t.name for t in build_tools(self.scoped({}))}
		self.assertFalse(names & set(TOOL_NEEDS))
		self.assertTrue({"find_entity", "run_cypher", "recall"} <= names)

	def test_result_drops_unreadable_sources_and_rows(self):
		hidden = []
		ctx = self.scoped(
			{"Customer": (0,)},
			can_read=lambda dt, name: (dt, name) != ("Sales Invoice", "SI-1"),
			on_hidden_source=hidden.append,
		)
		run = QueryRun("t", "RETURN 1", {}, [{"name": "SI-1"}, {"name": "SI-2"}], 0)
		sources = [{"doctype": "Sales Invoice", "name": "SI-1"}, {"doctype": "Sales Invoice", "name": "SI-2"}]
		out = result(ctx, [run], sources)
		self.assertEqual(out["sources"], [{"doctype": "Sales Invoice", "name": "SI-2"}])
		self.assertEqual(out["queries"][0]["rows"], [{"name": "SI-2"}])
		self.assertEqual(ctx.cited, out["sources"])
		self.assertEqual(hidden, [{"doctype": "Sales Invoice", "name": "SI-1", "tool": ""}])

	def test_scope_without_can_read_fails_closed(self):
		hidden = []
		ctx = self.scoped({"Customer": (0,)}, on_hidden_source=hidden.append)
		run = QueryRun("t", "RETURN 1", {}, [{"name": "SI-1"}, {"name": "X"}], 0)
		out = result(ctx, [run], [{"doctype": "Sales Invoice", "name": "SI-1"}])
		self.assertEqual(out["sources"], [])
		self.assertEqual(out["queries"][0]["rows"], [{"name": "X"}])
		self.assertEqual(len(hidden), 1)
		from eve_agent.chat.memory import readable_refs

		self.assertEqual(readable_refs(ctx, [{"doctype": "Customer", "name": "Acme"}]), [])
		ctx.scope = None
		self.assertEqual(len(readable_refs(ctx, [{"doctype": "Customer", "name": "Acme"}])), 1)

	def test_nested_rows_naming_hidden_source_are_dropped(self):
		ctx = self.scoped({}, can_read=lambda dt, name: name != "SI-1")
		rows = [
			{"nodes": [{"doctype": "Sales Invoice", "name": "SI-1"}]},
			{"nodes": [{"doctype": "Sales Invoice", "name": "SI-10"}]},
		]
		run = QueryRun("t", "RETURN 1", {}, rows, 0)
		out = result(ctx, [run], [{"doctype": "Sales Invoice", "name": "SI-1"}])
		self.assertEqual(out["queries"][0]["rows"], [rows[1]])

	def test_prompt_lists_visible_doctypes(self):
		ctx = self.scoped({"Customer": (0,), "Sales Order": (0,), "Item": (0,), "Employee": (0,)})
		ctx.scope.own_record_doctypes = ("Employee",)
		prompt = system_prompt(ctx.config, ctx.user, "", ctx.scope)
		self.assertIn("Visible DocTypes: Customer, Item, Sales Order.", prompt)
		self.assertIn("Never say the data doesn't exist and never guess it.", prompt)
		self.assertNotIn("Visible DocTypes", system_prompt(ctx.config, ctx.user, ""))

	def test_describe_refuses_and_filters_levels(self):
		fields = [
			{"fieldname": "customer_name", "fieldtype": "Data"},
			{"fieldname": "credit_limit", "fieldtype": "Currency", "permlevel": 1},
		]

		class Schema(FakeGraph):
			def ro(self, title, cypher, params=None):
				run = super().ro(title, cypher, params)
				run.rows = [
					{"doctype": "Customer", "fields": json.dumps(fields), "links": [
						{"type": "LINKS_TO", "field": "credit_limit", "target": "X"},
					]}
				]
				return run

		ctx = self.scoped({"Customer": (0,)})
		ctx.schema = Schema("schema")
		tools = {t.name: t for t in build_tools(ctx)}
		describe = tools["describe_doctype"]
		with self.assertRaisesRegex(ValueError, "You don't have access to Sales Invoice."):
			describe.run(describe.params.model_validate({"doctype": "Sales Invoice"}))
		row = describe.run(describe.params.model_validate({"doctype": "Customer"}))["queries"][0]["rows"][0]
		self.assertEqual(row["fields"], ["customer_name:Data"])
		self.assertEqual(row["links"], [])
		form_tool = tools["record_form"]
		form = form_tool.run(form_tool.params.model_validate({"doctype": "Customer"}))["form"]
		self.assertEqual([f["fieldname"] for f in form["optional"]], ["customer_name"])
		with self.assertRaises(ValueError):
			form_tool.run(form_tool.params.model_validate({"doctype": "Sales Invoice"}))
		ctx.scope.readable["Customer"] = (0, 1)
		row = describe.run(describe.params.model_validate({"doctype": "Customer"}))["queries"][0]["rows"][0]
		self.assertEqual(len(row["fields"]), 2)

	def test_memory_hides_unreadable_mentions(self):
		class Memory(FakeGraph):
			def ro(self, title, cypher, params=None):
				run = super().ro(title, cypher, params)
				if title == "Past messages":
					run.rows = [{"text": "hi", "mentions": [
						{"doctype": "Sales Invoice", "name": "SI-1"}, {"doctype": "Customer", "name": "Acme"},
					]}]
				elif title == "Memory context":
					run.rows = [{"interests": [
						{"doctype": "Sales Invoice", "name": "SI-1", "count": 2},
						{"doctype": "Customer", "name": "Acme", "count": 1},
						{"doctype": None, "name": None, "count": None},
					], "recent": [], "insights": []}]
				return run

		ctx = self.scoped({"Customer": (0,)}, can_read=lambda dt, name: dt == "Customer")
		ctx.memory = Memory("memory")
		recall = next(t for t in build_tools(ctx) if t.name == "recall")
		out = recall.run(recall.params.model_validate({}))
		self.assertEqual(out["queries"][0]["rows"][0]["mentions"], ["Customer Acme"])
		summary = memory_context(ctx)
		self.assertIn("Customer Acme (asked 1x)", summary)
		self.assertNotIn("SI-1", summary)


class FallbackTests(unittest.TestCase):
	"""Users who cannot read invoices get order-based answers and a note saying so."""

	class RowGraph(FakeGraph):
		def ro(self, title, cypher, params=None):
			run = super().ro(title, cypher, params)
			if title in ("Customer summary", "Item, suppliers, stock and BOMs"):
				run.rows = [{"customer": "Acme", "item": "I-1"}]
			return run

	def ctx_for(self, readable, data=None):
		ctx = make_ctx(data)
		ctx.scope = make_scope(readable)
		return ctx

	def no_invoices(self, data=None):
		readable = {"Customer": (0,), "Item": (0,), "Supplier": (0,), "Sales Order": (0,), "Purchase Order": (0,)}
		return self.ctx_for(readable, data)

	def call(self, ctx, name, **args):
		tool = next(t for t in build_tools(ctx) if t.name == name)
		return tool.run(tool.params.model_validate(args))

	def test_top_customers_falls_back_to_orders(self):
		data = FakeGraph()
		out = self.call(self.no_invoices(data), "top_customers", by="revenue")
		cypher = data.calls[0][1]
		self.assertIn("`Sales Order`", cypher)
		self.assertNotIn("`Sales Invoice`", cypher)
		self.assertEqual(out["note"], SALES_ORDERS_NOTE)

	def test_outstanding_ranking_falls_back_with_note(self):
		out = self.call(self.no_invoices(), "top_customers", by="outstanding")
		self.assertEqual(out["note"], OUTSTANDING_NOTE)

	def test_every_fallback_avoids_invoices(self):
		data = self.RowGraph()
		ctx = self.no_invoices(data)
		calls = {
			"top_customers": {},
			"dormant_customers": {},
			"customer_profile": {"customer": "Acme"},
			"top_selling_items": {},
			"item_profile": {"item": "I-1"},
			"also_bought": {"item": "I-1"},
			"top_suppliers": {},
			"supplier_risk": {},
		}
		for name, args in calls.items():
			out = self.call(ctx, name, **args)
			self.assertTrue(out["note"], name)
		self.assertTrue(data.calls)
		for title, cypher, _ in data.calls:
			self.assertNotIn("Sales Invoice", cypher, title)
			self.assertNotIn("Purchase Invoice", cypher, title)
			if title != "Item, suppliers, stock and BOMs":  # Reads SUPPLIES and STOCKED_IN, not orders.
				self.assertTrue("Sales Order" in cypher or "Purchase Order" in cypher, title)

	def test_purchase_fallback_note(self):
		out = self.call(self.no_invoices(), "top_suppliers", by="invoices")
		self.assertEqual(out["note"], PURCHASE_ORDERS_NOTE)

	def test_unknown_figures_are_null_not_zero(self):
		data = FakeGraph()
		ctx = self.no_invoices(data)
		self.call(ctx, "top_customers", by="revenue")
		from eve_agent.chat.tools.entities import CUSTOMER_DOCS_ORDERS, CUSTOMER_SUMMARY_ORDERS

		self.assertIn("null AS outstanding", data.calls[0][1])
		self.assertNotIn("0 AS outstanding", data.calls[0][1])
		self.assertIn("null AS outstanding", CUSTOMER_SUMMARY_ORDERS)
		self.assertIn("null AS paid", CUSTOMER_DOCS_ORDERS)
		self.assertNotIn(" 0 AS", CUSTOMER_DOCS_ORDERS + CUSTOMER_SUMMARY_ORDERS)

	def test_supplier_risk_without_sales_access_is_null(self):
		data = FakeGraph()
		readable = {"Item": (0,), "Supplier": (0,), "Purchase Order": (0,)}
		out = self.call(self.ctx_for(readable, data), "supplier_risk")
		cypher = data.calls[0][1]
		self.assertIn("null AS revenue_at_risk", cypher)
		self.assertIn("null AS customers", cypher)
		self.assertIn("Revenue at risk isn't available", out["note"])

	def test_profiles_without_any_sales_access_are_null_with_honest_note(self):
		data = self.RowGraph()
		ctx = self.ctx_for({"Customer": (0,), "Item": (0,)}, data)
		out = self.call(ctx, "customer_profile", customer="Acme")
		self.assertNotIn("Based on submitted sales orders", out["note"])
		self.assertIn("Sales figures aren't available", out["note"])
		cypher = data.calls[0][1]
		self.assertIn("null AS revenue", cypher)
		self.assertNotIn("Sales Order`", cypher)
		out = self.call(ctx, "item_profile", item="I-1")
		self.assertIn("Sales figures aren't available", out["note"])
		self.assertEqual(len(out["queries"]), 1)

	def test_invoice_scope_unchanged(self):
		data = FakeGraph()
		out = self.call(make_ctx(data), "top_customers", by="revenue")
		self.assertEqual(data.calls[0][1], top_customers_cypher("revenue"))
		self.assertNotIn("note", out)
		readable = {"Customer": (0,), "Item": (0,), "Sales Invoice": (0,), "Sales Order": (0,)}
		data = FakeGraph()
		out = self.call(self.ctx_for(readable, data), "top_customers", by="revenue")
		self.assertEqual(data.calls[0][1], top_customers_cypher("revenue"))
		self.assertNotIn("note", out)


class PlainTests(unittest.TestCase):
	def test_nodes_and_edges_become_plain_properties(self):
		decoded = {
			"name": "SINV-1",
			"_labels": ["Sales Invoice", "Record"],
			"nested": [{"name": "A", "_labels": ["Customer"]}],
			"rel": {"amount": 5, "_type": "PAID_BY"},
		}
		self.assertEqual(
			plain(decoded),
			{"name": "SINV-1", "nested": [{"name": "A"}], "rel": {"amount": 5, "type": "PAID_BY"}},
		)


class MemoryFormatTests(unittest.TestCase):
	def test_entity_refs_are_unique_and_capped(self):
		sources = [{"doctype": "Customer", "name": "A"}, {"doctype": "Customer", "name": "A"}]
		sources += [{"doctype": "Item", "name": f"I{i}"} for i in range(80)]
		refs = entity_refs(sources)
		self.assertEqual(refs[0]["key"], "Customer|A")
		self.assertEqual(len(refs), 50)
		self.assertEqual(refs[0]["title"], "")


@unittest.skipUnless(os.environ.get("EVE_TEST_LIVE", "1") != "0", "live tests disabled")
class LiveGraphTests(unittest.TestCase):
	"""Every tool against a real data graph, plus one scripted turn that is remembered."""

	url = os.environ.get("EVE_TEST_REDIS", "redis://127.0.0.1:6500")
	graph = os.environ.get("EVE_TEST_GRAPH", "eve_local_data")
	memory = "eve_test_memory"

	@classmethod
	def setUpClass(cls):
		import redis

		try:
			cls.client = redis.Redis.from_url(cls.url, decode_responses=True, socket_connect_timeout=2)
			if cls.graph not in cls.client.execute_command("GRAPH.LIST"):
				raise unittest.SkipTest(f"graph {cls.graph} is not in FalkorDB")
		except unittest.SkipTest:
			raise
		except Exception as exc:
			raise unittest.SkipTest(f"FalkorDB not reachable at {cls.url}: {exc}")

	@classmethod
	def tearDownClass(cls):
		try:
			cls.client.execute_command("GRAPH.DELETE", cls.memory)
		except Exception:
			pass  # The memory graph was never created.

	def setUp(self):
		schema = re.sub(r"_data(_next)?$", "_schema", self.graph)
		self.ctx = make_ctx()
		self.ctx.data = Graph(self.url, self.graph)
		self.ctx.schema = Graph(self.url, schema)
		self.ctx.memory = Graph(self.url, self.memory)
		self.tools = {t.name: t for t in build_tools(self.ctx)}

	def call(self, tool_name, **args):
		tool = self.tools[tool_name]
		return tool.run(tool.params.model_validate(args))

	def top_customer(self):
		return self.call("top_customers", by="revenue", limit=1)["queries"][0]["rows"][0]["customer"]

	def test_ranks_best_selling_items_and_best_customers(self):
		items = self.call("top_selling_items", by="amount", limit=5)
		amounts = [r["amount"] for r in items["queries"][0]["rows"]]
		self.assertTrue(amounts)
		self.assertEqual(sorted(amounts, reverse=True), amounts)
		self.assertEqual(items["sources"][0]["doctype"], "Item")
		customers = self.call("top_customers", by="revenue", limit=5)
		self.assertIn("revenue", customers["queries"][0]["rows"][0])

	def test_finds_profiles_and_connects_entities(self):
		name = self.top_customer()
		found = self.call("find_entity", text=name.split(" ")[0], doctype="Customer")
		self.assertIn(name, [s["name"] for s in found["sources"]])

		profile = self.call("customer_profile", customer=name)
		self.assertEqual(len(profile["queries"]), 3)
		item = next((r["item"] for r in profile["queries"][1]["rows"]), None)
		if item:
			also = self.call("also_bought", item=item, limit=5)
			self.assertIn("BOUGHT", also["queries"][0]["cypher"])
			self.assertEqual(self.call("item_profile", item=item)["queries"][0]["rows"][0]["item"], item)

		supplier = self.call("top_suppliers", limit=1)["queries"][0]["rows"][0]["supplier"]
		path = self.call(
			"explain_connection",
			from_doctype="Customer",
			from_name=name,
			to_doctype="Supplier",
			to_name=supplier,
		)
		self.assertEqual(len(path["queries"][0]["rows"]), 1)

	def test_runs_the_company_brain_and_schema_tools(self):
		name = self.top_customer()
		who = self.call("who_knows", doctype="Customer", name=name)
		self.assertIsInstance(who["people"], list)

		order = self.call(
			"run_cypher",
			cypher="MATCH (s:`Sales Order`) RETURN s.doctype AS doctype, s.name AS name LIMIT 1",
			purpose="one order",
		)
		trail = self.call(
			"decision_trail", doctype="Sales Order", name=order["queries"][0]["rows"][0]["name"]
		)
		self.assertTrue(trail["queries"][0]["rows"])

		self.call("dormant_customers", days=30, limit=5)
		self.call("supplier_risk", limit=5)
		self.assertTrue(self.call("graph_schema")["queries"][1]["rows"])
		desc = self.call("describe_doctype", doctype="Sales Invoice")
		self.assertIn("posting_date:Date", desc["queries"][0]["rows"][0]["fields"])

	def test_refuses_writes_in_run_cypher(self):
		with self.assertRaisesRegex(WriteQueryError, "not allowed"):
			self.call("run_cypher", cypher="MATCH (n) DETACH DELETE n", purpose="x")

	def test_a_scripted_turn_streams_sources_and_is_remembered(self):
		# The model calls top_customers, then answers. The turn is saved to the memory graph.
		completion = ScriptedCompletion(
			[
				chunk(
					tool_calls=[call_piece(0, id="call-1", name="top_customers", arguments='{"limit": 3}')]
				),
				chunk(finish="tool_calls"),
			],
			[chunk(content="Your best customer is at the top of the table."), chunk(finish="stop")],
		)
		question = "Who are our best customers?"
		events = list(
			run_turn(
				system=system_prompt(self.ctx.config, self.ctx.user, ""),
				ui_messages=[{"role": "user", "parts": [{"type": "text", "text": question}]}],
				tools=list(self.tools.values()),
				completion=completion,
				model_kwargs={"model": "anthropic/x"},
			)
		)
		output = next(e for e in events if e["type"] == "tool-output")
		self.assertTrue(any(s["doctype"] == "Customer" for s in output["output"]["sources"]))
		self.assertIn("FOR_CUSTOMER", output["output"]["queries"][0]["cypher"])
		self.assertEqual(events[-1]["delta"], "Your best customer is at the top of the table.")

		save_turn(self.ctx, "chat-1", question, "Your best customer is at the top of the table.")
		summary = memory_context(self.ctx)
		self.assertIn(question, summary)
		self.assertRegex(summary, r"Customer .+asked 1x")


if __name__ == "__main__":
	unittest.main()
