"""Pure-Python tests for the data graph planner, rows and importer. Run with:
env/bin/python -m unittest eve_agent.tests.test_data_rows  (from apps/eve_agent)."""

import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from eve_agent.falkor.connection import QueryResult
from eve_agent.falkor.cypher.data_queries import quote_label
from eve_agent.falkor.importer.data_import import import_data
from eve_agent.falkor.importer.data_rows import (
	build_link_edges,
	build_record_row,
	coerce,
	doctype_plan,
	parse_lines,
	plan_from_schema,
	schema_node_from_export_row,
)


def field(fieldname, fieldtype, options=None):
	return {"fieldname": fieldname, "fieldtype": fieldtype, "options": options}


NODES = [
	{
		"name": "Sales Order",
		"module": "Selling",
		"title_field": "customer_name",
		"fields": [
			field("customer", "Link", "Customer"),
			field("customer_name", "Data"),
			field("items", "Table", "Sales Order Item"),
			field("party_type", "Link", "DocType"),
			field("party", "Dynamic Link", "party_type"),
			field("terms", "Text Editor"),
			field("sb", "Section Break"),
			field("grand_total", "Currency"),
		],
	},
	{"name": "Sales Order Item", "module": "Selling", "is_child": True, "fields": [field("item_code", "Link", "Item"), field("qty", "Float")]},
	{"name": "Customer", "module": "Selling", "fields": [field("customer_name", "Data")]},
	{"name": "Item", "module": "Stock", "fields": [field("item_name", "Data")]},
	{"name": "Selling Settings", "module": "Selling", "is_single": True, "fields": []},
	{"name": "Error Log", "module": "Core", "fields": []},
]


class PlanTests(unittest.TestCase):
	def test_modules_and_exclude(self):
		plan = plan_from_schema(NODES, modules=["Selling", "Stock"], exclude=["Customer"])
		self.assertEqual([p.name for p in plan.parents], ["Item", "Sales Order"])
		self.assertEqual(list(plan.children), ["Sales Order Item"])
		self.assertTrue(plan.children["Sales Order Item"].is_child)

	def test_extra_doctypes_join_once(self):
		plan = plan_from_schema(NODES, modules=["Selling"], extra=["Item", "Customer", "Selling Settings", "Nope"])
		self.assertEqual([p.name for p in plan.parents], ["Customer", "Item", "Sales Order"])
		self.assertEqual([p.name for p in plan_from_schema(NODES, modules=["Selling"], exclude=["Item"], extra=["Item"]).parents], ["Customer", "Sales Order"])

	def test_field_selection(self):
		so = plan_from_schema(NODES, modules=["Selling"]).parents[-1]
		self.assertEqual(so.value_fields, ["customer", "customer_name", "party_type", "party", "grand_total"])
		self.assertEqual(so.links, {"customer": "Customer", "party_type": "DocType"})
		self.assertEqual(so.dynamic_links, {"party": "party_type"})
		self.assertEqual(so.tables, {"items": "Sales Order Item"})
		self.assertIn("parent", plan_from_schema(NODES, modules=["Selling"]).children["Sales Order Item"].fetch_fields)

	def test_field_levels_come_from_schema(self):
		node = {
			"name": "Customer",
			"fields": [
				{"fieldname": "credit_limit", "fieldtype": "Currency", "permlevel": 1},
				{"fieldname": "customer_name", "fieldtype": "Data"},
			],
		}
		self.assertEqual(doctype_plan(node).field_levels, {"credit_limit": 1, "customer_name": 0})

	def test_text_field_level_comes_from_schema(self):
		node = {"name": "Comment", "fields": [{"fieldname": "content", "fieldtype": "Long Text", "permlevel": 1}]}
		self.assertEqual(doctype_plan(node).field_levels, {"content": 1})

	def test_export_row_booleans_are_parsed(self):
		row = ["Sales Order Item", "Selling", "false", "false", "true", "false", "", 0, [], [], [], "[]", ""]
		node = schema_node_from_export_row(row)
		self.assertTrue(node["is_child"])
		self.assertFalse(node["is_single"])

	def test_parse_lines(self):
		self.assertEqual(parse_lines(" HR\n\nPayroll \n"), ["HR", "Payroll"])
		self.assertEqual(parse_lines(None), [])


