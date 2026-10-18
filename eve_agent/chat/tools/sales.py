"""Sales and buying analytics over the business layer:
(Sales Invoice)-[:FOR_CUSTOMER]->(Customer), (Sales Invoice)-[:CONTAINS {qty, amount}]->(Item)
(Customer)-[:BOUGHT {qty, amount, invoices}]->(Item), (Supplier)-[:SUPPLIES]->(Item)
"""

from typing import Literal

from pydantic import BaseModel, Field

from eve_agent.chat.common import Tool, ToolContext, iso_days_ago, refs, result, visible

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

SALES_ORDERS_NOTE = (
	"Based on submitted sales orders: invoices need access to Sales Invoice, which you don't have."
)
PURCHASE_ORDERS_NOTE = (
	"Based on submitted purchase orders: purchase invoices need access to Purchase Invoice, which you don't have."
)
OUTSTANDING_NOTE = (
	"Outstanding amounts need access to Sales Invoice, which you don't have; ranked by order value instead."
)
NO_REVENUE_NOTE = (
	"Revenue at risk isn't available: it needs access to Sales Invoice or Sales Order, which you don't have."
)
NO_SALES_NOTE = (
	"Sales figures aren't available: they need access to Sales Invoice or Sales Order, which you don't have."
)


def invoice_filter(v: str) -> str:
	# Submitted, non-return invoices in the period. Dates are stored as ISO strings, so string comparison works.
	return (
		f"{v}.docstatus = 1 AND coalesce({v}.is_return, 0) = 0 "
		f"AND ($date_from IS NULL OR {v}.posting_date >= $date_from) AND ($date_to IS NULL OR {v}.posting_date <= $date_to)"
	)


def order_filter(v: str) -> str:
	# Submitted orders in the period: the fallback when invoices are out of scope.
	return (
		f"{v}.docstatus = 1 AND ($date_from IS NULL OR {v}.transaction_date >= $date_from) "
		f"AND ($date_to IS NULL OR {v}.transaction_date <= $date_to)"
	)


def top_selling_items_cypher(order_by: str) -> str:
	si_filter = invoice_filter("si")
	return f"""
MATCH (si:`Sales Invoice`)-[k:CONTAINS]->(i:Item)
WHERE {si_filter} AND ($item_group IS NULL OR i.item_group = $item_group)
OPTIONAL MATCH (si)-[:FOR_CUSTOMER]->(c:Customer)
WITH i, sum(k.qty) AS qty, sum(k.amount) AS amount, count(DISTINCT si) AS invoices, count(DISTINCT c) AS customers
RETURN i.name AS item, i.item_name AS item_name, i.item_group AS item_group, qty, amount, invoices, customers
ORDER BY {order_by} DESC LIMIT $limit"""


def top_customers_cypher(order_by: str) -> str:
	return f"""
MATCH (c:Customer)<-[:FOR_CUSTOMER]-(si:`Sales Invoice`)
WHERE si.docstatus = 1
  AND ($date_from IS NULL OR si.posting_date >= $date_from) AND ($date_to IS NULL OR si.posting_date <= $date_to)
  AND ($territory IS NULL OR c.territory = $territory) AND ($customer_group IS NULL OR c.customer_group = $customer_group)
WITH c, sum(si.base_grand_total) AS revenue, count(si) AS invoices,
     sum(coalesce(si.outstanding_amount, 0)) AS outstanding, max(si.posting_date) AS last_invoice
OPTIONAL MATCH (c)-[:BOUGHT]->(i:Item)
WITH c, revenue, invoices, outstanding, last_invoice, count(i) AS distinct_items
RETURN c.name AS customer, c.customer_group AS customer_group, c.territory AS territory,
       revenue, invoices, outstanding, last_invoice, distinct_items
ORDER BY {order_by} DESC LIMIT $limit"""


def top_suppliers_cypher(order_by: str) -> str:
	pi_filter = invoice_filter("pi")
	return f"""
MATCH (s:Supplier)<-[:FROM_SUPPLIER]-(pi:`Purchase Invoice`)
WHERE {pi_filter}
WITH s, sum(pi.base_grand_total) AS spend, count(pi) AS invoices, max(pi.posting_date) AS last_invoice
OPTIONAL MATCH (s)-[:SUPPLIES]->(i:Item)
WITH s, spend, invoices, last_invoice, count(i) AS items_supplied
RETURN s.name AS supplier, s.supplier_group AS supplier_group, spend, invoices, last_invoice, items_supplied
ORDER BY {order_by} DESC LIMIT $limit"""


