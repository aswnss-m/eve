"""Synthetic "who knows what" activity for a demo site.

Seeded ERP data is usually all owned by Administrator, so the people layer
(CREATED_BY, MANAGED_BY, SOLD_TO, comments, assignments) has nothing to show.
This adds, for employees that have a user account:

- a Sales Person per employee, assigned to customers (Customer Sales Team)
- comments on top customers and recent sales orders, written as that user
- open ToDos on customers, assigned to that user

Safe to run twice: customers that already have a sales team, and records that
already have seeded comments, are skipped. Synthetic data only. Run with:

    bench --site <site> execute eve_agent.demo.seed_people.run
    bench --site <site> sync-data
"""

import random

import frappe

SEED_MARK = "[eve-demo]"
CUSTOMER_NOTES = (
	"Asked for a volume discount if they commit to quarterly orders.",
	"Prefers delivery on Mondays; receiving dock closes at 3pm.",
	"Procurement contact changed last month, new approver is in finance.",
	"Interested in the new connector range, send samples.",
	"Payment usually arrives 10 days late, follow up before month end.",
	"Wants a single consolidated invoice per month.",
)
ORDER_NOTES = (
	"Customer confirmed the revised delivery date.",
	"Split shipment approved: half now, rest next week.",
	"Price matched the competitor quote after manager approval.",
	"Expedite: this order is blocking their production line.",
)
TODO_NOTES = (
	"Quarterly business review with the customer",
	"Chase overdue payment",
	"Send updated price list",
	"Check stock before confirming the next order",
)


def _sales_people() -> list[tuple[str, str]]:
	"""(sales person, user) for every employee with a user account."""
	people = []
	employees = frappe.get_all(
		"Employee",
		filters={"user_id": ["is", "set"], "status": "Active"},
		fields=["name", "employee_name", "user_id"],
	)
	for emp in employees:
		sp = frappe.db.get_value("Sales Person", {"employee": emp.name})
		if not sp:
			sp = (
				frappe.get_doc(
					{
						"doctype": "Sales Person",
						"sales_person_name": emp.employee_name,
						"parent_sales_person": "Sales Team",
						"employee": emp.name,
						"enabled": 1,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		people.append((sp, emp.user_id))
	return people


def _top_customers(limit: int) -> list[str]:
	rows = frappe.db.sql(
		"""select customer from `tabSales Invoice` where docstatus = 1
		group by customer order by sum(base_grand_total) desc limit %s""",
		limit,
	)
	return [r[0] for r in rows]


def _already_seeded(doctype: str, name: str) -> bool:
	return bool(
		frappe.db.exists(
			"Comment",
			{"reference_doctype": doctype, "reference_name": name, "content": ["like", f"%{SEED_MARK}%"]},
		)
	)


def _comment_as(user: str, doctype: str, name: str, text: str) -> None:
	frappe.set_user(user)
	try:
		frappe.get_doc(doctype, name).add_comment("Comment", f"{text} {SEED_MARK}")
	finally:
		frappe.set_user("Administrator")


def run(customers: int = 30, orders: int = 40, seed: int = 7) -> str:
	rng = random.Random(seed)
	people = _sales_people()
	if not people:
		return "No active employees with a user account; nothing to seed."

	assigned = commented = todos = 0
	for i, customer in enumerate(_top_customers(customers)):
		sp, user = people[i % len(people)]
		doc = frappe.get_doc("Customer", customer)
		if not doc.get("sales_team"):
			doc.append("sales_team", {"sales_person": sp, "allocated_percentage": 100})
			doc.save(ignore_permissions=True)
			assigned += 1
		if not _already_seeded("Customer", customer):
			_comment_as(user, "Customer", customer, rng.choice(CUSTOMER_NOTES))
			commented += 1
			if i % 2 == 0:
				frappe.get_doc(
					{
						"doctype": "ToDo",
						"allocated_to": user,
						"assigned_by": "Administrator",
						"reference_type": "Customer",
						"reference_name": customer,
						"description": f"{rng.choice(TODO_NOTES)} {SEED_MARK}",
						"priority": rng.choice(["High", "Medium", "Low"]),
					}
				).insert(ignore_permissions=True)
				todos += 1

	# Comments on recent orders by the customer's sales person, so trails have notes.
	owner_by_customer = {}
	for c in frappe.get_all("Sales Team", filters={"parenttype": "Customer"}, fields=["parent", "sales_person"]):
		owner_by_customer[c.parent] = c.sales_person
	user_by_sp = dict(people)
	recent_orders = frappe.get_all(
		"Sales Order",
		filters={"docstatus": 1},
		fields=["name", "customer"],
		order_by="transaction_date desc",
		limit=orders,
	)
	for so in recent_orders:
		user = user_by_sp.get(owner_by_customer.get(so.customer))
		if user and not _already_seeded("Sales Order", so.name):
			_comment_as(user, "Sales Order", so.name, rng.choice(ORDER_NOTES))
			commented += 1

	frappe.db.commit()
	return f"Sales people: {len(people)}, customers assigned: {assigned}, comments: {commented}, todos: {todos}"
