"""Entity lookup and 360° views: find a record by fuzzy name, profile a customer or
item across every relationship, and explain how two records connect."""

import re

from pydantic import BaseModel, Field

from eve_agent.chat.common import Tool, ToolContext, label, refs, result, visible
from eve_agent.chat.tools.sales import NO_SALES_NOTE, SALES_ORDERS_NOTE
from eve_agent.falkor.cypher.data_queries import FULLTEXT_LABELS

FIND_FULLTEXT = """
CALL db.idx.fulltext.queryNodes($label, $query) YIELD node, score
RETURN node.doctype AS doctype, node.name AS name, node.title AS title, score
ORDER BY score DESC LIMIT 5"""


def find_contains(lbl: str) -> str:
	return f"""
MATCH (n:{lbl})
WHERE toLower(n.name) CONTAINS $text OR toLower(coalesce(n.title, '')) CONTAINS $text
RETURN n.doctype AS doctype, n.name AS name, n.title AS title
LIMIT 10"""


CUSTOMER_SUMMARY = """
MATCH (c:Customer {name: $name})
OPTIONAL MATCH (c)<-[:FOR_CUSTOMER]-(si:`Sales Invoice`) WHERE si.docstatus = 1
WITH c, sum(si.base_grand_total) AS revenue, count(si) AS invoices,
     sum(coalesce(si.outstanding_amount, 0)) AS outstanding, min(si.posting_date) AS first_invoice, max(si.posting_date) AS last_invoice
OPTIONAL MATCH (c)-[:IN_TERRITORY]->(t:Territory)
OPTIONAL MATCH (c)-[:IN_GROUP]->(g:`Customer Group`)
OPTIONAL MATCH (l:Lead)-[:CONVERTED_TO]->(c)
RETURN c.name AS customer, c.customer_type AS type, t.name AS territory, g.name AS customer_group,
       l.name AS from_lead, revenue, invoices, outstanding, first_invoice, last_invoice"""

CUSTOMER_ITEMS = """
MATCH (c:Customer {name: $name})-[b:BOUGHT]->(i:Item)
RETURN i.name AS item, i.item_name AS item_name, b.qty AS qty, b.amount AS amount, b.invoices AS invoices
ORDER BY amount DESC LIMIT 10"""

CUSTOMER_SUMMARY_ORDERS = """
MATCH (c:Customer {name: $name})
OPTIONAL MATCH (c)<-[:FOR_CUSTOMER]-(so:`Sales Order`) WHERE so.docstatus = 1
WITH c, sum(so.base_grand_total) AS revenue, count(so) AS orders,
     min(so.transaction_date) AS first_invoice, max(so.transaction_date) AS last_invoice
OPTIONAL MATCH (c)-[:IN_TERRITORY]->(t:Territory)
OPTIONAL MATCH (c)-[:IN_GROUP]->(g:`Customer Group`)
OPTIONAL MATCH (l:Lead)-[:CONVERTED_TO]->(c)
RETURN c.name AS customer, c.customer_type AS type, t.name AS territory, g.name AS customer_group,
       l.name AS from_lead, revenue, orders, null AS outstanding, first_invoice, last_invoice"""

CUSTOMER_SUMMARY_NO_SALES = """
MATCH (c:Customer {name: $name})
OPTIONAL MATCH (c)-[:IN_TERRITORY]->(t:Territory)
OPTIONAL MATCH (c)-[:IN_GROUP]->(g:`Customer Group`)
OPTIONAL MATCH (l:Lead)-[:CONVERTED_TO]->(c)
RETURN c.name AS customer, c.customer_type AS type, t.name AS territory, g.name AS customer_group,
       l.name AS from_lead, null AS revenue, null AS orders, null AS outstanding,
       null AS first_invoice, null AS last_invoice"""

CUSTOMER_ITEMS_ORDERS = """
MATCH (c:Customer {name: $name})<-[:FOR_CUSTOMER]-(so:`Sales Order`)-[k:CONTAINS]->(i:Item)
WHERE so.docstatus = 1
RETURN i.name AS item, i.item_name AS item_name, sum(k.qty) AS qty, sum(k.amount) AS amount, count(DISTINCT so) AS orders
ORDER BY amount DESC LIMIT 10"""

CUSTOMER_DOCS_ORDERS = """
MATCH (c:Customer {name: $name})<-[:FOR_CUSTOMER]-(d:Record)
WHERE d.doctype IN ['Quotation', 'Sales Order']
RETURN d.doctype AS doctype, d.name AS name, coalesce(d.posting_date, d.transaction_date) AS date,
       d.status AS status, d.base_grand_total AS amount, null AS paid
ORDER BY date DESC LIMIT 10"""

