"""Pure-Python tests for the FalkorDB layer. Run with: bench --site eve.local ... or
env/bin/python -m unittest eve_agent.tests.test_falkor_pure  (from apps/eve_agent)."""

import unittest

from eve_agent.falkor.connection import build_url
from eve_agent.falkor.importer.schema_import import SchemaImportSummary
from eve_agent.falkor.importer.schema_rows import build_doctype_row, build_edges
from eve_agent.falkor.literals import to_literal, with_params
from eve_agent.falkor.query.readonly import WriteQueryError, assert_read_only


class BuildUrlTests(unittest.TestCase):
	def test_no_auth(self):
		self.assertEqual(build_url("h"), "redis://h:6379")
		self.assertEqual(build_url("h", 6500, "", ""), "redis://h:6500")

	def test_pasted_endpoint_overrides_port(self):
		self.assertEqual(build_url("r-x.falkordb.cloud:51023", 6379, "falkordb", "pw"), "redis://falkordb:pw@r-x.falkordb.cloud:51023")
		self.assertEqual(build_url("redis://127.0.0.1:6500/", 6379), "redis://127.0.0.1:6500")

	def test_password_only(self):
		self.assertEqual(build_url("h", 6500, None, "pw"), "redis://:pw@h:6500")

	def test_user_password_tls_encoded(self):
		self.assertEqual(build_url("h", 61234, "falkordb", "p@ss/", True), "rediss://falkordb:p%40ss%2F@h:61234")


class LiteralTests(unittest.TestCase):
	def test_scalars(self):
		self.assertEqual(to_literal(None), "null")
		self.assertEqual(to_literal(True), "true")
		self.assertEqual(to_literal(3), "3")
		self.assertEqual(to_literal(1.5), "1.5")

	def test_string_escaping(self):
		self.assertEqual(to_literal("it's a\\b"), "'it\\'s a\\\\b'")

	def test_nested(self):
		self.assertEqual(to_literal([{"a": 1, "b": ["x"]}]), "[{a: 1, b: ['x']}]")

	def test_rejects_bad_key_and_nan(self):
		with self.assertRaises(ValueError):
			to_literal({"bad key": 1})
		with self.assertRaises(ValueError):
			to_literal(float("nan"))

	def test_with_params_prefix(self):
		self.assertEqual(with_params("RETURN $k", {"k": "x"}), "CYPHER k='x' RETURN $k")
		self.assertEqual(with_params("RETURN 1", None), "RETURN 1")


class ReadOnlyTests(unittest.TestCase):
	def test_reads_pass(self):
		assert_read_only("MATCH (d:DocType)-[:LINKS_TO]->(t) RETURN d.name, t.name")

	def test_writes_rejected(self):
		for q in ("MERGE (n:X)", "match (n) set n.a = 1", "MATCH (n) DETACH DELETE n", "CALL db.idx.fulltext.createNodeIndex('X','y')"):
			with self.subTest(q=q), self.assertRaises(WriteQueryError):
				assert_read_only(q)

	def test_keyword_inside_string_is_allowed(self):
		assert_read_only("MATCH (n) WHERE n.note = 'please SET this' RETURN n")


class SchemaRowTests(unittest.TestCase):
	def setUp(self):
		self.meta = {
			"name": "Sales Order",
			"module": "Selling",
			"is_submittable": True,
			"is_single": False,
			"is_child": False,
			"is_tree": False,
			"title_field": "customer_name",
			"modified": "2026-10-01 10:00:00",
			"fields": [
				{"fieldname": "customer", "fieldtype": "Link", "options": "Customer", "label": "Customer", "reqd": 1},
				{"fieldname": "items", "fieldtype": "Table", "options": "Sales Order Item", "label": "Items", "reqd": 0},
				{"fieldname": "ref", "fieldtype": "Dynamic Link", "options": "ref_doctype", "label": "Ref", "reqd": 0},
				{"fieldname": "total", "fieldtype": "Currency", "options": None, "label": "Total", "reqd": 0},
			],
		}

	def test_row_properties(self):
		row = build_doctype_row(self.meta)
		self.assertEqual(row["name"], "Sales Order")
		self.assertEqual(row["field_count"], 4)
		self.assertEqual(row["field_names"], ["customer", "items", "ref", "total"])
		self.assertEqual(row["field_types"], ["Link", "Table", "Dynamic Link", "Currency"])
		self.assertEqual(row["link_targets"], ["customer:Customer", "items:Sales Order Item", "dynamic:ref"])
		self.assertTrue(row["is_submittable"])
		self.assertIn('"fieldname": "customer"', row["fields_json"])

	def test_edges_skip_unknown_targets(self):
		links, children = build_edges(self.meta, {"Sales Order", "Customer"})
		self.assertEqual(links, [{"src": "Sales Order", "dst": "Customer", "fieldname": "customer"}])
		self.assertEqual(children, [])  # Sales Order Item is not in the known set

		links, children = build_edges(self.meta, {"Sales Order", "Customer", "Sales Order Item"})
		self.assertEqual(children, [{"src": "Sales Order", "dst": "Sales Order Item", "fieldname": "items"}])

	def test_summary_text(self):
		s = SchemaImportSummary(doctypes=2, links=1, children=0, pruned=3)
		self.assertIn("DocTypes: 2", s.as_text())
		self.assertIn("pruned: 3", s.as_text())


if __name__ == "__main__":
	unittest.main()