def top_selling_items_orders_cypher(order_by: str) -> str:
	return f"""
MATCH (so:`Sales Order`)-[k:CONTAINS]->(i:Item)
WHERE {order_filter("so")} AND ($item_group IS NULL OR i.item_group = $item_group)
OPTIONAL MATCH (so)-[:FOR_CUSTOMER]->(c:Customer)
WITH i, sum(k.qty) AS qty, sum(k.amount) AS amount, count(DISTINCT so) AS orders, count(DISTINCT c) AS customers
RETURN i.name AS item, i.item_name AS item_name, i.item_group AS item_group, qty, amount, orders, customers
ORDER BY {order_by} DESC LIMIT $limit"""


def top_customers_orders_cypher(order_by: str) -> str:
	return f"""
MATCH (c:Customer)<-[:FOR_CUSTOMER]-(so:`Sales Order`)
WHERE {order_filter("so")}
  AND ($territory IS NULL OR c.territory = $territory) AND ($customer_group IS NULL OR c.customer_group = $customer_group)
WITH c, sum(so.base_grand_total) AS revenue, count(so) AS orders, max(so.transaction_date) AS last_invoice
OPTIONAL MATCH (c)<-[:FOR_CUSTOMER]-(so2:`Sales Order`)-[:CONTAINS]->(i:Item) WHERE so2.docstatus = 1
WITH c, revenue, orders, last_invoice, count(DISTINCT i) AS distinct_items
RETURN c.name AS customer, c.customer_group AS customer_group, c.territory AS territory,
       revenue, orders, null AS outstanding, last_invoice, distinct_items
ORDER BY {order_by} DESC LIMIT $limit"""


def top_suppliers_orders_cypher(order_by: str) -> str:
	return f"""
MATCH (s:Supplier)<-[:FROM_SUPPLIER]-(po:`Purchase Order`)
WHERE {order_filter("po")}
WITH s, sum(po.base_grand_total) AS spend, count(po) AS orders, max(po.transaction_date) AS last_invoice
OPTIONAL MATCH (s)<-[:FROM_SUPPLIER]-(po2:`Purchase Order`)-[:CONTAINS]->(i:Item) WHERE po2.docstatus = 1
WITH s, spend, orders, last_invoice, count(DISTINCT i) AS items_supplied
RETURN s.name AS supplier, s.supplier_group AS supplier_group, spend, orders, last_invoice, items_supplied
ORDER BY {order_by} DESC LIMIT $limit"""


ALSO_BOUGHT_ORDERS = """
MATCH (i:Item {name: $item})<-[:CONTAINS]-(so:`Sales Order`)-[:FOR_CUSTOMER]->(c:Customer)
WHERE so.docstatus = 1
WITH DISTINCT i, c
MATCH (c)<-[:FOR_CUSTOMER]-(so2:`Sales Order`)-[b:CONTAINS]->(j:Item)
WHERE so2.docstatus = 1 AND j <> i
RETURN j.name AS item, j.item_name AS item_name, count(DISTINCT c) AS shared_customers,
       sum(b.amount) AS amount, collect(DISTINCT c.name)[0..5] AS example_customers
ORDER BY shared_customers DESC, amount DESC LIMIT $limit"""

DORMANT_CUSTOMERS_ORDERS = """
MATCH (c:Customer)<-[:FOR_CUSTOMER]-(so:`Sales Order`)
WHERE so.docstatus = 1
WITH c, sum(so.base_grand_total) AS lifetime_revenue, count(so) AS orders, max(so.transaction_date) AS last_invoice
WHERE last_invoice < $cutoff AND lifetime_revenue >= $min_revenue
RETURN c.name AS customer, c.territory AS territory, lifetime_revenue, orders, last_invoice
ORDER BY lifetime_revenue DESC LIMIT $limit"""


def single_source_items_cypher(purchase_invoices: bool, revenue: str) -> str:
	"""Items with one supplier. Suppliers come from invoices or purchase orders; revenue from the
	invoice-derived BOUGHT edges ("invoices"), sales orders ("orders") or nothing ("none")."""
	if purchase_invoices and revenue == "invoices":
		return SINGLE_SOURCE_ITEMS
	if purchase_invoices:
		suppliers = "MATCH (s:Supplier)-[:SUPPLIES]->(i:Item)\nWITH i, collect(s.name) AS suppliers"
	else:
		suppliers = (
			"MATCH (s:Supplier)<-[:FROM_SUPPLIER]-(po:`Purchase Order`)-[:CONTAINS]->(i:Item)\n"
			"WHERE po.docstatus = 1\nWITH DISTINCT s, i\nWITH i, collect(s.name) AS suppliers"
		)
	head = f"{suppliers}\nWHERE size(suppliers) = 1 AND ($item IS NULL OR i.name = $item)\n"
	columns = "RETURN i.name AS item, i.item_name AS item_name, suppliers[0] AS only_supplier,\n"
	if revenue == "invoices":
		tail = (
			"OPTIONAL MATCH (c:Customer)-[b:BOUGHT]->(i)\n"
			+ columns
			+ "       sum(coalesce(b.amount, 0)) AS revenue_at_risk, count(c) AS customers\n"
		)
	elif revenue == "orders":
		tail = (
			"OPTIONAL MATCH (c:Customer)<-[:FOR_CUSTOMER]-(so:`Sales Order`)-[k:CONTAINS]->(i) "
			"WHERE so.docstatus = 1\n"
			+ columns
			+ "       sum(coalesce(k.amount, 0)) AS revenue_at_risk, count(DISTINCT c) AS customers\n"
		)
	else:
		tail = columns + "       null AS revenue_at_risk, null AS customers\n"
	return "\n" + head + tail + "ORDER BY revenue_at_risk DESC LIMIT $limit"


