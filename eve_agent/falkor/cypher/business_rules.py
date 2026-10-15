"""ERPNext / HRMS business layer for the data graph.

The generic load gives every Link field a `LINKS_TO {field}` edge and every
child row a `HAS_CHILD` edge. That is faithful but hard to read. The rules
below run after the load, in order, and add named relationships:

- rename: a direct Link becomes a named edge (the LINKS_TO edge is removed),
  e.g. (Sales Order)-[:FOR_CUSTOMER]->(Customer).
- derive: an edge built across child rows or ledgers,
  e.g. (Sales Order)-[:DELIVERED_BY]->(Delivery Note) from Delivery Note Item.
- aggregate: totals rolled up onto one edge or node,
  e.g. (Customer)-[:BOUGHT {qty, amount}]->(Item).

Every rule returns a single count. Rules for DocTypes with no data simply
return 0, so the list can cover modules that a site does not use yet.
See docs/graph-guide.md for the resulting model and example queries.
"""

from dataclasses import dataclass

from eve_agent.falkor.cypher.data_queries import quote_label
from eve_agent.falkor.literals import to_literal

# Masters that almost every document links to. Their Link values stay as node
# properties (e.g. so.company), but the edges are dropped so the graph does not
# collapse into a few giant hubs.
HUB_DOCTYPES = (
	"Company",
	"Currency",
	"UOM",
	"Price List",
	"Fiscal Year",
	"Cost Center",
	"Mode of Payment",
	"Party Type",
	"Stock Entry Type",
	"Letter Head",
	"Print Heading",
	"Terms and Conditions",
	"Holiday List",
)


@dataclass(frozen=True)
class Rule:
	name: str
	cypher: str


def rename(name: str, new_type: str, fields: list[str], dst: str, src: list[str] | None = None, reverse=False) -> Rule:
	"""Replace LINKS_TO edges (by field name) to `dst` with `new_type`. reverse=True flips the direction."""
	where = f"r.field IN {to_literal(fields)}"
	if src:
		where += f" AND a.doctype IN {to_literal(src)}"
	pattern = "(b)-[:{t} {{field: r.field}}]->(a)" if reverse else "(a)-[:{t} {{field: r.field}}]->(b)"
	return Rule(
		name,
		f"MATCH (a:Record)-[r:LINKS_TO]->(b:{quote_label(dst)}) WHERE {where} "
		f"CREATE {pattern.format(t=new_type)} DELETE r RETURN count(*) AS n",
	)


def flow(name: str, new_type: str, prev: str, row: str, field: str, nxt: str) -> Rule:
	"""(prev)-[:new_type]->(nxt) when any `row` of `nxt` links to `prev` through `field`."""
	return Rule(
		name,
		f"MATCH (n:{quote_label(nxt)})-[:HAS_CHILD]->(:{quote_label(row)})"
		f"-[:LINKS_TO {{field: '{field}'}}]->(p:{quote_label(prev)}) "
		f"WITH DISTINCT p, n CREATE (p)-[:{new_type}]->(n) RETURN count(*) AS n",
	)


SELLING_DOCS = ["Quotation", "Sales Order", "Delivery Note", "Sales Invoice", "Payment Entry", "Project"]
BUYING_DOCS = ["Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry"]
ITEM_DOCS = [
	"Quotation",
	"Sales Order",
	"Delivery Note",
	"Sales Invoice",
	"Material Request",
	"Supplier Quotation",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
	"Stock Entry",
	"Stock Reconciliation",
]
EMPLOYEE_DOCS = [
	"Attendance",
	"Leave Application",
	"Leave Allocation",
	"Leave Ledger Entry",
	"Salary Slip",
	"Salary Structure Assignment",
	"Expense Claim",
	"Employee Advance",
	"Timesheet",
	"Employee Checkin",
	"Appraisal",
]