class RowTests(unittest.TestCase):
	def test_coerce(self):
		self.assertIsNone(coerce(None))
		self.assertIsNone(coerce(""))
		self.assertEqual(coerce(Decimal("1.50")), 1.5)
		self.assertEqual(coerce(date(2026, 1, 2)), "2026-01-02")
		self.assertEqual(coerce(datetime(2026, 1, 2, 3, 4, 5)), "2026-01-02 03:04:05")
		self.assertEqual(coerce(timedelta(hours=9)), "9:00:00")
		self.assertEqual(coerce(float("nan")), None)
		self.assertEqual(len(coerce("x" * 900)), 500)

	def test_record_row(self):
		so = plan_from_schema(NODES, modules=["Selling"]).parents[-1]
		row = build_record_row(so, {"name": "SO-1", "docstatus": 1, "customer": "C1", "customer_name": "Acme", "grand_total": Decimal("10")})
		self.assertEqual(row["doctype"], "Sales Order")
		self.assertEqual(row["title"], "Acme")
		self.assertEqual(row["grand_total"], 10.0)
		self.assertNotIn("party", row)

	def test_link_edges_skip_unknown_and_resolve_dynamic(self):
		so = plan_from_schema(NODES, modules=["Selling"]).parents[-1]
		record = {"name": "SO-1", "customer": "C1", "party_type": "Customer", "party": "C2"}
		edges = build_link_edges(so, record, {"Customer"})
		self.assertEqual(
			edges,
			[
				("Customer", {"src": "SO-1", "dst": "C1", "field": "customer"}),
				("Customer", {"src": "SO-1", "dst": "C2", "field": "party"}),
			],
		)
		self.assertEqual(build_link_edges(so, record, set()), [])

	def test_quote_label(self):
		self.assertEqual(quote_label("Sales Order"), "`Sales Order`")
		self.assertEqual(quote_label("a`b"), "`a``b`")


class FakeClient:
	graph_name = "fake"

	def __init__(self):
		self.queries = []

	def delete_graph(self):
		self.queries.append(("DELETE", None))

	def query(self, cypher, params=None):
		self.queries.append((cypher, params))
		rows = (params or {}).get("rows", [])
		return QueryResult(header=["n"], rows=[[len(rows)]])


class ImportTests(unittest.TestCase):
	def test_import_flow(self):
		plan = plan_from_schema(NODES, modules=["Selling", "Stock"])
		records = {
			"Customer": [{"name": "C1"}],
			"Item": None,  # table missing
			"Sales Order": [{"name": "SO-1", "customer": "C1"}, {"name": "SO-2", "customer": "C-missing"}],
		}
		children = [
			{"name": "row1", "parent": "SO-1", "parentfield": "items", "idx": 1, "item_code": "I1"},
			{"name": "row9", "parent": "SO-other", "parentfield": "items", "idx": 1},
		]
		client = FakeClient()
		summary = import_data(client, plan, lambda p: records.get(p.name, []), lambda c, parent, f: children)

		self.assertEqual(client.queries[0][0], "DELETE")
		self.assertEqual(summary.doctypes, 2)
		self.assertEqual(summary.records, 3)
		self.assertEqual(summary.children, 1)  # row9's parent was not loaded
		self.assertEqual(summary.child_edges, 1)
		self.assertEqual(summary.links, 1)  # C-missing and Item (not loaded) are dropped
		self.assertEqual(summary.skipped, ["Item"])
		self.assertTrue(any("CREATE INDEX FOR (n:`Sales Order Item`)" in q for q, _ in client.queries))
		self.assertTrue(any("CREATE INDEX FOR (n:Record)" in q for q, _ in client.queries))


if __name__ == "__main__":
	unittest.main()
