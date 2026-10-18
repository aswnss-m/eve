"""Pure-Python tests for charts in Ask Eve: query ids, the show_chart builder and the trend tools. Run with:
env/bin/python -m unittest eve_agent.tests.test_charts  (from apps/eve_agent)."""

import unittest
from datetime import date

from eve_agent.chat.common import QueryRun, result
from eve_agent.chat.prompt import system_prompt
from eve_agent.chat.tools import build_tools
from eve_agent.chat.tools.charts import ShowChartArgs, build_chart, chart_tools
from eve_agent.chat.tools.sales import PURCHASE_ORDERS_NOTE, SALES_ORDERS_NOTE
from eve_agent.tests.test_chat import make_ctx, make_scope

TODAY = date(2026, 10, 18)
MONTHS = [
	{"period": "2026-01", "amount": 543153},
	{"period": "2026-02", "amount": 759040},
	{"period": "2026-03", "amount": 825748},
]


def run_of(rows: list[dict], title: str = "Sales trend") -> QueryRun:
	return QueryRun(title=title, cypher="MATCH (n) RETURN n", params={}, rows=rows, ms=1)


def show_chart(ctx, **kwargs):
	tool = next(t for t in chart_tools(ctx) if t.name == "show_chart")
	args = {"type": "line", "x": "period", "y": ["amount"], "title": "Sales"} | kwargs
	return tool.run(ShowChartArgs(**args))


class QueryIdTests(unittest.TestCase):
	def test_result_assigns_query_ids_in_order(self):
		ctx = make_ctx()
		first, second = run_of(MONTHS), run_of(MONTHS)
		out1 = result(ctx, [first], [])
		out2 = result(ctx, [second], [])
		self.assertEqual(out1["queries"][0]["id"], "q1")
		self.assertEqual(out2["queries"][0]["id"], "q2")
		self.assertIs(ctx.runs["q1"], first)
		self.assertIs(ctx.runs["q2"], second)

	def test_cached_run_gets_a_fresh_id(self):
		ctx = make_ctx()
		cached = run_of(MONTHS)
		cached.id = "q7"  # left over from an earlier turn
		out = result(ctx, [cached], [])
		self.assertEqual(out["queries"][0]["id"], "q1")
		self.assertNotIn("q7", ctx.runs)


class ShowChartTests(unittest.TestCase):
	def test_line_chart_copies_rows(self):
		ctx = make_ctx()
		result(ctx, [run_of(MONTHS)], [])
		chart = show_chart(ctx, query="q1")["chart"]
		self.assertEqual(chart["type"], "line")
		self.assertEqual(chart["labels"], ["2026-01", "2026-02", "2026-03"])
		self.assertEqual(chart["datasets"], [{"name": "amount", "values": [543153, 759040, 825748]}])
		self.assertEqual(chart["unit"], "currency")

	def test_unknown_query_id_is_refused(self):
		ctx = make_ctx()
		result(ctx, [run_of(MONTHS)], [])
		with self.assertRaisesRegex(ValueError, "q9"):
			show_chart(ctx, query="q9")

	def test_unknown_column_is_refused(self):
		ctx = make_ctx()
		result(ctx, [run_of(MONTHS)], [])
		with self.assertRaisesRegex(ValueError, "nope"):
			show_chart(ctx, query="q1", y=["nope"])

	def test_null_values_are_refused(self):
		rows = [*MONTHS, {"period": "2026-04", "amount": None}]
		with self.assertRaises(ValueError):
			build_chart(run_of(rows), "line", "period", ["amount"], "Sales", TODAY)

	def test_non_numeric_values_are_refused(self):
		rows = [*MONTHS, {"period": "2026-04", "amount": "lots"}]
		with self.assertRaises(ValueError):
			build_chart(run_of(rows), "line", "period", ["amount"], "Sales", TODAY)

	def test_too_few_values_are_refused(self):
		with self.assertRaises(ValueError):
			build_chart(run_of(MONTHS[:2]), "line", "period", ["amount"], "Sales", TODAY)

	def test_counts_are_numbers_not_currency(self):
		rows = [{"period": p["period"], "documents": i + 3} for i, p in enumerate(MONTHS)]
		chart = build_chart(run_of(rows), "bar", "period", ["documents"], "Invoices", TODAY)
		self.assertEqual(chart["unit"], "number")

	def test_donut_groups_the_rest_into_other(self):
		rows = [{"group": f"G{v}", "amount": v} for v in range(12, 0, -1)]
		chart = build_chart(run_of(rows), "donut", "group", ["amount"], "Mix", TODAY)
		self.assertEqual(len(chart["labels"]), 8)
		self.assertEqual(chart["labels"][-1], "Other")
		self.assertEqual(chart["datasets"][0]["values"][-1], 5 + 4 + 3 + 2 + 1)
		self.assertEqual(chart["labels"][:7], ["G12", "G11", "G10", "G9", "G8", "G7", "G6"])

	def test_donut_takes_one_value_column(self):
		rows = [{"group": f"G{v}", "amount": v, "documents": v} for v in range(1, 5)]
		with self.assertRaises(ValueError):
			build_chart(run_of(rows), "donut", "group", ["amount", "documents"], "Mix", TODAY)

	def test_too_many_bars_are_refused(self):
		rows = [{"customer": f"C{i}", "revenue": i} for i in range(21)]
		with self.assertRaises(ValueError):
			build_chart(run_of(rows), "bar", "customer", ["revenue"], "Top", TODAY)

	def test_partial_last_period(self):
		def partial(rows):
			return build_chart(run_of(rows), "line", "period", ["amount"], "S", TODAY)["partial_last"]

		rows = [*MONTHS, {"period": "2026-10", "amount": 111055}]
		self.assertTrue(partial(rows))
		rows[-1]["period"] = "2026-09"
		self.assertFalse(partial(rows))
		self.assertTrue(partial([{"period": q, "amount": 1} for q in ("2026-Q2", "2026-Q3", "2026-Q4")]))

	def test_rows_dropped_by_the_safety_net_are_not_charted(self):
		ctx = make_ctx()
		ctx.can_read = lambda doctype, name: name != "Apex"
		ranking = (("Apex", 9), ("Titan", 7), ("Vertex", 5), ("Orion", 4))
		rows = [{"customer": n, "revenue": r} for n, r in ranking]
		sources = [{"doctype": "Customer", "name": r["customer"]} for r in rows]
		result(ctx, [run_of(rows, "Top customers")], sources)
		chart = show_chart(ctx, query="q1", type="bar", x="customer", y=["revenue"])["chart"]
		self.assertNotIn("Apex", chart["labels"])
		self.assertEqual(chart["labels"], ["Titan", "Vertex", "Orion"])

	def test_show_chart_output_shape(self):
		ctx = make_ctx()
		result(ctx, [run_of(MONTHS)], [])
		out = show_chart(ctx, query="q1")
		self.assertEqual(out["queries"], [])
		self.assertEqual(out["sources"], [])
		self.assertIn("as_of", out)


