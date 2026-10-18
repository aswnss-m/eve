"""Frappe tests for permission-aware chat. Run with (from ~/eve-bench):
bench --site eve.localhost run-tests --skip-before-tests --module eve_agent.tests.test_access_frappe

They use the seeded demo users (`<name>@eve-demo.local`) and skip when those are missing.
"""

import re
import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import frappe
import frappe.share
from frappe.core.doctype.user.user import STANDARD_USERS
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime, today
from frappe.utils.nestedset import get_root_of

from eve_agent.access.build import build_profile_graph, profile_graph_names
from eve_agent.access.profiles import (
	CAPACITY_LOG_FLAG,
	CAPACITY_MESSAGE,
	FAILED_MESSAGE,
	PREPARING_MESSAGE,
	PROFILE_DOCTYPE,
	READY_MESSAGE,
	build_flag,
	cleanup_profiles,
	delete_profile,
	enqueue_profile_rebuilds,
	ensure_ready,
	mark_all_pending_and_rebuild,
	run_profile_build,
)
from eve_agent.access.reader import ProfileReader
from eve_agent.access.resolve import hook_restricted, is_own_record, resolve_scope
from eve_agent.access.scope import SPECIAL_DROP, SPECIAL_KEEP, AccessScope, filter_plan
from eve_agent.falkor.importer.data_rows import FIELD_ALLOWLIST, STANDARD_FIELDS
from eve_agent.falkor.importer.incremental import IncrementalSummary
from eve_agent.sync import record_reader
from eve_agent.sync.data_sync import _run_data_sync, base_data_graph, build_data_graph, load_plan
from eve_agent.sync.schema_sync import get_graph_client

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


class ProfileGraphTests(FrappeTestCase):
	PROFILE_GRAPH = "eve_test_profile"
	MAIN_GRAPH = "eve_test_main"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.settings = frappe.get_single("Eve Agent Settings")
		try:
			get_graph_client(cls.settings, cls.PROFILE_GRAPH).ping()
		except Exception:
			raise unittest.SkipTest("FalkorDB is not reachable") from None
		cls.rahul = seeded_user("rahul")
		cls.plan = data_plan()
		cls.scope = resolve_scope(cls.rahul, cls.plan)
		cls.summary = build_profile_graph(cls.settings, cls.scope, cls.PROFILE_GRAPH)
		cls.graph = get_graph_client(cls.settings, cls.PROFILE_GRAPH)

	@classmethod
	def tearDownClass(cls):
		for name in (cls.PROFILE_GRAPH, cls.MAIN_GRAPH):
			get_graph_client(cls.settings, name).delete_graph()
		super().tearDownClass()

	def count(self, cypher: str) -> int:
		return int(self.graph.query(cypher).rows[0][0] or 0)

	def test_profile_graph_names(self):
		base = base_data_graph(self.settings)
		self.assertEqual(profile_graph_names(self.settings, "abc"), (f"{base}_pabc", f"{base}_pabc_next"))

	def test_sales_profile_has_no_accounts_nodes(self):
		doctypes = [*ACCOUNTS_DOCTYPES, "Employee"]
		self.assertEqual(self.count(f"MATCH (n:Record) WHERE n.doctype IN {doctypes} RETURN count(n)"), 0)

	def test_no_invoice_derived_edges(self):
		self.assertEqual(self.count("MATCH ()-[r:BOUGHT|BILLED_BY|PAID_BY|SOLD_TO]->() RETURN count(r)"), 0)

	def test_order_totals_match_sql(self):
		expected = frappe.db.sql("select sum(base_grand_total) from `tabSales Order` where docstatus=1")[0][0]
		query = "MATCH (o:`Sales Order` {docstatus: 1}) RETURN sum(o.base_grand_total)"
		got = self.graph.query(query).rows[0][0]
		self.assertTrue(expected)
		self.assertAlmostEqual(float(got), float(expected), places=2)

	def test_users_kept_with_allowlisted_fields(self):
		users = frappe.get_all("User", {"user_type": "System User", "name": ("not in", STANDARD_USERS)})
		self.assertEqual(self.count("MATCH (n:User) RETURN count(n)"), len(users))
		allowed = {*FIELD_ALLOWLIST["User"], *STANDARD_FIELDS, "doctype", "title"}
		rows = self.graph.query("MATCH (n:User) UNWIND keys(n) AS k RETURN DISTINCT k").rows
		props = {row[0] for row in rows}
		self.assertTrue(props)
		self.assertLessEqual(props, allowed)

	def test_main_graph_build_unchanged(self):
		default = build_data_graph(self.settings, self.MAIN_GRAPH)
		explicit = build_data_graph(
			self.settings, self.MAIN_GRAPH, plan=load_plan(self.settings), reader=record_reader
		)
		records = [re.search(r"records: (\d+)", text) for text in (default, explicit)]
		self.assertTrue(all(records))
		self.assertEqual(records[0].group(1), records[1].group(1))
		self.assertGreater(int(records[0].group(1)), int(re.search(r"records: (\d+)", self.summary).group(1)))


