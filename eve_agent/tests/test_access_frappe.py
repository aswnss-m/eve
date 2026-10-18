"""Frappe tests for permission-aware chat. Run with (from ~/eve-bench):
bench --site eve.localhost run-tests --skip-before-tests --module eve_agent.tests.test_access_frappe

They use the seeded demo users (`<name>@eve-demo.local`) and skip when those are missing.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.nestedset import get_root_of

from eve_agent.access.resolve import hook_restricted, is_own_record, resolve_scope
from eve_agent.access.scope import SPECIAL_DROP, SPECIAL_KEEP
from eve_agent.sync.data_sync import load_plan

TEST_TERRITORY = "Eve Test South"
ACCOUNTS_DOCTYPES = ("Sales Invoice", "Payment Entry", "Journal Entry", "GL Entry", "Purchase Invoice")


def seeded_user(name: str) -> str:
	"""The seeded demo user `<name>@eve-demo.local`. Skips the test (or class) when it is missing."""
	user = f"{name}@eve-demo.local"
	if not frappe.db.exists("User", user):
		raise unittest.SkipTest(f"seeded user {user} not found")
	return user


def data_plan():
	return load_plan(frappe.get_single("Eve Agent Settings"))


def grant_test_territory(user: str) -> list[tuple[str, str]]:
	"""Create Territory TEST_TERRITORY and a User Permission on it for `user` (all DocTypes).

	Returns the created (doctype, name) pairs, in creation order, for delete_records() in tearDown.
	"""
	root = get_root_of("Territory")
	territory = frappe.get_doc(
		{"doctype": "Territory", "territory_name": TEST_TERRITORY, "parent_territory": root}
	).insert(ignore_permissions=True)
	permission = frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": user,
			"allow": "Territory",
			"for_value": territory.name,
			"apply_to_all_doctypes": 1,
		}
	).insert(ignore_permissions=True)
	frappe.clear_cache(user=user)
	return [("Territory", territory.name), ("User Permission", permission.name)]


def delete_records(records: list[tuple[str, str]], users=()) -> None:
	"""Delete fixture records newest first, then drop the cached permissions of `users`."""
	for doctype, name in reversed(records):
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
	for user in users:
		frappe.clear_cache(user=user)


class ResolveScopeTests(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.rahul = seeded_user("rahul")
		cls.ananya = seeded_user("ananya")
		cls.meera = seeded_user("meera")
		cls.plan = data_plan()

	def setUp(self):
		self.records = []

	def tearDown(self):
		delete_records(self.records, users=(self.ananya,))

	def test_sales_user_cannot_read_accounts(self):
		scope = resolve_scope(self.rahul, self.plan)
		for doctype in ACCOUNTS_DOCTYPES:
			self.assertNotIn(doctype, scope.readable)
		self.assertIn("Sales Order", scope.readable)
		self.assertIn("Customer", scope.readable)

	def test_employee_permission_is_own_record(self):
		scope = resolve_scope(self.rahul, self.plan)
		self.assertEqual(scope.own_record_doctypes, ("Employee",))
		self.assertIn("Company", scope.user_permissions)
		self.assertNotIn("Employee", scope.user_permissions)

	def test_same_access_same_key(self):
		rahul = resolve_scope(self.rahul, self.plan).key
		self.assertEqual(rahul, resolve_scope(self.ananya, self.plan).key)
		self.assertNotEqual(rahul, resolve_scope(self.meera, self.plan).key)

	def test_administrator_is_full(self):
		self.assertIs(resolve_scope("Administrator", self.plan).full_access, True)

	def test_territory_permission_changes_key(self):
		self.records = grant_test_territory(self.ananya)
		scope = resolve_scope(self.ananya, self.plan)
		self.assertNotEqual(scope.key, resolve_scope(self.rahul, self.plan).key)
		self.assertEqual(scope.user_permissions["Territory"], ((TEST_TERRITORY, ""),))

	def test_parents_exclude_children_and_special_doctypes(self):
		scope = resolve_scope(self.rahul, self.plan)
		self.assertFalse(scope.full_access)
		parent_names = self.plan.parent_names - set(SPECIAL_KEEP) - set(SPECIAL_DROP)
		self.assertEqual(scope.parents, tuple(sorted(dt for dt in scope.readable if dt in parent_names)))
		for doctype in (*SPECIAL_KEEP, *SPECIAL_DROP):
			self.assertNotIn(doctype, scope.readable)
		self.assertIn("Sales Order Item", scope.readable)  # child of a readable parent
		self.assertNotIn("Sales Order Item", scope.parents)
		self.assertNotIn("Sales Invoice Item", scope.readable)  # its only parent is unreadable

	def test_hook_and_own_record_helpers(self):
		self.assertTrue(hook_restricted("ToDo", self.rahul))  # Frappe's has_permission hook
		self.assertFalse(hook_restricted("Sales Order", self.rahul))
		# Dashboard has only a permission_query_conditions hook, empty for Administrator.
		self.assertTrue(hook_restricted("Dashboard", self.rahul))
		self.assertFalse(hook_restricted("Dashboard", "Administrator"))
		own = frappe.db.get_value("Employee", {"user_id": self.rahul})
		other = frappe.db.get_value("Employee", {"user_id": self.meera})
		self.assertTrue(is_own_record("Employee", own, self.rahul))
		self.assertFalse(is_own_record("Employee", other, self.rahul))
