"""Pure-Python tests for the incremental sync. Run with:
env/bin/python -m unittest eve_agent.tests.test_incremental  (from apps/eve_agent)."""

import unittest

from eve_agent.falkor.connection import QueryResult
from eve_agent.falkor.cypher import incremental_queries as iq
from eve_agent.falkor.cypher.business_rules import RULES, Rule, derived, flow, rename, scope
from eve_agent.falkor.importer.business_layer import apply_scoped_rules
from eve_agent.falkor.importer.data_rows import plan_from_schema
from eve_agent.falkor.importer.incremental import (
	ensure_index,
	group_keys,
	make_key,
	parse_key,
	sync_keys,
)
from eve_agent.falkor.query.readonly import WriteQueryError, assert_read_only
from eve_agent.tests.test_data_rows import NODES


class FakeClient:
	"""Records every query. Pivot captures answer from `pivots` (record name -> neighbours)."""

	graph_name = "fake"

	def __init__(self, pivots=None, fail_index=None):
		self.queries = []
		self.pivots = pivots or {}
		self.fail_index = fail_index

	def query(self, cypher, params=None):
		params = params or {}
		self.queries.append((cypher, params))
		if cypher.startswith("CREATE INDEX") and self.fail_index:
			raise self.fail_index
		if "RETURN DISTINCT" in cypher:
			names = [n for nm in params["names"] for n in self.pivots.get(nm, [])]
			return QueryResult(header=["name"], rows=[[n] for n in names])
		rows = params.get("rows") or params.get("names") or []
		return QueryResult(header=["n"], rows=[[len(rows)]])

	def kinds(self):
		return [q.split("\n")[1].split(" ")[0] if "\n" in q else q.split(" ")[0] for q, _ in self.queries]


RULES_SMALL = (
	rename("customer docs", "FOR_CUSTOMER", ["customer"], "Customer", ["Sales Order"]),
	derived(
		"customer bought",
		"MATCH (c:Customer)<-[:FOR_CUSTOMER]-(so:`Sales Order`)-[k:CONTAINS]->(i:Item) "
		"WITH c, i, sum(k.qty) AS qty CREATE (c)-[:BOUGHT {qty: qty}]->(i) RETURN count(*) AS n",
		"(c:Customer)-[r:BOUGHT]->(:Item)",
		"c.name IN $pivots",
	),
)


class KeyTests(unittest.TestCase):
	def test_round_trip_odd_names(self):
		for name in ("SO-1", "a|b", 'say "hi"', "ünïcode", "12"):
			self.assertEqual(parse_key(make_key("Sales Order", name)), ("Sales Order", name))

	def test_group_keys(self):
		plan = plan_from_schema(NODES, modules=["Selling", "Stock"])
		by, ignored = group_keys(
			[make_key("Sales Order", "SO-1"), make_key("Sales Order", "SO-1"), make_key("Error Log", "x"), "junk"],
			plan,
		)
		self.assertEqual(by, {"Sales Order": ["SO-1"]})
		self.assertEqual(ignored, [make_key("Error Log", "x"), "junk"])


class IndexTests(unittest.TestCase):
	def test_existing_index_is_fine(self):
		ensure_index(FakeClient(fail_index=Exception("Attribute 'name' is already indexed")), "Customer")

	def test_other_errors_raise(self):
		with self.assertRaises(Exception):
			ensure_index(FakeClient(fail_index=Exception("connection refused")), "Customer")


