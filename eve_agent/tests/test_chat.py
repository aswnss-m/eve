"""Tests for the Ask Eve chat core: model mapping, the turn loop, the tools and the memory format.

No Frappe and no LLM are needed. The model is a scripted completion function, and
the graph is a fake that records the Cypher it is given. Run with:
  env/bin/python -m unittest eve_agent.tests.test_chat   (from apps/eve_agent)

The live class runs against FalkorDB when it is reachable (EVE_TEST_REDIS,
default redis://127.0.0.1:6500; EVE_TEST_GRAPH, default eve_local_data) and skips otherwise.
"""

import os
import re
import unittest
from types import SimpleNamespace

from eve_agent.chat.common import ChatConfig, ChatUser, QueryRun, Tool, ToolContext
from eve_agent.chat.graph import Graph, plain
from eve_agent.chat.llm import model_arguments
from eve_agent.chat.loop import run_turn, to_model_messages
from eve_agent.chat.memory import entity_refs, memory_context, save_turn
from eve_agent.chat.prompt import system_prompt
from eve_agent.chat.tools import build_tools
from eve_agent.chat.tools.entities import fulltext_query
from eve_agent.chat.tools.sales import TopCustomersArgs
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

	def test_date_filters_are_validated(self):
		with self.assertRaises(ValueError):
			TopCustomersArgs.model_validate({"date_from": "last month"})
		self.assertEqual(TopCustomersArgs.model_validate({"date_from": "2026-01-01"}).date_from, "2026-01-01")

	def test_fulltext_query_strips_operators(self):
		self.assertEqual(fulltext_query("Acme (Ltd) & Co_1"), "acme* ltd* co* 1*")
		self.assertEqual(fulltext_query("   "), "")


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
