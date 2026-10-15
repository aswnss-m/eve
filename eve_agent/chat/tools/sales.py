"""Sales and buying analytics over the business layer:
(Sales Invoice)-[:FOR_CUSTOMER]->(Customer), (Sales Invoice)-[:CONTAINS {qty, amount}]->(Item)
(Customer)-[:BOUGHT {qty, amount, invoices}]->(Item), (Supplier)-[:SUPPLIES]->(Item)
"""

from typing import Literal

from pydantic import BaseModel, Field

from eve_agent.chat.common import Tool, ToolContext, iso_days_ago, refs, result

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def invoice_filter(v: str) -> str:
	# Submitted, non-return invoices in the period. Dates are stored as ISO strings, so string comparison works.
	return (
		f"{v}.docstatus = 1 AND coalesce({v}.is_return, 0) = 0 "
		f"AND ($date_from IS NULL OR {v}.posting_date >= $date_from) AND ($date_to IS NULL OR {v}.posting_date <= $date_to)"
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
		run = ctx.data.ro(
			"Top selling items",
			top_selling_items_cypher(args.by),
			{
				"date_from": args.date_from,
				"date_to": args.date_to,
				"item_group": args.item_group,
				"limit": args.limit,
			},
		)
		return result(ctx, [run], refs(run.rows, "Item", "item", "item_name"))

	def run_top_customers(args: TopCustomersArgs) -> dict:
		run = ctx.data.ro(
			"Top customers",
			top_customers_cypher(args.by),
			{
				"date_from": args.date_from,
				"date_to": args.date_to,
				"territory": args.territory,
				"customer_group": args.customer_group,
				"limit": args.limit,
			},
		)
		return result(ctx, [run], refs(run.rows, "Customer", "customer"))

	def run_top_suppliers(args: TopSuppliersArgs) -> dict:
		run = ctx.data.ro(
			"Top suppliers",
			top_suppliers_cypher(args.by),
			{"date_from": args.date_from, "date_to": args.date_to, "limit": args.limit},
		)
		return result(ctx, [run], refs(run.rows, "Supplier", "supplier"))

	def also_bought(args: AlsoBoughtArgs) -> dict:
		run = ctx.data.ro(
			"Customers who bought this also bought", ALSO_BOUGHT, {"item": args.item, "limit": args.limit}
		)
		return result(
			ctx, [run], [{"doctype": "Item", "name": args.item}, *refs(run.rows, "Item", "item", "item_name")]
		)

	def dormant_customers(args: DormantCustomersArgs) -> dict:
		run = ctx.data.ro(
			"Dormant customers",
			DORMANT_CUSTOMERS,
			{"cutoff": iso_days_ago(args.days), "min_revenue": args.min_revenue, "limit": args.limit},
		)
		return result(ctx, [run], refs(run.rows, "Customer", "customer"))

	def supplier_risk(args: SupplierRiskArgs) -> dict:
		run = ctx.data.ro(
			"Single-source items", SINGLE_SOURCE_ITEMS, {"item": args.item, "limit": args.limit}
		)
		return result(
			ctx,
			[run],
			[*refs(run.rows, "Item", "item", "item_name"), *refs(run.rows, "Supplier", "only_supplier")],
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
