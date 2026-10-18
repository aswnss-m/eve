"""Entity lookup and 360° views: find a record by fuzzy name, profile a customer or
item across every relationship, and explain how two records connect."""

import re
import time

from pydantic import BaseModel, Field

from eve_agent.chat.common import QueryRun, Tool, ToolContext, label, refs, result, visible
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


# explain_connection searches from both records at once, one hop per query. FalkorDB 4.22 rejects an
# undirected shortestPath(); algo.SPpaths and variable-length matches can run far past the query
# timeout (the whole server stalls while a sync's write waits behind them), and an untyped [*2]
# between two bound nodes crashes it. A one-hop expansion streams rows, so its timeout and limit hold.
MAX_HOPS = 6
PATH_TIMEOUT_MS = 2000
PATH_MAX_EDGES = 20000


def path_endpoints(a: str, b: str) -> str:
	return f"MATCH (a:{a} {{name: $a}}), (b:{b} {{name: $b}}) RETURN id(a) AS a, id(b) AS b"


NEIGHBOURS = """
UNWIND $ids AS i
MATCH (n) WHERE id(n) = i
MATCH (n)-[r]-(m)
RETURN id(n) AS node, id(m) AS next, type(r) AS rel
LIMIT $limit"""

PATH_RECORDS = """
UNWIND $ids AS i
MATCH (n) WHERE id(n) = i
RETURN id(n) AS id, n.doctype AS doctype, n.name AS name, n.title AS title"""


def _walk(seen: dict, node: int) -> tuple[list[int], list[str]]:
	"""Node ids and relationship types from `node` back to the record this side started from."""
	nodes, rels = [node], []
	while seen[node][0] is not None:
		node, rel, _ = seen[node]
		nodes.append(node)
		rels.append(rel)
	return nodes, rels


def find_path(ctx: ToolContext, start: int, goal: int) -> tuple[list[int], list[str], str | None]:
	"""Bidirectional breadth-first search: expand whichever side has fewer records, one hop at a time.
	Returns the node ids and relationship types from start to goal, or a note saying why there are none."""
	seen = ({start: (None, None, 0)}, {goal: (None, None, 0)})  # node -> (previous node, relationship, hops)
	frontier = [[start], [goal]]
	for step in range(1, MAX_HOPS + 1):
		side = 0 if len(frontier[0]) <= len(frontier[1]) else 1
		params = {"ids": frontier[side], "limit": PATH_MAX_EDGES + 1}
		try:
			run = ctx.data.ro(f"Neighbours, step {step}", NEIGHBOURS, params, timeout_ms=PATH_TIMEOUT_MS)
		except Exception as exc:
			if "timed out" not in str(exc).lower():
				raise
			return [], [], f"No path within {step - 1} hops; the search took too long."
		if len(run.rows) > PATH_MAX_EDGES:
			return [], [], f"No path within {step - 1} hops; too many connections to search through."
		meet, best, added = None, None, []
		for row in run.rows:
			node, nxt = row["node"], row["next"]
			if nxt in seen[side]:
				continue
			hops = seen[side][node][2] + 1
			seen[side][nxt] = (node, row["rel"], hops)
			added.append(nxt)
			if nxt in seen[1 - side] and (best is None or hops + seen[1 - side][nxt][2] < best):
				meet, best = nxt, hops + seen[1 - side][nxt][2]
		if meet is not None:
			head, head_rels = _walk(seen[0], meet)
			tail, tail_rels = _walk(seen[1], meet)
			return head[::-1] + tail[1:], head_rels[::-1] + tail_rels, None
		if not added:
			break  # one side has nowhere left to go
		frontier[side] = added
	return [], [], f"No path within {MAX_HOPS} hops."


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
			# A label without a full-text index returns no rows, not an error. Errors (a timeout,
			# a lost connection) go to the model instead of reading as "Nothing matched".
			run = ctx.data.ro(f"Full-text search in {lbl}", FIND_FULLTEXT, {"label": lbl, "query": query})
			if run.rows:
				runs.append(run)
		if not runs:
			for lbl in labels:
				try:
					cypher = find_contains(label(ctx, lbl))
				except ValueError:
					continue  # No records with this label.
				run = ctx.data.ro(f"Name contains, {lbl}", cypher, {"text": args.text.lower()})
				if run.rows:
					runs.append(run)
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
		started = time.monotonic()
		cypher = path_endpoints(label(ctx, args.from_doctype), label(ctx, args.to_doctype))
		params = {"a": args.from_name, "b": args.to_name}
		ends = ctx.data.ro("Endpoints", cypher, params, timeout_ms=PATH_TIMEOUT_MS)
		if not ends.rows or ends.rows[0].get("a") is None or ends.rows[0].get("b") is None:
			raise ValueError(
				f"No {args.from_doctype} '{args.from_name}' or no {args.to_doctype} '{args.to_name}'. Try find_entity."
			)
		ids, rels, note = find_path(ctx, ends.rows[0]["a"], ends.rows[0]["b"])
		nodes = []
		if ids:
			found = ctx.data.ro("Records on the path", PATH_RECORDS, {"ids": ids}, timeout_ms=PATH_TIMEOUT_MS)
			by_id = {row["id"]: row for row in found.rows}
			nodes = [
				{"doctype": by_id[i]["doctype"], "name": by_id[i]["name"], "title": by_id[i].get("title")}
				for i in ids
				if by_id.get(i, {}).get("name")
			]
		# The search runs several queries; the step shown is its answer, with the query it repeats.
		run = QueryRun(
			title="Shortest path (searched from both records, one hop at a time)",
			cypher=NEIGHBOURS.strip(),
			params={
				"from": f"{args.from_doctype} {args.from_name}",
				"to": f"{args.to_doctype} {args.to_name}",
			},
			rows=[{"nodes": nodes, "relationships": rels}] if nodes else [],
			ms=round((time.monotonic() - started) * 1000),
		)
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
