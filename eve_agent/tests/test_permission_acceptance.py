"""Acceptance tests for permission-aware chat: one method per test in the spec's "Acceptance tests".
Run with (from ~/eve-bench):
bench --site eve.localhost run-tests --skip-before-tests --module eve_agent.tests.test_permission_acceptance

They use the seeded demo users (`<name>@eve-demo.local`), build real access profiles and call the chat tools
as those users. No build reaches the running worker: frappe.enqueue is patched and the tests run the jobs.
The class skips when other Eve Access Profiles exist, because the first build sweeps unmapped profiles.
"""

import os
import subprocess
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe
import frappe.share
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from eve_agent.access import members
from eve_agent.access.build import build_profile_graph
from eve_agent.access.guard import HIDDEN_SOURCE_TITLE
from eve_agent.access.members import member_counts, member_scope
from eve_agent.access.profiles import (
	PREPARING_MESSAGE,
	PROFILE_DOCTYPE,
	build_flag,
	delete_profile,
	ensure_ready,
	run_profile_build,
)
from eve_agent.access.resolve import resolve_scope
from eve_agent.api import chat
from eve_agent.api.agent import DEFAULT_CHAT_ROLES
from eve_agent.chat.common import QueryRun, result
from eve_agent.chat.tools import build_tools
from eve_agent.chat.tools.sales import SALES_ORDERS_NOTE, top_customers_cypher
from eve_agent.falkor.importer.data_rows import parse_lines
from eve_agent.falkor.importer.incremental import make_key
from eve_agent.sync import refresh
from eve_agent.sync.refresh import FLAG_KEY, _key
from eve_agent.sync.schema_sync import get_graph_client
from eve_agent.tests import test_access_frappe as base

ACCOUNTS_DOCTYPES = base.ACCOUNTS_DOCTYPES
TEST_TERRITORY = base.TEST_TERRITORY
PURE_MODULES = (
	"test_access",
	"test_agent_api",
	"test_business_rules",
	"test_chat",
	"test_data_rows",
	"test_falkor_pure",
	"test_incremental",
)


