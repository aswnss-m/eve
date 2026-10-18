"""Sales and purchases over time: monthly, quarterly or total series, for trend questions and charts.

Invoices when the user can see them; otherwise orders, with the same note the other sales tools give.
Columns: period, amount, and documents (or group, when grouped by an item, party or document field).
"""

from typing import Literal

from eve_agent.chat.common import Tool, ToolContext, result, visible
from eve_agent.chat.tools.sales import (
	PURCHASE_ORDERS_NOTE,
	SALES_ORDERS_NOTE,
	Period,
	invoice_filter,
	order_filter,
)

Interval = Literal["month", "quarter", "total"]


class TrendArgs(Period):
	interval: Interval = "month"


class SalesTrendArgs(TrendArgs):
	group_by: Literal["item_group", "territory", "customer_group"] | None = None


class PurchaseTrendArgs(TrendArgs):
	group_by: Literal["item_group", "supplier_group"] | None = None


def period_expr(date: str, interval: str) -> str:
	if interval == "month":
		return f"substring({date}, 0, 7)"
	if interval == "quarter":
		return f"substring({date}, 0, 4) + '-Q' + toString((toInteger(substring({date}, 5, 2)) - 1) / 3 + 1)"
	return "'total'"


def trend_cypher(doctype: str, date: str, where: str, interval: str, group_by: str | None) -> str:
	"""`d` is the document; `date` and `where` are written against it."""
	period = period_expr(date, interval)
	if group_by is None:
		return f"""
MATCH (d:`{doctype}`)
WHERE {where}
RETURN {period} AS period, sum(d.base_grand_total) AS amount, count(d) AS documents
ORDER BY period"""
	if group_by == "item_group":
		match, group, amount = f"(d:`{doctype}`)-[k:CONTAINS]->(i:Item)", "i.item_group", "k.amount"
	elif group_by == "supplier_group":
		match = f"(d:`{doctype}`)-[:FROM_SUPPLIER]->(s:Supplier)"
		group, amount = "s.supplier_group", "d.base_grand_total"
	else:  # territory, customer_group: fields on the document itself
		match, group, amount = f"(d:`{doctype}`)", f"d.{group_by}", "d.base_grand_total"
	return f"""
MATCH {match}
WHERE {where}
RETURN {period} AS period, coalesce({group}, 'Unknown') AS group, sum({amount}) AS amount
ORDER BY period, amount DESC"""


def trend_tools(ctx: ToolContext) -> list[Tool]:
	def run(title: str, args: TrendArgs, invoice: str, order: str, note: str) -> dict:
		if visible(ctx, invoice):
			doctype, date, where, fallback = invoice, "d.posting_date", invoice_filter("d"), None
		else:
			doctype, date, where, fallback = order, "d.transaction_date", order_filter("d"), note
		cypher = trend_cypher(doctype, date, where, args.interval, args.group_by)
		params = {"date_from": args.date_from, "date_to": args.date_to}
		return result(ctx, [ctx.data.ro(title, cypher, params)], [], fallback)

	def sales_trend(args: SalesTrendArgs) -> dict:
		return run("Sales over time", args, "Sales Invoice", "Sales Order", SALES_ORDERS_NOTE)

	def purchase_trend(args: PurchaseTrendArgs) -> dict:
		return run("Purchases over time", args, "Purchase Invoice", "Purchase Order", PURCHASE_ORDERS_NOTE)

	return [
		Tool(
			"sales_trend",
			"Sales over time from submitted Sales Invoices (returns excluded), per month, quarter or in "
			"total. Optional period and group_by (item_group, territory, customer_group). Use for trend "
			"questions and breakdowns, then show_chart on its query.",
			SalesTrendArgs,
			sales_trend,
		),
		Tool(
			"purchase_trend",
			"Purchases over time from submitted Purchase Invoices (returns excluded), per month, quarter "
			"or in total. Optional period and group_by (item_group, supplier_group). Then show_chart on "
			"its query.",
			PurchaseTrendArgs,
			purchase_trend,
		),
	]