CHARTS_RULE = (
	"Charts: after a data tool returns a time series, a ranking or a breakdown with at least 3 values, "
	"call show_chart with that query's id (for example q1), the label column and the value column(s): "
	"line for trends, bar for rankings, donut for parts of a whole. One chart per answer unless asked. "
	"Still write the one-line summary. Never put numbers in show_chart. "
	"If the user asks for just the table, don't chart."
)
SALES_VISIBLE = {"Customer": (0,), "Sales Invoice": (0,), "Sales Order": (0,), "Item": (0,)}
ORDERS_ONLY = {"Customer": (0,), "Sales Order": (0,), "Item": (0,), "Supplier": (0,), "Purchase Order": (0,)}


class TrendTests(unittest.TestCase):
	def ctx(self, readable=None):
		ctx = make_ctx()
		if readable is not None:
			ctx.scope = make_scope(readable)
		return ctx

	def run_trend(self, ctx, name, **kwargs):
		tool = next(t for t in build_tools(ctx) if t.name == name)
		return tool.run(tool.params(**kwargs))

	def cypher(self, ctx):
		return ctx.data.calls[-1][1]

	def test_sales_trend_reads_invoices_by_month(self):
		ctx = self.ctx()
		out = self.run_trend(ctx, "sales_trend", date_from="2026-01-01", date_to="2026-12-31")
		self.assertIn("`Sales Invoice`", self.cypher(ctx))
		self.assertIn("substring(d.posting_date, 0, 7)", self.cypher(ctx))
		self.assertNotIn("note", out)
		self.assertEqual(ctx.data.calls[-1][2]["date_from"], "2026-01-01")

	def test_sales_trend_falls_back_to_orders(self):
		ctx = self.ctx(ORDERS_ONLY)
		out = self.run_trend(ctx, "sales_trend")
		self.assertIn("`Sales Order`", self.cypher(ctx))
		self.assertNotIn("Sales Invoice", self.cypher(ctx))
		self.assertEqual(out["note"], SALES_ORDERS_NOTE)

	def test_total_by_item_group_feeds_a_donut(self):
		ctx = self.ctx()
		self.run_trend(ctx, "sales_trend", interval="total", group_by="item_group")
		cypher = self.cypher(ctx)
		self.assertIn("AS group", cypher)
		self.assertIn("i.item_group", cypher)
		self.assertNotIn("substring(d.posting_date, 0, 7)", cypher)

	def test_quarters_are_labelled(self):
		ctx = self.ctx()
		self.run_trend(ctx, "sales_trend", interval="quarter")
		self.assertIn("'-Q'", self.cypher(ctx))

	def test_purchase_trend_falls_back_to_purchase_orders(self):
		ctx = self.ctx(ORDERS_ONLY)
		out = self.run_trend(ctx, "purchase_trend")
		self.assertIn("`Purchase Order`", self.cypher(ctx))
		self.assertNotIn("Purchase Invoice", self.cypher(ctx))
		self.assertEqual(out["note"], PURCHASE_ORDERS_NOTE)

	def test_empty_period_returns_no_rows(self):
		ctx = self.ctx()
		out = self.run_trend(ctx, "sales_trend", date_from="2030-01-01", date_to="2030-12-31")
		self.assertEqual(out["queries"][0]["rows"], [])
		self.assertEqual(out["queries"][0]["id"], "q1")

	def test_prompt_has_charts_rule(self):
		ctx = self.ctx()
		self.assertIn(CHARTS_RULE, system_prompt(ctx.config, ctx.user, ""))

	def test_trend_tools_gated(self):
		ctx = self.ctx({"Customer": (0,), "Sales Order": (0,)})
		names = {t.name for t in build_tools(ctx)}
		self.assertIn("sales_trend", names)
		self.assertNotIn("purchase_trend", names)


if __name__ == "__main__":
	unittest.main()