ITEM_BUYERS_ORDERS = """
MATCH (i:Item {name: $name})<-[k:CONTAINS]-(so:`Sales Order`)-[:FOR_CUSTOMER]->(c:Customer)
WHERE so.docstatus = 1
RETURN c.name AS customer, sum(k.qty) AS qty, sum(k.amount) AS amount, count(DISTINCT so) AS orders
ORDER BY amount DESC LIMIT 10"""

ITEM_MONTHLY_SALES_ORDERS = """
MATCH (so:`Sales Order`)-[k:CONTAINS]->(i:Item {name: $name})
WHERE so.docstatus = 1
RETURN substring(so.transaction_date, 0, 7) AS month, sum(k.qty) AS qty, sum(k.amount) AS amount
ORDER BY month DESC LIMIT 12"""

CUSTOMER_DOCS = """
MATCH (c:Customer {name: $name})<-[:FOR_CUSTOMER]-(d:Record)
WHERE d.doctype IN ['Quotation', 'Sales Order', 'Sales Invoice']
OPTIONAL MATCH (d)-[p:PAID_BY]->(pe:`Payment Entry`)
RETURN d.doctype AS doctype, d.name AS name, coalesce(d.posting_date, d.transaction_date) AS date,
       d.status AS status, d.base_grand_total AS amount, sum(coalesce(p.amount, 0)) AS paid
ORDER BY date DESC LIMIT 10"""

ITEM_BUYERS = """
MATCH (i:Item {name: $name})<-[b:BOUGHT]-(c:Customer)
RETURN c.name AS customer, b.qty AS qty, b.amount AS amount, b.invoices AS invoices
ORDER BY amount DESC LIMIT 10"""

ITEM_SUPPLY = """
MATCH (i:Item {name: $name})
OPTIONAL MATCH (s:Supplier)-[sp:SUPPLIES]->(i)
WITH i, collect({supplier: s.name, qty: sp.qty, amount: sp.amount}) AS suppliers
OPTIONAL MATCH (i)-[st:STOCKED_IN]->(w:Warehouse)
WITH i, suppliers, collect({warehouse: w.name, actual_qty: st.actual_qty, projected_qty: st.projected_qty}) AS stock
OPTIONAL MATCH (bom:BOM)-[:PRODUCES]->(i)
OPTIONAL MATCH (used_in:BOM)-[:CONSUMES]->(i)
RETURN i.name AS item, i.item_name AS item_name, i.item_group AS item_group, suppliers, stock,
       collect(DISTINCT bom.name) AS made_by_boms, collect(DISTINCT used_in.name) AS used_in_boms"""

ITEM_MONTHLY_SALES = """
MATCH (si:`Sales Invoice`)-[k:CONTAINS]->(i:Item {name: $name})
WHERE si.docstatus = 1 AND coalesce(si.is_return, 0) = 0
RETURN substring(si.posting_date, 0, 7) AS month, sum(k.qty) AS qty, sum(k.amount) AS amount
ORDER BY month DESC LIMIT 12"""


def shortest_path(a: str, b: str) -> str:
	return f"""
MATCH (a:{a} {{name: $a}}), (b:{b} {{name: $b}})
WITH shortestPath((a)-[*..6]-(b)) AS p
WHERE p IS NOT NULL
RETURN [n IN nodes(p) | {{doctype: n.doctype, name: n.name, title: n.title}}] AS nodes,
       [r IN relationships(p) | type(r)] AS relationships"""


def fulltext_query(text: str) -> str:
	"""RediSearch syntax: prefix-match every word. Strip operators so user text cannot change the query."""
	words = re.sub(r"[\W_]+", " ", text.lower()).split()
	return " ".join(f"{word}*" for word in words)


class FindEntityArgs(BaseModel):
	text: str = Field(min_length=1)
	doctype: str | None = Field(None, description="Restrict to one DocType, e.g. Customer")


class CustomerProfileArgs(BaseModel):
	customer: str = Field(description="Exact Customer name (use find_entity first if unsure)")


class ItemProfileArgs(BaseModel):
	item: str = Field(description="Item code (use find_entity first if unsure)")


class ExplainConnectionArgs(BaseModel):
	from_doctype: str
	from_name: str
	to_doctype: str
	to_name: str