ALSO_BOUGHT = """
MATCH (i:Item {name: $item})<-[:BOUGHT]-(c:Customer)-[b:BOUGHT]->(j:Item)
WHERE j <> i
RETURN j.name AS item, j.item_name AS item_name, count(DISTINCT c) AS shared_customers,
       sum(b.amount) AS amount, collect(DISTINCT c.name)[0..5] AS example_customers
ORDER BY shared_customers DESC, amount DESC LIMIT $limit"""

DORMANT_CUSTOMERS = """
MATCH (c:Customer)<-[:FOR_CUSTOMER]-(si:`Sales Invoice`)
WHERE si.docstatus = 1
WITH c, sum(si.base_grand_total) AS lifetime_revenue, count(si) AS invoices, max(si.posting_date) AS last_invoice
WHERE last_invoice < $cutoff AND lifetime_revenue >= $min_revenue
RETURN c.name AS customer, c.territory AS territory, lifetime_revenue, invoices, last_invoice
ORDER BY lifetime_revenue DESC LIMIT $limit"""

SINGLE_SOURCE_ITEMS = """
MATCH (s:Supplier)-[:SUPPLIES]->(i:Item)
WITH i, collect(s.name) AS suppliers
WHERE size(suppliers) = 1 AND ($item IS NULL OR i.name = $item)
OPTIONAL MATCH (c:Customer)-[b:BOUGHT]->(i)
RETURN i.name AS item, i.item_name AS item_name, suppliers[0] AS only_supplier,
       sum(coalesce(b.amount, 0)) AS revenue_at_risk, count(c) AS customers
ORDER BY revenue_at_risk DESC LIMIT $limit"""


class Period(BaseModel):
	date_from: str | None = Field(None, pattern=DATE_PATTERN, description="Inclusive start date, YYYY-MM-DD")
	date_to: str | None = Field(None, pattern=DATE_PATTERN, description="Inclusive end date, YYYY-MM-DD")


class TopSellingItemsArgs(Period):
	by: Literal["amount", "qty"] = "amount"
	item_group: str | None = None
	limit: int = Field(10, ge=1, le=50)


class TopCustomersArgs(Period):
	by: Literal["revenue", "invoices", "outstanding"] = "revenue"
	territory: str | None = None
	customer_group: str | None = None
	limit: int = Field(10, ge=1, le=50)


class TopSuppliersArgs(Period):
	by: Literal["spend", "invoices"] = "spend"
	limit: int = Field(10, ge=1, le=50)


class AlsoBoughtArgs(BaseModel):
	item: str = Field(description="Item code, e.g. SDR-0060")
	limit: int = Field(10, ge=1, le=50)


class DormantCustomersArgs(BaseModel):
	days: int = Field(90, ge=1, le=3650)
	min_revenue: float = Field(0, ge=0)
	limit: int = Field(10, ge=1, le=50)


class SupplierRiskArgs(BaseModel):
	item: str | None = Field(None, description="Item code to check")
	limit: int = Field(10, ge=1, le=50)


