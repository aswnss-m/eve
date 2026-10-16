"""Frappe tests for record proposals applied from Ask Eve. Needs a site with ERPNext data:
bench --site <site> run-tests --module eve_agent.tests.test_actions"""

import frappe
from frappe.tests.utils import FrappeTestCase

from eve_agent.api.actions import apply

SETTINGS = "Eve Agent Settings"


class ApplyTests(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.saved = {k: frappe.db.get_single_value(SETTINGS, k) for k in ("allow_create", "allow_update", "write_doctypes", "enable_chat")}
		frappe.db.set_single_value(
			SETTINGS,
			{"allow_create": 1, "allow_update": 1, "write_doctypes": "Customer\nSales Invoice", "enable_chat": 1},
		)
		frappe.clear_cache(doctype=SETTINGS)
		self.created = []

	def tearDown(self):
		for doctype, name in reversed(self.created):
			doc = frappe.get_doc(doctype, name)
			if doc.docstatus == 1:
				doc.cancel()
			doc.delete()
		frappe.db.set_single_value(SETTINGS, self.saved)
		frappe.clear_cache(doctype=SETTINGS)

	def test_create_customer_and_update_draft(self):
		group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		out = apply("create", "Customer", {"customer_name": "Eve Apply Test", "customer_group": group, "docstatus": 1, "bogus": "x"})
		self.created.append(("Customer", out["name"]))
		self.assertEqual(out["doctype"], "Customer")
		self.assertEqual(out["docstatus"], 0)
		self.assertFalse(out["is_submittable"])
		self.assertEqual(frappe.db.get_value("Customer", out["name"], "customer_group"), group)

		other = frappe.db.get_value("Customer Group", {"is_group": 0, "name": ("!=", group)}, "name") or group
		updated = apply("update", "Customer", {"customer_group": other}, name=out["name"])
		self.assertEqual(frappe.db.get_value("Customer", updated["name"], "customer_group"), other)

	def test_submittable_document_is_created_as_draft_with_child_rows(self):
		row = frappe.db.sql(
			"""select si.customer, sii.item_code, si.company from `tabSales Invoice` si
			join `tabSales Invoice Item` sii on sii.parent = si.name where si.docstatus = 1 limit 1""",
			as_dict=True,
		)
		if not row:
			self.skipTest("No submitted Sales Invoice to copy a customer and item from")
		row = row[0]
		out = apply(
			"create",
			"Sales Invoice",
			{"customer": row.customer, "company": row.company, "items": [{"item_code": row.item_code, "qty": 2, "nope": 1}]},
		)
		self.created.append(("Sales Invoice", out["name"]))
		self.assertTrue(out["is_submittable"])
		self.assertEqual(out["docstatus"], 0)
		self.assertEqual(frappe.db.get_value("Sales Invoice", out["name"], "docstatus"), 0)
		items = frappe.get_all("Sales Invoice Item", filters={"parent": out["name"]}, fields=["item_code", "qty"])
		self.assertEqual([(i.item_code, i.qty) for i in items], [(row.item_code, 2.0)])

	def test_refusals(self):
		frappe.db.set_single_value(SETTINGS, "allow_create", 0)
		frappe.clear_cache(doctype=SETTINGS)
		with self.assertRaises(frappe.ValidationError):
			apply("create", "Customer", {"customer_name": "x"})
		frappe.db.set_single_value(SETTINGS, "allow_create", 1)
		frappe.clear_cache(doctype=SETTINGS)
		with self.assertRaises(frappe.ValidationError):
			apply("create", "Item", {"item_code": "x"})  # not in the list
		submitted = frappe.db.get_value("Sales Invoice", {"docstatus": 1}, "name")
		if submitted:
			with self.assertRaises(frappe.ValidationError):
				apply("update", "Sales Invoice", {"remarks": "x"}, name=submitted)