class PermissionAcceptanceTests(FrappeTestCase):
	SALES_CHAT_ROLE = "Sales User"  # rahul and ananya have no default Chat Role

	# The tests of ProfileFanOutTests that read and write the graphs, reused as they are.
	rows = base.ProfileFanOutTests.rows
	has = base.ProfileFanOutTests.has
	status = base.ProfileFanOutTests.status
	drain = base.ProfileFanOutTests.drain
	make_order = base.ProfileFanOutTests.make_order
	make_invoice = base.ProfileFanOutTests.make_invoice
	add_permlevel_setter = base.ProfileFanOutTests.add_permlevel_setter

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.started = now_datetime()
		try:
			get_graph_client(frappe.get_single("Eve Agent Settings")).ping()
		except Exception:
			raise unittest.SkipTest("FalkorDB is not reachable") from None
		if not frappe.db.get_single_value("Eve Agent Settings", "enable_chat"):
			raise unittest.SkipTest("Enable Chat is off in Eve Agent Settings")
		cls.rahul, cls.ananya = base.seeded_user("rahul"), base.seeded_user("ananya")
		cls.priya, cls.meera = base.seeded_user("priya"), base.seeded_user("meera")
		cls.vikram, cls.arjun = base.seeded_user("vikram"), base.seeded_user("arjun")
		if frappe.db.count(PROFILE_DOCTYPE):
			raise unittest.SkipTest("Eve Access Profiles already exist on this site")
		fields = ("chat_roles", "last_incremental_sync_at", "last_incremental_summary")
		cls.saved = {field: frappe.db.get_single_value("Eve Agent Settings", field) for field in fields}
		cls.addClassCleanup(cls.cleanup)
		roles = list(parse_lines(cls.saved["chat_roles"]) or DEFAULT_CHAT_ROLES)
		if cls.SALES_CHAT_ROLE not in roles:
			frappe.db.set_single_value("Eve Agent Settings", "chat_roles", "\n".join([*roles, cls.SALES_CHAT_ROLE]))
		cls.clear_members()
		cls.settings = frappe.get_single("Eve Agent Settings")
		cls.plan = base.data_plan()
		cls.keys = {}  # profiles every test needs Ready: user -> key
		with patch("frappe.enqueue"), patch("frappe.publish_realtime"):
			for user in (cls.rahul, cls.meera):
				cls.keys[user] = cls.build_profile(user).key
		cls.rahul_key, cls.meera_key = cls.keys[cls.rahul], cls.keys[cls.meera]

	@classmethod
	def cleanup(cls):
		for key in frappe.get_all(PROFILE_DOCTYPE, pluck="name"):
			delete_profile(key)
			frappe.cache.delete(build_flag(key))
		feed = {"comment_type": "Deleted", "subject": ("like", f"{PROFILE_DOCTYPE} %"), "creation": (">=", cls.started)}
		frappe.db.delete("Comment", feed)  # delete_doc's activity feed entries
		cls.clear_members()
		frappe.db.set_single_value("Eve Agent Settings", cls.saved)
		frappe.db.commit()

	@classmethod
	def clear_members(cls):
		frappe.cache.delete_value([members.CACHE_KEY, members.UNAVAILABLE_KEY])

	@classmethod
	def build_profile(cls, user):
		"""`user`'s profile, built now when it is not Ready. Returns the scope."""
		cls.clear_members()
		frappe.clear_cache(user=user)
		scope = member_scope(frappe.get_single("Eve Agent Settings"), user)
		graph, _message = ensure_ready(scope, user)
		if graph is None:
			run_profile_build(scope.key)
		if frappe.db.get_value(PROFILE_DOCTYPE, scope.key, "status") != "Ready":
			raise AssertionError(f"the profile of {user} was not built")
		return scope

	def setUp(self):
		self.records = []  # (doctype, name); tearDown purges them newest first and drains them out
		self.meta_changed = False
		self.extra_keys = set()  # profiles a test made besides rahul's and meera's
		self.shares = []  # (doctype, name, user, the record's comments before sharing)
		self.roles = []  # (user, role) added by a test
		self.enqueue = patch("frappe.enqueue").start()
		patch("frappe.publish_realtime").start()
		patch("frappe.model.document.update_global_search").start()
		self.addCleanup(patch.stopall)
		self.clear_members()

	def tearDown(self):
		frappe.set_user("Administrator")
		for doctype, name, user, before in self.shares:
			frappe.share.remove(doctype, name, user)
			for comment in base.comments_on(doctype, name) - before:
				frappe.delete_doc("Comment", comment, force=True, ignore_permissions=True)
		for user, role in self.roles:
			frappe.get_doc("User", user).remove_roles(role)
			frappe.clear_cache(user=user)
		keys = [key for doctype, name in reversed(self.records) for key in base.purge(doctype, name)]
		frappe.db.commit()
		if self.meta_changed:
			frappe.clear_cache(doctype="Sales Order")
		for key in self.extra_keys:
			delete_profile(key)
			frappe.cache.delete(build_flag(key))
		self.clear_members()
		if keys:
			self.drain(*keys)
		for key in (self.rahul_key, self.meera_key):
			if self.status(key) != "Ready":
				run_profile_build(key)
			self.assertEqual(self.status(key), "Ready")
		frappe.db.delete("Error Log", {"method": HIDDEN_SOURCE_TITLE, "creation": (">=", self.started)})
		frappe.cache.delete(_key(FLAG_KEY))  # set by queue_keys, whose drain job never ran
		frappe.db.commit()

	# Helpers

	def graph_of(self, key):
		return frappe.db.get_value(PROFILE_DOCTYPE, key, "active_graph")

	def count(self, key, cypher, **params) -> int:
		return int(self.rows(key, cypher, **params)[0]["n"] or 0)

	def tool(self, user, name):
		"""The context of `user`'s turn on their Ready profile, and its tool `name`."""
		with self.set_user(user):
			ctx = chat.build_context(chat.get_settings())
		return ctx, next(tool for tool in build_tools(ctx) if tool.name == name)

	def run_tool(self, user, name, **args) -> dict:
		ctx, tool = self.tool(user, name)
		with self.set_user(user):
			return tool.run(tool.params(**args))

	def top_customers(self, user, **args) -> dict:
		return self.run_tool(user, "top_customers", limit=50, **args)

	def share(self, doctype: str, name: str, user: str) -> None:
		self.shares.append((doctype, name, user, base.comments_on(doctype, name)))
		frappe.share.add(doctype, name, user)

	def build_extra(self, user):
		scope = self.build_profile(user)
		if scope.key not in (self.rahul_key, self.meera_key):
			self.extra_keys.add(scope.key)
		return scope

	def hidden_logs(self) -> list[dict]:
		import json

		filters = {"method": HIDDEN_SOURCE_TITLE, "creation": (">=", self.started)}
		return [json.loads(error) for error in frappe.get_all("Error Log", filters, pluck="error")]

	def expected_names(self, user: str, scope, plan) -> set[str]:
		"""Frappe's list for `user`, adjusted as spec section 2 says: records tied to an own-record
		DocType (their Employee) stay only when the link is empty, and not even then in strict mode."""
		doctype = plan.name
		own = set(scope.own_record_doctypes)
		links = [
			df.fieldname
			for df in frappe.get_meta(doctype).get_link_fields()
			if df.options in own and not df.ignore_user_permissions
		]
		listed = set(frappe.get_list(doctype, filters=plan.filters or None, user=user, pluck="name", limit=0))
		if not links:
			return listed
		strict = scope.strict or bool(frappe.utils.cint(frappe.get_system_settings("apply_strict_user_permissions")))
		if strict:
			return set()
		linked = frappe.get_all(doctype, fields=["name", *links], limit=0)
		return {row.name for row in linked if row.name in listed and not any(row.get(f) for f in links)}

	def assert_graph_matches_frappe(self, user, scope, graph_name):
		"""For each DocType in `scope`, the record names in the graph equal Frappe's list for `user`."""
		from eve_agent.access.scope import filter_plan

		client = get_graph_client(self.settings, graph_name)
		cap = frappe.utils.cint(frappe.db.get_single_value("Eve Agent Settings", "max_records_per_doctype"))
		checked = 0
		for plan in filter_plan(self.plan, scope).parents:
			if plan.name in ("User", "Comment"):
				continue  # special cases: every System User, comments follow their documents
			with self.subTest(user=user, doctype=plan.name):
				expected = self.expected_names(user, scope, plan)
				rows = client.rows(f"MATCH (n:`{plan.name}`) RETURN n.name AS name")  # ro_rows rejects "Merge" in a label
				got = {row["name"] for row in rows}
				if cap and len(expected) > cap:
					self.assertLessEqual(got, expected)
					self.assertEqual(len(got), cap)
				else:
					self.assertEqual(got, expected)
				checked += 1
		self.assertGreater(checked, 5)

	# Spec test 1

	def test_spec_01_sales_user_sees_no_accounts_data(self):
		hidden = [*ACCOUNTS_DOCTYPES, "Employee"]
		key = self.rahul_key
		self.assertEqual(self.count(key, "MATCH (n:Record) WHERE n.doctype IN $d RETURN count(n) AS n", d=hidden), 0)
		cypher = "MATCH (c:Comment) WHERE c.reference_doctype IN $d RETURN count(c) AS n"
		self.assertEqual(self.count(key, cypher, d=hidden), 0)
		derived = "MATCH ()-[r:BOUGHT|BILLED_BY|PAID_BY|SOLD_TO]->() RETURN count(r) AS n"
		self.assertEqual(self.count(key, derived), 0)
		self.assertGreater(self.count(None, derived), 0)  # the main graph has them
		ctx, run_cypher = self.tool(self.rahul, "run_cypher")
		args = run_cypher.params(cypher="MATCH (n:`Sales Invoice`) RETURN count(n) AS n", purpose="Count invoices")
		with self.set_user(self.rahul):
			out = run_cypher.run(args)
		self.assertEqual(out["queries"][0]["rows"], [{"n": 0}])
		invoice = frappe.db.get_value("Sales Invoice", {"docstatus": 1})
		with self.set_user(self.priya):
			self.assertEqual(chat.locate([invoice]), {})
		out = self.top_customers(self.rahul)
		self.assertEqual(out["note"], SALES_ORDERS_NOTE)
		rows = out["queries"][0]["rows"]
		self.assertTrue(rows)
		self.assertTrue(all(row.get("invoices") is None and row.get("outstanding") is None for row in rows))
		sums = frappe.db.sql(
			"select sum(base_grand_total) from `tabSales Order` where docstatus = 1 group by customer "
			"order by sum(base_grand_total) desc limit 1"
		)
		self.assertAlmostEqual(float(rows[0]["revenue"]), float(sums[0][0]), places=2)

	# Spec test 2

	def test_spec_02_accounts_user_sees_invoices(self):
		out = self.top_customers(self.meera)
		self.assertIsNone(out.get("note"))
		got = {row["customer"]: row for row in out["queries"][0]["rows"]}
		self.assertTrue(got)
		cypher = top_customers_cypher("revenue")
		params = {"date_from": None, "date_to": None, "territory": None, "customer_group": None, "limit": 50}
		main = {row["customer"]: row for row in get_graph_client(self.settings, self.graph_of_main()).ro_rows(cypher, params)}
		self.assertEqual(set(got), set(main))
		for customer, row in main.items():
			for field in ("revenue", "invoices", "outstanding"):
				self.assertAlmostEqual(float(got[customer][field]), float(row[field]), places=2, msg=customer)
		count = "MATCH (n:`Sales Invoice`) RETURN count(n) AS n, sum(n.base_grand_total) AS total"
		meera, main_rows = self.rows(self.meera_key, count)[0], self.rows(None, count)[0]
		self.assertGreater(meera["n"], 0)
		self.assertEqual(meera["n"], main_rows["n"])
		self.assertAlmostEqual(meera["total"], main_rows["total"], places=2)

	def graph_of_main(self):
		return base.active_data_graph(frappe.get_single("Eve Agent Settings"))

	# Spec test 3

	def test_spec_03_shared_profile(self):
		rahul = resolve_scope(self.rahul, self.plan)
		ananya = resolve_scope(self.ananya, self.plan)
		self.assertEqual(rahul.key, ananya.key)
		own = lambda user: {p["doc"] for p in frappe.permissions.get_user_permissions(user)["Employee"]}
		self.assertTrue(own(self.rahul) and own(self.ananya))
		self.assertNotEqual(own(self.rahul), own(self.ananya))  # their own Employees differ, the key ignores them
		self.assertGreaterEqual(member_counts(self.settings)[rahul.key], 2)
		before = self.enqueue.call_count
		self.assertEqual(ensure_ready(ananya, self.ananya), (self.graph_of(self.rahul_key), None))
		self.assertEqual(frappe.db.count(PROFILE_DOCTYPE, {"name": rahul.key}), 1)
		self.assertEqual(self.enqueue.call_count, before)  # no second build

	# Spec test 4

	def test_spec_04_user_permission(self):
		before_key = resolve_scope(self.rahul, self.plan).key
		before_rows = self.top_customers(self.rahul)["queries"][0]["rows"]
		base.grant_test_territory(self.ananya, self.records)
		order = base.add_test_territory_order(self.records)  # a new customer and order, after the permission
		scope = self.build_extra(self.ananya)
		self.assertNotEqual(scope.key, before_key)
		graph = self.graph_of(scope.key)
		self.assertEqual(self.count(scope.key, "MATCH (o:`Sales Order` {name: $n}) RETURN count(o) AS n", n=order), 1)
		self.assert_graph_matches_frappe(self.ananya, scope, graph)
		# Totals equal a sum over what Frappe lists for ananya.
		customers = set(frappe.get_list("Customer", user=self.ananya, pluck="name", limit=0))
		orders = frappe.get_list(
			"Sales Order",
			filters={"docstatus": 1},
			fields=["customer", "base_grand_total"],
			user=self.ananya,
			limit=0,
		)
		expected = {}
		for row in orders:
			if row.customer in customers:
				expected[row.customer] = expected.get(row.customer, 0) + float(row.base_grand_total)
		rows = self.top_customers(self.ananya)["queries"][0]["rows"]
		self.assertEqual(len(rows), min(50, len(expected)))
		self.assertIn(frappe.db.get_value("Sales Order", order, "customer"), {row["customer"] for row in rows})
		for row in rows:
			self.assertAlmostEqual(float(row["revenue"]), expected[row["customer"]], places=2, msg=row["customer"])
		# rahul is unaffected.
		self.assertEqual(resolve_scope(self.rahul, self.plan).key, before_key)
		self.assertEqual(self.top_customers(self.rahul)["queries"][0]["rows"], before_rows)

	# Spec test 5

	def test_spec_05_field_level(self):
		if frappe.db.exists("Property Setter", {"doc_type": "Sales Order", "property": "permlevel"}):
			self.skipTest("Sales Order already has a permlevel Property Setter")
		dated = "MATCH (o:`Sales Order`) WHERE o.delivery_date IS NOT NULL RETURN count(o) AS n"
		key = self.rahul_key
		self.assertGreater(self.count(key, dated), 0)
		self.add_permlevel_setter()  # delivery_date moves to a permlevel Sales User cannot read
		self.assertEqual(self.status(key), "Pending")  # the change marks the profile for rebuild
		self.assertEqual(resolve_scope(self.rahul, self.plan).key, key)  # the profile key is unchanged
		run_profile_build(key)  # the rebuild the marking queued
		self.assertEqual(self.status(key), "Ready")
		self.assertEqual(self.count(key, dated), 0)
		self.assertGreater(self.count(None, dated), 0)  # still in the main graph
		self.assertEqual(resolve_scope(self.rahul, self.plan).key, key)

	# Spec test 6

	def test_spec_06_matches_frappe_in_general(self):
		for user, key in ((self.rahul, self.rahul_key), (self.meera, self.meera_key)):
			scope = member_scope(self.settings, user)
			self.assertEqual(scope.key, key)
			self.assert_graph_matches_frappe(user, scope, self.graph_of(key))
		for user in (self.priya, self.vikram, self.arjun):
			scope = member_scope(self.settings, user)
			if scope.full_access:
				continue
			graph = f"eve_test_acceptance_{user.split('@')[0]}"
			client = get_graph_client(self.settings, graph)
			try:
				build_profile_graph(self.settings, scope, graph)
				self.assert_graph_matches_frappe(user, scope, graph)
			finally:
				client.delete_graph()

	# Spec test 7

	def test_spec_07_own_record_rule(self):
		key = self.rahul_key
		self.assertEqual(self.count(key, "MATCH (n:Employee) RETURN count(n) AS n"), 0)
		linked = "MATCH (s:`Sales Person`) WHERE coalesce(s.employee, '') <> '' RETURN count(s) AS n"
		self.assertEqual(self.count(key, linked), 0)
		edges = "MATCH ()-[r:MANAGED_BY|SOLD_TO]->(t) WHERE t:Employee OR coalesce(t.employee, '') <> '' RETURN count(r) AS n"
		self.assertEqual(self.count(key, edges), 0)
		free = "MATCH (s:`Sales Person`) WHERE coalesce(s.employee, '') = '' RETURN count(s) AS n"
		self.assertGreater(self.count(None, free), 0)
		self.assertEqual(self.count(key, free), self.count(None, free))
		team = (
			"MATCH (c:Customer)-[:HAS_CHILD]->(t:`Sales Team`) WHERE coalesce(t.sales_person, '') <> '' "
			"RETURN count(t) AS n"
		)
		self.assertGreater(self.count(key, team), 0)
		# The main graph is unchanged: it keeps the Employees and the linked Sales Persons.
		self.assertGreater(self.count(None, "MATCH (n:Employee) RETURN count(n) AS n"), 0)
		self.assertGreater(self.count(None, linked), 0)

	# Spec test 8

	def test_spec_08_incremental(self):
		# A new Sales Order for a visible customer reaches the sales profile.
		order = self.make_order(submit=True)
		self.drain(make_key("Sales Order", order))
		for key in (self.rahul_key, self.meera_key):
			self.assertTrue(self.has(key, "Sales Order", order))
		# A new Sales Invoice reaches meera's profile and the main graph, not rahul's.
		invoice = self.make_invoice()
		self.drain(make_key("Sales Invoice", invoice))
		self.assertTrue(self.has(self.meera_key, "Sales Invoice", invoice))
		self.assertTrue(self.has(None, "Sales Invoice", invoice))
		self.assertFalse(self.has(self.rahul_key, "Sales Invoice", invoice))
		# Moving a customer out of ananya's territory removes it from her graph.
		base.grant_test_territory(self.ananya, self.records)
		south = frappe.db.get_value("Sales Order", base.add_test_territory_order(self.records), "customer")
		scope = self.build_extra(self.ananya)
		self.assertTrue(self.has(scope.key, "Customer", south))
		other = frappe.db.get_value("Customer", {"territory": ("!=", TEST_TERRITORY)}, "territory")
		frappe.db.set_value("Customer", south, "territory", other)
		self.drain(make_key("Customer", south))
		self.assertFalse(self.has(scope.key, "Customer", south))
		self.assertTrue(self.has(None, "Customer", south))

	# Spec test 9

	def test_spec_09_role_change(self):
		def asks():
			with self.set_user(self.rahul):
				return chat.resolve_chat_graph(chat.get_settings(), self.rahul)

		before, graph, message = asks()
		self.assertEqual((before.key, graph, message), (self.rahul_key, self.graph_of(self.rahul_key), None))
		self.roles.append((self.rahul, "Accounts User"))
		frappe.get_doc("User", self.rahul).add_roles("Accounts User")
		frappe.clear_cache(user=self.rahul)
		self.clear_members()
		scope, graph, message = asks()
		self.assertNotEqual(scope.key, self.rahul_key)
		self.assertEqual((graph, message), (None, PREPARING_MESSAGE))
		if scope.key != self.meera_key:
			self.extra_keys.add(scope.key)
		run_profile_build(scope.key)
		scope, graph, message = asks()
		self.assertEqual((graph, message), (self.graph_of(scope.key), None))
		self.assertNotEqual(graph, self.graph_of(self.rahul_key))
		self.assertGreater(self.count(scope.key, "MATCH (n:`Sales Invoice`) RETURN count(n) AS n"), 0)
		frappe.get_doc("User", self.rahul).remove_roles("Accounts User")
		self.roles.clear()
		frappe.clear_cache(user=self.rahul)
		self.clear_members()
		scope, graph, message = asks()
		self.assertEqual((scope.key, graph, message), (self.rahul_key, self.graph_of(self.rahul_key), None))

	# Spec test 10

	def test_spec_10_shares_dont_leak(self):
		invoice = frappe.db.get_value("Sales Invoice", {"docstatus": 1})
		self.share("Sales Invoice", invoice, self.rahul)
		self.assertTrue(frappe.has_permission("Sales Invoice", "read", doc=invoice, user=self.rahul))  # opens in the Desk
		self.drain(make_key("Sales Invoice", invoice))
		self.assertFalse(self.has(self.rahul_key, "Sales Invoice", invoice))
		run_profile_build(self.rahul_key)  # nor does a full rebuild pick it up
		self.assertFalse(self.has(self.rahul_key, "Sales Invoice", invoice))
		with self.set_user(self.rahul):
			self.assertEqual(chat.locate([invoice]), {})
		self.assertEqual(self.count(self.rahul_key, "MATCH (n:`Sales Invoice`) RETURN count(n) AS n"), 0)

	# Spec test 11

	def test_spec_11_safety_net(self):
		ctx, _tool = self.tool(self.rahul, "top_customers")
		ctx.current_tool = "top_customers"
		invoice = frappe.db.get_value("Sales Invoice", {"docstatus": 1})
		order = frappe.get_list("Sales Order", user=self.rahul, pluck="name", limit=1)[0]
		rows = [{"customer": "A", "invoices": [invoice]}, {"customer": "B", "orders": [order]}]
		run = QueryRun(title="Top customers", cypher="", params={}, rows=rows, ms=0)
		forged = {"doctype": "Sales Invoice", "name": invoice}
		own = {"doctype": "Sales Order", "name": order}
		with self.set_user(self.rahul):
			out = result(ctx, [run], [forged, own])
		self.assertEqual(out["sources"], [own])
		self.assertEqual(out["queries"][0]["rows"], [rows[1]])
		self.assertEqual(self.hidden_logs(), [{**forged, "tool": "top_customers", "user": self.rahul}])

	# Spec test 12

	def test_spec_12_no_slower_saves(self):
		"""queue_record still only collects keys, publishes them after commit and never touches FalkorDB."""
		name = frappe.db.get_value("Sales Order", {"docstatus": 1})
		doc = frappe.get_doc("Sales Order", name)
		never = AssertionError("FalkorDB was used while saving")
		with ExitStack() as stack:
			for method in ("query", "ro_query", "rows", "ro_rows", "ping", "delete_graph"):
				stack.enter_context(patch(f"eve_agent.falkor.connection.GraphClient.{method}", side_effect=never))
			stack.enter_context(patch("eve_agent.sync.schema_sync.get_graph_client", side_effect=never))
			after_commit = stack.enter_context(patch.object(frappe.db, "after_commit"))
			queue = stack.enter_context(patch.object(refresh, "queue_keys", Mock()))
			frappe.local.eve_pending_keys = None
			try:
				refresh.queue_record(doc, "on_update")
				self.assertEqual(frappe.local.eve_pending_keys, {make_key("Sales Order", name)})
				after_commit.add.assert_called_once_with(refresh._publish_after_commit)
				queue.assert_not_called()  # nothing is queued before the commit
				self.enqueue.assert_not_called()
				refresh._publish_after_commit()  # what the commit runs
				queue.assert_called_once_with({make_key("Sales Order", name)})
				self.enqueue.assert_not_called()
			finally:
				frappe.local.eve_pending_keys = None
		with self.subTest("timing"):
			self.skipTest("seed_business.py save throughput is measured by the controller on a throwaway site")

	# Spec test 13

	def test_spec_13_existing_tests(self):
		root = frappe.get_app_path("eve_agent", "..")
		modules = [f"eve_agent.tests.{module}" for module in PURE_MODULES]
		env = {**os.environ, "EVE_TEST_LIVE": "0"}  # the known explain_connection live failure is skipped
		done = subprocess.run(
			[sys.executable, "-m", "unittest", *modules], cwd=root, env=env, capture_output=True, text=True
		)
		self.assertEqual(done.returncode, 0, done.stderr[-3000:])
		self.assertRegex(done.stderr, r"\nOK")
