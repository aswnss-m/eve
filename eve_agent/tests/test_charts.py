"""Pure-Python tests for charts in Ask Eve: query ids, the show_chart builder and the trend tools. Run with:
env/bin/python -m unittest eve_agent.tests.test_charts  (from apps/eve_agent)."""

import unittest
from datetime import date

from eve_agent.chat.common import QueryRun, result
from eve_agent.chat.tools.charts import ShowChartArgs, build_chart, chart_tools
from eve_agent.tests.test_chat import make_ctx

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
		rows = [{"customer": n, "revenue": r} for n, r in (("Apex", 9), ("Titan", 7), ("Vertex", 5), ("Orion", 4))]
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


if __name__ == "__main__":
	unittest.main()