def entity_tools(ctx: ToolContext) -> list[Tool]:
	def find_entity(args: FindEntityArgs) -> dict:
		query = fulltext_query(args.text)
		runs = []
		labels = [args.doctype] if args.doctype else list(FULLTEXT_LABELS)
		for lbl in labels:
			if not query:
				continue
			try:
				run = ctx.data.ro(f"Full-text search in {lbl}", FIND_FULLTEXT, {"label": lbl, "query": query})
				if run.rows:
					runs.append(run)
			except Exception:
				pass  # No full-text index for this label (custom DocType, or a graph built before indexes existed).
		if not runs:
			for lbl in labels:
				try:
					run = ctx.data.ro(
						f"Name contains, {lbl}", find_contains(label(ctx, lbl)), {"text": args.text.lower()}
					)
					if run.rows:
						runs.append(run)
				except Exception:
					pass  # Unknown label: skip.
		sources = [
			{
				"doctype": str(row.get("doctype")),
				"name": str(row.get("name")),
				"title": str(row.get("title") or ""),
			}
			for run in runs
			for row in run.rows
		]
		note = None if sources else f"Nothing matched '{args.text}'."
		return result(ctx, runs, sources, note)

	def customer_profile(args: CustomerProfileArgs) -> dict:
		params = {"name": args.customer}
		invoices = visible(ctx, "Sales Invoice")
		orders = not invoices and visible(ctx, "Sales Order")
		if invoices:
			summary, items, docs = CUSTOMER_SUMMARY, CUSTOMER_ITEMS, CUSTOMER_DOCS
		elif orders:
			summary, items, docs = CUSTOMER_SUMMARY_ORDERS, CUSTOMER_ITEMS_ORDERS, CUSTOMER_DOCS_ORDERS
		else:
			summary, items, docs = CUSTOMER_SUMMARY_NO_SALES, None, CUSTOMER_DOCS_ORDERS
		runs = [ctx.data.ro("Customer summary", summary, params)]
		if items:
			runs.append(ctx.data.ro("Top items bought", items, params))
		runs.append(ctx.data.ro("Recent documents", docs, params))
		if not runs[0].rows:
			raise ValueError(f"No Customer named '{args.customer}'. Try find_entity.")
		sources = [
			{"doctype": "Customer", "name": args.customer},
			*(refs(runs[1].rows, "Item", "item", "item_name") if items else []),
			*({"doctype": str(r.get("doctype")), "name": str(r.get("name"))} for r in runs[-1].rows),
		]
		note = None if invoices else SALES_ORDERS_NOTE if orders else NO_SALES_NOTE
		return result(ctx, runs, sources, note)

	def item_profile(args: ItemProfileArgs) -> dict:
		params = {"name": args.item}
		invoices = visible(ctx, "Sales Invoice")
		orders = not invoices and visible(ctx, "Sales Order")
		runs = [ctx.data.ro("Item, suppliers, stock and BOMs", ITEM_SUPPLY, params)]
		if invoices:
			runs += [
				ctx.data.ro("Top buyers", ITEM_BUYERS, params),
				ctx.data.ro("Monthly sales", ITEM_MONTHLY_SALES, params),
			]
		elif orders:
			runs += [
				ctx.data.ro("Top buyers", ITEM_BUYERS_ORDERS, params),
				ctx.data.ro("Monthly sales", ITEM_MONTHLY_SALES_ORDERS, params),
			]
		if not runs[0].rows:
			raise ValueError(f"No Item with code '{args.item}'. Try find_entity.")
		sources = [{"doctype": "Item", "name": args.item}]
		if len(runs) > 1:
			sources += refs(runs[1].rows, "Customer", "customer")
		note = None if invoices else SALES_ORDERS_NOTE if orders else NO_SALES_NOTE
		return result(ctx, runs, sources, note)

	def explain_connection(args: ExplainConnectionArgs) -> dict:
		cypher = shortest_path(label(ctx, args.from_doctype), label(ctx, args.to_doctype))
		run = ctx.data.ro("Shortest path", cypher, {"a": args.from_name, "b": args.to_name})
		path = run.rows[0].get("nodes") if run.rows else None
		nodes = [n for n in path or [] if n.get("name")]
		note = None if nodes else "No path within 6 hops."
		return result(ctx, [run], nodes, note)

	return [
		Tool(
			"find_entity",
			"Resolve a name the user typed (customer, supplier, item, employee, project, sales person, user, lead, or any DocType) to exact record names. Call this before other tools when the user gives a partial or fuzzy name.",
			FindEntityArgs,
			find_entity,
		),
		Tool(
			"customer_profile",
			"Everything about one customer: revenue, invoices, outstanding, territory, group, originating lead, top items bought, and recent documents with payments.",
			CustomerProfileArgs,
			customer_profile,
		),
		Tool(
			"item_profile",
			"Everything about one item: top buyers, suppliers, stock by warehouse, BOMs that make or use it, and monthly sales for the last 12 months.",
			ItemProfileArgs,
			item_profile,
		),
		Tool(
			"explain_connection",
			"How two records are connected: the shortest path between them in the graph (up to 6 hops), e.g. a customer and a supplier through the items they share.",
			ExplainConnectionArgs,
			explain_connection,
		),
	]
