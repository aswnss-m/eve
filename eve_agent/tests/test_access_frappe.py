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
TEST_ROLE = "Eve Test Owner Role"
OWNER_ONLY_DOCTYPE = "Budget"  # synced, read by Accounts Manager only
ACCOUNTS_DOCTYPES = ("Sales Invoice", "Payment Entry", "Journal Entry", "GL Entry", "Purchase Invoice")


def seeded_user(name: str) -> str:
	"""The seeded demo user `<name>@eve-demo.local`. Skips the test (or class) when it is missing."""
	user = f"{name}@eve-demo.local"
	if not frappe.db.exists("User", user):
		raise unittest.SkipTest(f"seeded user {user} not found")
	return user


def data_plan():
	return load_plan(frappe.get_single("Eve Agent Settings"))


# Fixture helpers append each (doctype, name) to `records` as soon as it exists; tearDown passes the
# list to delete_records().


def add_user_permission(user: str, allow: str, value: str, records: list) -> str:
	"""User Permission `allow` = `value` for `user`, applying to all DocTypes."""
	doc = frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": user,
			"allow": allow,
			"for_value": value,
			"apply_to_all_doctypes": 1,
		}
	).insert(ignore_permissions=True)
	records.append(("User Permission", doc.name))
	frappe.clear_cache(user=user)
	return doc.name


def grant_test_territory(user: str, records: list) -> str:
	"""Create the leaf Territory TEST_TERRITORY and a User Permission on it for `user`."""
	root = get_root_of("Territory")
	territory = frappe.get_doc(
		{"doctype": "Territory", "territory_name": TEST_TERRITORY, "parent_territory": root}
	).insert(ignore_permissions=True)
	records.append(("Territory", territory.name))
	add_user_permission(user, "Territory", territory.name, records)
	return territory.name


def grant_owner_only_read(user: str, doctype: str, records: list) -> None:
	"""Give `user` the Role TEST_ROLE, whose only right is read "if owner" on `doctype`.

	Frappe's setup_custom_perms copies the standard DocPerms to Custom DocPerms first, so everyone
	else keeps their access while the test runs. Pass `doctypes=(doctype,)` to delete_records().
	"""
	if frappe.db.exists("Custom DocPerm", {"parent": doctype}):
		raise unittest.SkipTest(f"{doctype} already has Custom DocPerms")
	role = frappe.get_doc({"doctype": "Role", "role_name": TEST_ROLE, "desk_access": 1})
	records.append(("Role", role.insert(ignore_permissions=True).name))
	frappe.permissions.setup_custom_perms(doctype)
	copies = frappe.get_all("Custom DocPerm", {"parent": doctype}, pluck="name")
	records.extend(("Custom DocPerm", name) for name in copies)
	perm = frappe.get_doc(
		{
			"doctype": "Custom DocPerm",
			"parent": doctype,
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": TEST_ROLE,
			"permlevel": 0,
			"read": 1,
			"if_owner": 1,
		}
	)
	records.append(("Custom DocPerm", perm.insert(ignore_permissions=True).name))
	frappe.get_doc("User", user).add_roles(TEST_ROLE)
	records.append(("Has Role", frappe.db.get_value("Has Role", {"parent": user, "role": TEST_ROLE})))
	frappe.clear_cache(user=user)


def delete_records(records: list, users=(), doctypes=()) -> None:
	"""Delete fixture records newest first, then drop the cached state of `users` and `doctypes`."""
	for doctype, name in reversed(records):
		if frappe.get_meta(doctype).istable:  # e.g. a Has Role row
			frappe.db.delete(doctype, {"name": name})
		else:
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
	for doctype in doctypes:
		frappe.clear_cache(doctype=doctype)  # deleting a Custom DocPerm does not clear the meta cache
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
		delete_records(self.records, users=(self.rahul, self.ananya), doctypes=(OWNER_ONLY_DOCTYPE,))

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
		grant_test_territory(self.ananya, self.records)
		scope = resolve_scope(self.ananya, self.plan)
		self.assertNotEqual(scope.key, resolve_scope(self.rahul, self.plan).key)
		self.assertEqual(scope.user_permissions["Territory"], ((TEST_TERRITORY, ""),))

	def test_own_record_doctype_drops_all_its_entries(self):
		other = frappe.db.get_value("Employee", {"user_id": self.meera})
		add_user_permission(self.rahul, "Employee", other, self.records)
		self.assertEqual(len(frappe.permissions.get_user_permissions(self.rahul)["Employee"]), 2)
		scope = resolve_scope(self.rahul, self.plan)
		self.assertNotIn("Employee", scope.user_permissions)
		self.assertEqual(scope.own_record_doctypes, ("Employee",))
		self.assertEqual(scope.key, resolve_scope(self.ananya, self.plan).key)

	def test_permlevels_are_the_users(self):
		readable = resolve_scope(self.rahul, self.plan).readable
		self.assertEqual(readable["Customer"], (0, 1))
		self.assertEqual(readable["Sales Order"], (0,))  # Administrator reads (0, 1)
		self.assertEqual(readable["Quotation Item"], (0, 1))  # a child follows its parent's levels
		# Sales Team rows sit under Customer (0, 1) and Sales Order (0,): the intersection wins.
		self.assertEqual(readable["Sales Team"], (0,))

	def test_owner_only_read_is_not_readable(self):
		if OWNER_ONLY_DOCTYPE not in self.plan.parent_names:
			self.skipTest(f"{OWNER_ONLY_DOCTYPE} is not in the data plan")
		self.assertNotIn(OWNER_ONLY_DOCTYPE, resolve_scope(self.ananya, self.plan).readable)
		grant_owner_only_read(self.ananya, OWNER_ONLY_DOCTYPE, self.records)
		perms = frappe.permissions.get_role_permissions(frappe.get_meta(OWNER_ONLY_DOCTYPE), user=self.ananya)
		self.assertTrue(perms.read)  # the role took effect: Frappe lists the DocType, filtered by owner
		self.assertIn("read", perms.if_owner)
		self.assertNotIn(OWNER_ONLY_DOCTYPE, resolve_scope(self.ananya, self.plan).readable)

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
