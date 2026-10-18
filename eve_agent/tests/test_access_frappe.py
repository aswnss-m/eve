"""Frappe tests for permission-aware chat. Run with (from ~/eve-bench):
bench --site eve.localhost run-tests --skip-before-tests --module eve_agent.tests.test_access_frappe

They use the seeded demo users (`<name>@eve-demo.local`) and skip when those are missing.
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

import frappe
import frappe.share
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today
from frappe.utils.nestedset import get_root_of

from eve_agent.access.reader import ProfileReader
from eve_agent.access.resolve import hook_restricted, is_own_record, resolve_scope
from eve_agent.access.scope import SPECIAL_DROP, SPECIAL_KEEP, filter_plan
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


def add_comment(doctype: str, name: str, records: list, comment_type: str = "Comment") -> str:
	"""A comment on record `name`; by default a user comment, the only kind Eve syncs."""
	comment = frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": comment_type,
			"reference_doctype": doctype,
			"reference_name": name,
			"content": "Eve test comment",
		}
	).insert(ignore_permissions=True)
	records.append(("Comment", comment.name))
	return comment.name


def add_test_territory_order(records: list) -> str:
	"""A Customer in TEST_TERRITORY and a submitted Sales Order for it, with the latest order's first item."""
	template = frappe.get_last_doc("Sales Order", filters={"docstatus": 1})
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": "Eve Test South Customer",
			"customer_type": "Company",
			"customer_group": frappe.db.get_value("Customer", template.customer, "customer_group"),
			"territory": TEST_TERRITORY,
		}
	).insert(ignore_permissions=True)
	records.append(("Customer", customer.name))
	item = template.items[0]
	row = {"item_code": item.item_code, "qty": 1, "rate": item.rate, "warehouse": item.warehouse}
	order = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"customer": customer.name,
			"company": template.company,
			"territory": TEST_TERRITORY,
			"transaction_date": today(),
			"delivery_date": add_days(today(), 7),
			"items": [row],
		}
	).insert(ignore_permissions=True)
	records.append(("Sales Order", order.name))
	order.submit()
	return order.name


def comments_on(doctype: str, name: str) -> set[str]:
	"""Names of every Comment (of any type) on record `name`."""
	filters = {"reference_doctype": doctype, "reference_name": name}
	return set(frappe.get_all("Comment", filters, pluck="name"))


def delete_records(records: list, users=(), doctypes=()) -> None:
	"""Delete fixture records newest first (cancelling submitted ones), then drop the cached state of
	`users` and `doctypes`."""
	for doctype, name in reversed(records):
		meta = frappe.get_meta(doctype)
		if meta.istable:  # e.g. a Has Role row
			frappe.db.delete(doctype, {"name": name})
			continue
		if meta.is_submittable and frappe.db.get_value(doctype, name, "docstatus") == 1:
			frappe.get_doc(doctype, name).cancel()
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


