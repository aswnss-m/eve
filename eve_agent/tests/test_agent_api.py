"""Pure-Python tests for the people layer. Run with:
env/bin/python -m unittest eve_agent.tests.test_agent_api  (from apps/eve_agent)."""

import unittest

from eve_agent.falkor.cypher.business_rules import RULES, SYSTEM_USERS
from eve_agent.falkor.importer.data_rows import build_record_row, doctype_plan, plan_from_schema


class PeopleLayerTests(unittest.TestCase):
	def test_user_fields_are_allowlisted(self):
		node = {
			"name": "User",
			"module": "Core",
			"fields": [
				{"fieldname": "full_name", "fieldtype": "Data"},
				{"fieldname": "reset_password_key", "fieldtype": "Data"},
				{"fieldname": "last_ip", "fieldtype": "Read Only"},
				{"fieldname": "roles", "fieldtype": "Table", "options": "Has Role"},
			],
		}
		plan = doctype_plan(node)
		self.assertEqual(plan.value_fields, ["full_name"])
		self.assertEqual(plan.tables, {})
		self.assertEqual(plan.filters, {"user_type": "System User"})

	def test_extra_doctypes_join_the_plan(self):
		nodes = [
			{
				"name": "Comment",
				"module": "Core",
				"fields": [{"fieldname": "content", "fieldtype": "HTML Editor"}],
			},
			{"name": "Version", "module": "Core", "fields": []},
			{"name": "Customer", "module": "Selling", "fields": []},
		]
		names = {p.name for p in plan_from_schema(nodes, modules=["Selling"]).parents}
		self.assertEqual(names, {"Comment", "Customer"})

	def test_comment_html_is_stripped_and_owner_kept(self):
		plan = doctype_plan(
			{"name": "Comment", "fields": [{"fieldname": "comment_type", "fieldtype": "Select"}]}
		)
		row = build_record_row(
			plan,
			{
				"name": "c1",
				"owner": "a@x.com",
				"comment_type": "Comment",
				"content": "<p>Call <b>Monday</b></p>",
			},
		)
		self.assertEqual(row["content"], "Call Monday")
		self.assertEqual(row["owner"], "a@x.com")

	def test_people_rules_skip_system_users(self):
		rule = next(r for r in RULES if r.name == "created by")
		for user in SYSTEM_USERS:
			self.assertIn(f"'{user}'", rule.cypher)
		self.assertIn("CREATE (r)-[:CREATED_BY]->(u)", rule.cypher)
