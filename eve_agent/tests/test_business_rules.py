"""Pure-Python tests for the business layer rules. Run with:
env/bin/python -m unittest eve_agent.tests.test_business_rules  (from apps/eve_agent)."""

import unittest

from eve_agent.falkor.connection import QueryResult
from eve_agent.falkor.cypher.business_rules import RULES, flow, rename
from eve_agent.falkor.importer.business_layer import apply_business_rules
from eve_agent.falkor.query.readonly import WriteQueryError, assert_read_only


class RuleTests(unittest.TestCase):
	def test_rename_forward(self):
		rule = rename("x", "FOR_CUSTOMER", ["customer"], "Customer", ["Sales Order"])
		self.assertIn("MATCH (a:Record)-[r:LINKS_TO]->(b:`Customer`)", rule.cypher)
		self.assertIn("r.field IN ['customer'] AND a.doctype IN ['Sales Order']", rule.cypher)
		self.assertIn("CREATE (a)-[:FOR_CUSTOMER {field: r.field}]->(b) DELETE r", rule.cypher)

	def test_rename_reverse(self):
		rule = rename("x", "CONVERTED_TO", ["lead_name"], "Lead", reverse=True)
		self.assertIn("CREATE (b)-[:CONVERTED_TO]->(a)", rule.cypher)  # reverse edges carry no field: see Rule docs
		self.assertNotIn("a.doctype", rule.cypher)

	def test_flow(self):
		rule = flow("x", "DELIVERED_BY", "Sales Order", "Delivery Note Item", "against_sales_order", "Delivery Note")
		self.assertIn(
			"MATCH (n:`Delivery Note`)-[:HAS_CHILD]->(:`Delivery Note Item`)"
			"-[:LINKS_TO {field: 'against_sales_order'}]->(p:`Sales Order`)",
			rule.cypher,
		)
		self.assertIn("CREATE (p)-[:DELIVERED_BY]->(n)", rule.cypher)

	def test_rules_are_writes_with_unique_names(self):
		names = [r.name for r in RULES]
		self.assertEqual(len(names), len(set(names)))
		for rule in RULES:
			with self.subTest(rule=rule.name), self.assertRaises(WriteQueryError):
				assert_read_only(rule.cypher)

	def test_apply_counts(self):
		class Client:
			def query(self, cypher, params=None):
				return QueryResult(header=["n"], rows=[[2]])

		summary = apply_business_rules(Client(), RULES[:3])
		self.assertEqual(summary.total, 6)
		self.assertIn("applied: 3/3", summary.as_text())


if __name__ == "__main__":
	unittest.main()