RULES: tuple[Rule, ...] = (
	Rule(
		"drop hub edges",
		f"MATCH ()-[r:LINKS_TO]->(h:Record) WHERE h.doctype IN {to_literal(list(HUB_DOCTYPES))} "
		"DELETE r RETURN count(*) AS n",
	),
	# Parties
	rename("customer docs", "FOR_CUSTOMER", ["customer", "party_name", "party"], "Customer", SELLING_DOCS),
	rename("supplier docs", "FROM_SUPPLIER", ["supplier", "party"], "Supplier", BUYING_DOCS),
	rename("lead docs", "FOR_LEAD", ["party_name"], "Lead", ["Opportunity", "Quotation"]),
	rename("lead converted", "CONVERTED_TO", ["lead_name"], "Lead", ["Customer"], reverse=True),
	rename("customer group", "IN_GROUP", ["customer_group"], "Customer Group", ["Customer"]),
	rename("customer territory", "IN_TERRITORY", ["territory"], "Territory", ["Customer"]),
	rename("supplier group", "IN_GROUP", ["supplier_group"], "Supplier Group", ["Supplier"]),
	# Selling flow: Lead -> Opportunity -> Quotation -> Sales Order -> Delivery Note -> Sales Invoice -> Payment Entry
	rename("opportunity quoted", "QUOTED_AS", ["opportunity"], "Opportunity", ["Quotation"], reverse=True),
	flow("quotation ordered", "ORDERED_AS", "Quotation", "Sales Order Item", "prevdoc_docname", "Sales Order"),
	flow("order delivered", "DELIVERED_BY", "Sales Order", "Delivery Note Item", "against_sales_order", "Delivery Note"),
	flow("order billed", "BILLED_BY", "Sales Order", "Sales Invoice Item", "sales_order", "Sales Invoice"),
	flow("delivery billed", "BILLED_BY", "Delivery Note", "Sales Invoice Item", "delivery_note", "Sales Invoice"),
	# Buying flow: Material Request -> Supplier Quotation -> Purchase Order -> Purchase Receipt -> Purchase Invoice -> Payment Entry
	flow("request quoted", "QUOTED_AS", "Material Request", "Supplier Quotation Item", "material_request", "Supplier Quotation"),
	flow("request ordered", "ORDERED_AS", "Material Request", "Purchase Order Item", "material_request", "Purchase Order"),
	flow("supplier quote ordered", "ORDERED_AS", "Supplier Quotation", "Purchase Order Item", "supplier_quotation", "Purchase Order"),
	flow("order received", "RECEIVED_BY", "Purchase Order", "Purchase Receipt Item", "purchase_order", "Purchase Receipt"),
	flow("purchase order billed", "BILLED_BY", "Purchase Order", "Purchase Invoice Item", "purchase_order", "Purchase Invoice"),
	flow("receipt billed", "BILLED_BY", "Purchase Receipt", "Purchase Invoice Item", "purchase_receipt", "Purchase Invoice"),
	rename("receipt inspected", "INSPECTED_BY", ["reference_name"], "Purchase Receipt", ["Quality Inspection"], reverse=True),
	# Payments: (invoice or order)-[:PAID_BY {amount}]->(Payment Entry)
	Rule(
		"payments",
		"MATCH (pe:`Payment Entry`)-[:HAS_CHILD]->(ref:`Payment Entry Reference`)-[:LINKS_TO {field: 'reference_name'}]->(doc) "
		"WITH pe, doc, sum(ref.allocated_amount) AS amount "
		"CREATE (doc)-[:PAID_BY {amount: amount}]->(pe) RETURN count(*) AS n",
	),
	# Items on documents: (doc)-[:CONTAINS {qty, amount, rate}]->(Item), one edge per doc and item
	Rule(
		"document items",
		"MATCH (d:Record)-[:HAS_CHILD]->(row)-[:LINKS_TO {field: 'item_code'}]->(i:Item) "
		f"WHERE d.doctype IN {to_literal(ITEM_DOCS)} "
		"WITH d, i, sum(coalesce(row.qty, 0)) AS qty, sum(coalesce(row.amount, 0)) AS amount, "
		"avg(coalesce(row.rate, row.basic_rate, row.valuation_rate, 0)) AS rate "
		"CREATE (d)-[:CONTAINS {qty: qty, amount: amount, rate: rate}]->(i) RETURN count(*) AS n",
	),
	Rule(
		"customer bought",
		"MATCH (c:Customer)<-[:FOR_CUSTOMER]-(si:`Sales Invoice`)-[k:CONTAINS]->(i:Item) WHERE si.docstatus = 1 "
		"WITH c, i, sum(k.qty) AS qty, sum(k.amount) AS amount, count(DISTINCT si) AS invoices "
		"CREATE (c)-[:BOUGHT {qty: qty, amount: amount, invoices: invoices}]->(i) RETURN count(*) AS n",
	),
	Rule(
		"supplier supplies",
		"MATCH (s:Supplier)<-[:FROM_SUPPLIER]-(pi:`Purchase Invoice`)-[k:CONTAINS]->(i:Item) WHERE pi.docstatus = 1 "
		"WITH s, i, sum(k.qty) AS qty, sum(k.amount) AS amount, count(DISTINCT pi) AS invoices "
		"CREATE (s)-[:SUPPLIES {qty: qty, amount: amount, invoices: invoices}]->(i) RETURN count(*) AS n",
	),
	# Stock and manufacturing
	rename("item group", "IN_GROUP", ["item_group"], "Item Group", ["Item"]),
	rename("item group tree", "CHILD_OF", ["parent_item_group"], "Item Group", ["Item Group"]),
	rename("warehouse tree", "CHILD_OF", ["parent_warehouse"], "Warehouse", ["Warehouse"]),
	Rule(
		"stock levels",
		"MATCH (b:Bin)-[:LINKS_TO {field: 'item_code'}]->(i:Item), (b)-[:LINKS_TO {field: 'warehouse'}]->(w:Warehouse) "
		"CREATE (i)-[:STOCKED_IN {actual_qty: b.actual_qty, projected_qty: b.projected_qty, "
		"reserved_qty: b.reserved_qty, ordered_qty: b.ordered_qty}]->(w) RETURN count(*) AS n",
	),
	rename("bom output", "PRODUCES", ["item"], "Item", ["BOM"]),
	Rule(
		"bom inputs",
		"MATCH (bom:BOM)-[:HAS_CHILD]->(row:`BOM Item`)-[:LINKS_TO {field: 'item_code'}]->(i:Item) "
		"WITH bom, i, sum(row.qty) AS qty CREATE (bom)-[:CONSUMES {qty: qty}]->(i) RETURN count(*) AS n",
	),
	# Accounting: (voucher)-[:POSTED_TO {debit, credit}]->(Account) from non-cancelled GL Entries
	Rule(
		"gl postings",
		"MATCH (g:`GL Entry`)-[:LINKS_TO {field: 'voucher_no'}]->(v), (g)-[:LINKS_TO {field: 'account'}]->(a:Account) "
		"WHERE coalesce(g.is_cancelled, 0) = 0 "
		"WITH v, a, sum(coalesce(g.debit, 0)) AS debit, sum(coalesce(g.credit, 0)) AS credit "
		"CREATE (v)-[:POSTED_TO {debit: debit, credit: credit}]->(a) RETURN count(*) AS n",
	),
	rename("account tree", "CHILD_OF", ["parent_account"], "Account", ["Account"]),
	# HR
	rename("employee department", "WORKS_IN", ["department"], "Department", ["Employee"]),
	rename("employee designation", "HAS_DESIGNATION", ["designation"], "Designation", ["Employee"]),
	rename("employee branch", "AT_BRANCH", ["branch"], "Branch", ["Employee"]),
	rename("employee manager", "REPORTS_TO", ["reports_to"], "Employee", ["Employee"]),
	rename("department tree", "CHILD_OF", ["parent_department"], "Department", ["Department"]),
	rename("employee docs", "FOR_EMPLOYEE", ["employee"], "Employee", EMPLOYEE_DOCS),
	rename("leave type", "OF_TYPE", ["leave_type"], "Leave Type"),
	Rule(
		"salary structure",
		"MATCH (ssa:`Salary Structure Assignment`)-[:FOR_EMPLOYEE]->(e:Employee), "
		"(ssa)-[:LINKS_TO {field: 'salary_structure'}]->(st:`Salary Structure`) WHERE ssa.docstatus = 1 "
		"CREATE (e)-[:ON_STRUCTURE {from_date: ssa.from_date, base: ssa.base}]->(st) RETURN count(*) AS n",
	),
	Rule(
		"salary earnings",
		"MATCH (e:Employee)<-[:FOR_EMPLOYEE]-(ss:`Salary Slip`)-[:HAS_CHILD]->(sd:`Salary Detail`)"
		"-[:LINKS_TO {field: 'salary_component'}]->(c:`Salary Component`) "
		"WHERE ss.docstatus = 1 AND sd.parentfield = 'earnings' "
		"WITH e, c, sum(sd.amount) AS total, count(DISTINCT ss) AS slips "
		"CREATE (e)-[:EARNS {total: total, slips: slips}]->(c) RETURN count(*) AS n",
	),
	Rule(
		"salary deductions",
		"MATCH (e:Employee)<-[:FOR_EMPLOYEE]-(ss:`Salary Slip`)-[:HAS_CHILD]->(sd:`Salary Detail`)"
		"-[:LINKS_TO {field: 'salary_component'}]->(c:`Salary Component`) "
		"WHERE ss.docstatus = 1 AND sd.parentfield = 'deductions' "
		"WITH e, c, sum(sd.amount) AS total, count(DISTINCT ss) AS slips "
		"CREATE (e)-[:DEDUCTS {total: total, slips: slips}]->(c) RETURN count(*) AS n",
	),
	Rule(
		"leave taken",
		"MATCH (e:Employee)<-[:FOR_EMPLOYEE]-(la:`Leave Application`)-[:OF_TYPE]->(t:`Leave Type`) "
		"WHERE la.status = 'Approved' "
		"WITH e, t, sum(la.total_leave_days) AS days, count(la) AS applications "
		"CREATE (e)-[:TOOK_LEAVE {days: days, applications: applications}]->(t) RETURN count(*) AS n",
	),
	Rule(
		"attendance summary",
		"MATCH (a:Attendance)-[:FOR_EMPLOYEE]->(e:Employee) WHERE a.docstatus = 1 "
		"WITH e, sum(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present, "
		"sum(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent, "
		"sum(CASE WHEN a.status = 'On Leave' THEN 1 ELSE 0 END) AS on_leave, "
		"sum(CASE WHEN a.status = 'Half Day' THEN 1 ELSE 0 END) AS half_day, "
		"sum(CASE WHEN a.status = 'Work From Home' THEN 1 ELSE 0 END) AS wfh "
		"SET e.days_present = present, e.days_absent = absent, e.days_on_leave = on_leave, "
		"e.days_half_day = half_day, e.days_wfh = wfh RETURN count(*) AS n",
	),
	# Projects and assets
	rename("task project", "PART_OF", ["project"], "Project", ["Task", "Timesheet Detail"]),
	rename("task tree", "CHILD_OF", ["parent_task"], "Task", ["Task"]),
	rename("asset location", "LOCATED_AT", ["location", "target_location"], "Location", ["Asset"]),
	rename("asset item", "OF_ITEM", ["item_code"], "Item", ["Asset"]),
	rename("asset category", "IN_CATEGORY", ["asset_category"], "Asset Category", ["Asset", "Item"]),
	rename("asset records", "FOR_ASSET", ["asset"], "Asset"),
)