class SyncKeysTests(unittest.TestCase):
	def setUp(self):
		self.plan = plan_from_schema(NODES, modules=["Selling", "Stock"])
		self.records = {
			"Sales Order": [{"name": "SO-1", "customer": "C1", "customer_name": "Acme"}],
			"Customer": [],
		}
		self.children = [{"name": "row1", "parent": "SO-1", "parentfield": "items", "idx": 1, "item_code": "I1"}]

	def run_sync(self, keys, pivots=None):
		client = FakeClient(pivots=pivots or {})
		summary = sync_keys(
			client,
			self.plan,
			keys,
			fetch_by_names=lambda p, names: [r for r in self.records.get(p.name, []) if r["name"] in names],
			fetch_children_by_parents=lambda c, parent, f, parents: [r for r in self.children if r["parent"] in parents],
			rules=RULES_SMALL,
		)
		return client, summary

	def test_order_capture_delete_merge_links_rules(self):
		client, summary = self.run_sync([make_key("Sales Order", "SO-1")], pivots={"SO-1": ["C-old"]})
		text = [q for q, _ in client.queries]

		def first(fragment):
			return next(i for i, q in enumerate(text) if fragment in q)

		self.assertLess(first("RETURN DISTINCT"), first("DETACH DELETE c"))
		self.assertLess(first("DETACH DELETE c"), first("MERGE (n:`Sales Order`:Record"))
		self.assertLess(first("MERGE (n:`Sales Order`:Record"), first("CREATE (a)-[r:HAS_CHILD"))
		self.assertLess(first("CREATE (a)-[r:HAS_CHILD"), first("CREATE (a)-[r:LINKS_TO"))
		self.assertLess(first("CREATE (a)-[r:LINKS_TO"), first("FOR_CUSTOMER"))
		self.assertTrue(any(q.startswith("CREATE INDEX FOR (n:Record)") for q in text))
		self.assertEqual(summary.upserted, 1)
		self.assertEqual(summary.children, 1)
		self.assertEqual(summary.child_edges, 1)
		self.assertEqual(summary.links, 2)  # SO-1 -> C1, row1 -> I1
		self.assertEqual(summary.deleted, 0)

	def test_rules_get_names_and_union_of_pivots(self):
		client, _ = self.run_sync([make_key("Sales Order", "SO-1")], pivots={"SO-1": ["C-old"]})
		rule_calls = [(q, p) for q, p in client.queries if "BOUGHT" in q]
		self.assertEqual(len(rule_calls), 2)  # delete, then create
		self.assertTrue(rule_calls[0][0].startswith("MATCH (c:Customer)-[r:BOUGHT]"))
		self.assertEqual(set(rule_calls[0][1]["pivots"]), {"C-old", "SO-1"})

	def test_rules_names_include_child_rows(self):
		# Child rows come back with raw LINKS_TO edges; rules that rewrite them (drop hub edges,
		# renames whose source is a child DocType) can only reach them through $names.
		client, _ = self.run_sync([make_key("Sales Order", "SO-1")])
		rename_calls = [p for q, p in client.queries if "FOR_CUSTOMER" in q]
		self.assertEqual(rename_calls[0]["names"], ["SO-1", "row1"])

	def test_gone_record_is_deleted_and_its_pivots_still_reach_rules(self):
		client, summary = self.run_sync([make_key("Sales Order", "SO-gone")], pivots={"SO-gone": ["C9"]})
		text = [q for q, _ in client.queries]
		self.assertTrue(any("DETACH DELETE n" in q for q in text))
		self.assertFalse(any("MERGE (n:" in q for q in text))
		self.assertEqual(summary.deleted, 1)
		create = next(p for q, p in client.queries if "CREATE (c)-[:BOUGHT" in q)
		self.assertIn("C9", create["pivots"])

	def test_keys_outside_plan_are_ignored(self):
		client, summary = self.run_sync([make_key("Error Log", "x")])
		self.assertEqual(summary.ignored, [make_key("Error Log", "x")])
		self.assertEqual(client.queries, [])


class ScopedRuleTests(unittest.TestCase):
	def test_every_rule_is_scoped_with_writes(self):
		for rule in RULES:
			self.assertTrue(rule.scoped, rule.name)
			for statement in rule.scoped:
				self.assertTrue("$names" in statement or "$pivots" in statement, rule.name)
				with self.assertRaises(WriteQueryError, msg=rule.name):
					assert_read_only(statement)
				self.assertNotIn(" OR ", statement.split(" WHERE ")[-1].split(" WITH ")[0], rule.name)

	def test_scope_with_and_without_where(self):
		self.assertEqual(scope("MATCH (a) WHERE a.x = 1 CREATE (a)", "a.name IN $names"), "MATCH (a) WHERE a.name IN $names AND a.x = 1 CREATE (a)")
		self.assertEqual(scope("MATCH (a) WITH a CREATE (a)", "a.name IN $names"), "MATCH (a) WHERE a.name IN $names WITH a CREATE (a)")

	def test_reverse_rename_deletes_then_creates_without_field(self):
		rule = rename("lead converted", "CONVERTED_TO", ["lead_name"], "Lead", ["Customer"], reverse=True)
		self.assertIn("CREATE (b)-[:CONVERTED_TO]->(a)", rule.cypher)
		self.assertTrue(rule.scoped[0].startswith("MATCH (b:`Lead`)-[r:CONVERTED_TO]->(a:Record) WHERE a.name IN $names DELETE r"))
		self.assertIn("WHERE a.name IN $names AND r.field IN", rule.scoped[1])

	def test_forward_rename_keeps_field_and_flow_deletes_first(self):
		rule = rename("customer docs", "FOR_CUSTOMER", ["customer"], "Customer")
		self.assertIn("{field: r.field}", rule.cypher)
		self.assertEqual(len(rule.scoped), 1)
		f = flow("order billed", "BILLED_BY", "Sales Order", "Sales Invoice Item", "sales_order", "Sales Invoice")
		self.assertIn("DELETE r", f.scoped[0])
		self.assertIn("WHERE n.name IN $names WITH DISTINCT p, n", f.scoped[1])

	def test_apply_scoped_rules_chunks_the_list_it_reads(self):
		client = FakeClient()
		rules = (Rule("r", "", ("MATCH (a) WHERE a.name IN $pivots DELETE a RETURN count(*) AS n",)),)
		summary = apply_scoped_rules(client, ["n1"], [f"p{i}" for i in range(7)], rules, batch=3)
		self.assertEqual(len(client.queries), 3)
		self.assertEqual(summary.counts["r"], 7)


if __name__ == "__main__":
	unittest.main()
