"""Pure-Python tests for AccessScope and plan filtering. Run with:
env/bin/python -m unittest eve_agent.tests.test_access  (from apps/eve_agent)."""

import unittest

from eve_agent.access.scope import FULL_SCOPE, AccessScope, filter_plan, make_key
from eve_agent.falkor.importer.data_rows import plan_from_schema


def field(fieldname, fieldtype, options=None, permlevel=0):
	return {"fieldname": fieldname, "fieldtype": fieldtype, "options": options, "permlevel": permlevel}


NODES = [
	{
		"name": "Sales Order",
		"module": "Selling",
		"fields": [
			field("customer", "Link", "Customer"),
			field("team", "Table", "Sales Team"),
			field("items", "Table", "Sales Order Item"),
		],
	},
	{"name": "Sales Order Item", "module": "Selling", "is_child": True, "fields": [field("qty", "Float")]},
	{
		"name": "Sales Team",
		"module": "Selling",
		"is_child": True,
		"fields": [field("sales_person", "Data"), field("commission_rate", "Percent", permlevel=1)],
	},
	{
		"name": "Customer",
		"module": "Selling",
		"fields": [
			field("customer_name", "Data"),
			field("credit_limit", "Currency", permlevel=1),
			field("sales_team", "Table", "Sales Team"),
		],
	},
	{"name": "Sales Invoice", "module": "Accounts", "fields": [field("customer", "Link", "Customer")]},
	{"name": "Employee", "module": "HR", "fields": [field("employee_name", "Data")]},
	{"name": "User", "module": "Core", "fields": [field("full_name", "Data")]},
	{"name": "Comment", "module": "Core", "fields": [field("content", "Text")]},
	{"name": "ToDo", "module": "Core", "fields": [field("description", "Text")]},
]


def make_plan():
	return plan_from_schema(NODES, modules=["Selling", "Accounts", "HR", "Core"])


def make_scope(readable, own=(), user_permissions=None, strict=False):
	user_permissions = user_permissions or {}
	parents = tuple(sorted(n["name"] for n in NODES if n["name"] in readable and not n.get("is_child")))
	return AccessScope(
		key=make_key(readable, user_permissions, own, strict),
		full_access=False,
		readable=readable,
		user_permissions=user_permissions,
		own_record_doctypes=tuple(own),
		strict=strict,
		parents=parents,
	)


class AccessTests(unittest.TestCase):
	def test_key_ignores_order_and_user(self):
		up_a = {"T": (("North", ""), ("East", "Customer"))}
		up_b = {"T": (("East", "Customer"), ("North", ""))}
		a = make_key({"A": (0, 1), "B": (0,)}, up_a, ("Employee",), False)
		b = make_key({"B": (0,), "A": (1, 0)}, up_b, ("Employee",), False)
		self.assertEqual(a, b)
		self.assertRegex(a, r"^[0-9a-f]{12}$")

	def test_key_changes_with_outcome(self):
		base = {"Territory": (("North", ""),)}
		more = {"Territory": (("North", ""), ("South", ""))}
		self.assertNotEqual(make_key({"A": (0,)}, base, (), False), make_key({"A": (0,)}, more, (), False))

	def test_unreadable_doctypes_are_dropped(self):
		readable = {"Sales Order": (0,), "Sales Order Item": (0,), "Customer": (0,)}
		out = filter_plan(make_plan(), make_scope(readable))
		self.assertNotIn("Sales Invoice", out.parent_names)
		self.assertIn("User", out.parent_names)
		self.assertIn("Comment", out.parent_names)
		self.assertNotIn("ToDo", out.parent_names)
		self.assertEqual(list(out.children), ["Sales Order Item"])

	def test_fields_above_readable_level_are_dropped(self):
		out = filter_plan(make_plan(), make_scope({"Customer": (0,)}))
		customer = next(p for p in out.parents if p.name == "Customer")
		self.assertNotIn("credit_limit", customer.value_fields)
		self.assertIn("customer_name", customer.value_fields)
		out = filter_plan(make_plan(), make_scope({"Customer": (0, 1)}))
		customer = next(p for p in out.parents if p.name == "Customer")
		self.assertIn("credit_limit", customer.value_fields)

	def test_own_record_doctypes_are_dropped(self):
		readable = {"Employee": (0,), "Customer": (0,)}
		out = filter_plan(make_plan(), make_scope(readable, own=("Employee",)))
		self.assertNotIn("Employee", out.parent_names)
		self.assertIn("Customer", out.parent_names)

	def test_shared_child_table_takes_intersection(self):
		readable = {"Customer": (0,), "Sales Order": (0,), "Sales Team": (0,)}
		out = filter_plan(make_plan(), make_scope(readable))
		team = out.children["Sales Team"]
		self.assertIn("sales_person", team.value_fields)
		self.assertNotIn("commission_rate", team.value_fields)

	def test_unreadable_child_is_dropped(self):
		out = filter_plan(make_plan(), make_scope({"Sales Order": (0,)}))
		self.assertEqual(out.children, {})

	def test_full_scope_returns_plan_unchanged(self):
		plan = make_plan()
		self.assertIs(filter_plan(plan, FULL_SCOPE), plan)

	def test_scope_json_round_trip(self):
		s = make_scope(
			{"Customer": (0, 1), "Sales Order": (0,)},
			own=("Employee",),
			user_permissions={"Territory": (("North", ""), ("East", "Customer"))},
			strict=True,
		)
		self.assertEqual(s.parents, ("Customer", "Sales Order"))
		self.assertEqual(AccessScope.from_json(s.to_json()), s)
		self.assertEqual(AccessScope.from_json(s.to_json()).parents, ("Customer", "Sales Order"))