def sales_tools(ctx: ToolContext) -> list[Tool]:
	def run_top_selling_items(args: TopSellingItemsArgs) -> dict:
		orders = not visible(ctx, "Sales Invoice")
		run = ctx.data.ro(
			"Top selling items",
			top_selling_items_orders_cypher(args.by) if orders else top_selling_items_cypher(args.by),
			{
				"date_from": args.date_from,
				"date_to": args.date_to,
				"item_group": args.item_group,
				"limit": args.limit,
			},
		)
		note = SALES_ORDERS_NOTE if orders else None
		return result(ctx, [run], refs(run.rows, "Item", "item", "item_name"), note)

	def run_top_customers(args: TopCustomersArgs) -> dict:
		if not visible(ctx, "Sales Invoice"):
			by = {"invoices": "orders", "outstanding": "revenue"}.get(args.by, args.by)
			cypher = top_customers_orders_cypher(by)
			note = OUTSTANDING_NOTE if args.by == "outstanding" else SALES_ORDERS_NOTE
		else:
			cypher, note = top_customers_cypher(args.by), None
		run = ctx.data.ro(
			"Top customers",
			cypher,
			{
				"date_from": args.date_from,
				"date_to": args.date_to,
				"territory": args.territory,
				"customer_group": args.customer_group,
				"limit": args.limit,
			},
		)
		return result(ctx, [run], refs(run.rows, "Customer", "customer"), note)

	def run_top_suppliers(args: TopSuppliersArgs) -> dict:
		orders = not visible(ctx, "Purchase Invoice")
		if orders:
			cypher = top_suppliers_orders_cypher("orders" if args.by == "invoices" else args.by)
		else:
			cypher = top_suppliers_cypher(args.by)
		run = ctx.data.ro(
			"Top suppliers", cypher, {"date_from": args.date_from, "date_to": args.date_to, "limit": args.limit}
		)
		note = PURCHASE_ORDERS_NOTE if orders else None
		return result(ctx, [run], refs(run.rows, "Supplier", "supplier"), note)

	def also_bought(args: AlsoBoughtArgs) -> dict:
		orders = not visible(ctx, "Sales Invoice")
		run = ctx.data.ro(
			"Customers who bought this also bought",
			ALSO_BOUGHT_ORDERS if orders else ALSO_BOUGHT,
			{"item": args.item, "limit": args.limit},
		)
		return result(
			ctx,
			[run],
			[{"doctype": "Item", "name": args.item}, *refs(run.rows, "Item", "item", "item_name")],
			SALES_ORDERS_NOTE if orders else None,
		)

	def dormant_customers(args: DormantCustomersArgs) -> dict:
		orders = not visible(ctx, "Sales Invoice")
		run = ctx.data.ro(
			"Dormant customers",
			DORMANT_CUSTOMERS_ORDERS if orders else DORMANT_CUSTOMERS,
			{"cutoff": iso_days_ago(args.days), "min_revenue": args.min_revenue, "limit": args.limit},
		)
		return result(ctx, [run], refs(run.rows, "Customer", "customer"), SALES_ORDERS_NOTE if orders else None)

	def supplier_risk(args: SupplierRiskArgs) -> dict:
		purchase_invoices = visible(ctx, "Purchase Invoice")
		if visible(ctx, "Sales Invoice"):
			revenue = "invoices"
		else:
			revenue = "orders" if visible(ctx, "Sales Order") else "none"
		notes = []
		if not purchase_invoices:
			notes.append(PURCHASE_ORDERS_NOTE)
		if revenue == "orders":
			notes.append(SALES_ORDERS_NOTE)
		elif revenue == "none":
			notes.append(NO_REVENUE_NOTE)
		run = ctx.data.ro(
			"Single-source items",
			single_source_items_cypher(purchase_invoices, revenue),
			{"item": args.item, "limit": args.limit},
		)
		return result(
			ctx,
			[run],
			[*refs(run.rows, "Item", "item", "item_name"), *refs(run.rows, "Supplier", "only_supplier")],
			" ".join(notes) or None,
		)

	return [
		Tool(
			"top_selling_items",
			"Best-selling items from submitted Sales Invoices (returns excluded), ranked by revenue or quantity. Optional period and item group.",
			TopSellingItemsArgs,
			run_top_selling_items,
		),
		Tool(
			"top_customers",
			"Best customers from submitted Sales Invoices, ranked by revenue (net of returns), invoice count or outstanding amount. Optional period, territory, customer group.",
			TopCustomersArgs,
			run_top_customers,
		),
		Tool(
			"top_suppliers",
			"Biggest suppliers from submitted Purchase Invoices, ranked by spend or invoice count.",
			TopSuppliersArgs,
			run_top_suppliers,
		),
		Tool(
			"also_bought",
			"Cross-sell: items most often bought by the customers who bought the given item (Customer-BOUGHT->Item<-BOUGHT-Customer-BOUGHT->Item). Item is the item code.",
			AlsoBoughtArgs,
			also_bought,
		),
		Tool(
			"dormant_customers",
			"Customers who used to buy but have no invoice in the last N days, ranked by lifetime revenue. Use for churn risk / who to call.",
			DormantCustomersArgs,
			dormant_customers,
		),
		Tool(
			"supplier_risk",
			"Items that only one supplier supplies, ranked by the customer revenue that depends on them. Optionally check one item.",
			SupplierRiskArgs,
			supplier_risk,
		),
	]