class ProfileReaderTests(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.rahul = seeded_user("rahul")
		cls.ananya = seeded_user("ananya")
		cls.meera = seeded_user("meera")
		cls.plan = data_plan()

	def setUp(self):
		self.records = []
		self.shares = []  # (doctype, name, user, the record's comments before sharing)

	def tearDown(self):
		for doctype, name, user, before in self.shares:
			frappe.share.remove(doctype, name, user)
			# Sharing and unsharing log "Shared" / "Unshared" comments on the record.
			for comment in comments_on(doctype, name) - before:
				frappe.delete_doc("Comment", comment, force=True, ignore_permissions=True)
		delete_records(self.records, users=(self.rahul, self.ananya))

	def share(self, doctype: str, name: str, user: str) -> None:
		self.shares.append((doctype, name, user, comments_on(doctype, name)))
		frappe.share.add(doctype, name, user)

	def reader(self, user: str, doctype: str, **changes):
		"""`user`'s ProfileReader (scope fields replaced by `changes`) and its plan for `doctype`."""
		scope = replace(resolve_scope(user, self.plan), **changes)
		plans = {p.name: p for p in filter_plan(self.plan, scope).parents}
		return ProfileReader(scope), plans[doctype]

	def names(self, user: str, doctype: str) -> set[str]:
		reader, plan = self.reader(user, doctype)
		return {r["name"] for r in reader.fetch_records(plan, 0)}

	def test_reader_matches_get_list(self):
		for doctype in ("Customer", "Quotation", "Sales Order", "Delivery Note"):
			with self.subTest(doctype=doctype):
				listed = set(frappe.get_list(doctype, user=self.rahul, pluck="name", limit=0))
				self.assertTrue(listed)
				self.assertEqual(self.names(self.rahul, doctype), listed)

	def test_own_record_rule_hides_employee_linked_sales_persons(self):
		names = self.names(self.rahul, "Sales Person")
		self.assertTrue(names)  # e.g. the group "Sales Team"
		self.assertFalse(any(frappe.get_all("Sales Person", {"name": ("in", names)}, pluck="employee")))
		# Frappe also lists rahul's own Sales Person (employee = his Employee); the profile leaves it out.
		listed = frappe.get_list("Sales Person", user=self.rahul, fields=["name", "employee"], limit=0)
		self.assertTrue(any(r.employee for r in listed))
		self.assertEqual(names, {r.name for r in listed if not r.employee})

	def test_strict_mode_hides_empty_own_record_links_too(self):
		reader, plan = self.reader(self.rahul, "Sales Person", strict=True)
		self.assertEqual(reader.fetch_records(plan, 0), [])
		# A scope resolved before strict mode was switched on: the stricter setting wins. (Patched rather
		# than saved: saving System Settings clears site-wide caches and slows every later test.)
		reader, plan = self.reader(self.rahul, "Sales Person")
		self.assertFalse(reader.scope.strict)
		real = frappe.get_system_settings

		def strict_settings(key):
			return 1 if key == "apply_strict_user_permissions" else real(key)

		with patch("frappe.get_system_settings", strict_settings):
			self.assertEqual(reader.fetch_records(plan, 0), [])

	def test_unreadable_doctype_reads_nothing(self):
		reader, _ = self.reader(self.rahul, "Sales Order")
		invoices = next(p for p in self.plan.parents if p.name == "Sales Invoice")  # not in rahul's plan
		self.assertEqual(reader.fetch_records(invoices, 0), [])
		invoice = frappe.db.get_value("Sales Invoice", {})
		self.assertEqual(reader.fetch_records_by_names(invoices, [invoice]), [])

	def test_reader_does_not_depend_on_the_session_user(self):
		reader, plan = self.reader(self.rahul, "Comment")
		as_admin = sorted(reader.fetch_records(plan, 0), key=lambda row: row["name"])
		with self.set_user(self.rahul):  # e.g. a profile build queued by rahul's chat request
			as_rahul = sorted(reader.fetch_records(plan, 0), key=lambda row: row["name"])
		self.assertTrue(as_admin)
		self.assertEqual(as_rahul, as_admin)

	def test_comments_follow_their_reference(self):
		order = frappe.get_list("Sales Order", user=self.rahul, pluck="name", limit=1)[0]
		visible = add_comment("Sales Order", order, self.records)
		hidden = (
			add_comment("Sales Invoice", frappe.db.get_value("Sales Invoice", {}), self.records),
			# Frappe lists rahul's own Employee only, and the profile not even that one.
			add_comment("Employee", frappe.db.get_value("Employee", {"user_id": self.meera}), self.records),
		)
		reader, plan = self.reader(self.rahul, "Comment")
		rows = reader.fetch_records(plan, 0)
		for row in rows:
			self.assertIn(row["reference_doctype"], reader.scope.readable)
		self.assertNotIn("Sales Invoice", {row["reference_doctype"] for row in rows})
		names = {row["name"] for row in rows}
		self.assertIn(visible, names)
		for name in hidden:
			self.assertNotIn(name, names)

	def test_standard_users_are_hidden(self):
		names = self.names(self.rahul, "User")
		self.assertIn(self.ananya, names)
		self.assertNotIn("Administrator", names)  # Frappe's User list hook hides it from non-admins

	def test_fetch_by_names_honours_plan_filters(self):
		order = frappe.get_list("Sales Order", user=self.rahul, pluck="name", limit=1)[0]
		comment = add_comment("Sales Order", order, self.records)
		info = add_comment("Sales Order", order, self.records, comment_type="Info")
		reader, plan = self.reader(self.rahul, "Comment")
		rows = reader.fetch_records_by_names(plan, [comment, info])
		self.assertEqual([row["name"] for row in rows], [comment])  # the plan keeps comment_type "Comment"

	def test_territory_permission_matches_frappe(self):
		grant_test_territory(self.ananya, self.records)
		order = add_test_territory_order(self.records)
		listed = set(frappe.get_list("Sales Order", user=self.ananya, pluck="name", limit=0))
		self.assertLess(len(listed), frappe.db.count("Sales Order"))  # the permission narrows the list
		self.assertEqual(self.names(self.ananya, "Sales Order"), listed)
		self.assertIn(order, listed)
		reader, plan = self.reader(self.ananya, "Sales Order")
		outside = frappe.db.get_value("Sales Order", {"territory": ("!=", TEST_TERRITORY)})
		self.assertEqual([r["name"] for r in reader.fetch_records_by_names(plan, [order, outside])], [order])

	def test_shared_document_is_not_included(self):
		grant_test_territory(self.ananya, self.records)
		outside = frappe.db.get_value("Sales Order", {"territory": ("!=", TEST_TERRITORY), "docstatus": 1})
		self.share("Sales Order", outside, self.ananya)
		listed = set(frappe.get_list("Sales Order", user=self.ananya, pluck="name", limit=0))
		self.assertIn(outside, listed)  # Frappe lists the shared order
		self.assertNotIn(outside, self.names(self.ananya, "Sales Order"))
		reader, plan = self.reader(self.ananya, "Sales Order")
		self.assertEqual(reader.fetch_records_by_names(plan, [outside]), [])