class ProfileLifecycleTests(FrappeTestCase):
	"""No build reaches the running worker (ruling R3): frappe.enqueue is patched, tests run builds directly.

	run_profile_build commits, so tearDown deletes every profile a test made and commits as well.
	"""

	UNMAPPED_KEY = "evetestunmapped"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.settings = frappe.get_single("Eve Agent Settings")
		try:
			get_graph_client(cls.settings).ping()
		except Exception:
			raise unittest.SkipTest("FalkorDB is not reachable") from None
		cls.rahul, cls.ananya = seeded_user("rahul"), seeded_user("ananya")
		cls.priya, cls.meera = seeded_user("priya"), seeded_user("meera")
		plan = data_plan()
		cls.scope = resolve_scope(cls.rahul, plan)
		cls.priya_scope = resolve_scope(cls.priya, plan)
		cls.meera_scope = resolve_scope(cls.meera, plan)
		cls.all_keys = (cls.scope.key, cls.priya_scope.key, cls.meera_scope.key, cls.UNMAPPED_KEY)

	def setUp(self):
		self.keys = set()
		self.saved = {}
		self.enqueue = patch("frappe.enqueue").start()
		self.addCleanup(patch.stopall)
		self.clear_flags()

	def tearDown(self):
		for key in self.keys:
			delete_profile(key)
		if self.saved:
			frappe.db.set_single_value("Eve Agent Settings", self.saved)
		frappe.db.commit()
		self.clear_flags()

	def clear_flags(self):
		for key in self.all_keys:
			frappe.cache.delete(build_flag(key))
		frappe.cache.delete(frappe.cache.make_key(CAPACITY_LOG_FLAG))

	def ask(self, scope, user):
		self.keys.add(scope.key)
		return ensure_ready(scope, user)

	def profile(self, key):
		fields = ["status", "active_graph", "waiting_users", "last_error", "scope_json"]
		fields += ["member_count", "graph_size"]
		return frappe.db.get_value(PROFILE_DOCTYPE, key, fields, as_dict=True)

	def make_ready(self, scope, user) -> str:
		"""A Ready profile on its first slot (a tiny graph) with no build in flight, as after a build."""
		self.ask(scope, user)
		graph = profile_graph_names(self.settings, scope.key)[0]
		self.make_graph(graph)
		frappe.db.set_value(PROFILE_DOCTYPE, scope.key, {"status": "Ready", "active_graph": graph})
		self.clear_flags()
		return graph

	def fake_build(self, settings, scope, graph_name):
		self.make_graph(graph_name)
		return "built"

	def builds(self) -> int:
		method = "eve_agent.access.profiles.run_profile_build"
		return sum(c.args[0] == method for c in self.enqueue.call_args_list)

	def graphs(self) -> set[str]:
		return set(get_graph_client(self.settings).redis.execute_command("GRAPH.LIST"))

	def make_graph(self, name):
		get_graph_client(self.settings, name).query("CREATE (:EveTest)")

	def set_setting(self, field, value):
		self.saved.setdefault(field, frappe.db.get_single_value("Eve Agent Settings", field))
		frappe.db.set_single_value("Eve Agent Settings", field, value)

	def require_no_other_profiles(self):
		if frappe.db.count(PROFILE_DOCTYPE):
			self.skipTest("other Eve Access Profiles exist on this site")

	def test_first_question_returns_preparing(self):
		before = frappe.db.count(PROFILE_DOCTYPE)
		self.assertEqual(self.ask(self.scope, self.rahul), (None, PREPARING_MESSAGE))
		self.assertEqual(frappe.db.count(PROFILE_DOCTYPE), before + 1)
		profile = self.profile(self.scope.key)
		self.assertEqual(profile.status, "Pending")
		self.assertEqual(profile.waiting_users, self.rahul)
		self.assertEqual(AccessScope.from_json(profile.scope_json), self.scope)
		self.assertEqual(self.builds(), 1)

	def test_ensure_ready_dedupes_builds(self):
		before = frappe.db.count(PROFILE_DOCTYPE)
		self.ask(self.scope, self.rahul)
		self.assertEqual(self.ask(self.scope, self.ananya), (None, PREPARING_MESSAGE))  # same access
		self.ask(self.scope, self.rahul)
		self.assertEqual(self.builds(), 1)
		self.assertEqual(frappe.db.count(PROFILE_DOCTYPE), before + 1)
		self.assertEqual(self.profile(self.scope.key).waiting_users.split("\n"), [self.rahul, self.ananya])

	def test_build_makes_profile_ready(self):
		# priya has a Chat Role: a first build drops profiles no chat user maps to, such as rahul's.
		scope, key = self.priya_scope, self.priya_scope.key
		first, second = profile_graph_names(self.settings, key)
		self.ask(scope, self.priya)
		with patch("frappe.publish_realtime") as publish:
			run_profile_build(key)
		publish.assert_called_once_with("msgprint", READY_MESSAGE, user=self.priya)
		profile = self.profile(key)
		self.assertEqual((profile.status, profile.active_graph), ("Ready", first))
		self.assertFalse(profile.waiting_users)
		self.assertRegex(profile.graph_size, r"^\d+ MB$")
		self.assertGreaterEqual(profile.member_count, 1)
		self.assertEqual(self.ask(scope, self.priya), (first, None))
		self.assertIn(first, self.graphs())
		# A rebuild after a main sync goes into the other slot; the old one is served meanwhile.
		enqueue_profile_rebuilds()
		self.assertEqual(self.builds(), 2)
		self.assertEqual(self.ask(scope, self.priya), (first, None))
		run_profile_build(key)
		self.assertEqual(self.ask(scope, self.priya), (second, None))
		self.assertLessEqual({first, second}, self.graphs())

	def test_failed_build_marks_failed_and_retries(self):
		key = self.scope.key
		self.ask(self.scope, self.rahul)
		down = RuntimeError("FalkorDB is down")
		with patch("frappe.log_error") as log_error:
			with patch("eve_agent.access.profiles.build_profile_graph", side_effect=down):
				run_profile_build(key)
		log_error.assert_called_once()
		profile = self.profile(key)
		self.assertEqual(profile.status, "Failed")
		self.assertIn("FalkorDB is down", profile.last_error)
		self.assertEqual(self.ask(self.scope, self.rahul), (None, FAILED_MESSAGE))
		self.assertEqual(self.builds(), 2)  # the next question retries

	def test_failed_rebuild_keeps_ready_profile(self):
		key = self.priya_scope.key
		graph = self.make_ready(self.priya_scope, self.priya)
		down = RuntimeError("FalkorDB is down")
		failing = patch("eve_agent.access.profiles.build_profile_graph", side_effect=down)
		with patch("frappe.log_error") as log_error, failing:
			run_profile_build(key)
			log_error.assert_called_once()
			profile = self.profile(key)
			self.assertEqual(profile.status, "Ready")
			self.assertIn("FalkorDB is down", profile.last_error)
			self.assertEqual(self.ask(self.priya_scope, self.priya), (graph, None))
			# Marked for rebuild (say a permlevel change), the old graph may show too much: Failed.
			mark_all_pending_and_rebuild()
			self.clear_flags()
			run_profile_build(key)
			self.assertEqual(self.profile(key).status, "Failed")

	def test_capacity_refuses_when_all_recent(self):
		self.require_no_other_profiles()
		self.set_setting("max_access_profiles", 1)
		self.ask(self.priya_scope, self.priya)
		with patch("frappe.log_error") as log_error:
			self.assertEqual(self.ask(self.meera_scope, self.meera), (None, CAPACITY_MESSAGE))
			self.assertEqual(self.ask(self.meera_scope, self.meera), (None, CAPACITY_MESSAGE))
		log_error.assert_called_once()  # at most once an hour
		self.assertFalse(frappe.db.exists(PROFILE_DOCTYPE, self.meera_scope.key))
		self.assertTrue(frappe.db.exists(PROFILE_DOCTYPE, self.priya_scope.key))
		self.assertEqual(self.builds(), 1)

	def test_capacity_evicts_least_recently_used_idle_profile(self):
		self.require_no_other_profiles()
		self.set_setting("max_access_profiles", 2)
		self.ask(self.scope, self.rahul)
		self.ask(self.priya_scope, self.priya)
		for key, days in ((self.scope.key, 3), (self.priya_scope.key, 2)):
			frappe.db.set_value(PROFILE_DOCTYPE, key, "last_used_at", add_days(now_datetime(), -days))
		mapped = {self.scope.key: 1, self.priya_scope.key: 1}  # rahul has no Chat Role: count him in
		with patch("eve_agent.access.profiles.member_counts", return_value=mapped):
			self.assertEqual(self.ask(self.meera_scope, self.meera), (None, PREPARING_MESSAGE))
		self.assertFalse(frappe.db.exists(PROFILE_DOCTYPE, self.scope.key))
		self.assertTrue(frappe.db.exists(PROFILE_DOCTYPE, self.priya_scope.key))

	def test_capacity_evicts_unmapped_profile_used_just_now(self):
		self.require_no_other_profiles()
		self.set_setting("max_access_profiles", 1)
		self.ask(replace(self.scope, key=self.UNMAPPED_KEY), self.rahul)
		self.assertEqual(self.ask(self.meera_scope, self.meera), (None, PREPARING_MESSAGE))
		self.assertFalse(frappe.db.exists(PROFILE_DOCTYPE, self.UNMAPPED_KEY))
		self.assertTrue(frappe.db.exists(PROFILE_DOCTYPE, self.meera_scope.key))

	def test_cleanup_drops_unmapped_and_stale(self):
		stale, kept = self.priya_scope, self.meera_scope  # priya and meera have a Chat Role
		unmapped = replace(self.scope, key=self.UNMAPPED_KEY)
		self.assertNotEqual(stale.key, kept.key)
		for scope, user in ((stale, self.priya), (kept, self.meera), (unmapped, self.rahul)):
			self.ask(scope, user)
			for name in profile_graph_names(self.settings, scope.key):
				self.make_graph(name)
		frappe.db.set_value(PROFILE_DOCTYPE, stale.key, "last_used_at", add_days(now_datetime(), -15))
		cleanup_profiles()
		graphs = self.graphs()
		for key in (stale.key, unmapped.key):
			self.assertFalse(frappe.db.exists(PROFILE_DOCTYPE, key))
			self.assertFalse(set(profile_graph_names(self.settings, key)) & graphs)
		self.assertTrue(frappe.db.exists(PROFILE_DOCTYPE, kept.key))
		self.assertLessEqual(set(profile_graph_names(self.settings, kept.key)), graphs)
		self.assertGreaterEqual(self.profile(kept.key).member_count, 1)

	def test_first_build_drops_unmapped_profiles(self):
		unmapped = replace(self.scope, key=self.UNMAPPED_KEY)
		self.ask(unmapped, self.rahul)
		for name in profile_graph_names(self.settings, unmapped.key):
			self.make_graph(name)
		self.ask(self.priya_scope, self.priya)
		with patch("eve_agent.access.profiles.build_profile_graph", self.fake_build):
			run_profile_build(self.priya_scope.key)
		self.assertFalse(frappe.db.exists(PROFILE_DOCTYPE, unmapped.key))
		self.assertFalse(set(profile_graph_names(self.settings, unmapped.key)) & self.graphs())
		profile = self.profile(self.priya_scope.key)
		self.assertEqual(profile.status, "Ready")
		self.assertGreaterEqual(profile.member_count, 1)

	def test_mark_all_pending(self):
		self.ask(self.scope, self.rahul)
		self.make_ready(self.meera_scope, self.meera)  # also clears the flags, as after both first builds
		mark_all_pending_and_rebuild()
		self.assertEqual(set(frappe.get_all(PROFILE_DOCTYPE, pluck="status")), {"Pending"})
		self.assertEqual(self.builds(), 4)
		self.assertEqual(self.ask(self.meera_scope, self.meera), (None, PREPARING_MESSAGE))

	def test_rebuild_requested_during_a_build_starts_it_over(self):
		"""E.g. a permlevel change while the build runs: the graph built before it is never served."""
		key = self.priya_scope.key
		self.ask(self.priya_scope, self.priya)
		built = []

		def build(settings, scope, graph_name):
			built.append(graph_name)
			if len(built) == 1:
				mark_all_pending_and_rebuild()
			return self.fake_build(settings, scope, graph_name)

		with patch("eve_agent.access.profiles.build_profile_graph", build):
			run_profile_build(key)
		self.assertEqual(len(built), 2)
		self.assertEqual(self.profile(key).status, "Ready")
		self.assertEqual(self.builds(), 1)  # the running build started over; nothing else was queued

	def test_rebuild_requested_during_an_in_place_rebuild_starts_it_over(self):
		"""E.g. a main sync finishing while the profile rebuilds: the rebuild must not end on older data."""
		key = self.priya_scope.key
		first, second = profile_graph_names(self.settings, key)
		self.make_ready(self.priya_scope, self.priya)
		enqueue_profile_rebuilds()
		self.assertEqual(self.builds(), 2)  # the first question, then this rebuild
		built = []

		def build(settings, scope, graph_name):
			built.append(graph_name)
			if len(built) == 1:
				enqueue_profile_rebuilds()  # while this build is in flight
			return self.fake_build(settings, scope, graph_name)

		with patch("eve_agent.access.profiles.build_profile_graph", build):
			run_profile_build(key)
		self.assertEqual(built, [second, second])
		self.assertEqual(self.builds(), 2)  # not queued again: the running build started over
		self.assertEqual(self.ask(self.priya_scope, self.priya), (second, None))

	def test_profile_deleted_during_its_build_leaves_no_graph(self):
		key = self.scope.key
		self.ask(self.scope, self.rahul)

		def build(settings, scope, graph_name):
			delete_profile(key)
			self.make_graph(graph_name)
			return "built"

		with patch("eve_agent.access.profiles.build_profile_graph", build):
			run_profile_build(key)
		self.assertFalse(frappe.db.exists(PROFILE_DOCTYPE, key))
		self.assertFalse(set(profile_graph_names(self.settings, key)) & self.graphs())

	def test_main_sync_queues_profile_rebuilds_on_success_only(self):
		# The build, catch-up and Settings writes are stubbed, so the site's main graph stays as it is.
		cases = (
			(None, None, "Success", True),
			(RuntimeError("FalkorDB is down"), None, "Failed", False),
			(None, RuntimeError("queue is down"), "Success", True),  # a queueing error never fails the sync
		)
		for build_error, queue_error, status, queued in cases:
			stubs = {
				"build_data_graph": {"return_value": "built", "side_effect": build_error},
				"load_plan": {},
				"catch_up": {"return_value": IncrementalSummary()},
				"publish_sync_done": {},
			}
			with self.subTest(build_error=build_error, queue_error=queue_error), ExitStack() as stack:
				for name, stub in stubs.items():
					stack.enter_context(patch(f"eve_agent.sync.data_sync.{name}", **stub))
				rebuilds = stack.enter_context(
					patch("eve_agent.access.profiles.enqueue_profile_rebuilds", side_effect=queue_error)
				)
				log_error = stack.enter_context(patch("frappe.log_error"))
				written = stack.enter_context(patch.object(frappe.db, "set_single_value"))
				_run_data_sync()
				self.assertEqual(rebuilds.called, queued)
				self.assertEqual(written.call_args.args[1]["last_data_sync_status"], status)
				self.assertEqual(log_error.called, bool(build_error or queue_error))
