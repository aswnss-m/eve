# Cypher the Eve agent runs

Generated from the agent source by `npm run docs:queries` (in `agent/`). Do not edit by hand.

All ERP reads use `GRAPH.RO_QUERY` on the active data graph, so FalkorDB refuses any write. Values are passed as query parameters (`$name`). DocType labels can't be parameters, so tools check them against `CALL db.labels()` before splicing them in. The examples below use `Customer`.

`run_cypher` is the fallback for questions no tool covers. It runs model-written Cypher after a local write check (`assertReadOnly`, the same rules as `eve_agent/falkor/query/readonly.py`), adds `LIMIT 200` when there is none, and also goes through `GRAPH.RO_QUERY`.

The graph these queries read is built by the Python sync. Its rules are in `eve_agent/falkor/cypher/business_rules.py`, documented in [graph-guide.md](graph-guide.md).

## Sales and buying (`agent/src/tools/sales.ts`)

### top_selling_items (by amount; `by: qty` orders by qty)
```cypher
MATCH (si:`Sales Invoice`)-[k:CONTAINS]->(i:Item)
WHERE si.docstatus = 1 AND coalesce(si.is_return, 0) = 0 AND ($date_from IS NULL OR si.posting_date >= $date_from) AND ($date_to IS NULL OR si.posting_date <= $date_to) AND ($item_group IS NULL OR i.item_group = $item_group)
OPTIONAL MATCH (si)-[:FOR_CUSTOMER]->(c:Customer)
WITH i, sum(k.qty) AS qty, sum(k.amount) AS amount, count(DISTINCT si) AS invoices, count(DISTINCT c) AS customers
RETURN i.name AS item, i.item_name AS item_name, i.item_group AS item_group, qty, amount, invoices, customers
ORDER BY amount DESC LIMIT $limit
```

### top_customers (by revenue; also invoices, outstanding)
```cypher
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
ORDER BY revenue DESC LIMIT $limit
```

### top_suppliers (by spend; also invoices)
```cypher
MATCH (s:Supplier)<-[:FROM_SUPPLIER]-(pi:`Purchase Invoice`)
WHERE pi.docstatus = 1 AND coalesce(pi.is_return, 0) = 0 AND ($date_from IS NULL OR pi.posting_date >= $date_from) AND ($date_to IS NULL OR pi.posting_date <= $date_to)
WITH s, sum(pi.base_grand_total) AS spend, count(pi) AS invoices, max(pi.posting_date) AS last_invoice
OPTIONAL MATCH (s)-[:SUPPLIES]->(i:Item)
WITH s, spend, invoices, last_invoice, count(i) AS items_supplied
RETURN s.name AS supplier, s.supplier_group AS supplier_group, spend, invoices, last_invoice, items_supplied
ORDER BY spend DESC LIMIT $limit
```

### also_bought: 3-hop cross-sell
```cypher
MATCH (i:Item {name: $item})<-[:BOUGHT]-(c:Customer)-[b:BOUGHT]->(j:Item)
WHERE j <> i
RETURN j.name AS item, j.item_name AS item_name, count(DISTINCT c) AS shared_customers,
       sum(b.amount) AS amount, collect(DISTINCT c.name)[0..5] AS example_customers
ORDER BY shared_customers DESC, amount DESC LIMIT $limit
```

### dormant_customers ($cutoff = today minus N days)
```cypher
MATCH (c:Customer)<-[:FOR_CUSTOMER]-(si:`Sales Invoice`)
WHERE si.docstatus = 1
WITH c, sum(si.base_grand_total) AS lifetime_revenue, count(si) AS invoices, max(si.posting_date) AS last_invoice
WHERE last_invoice < $cutoff AND lifetime_revenue >= $min_revenue
RETURN c.name AS customer, c.territory AS territory, lifetime_revenue, invoices, last_invoice
ORDER BY lifetime_revenue DESC LIMIT $limit
```

### supplier_risk: single-source items
```cypher
MATCH (s:Supplier)-[:SUPPLIES]->(i:Item)
WITH i, collect(s.name) AS suppliers
WHERE size(suppliers) = 1 AND ($item IS NULL OR i.name = $item)
OPTIONAL MATCH (c:Customer)-[b:BOUGHT]->(i)
RETURN i.name AS item, i.item_name AS item_name, suppliers[0] AS only_supplier,
       sum(coalesce(b.amount, 0)) AS revenue_at_risk, count(c) AS customers
ORDER BY revenue_at_risk DESC LIMIT $limit
```

## Entities (`agent/src/tools/entities.ts`)

### find_entity: full-text index
```cypher
CALL db.idx.fulltext.queryNodes($label, $query) YIELD node, score
RETURN node.doctype AS doctype, node.name AS name, node.title AS title, score
ORDER BY score DESC LIMIT 5
```

### find_entity: fallback when no index matches
```cypher
MATCH (n:`Customer`)
WHERE toLower(n.name) CONTAINS $text OR toLower(coalesce(n.title, '')) CONTAINS $text
RETURN n.doctype AS doctype, n.name AS name, n.title AS title
LIMIT 10
```

### customer_profile: summary
```cypher
MATCH (c:Customer {name: $name})
OPTIONAL MATCH (c)<-[:FOR_CUSTOMER]-(si:`Sales Invoice`) WHERE si.docstatus = 1
WITH c, sum(si.base_grand_total) AS revenue, count(si) AS invoices,
     sum(coalesce(si.outstanding_amount, 0)) AS outstanding, min(si.posting_date) AS first_invoice, max(si.posting_date) AS last_invoice
OPTIONAL MATCH (c)-[:IN_TERRITORY]->(t:Territory)
OPTIONAL MATCH (c)-[:IN_GROUP]->(g:`Customer Group`)
OPTIONAL MATCH (l:Lead)-[:CONVERTED_TO]->(c)
RETURN c.name AS customer, c.customer_type AS type, t.name AS territory, g.name AS customer_group,
       l.name AS from_lead, revenue, invoices, outstanding, first_invoice, last_invoice
```

### customer_profile: items bought
```cypher
MATCH (c:Customer {name: $name})-[b:BOUGHT]->(i:Item)
RETURN i.name AS item, i.item_name AS item_name, b.qty AS qty, b.amount AS amount, b.invoices AS invoices
ORDER BY amount DESC LIMIT 10
```

### customer_profile: recent documents and payments
```cypher
MATCH (c:Customer {name: $name})<-[:FOR_CUSTOMER]-(d:Record)
WHERE d.doctype IN ['Quotation', 'Sales Order', 'Sales Invoice']
OPTIONAL MATCH (d)-[p:PAID_BY]->(pe:`Payment Entry`)
RETURN d.doctype AS doctype, d.name AS name, coalesce(d.posting_date, d.transaction_date) AS date,
       d.status AS status, d.base_grand_total AS amount, sum(coalesce(p.amount, 0)) AS paid
ORDER BY date DESC LIMIT 10
```

### item_profile: suppliers, stock, BOMs
```cypher
MATCH (i:Item {name: $name})
OPTIONAL MATCH (s:Supplier)-[sp:SUPPLIES]->(i)
WITH i, collect({supplier: s.name, qty: sp.qty, amount: sp.amount}) AS suppliers
OPTIONAL MATCH (i)-[st:STOCKED_IN]->(w:Warehouse)
WITH i, suppliers, collect({warehouse: w.name, actual_qty: st.actual_qty, projected_qty: st.projected_qty}) AS stock
OPTIONAL MATCH (bom:BOM)-[:PRODUCES]->(i)
OPTIONAL MATCH (used_in:BOM)-[:CONSUMES]->(i)
RETURN i.name AS item, i.item_name AS item_name, i.item_group AS item_group, suppliers, stock,
       collect(DISTINCT bom.name) AS made_by_boms, collect(DISTINCT used_in.name) AS used_in_boms
```

### item_profile: top buyers
```cypher
MATCH (i:Item {name: $name})<-[b:BOUGHT]-(c:Customer)
RETURN c.name AS customer, b.qty AS qty, b.amount AS amount, b.invoices AS invoices
ORDER BY amount DESC LIMIT 10
```

### item_profile: monthly sales
```cypher
MATCH (si:`Sales Invoice`)-[k:CONTAINS]->(i:Item {name: $name})
WHERE si.docstatus = 1 AND coalesce(si.is_return, 0) = 0
RETURN substring(si.posting_date, 0, 7) AS month, sum(k.qty) AS qty, sum(k.amount) AS amount
ORDER BY month DESC LIMIT 12
```

### explain_connection: shortest path (labels are validated against db.labels())
```cypher
MATCH (a:`Customer` {name: $a}), (b:`Supplier` {name: $b})
WITH shortestPath((a)-[*..6]-(b)) AS p
WHERE p IS NOT NULL
RETURN [n IN nodes(p) | {doctype: n.doctype, name: n.name, title: n.title}] AS nodes,
       [r IN relationships(p) | type(r)] AS relationships
```

## Company brain (`agent/src/tools/brain.ts`)

### who_knows: created the record
```cypher
MATCH (e:`Customer` {name: $name})-[:CREATED_BY]->(u:User)
RETURN u.name AS user, u.full_name AS full_name, 'created this record' AS how, 1 AS touches, [] AS examples
```

### who_knows: created related documents
```cypher
MATCH (e:`Customer` {name: $name})-[]-(d:Record)-[:CREATED_BY]->(u:User)
WHERE NOT d.doctype IN ['Comment', 'ToDo']
RETURN u.name AS user, u.full_name AS full_name, 'created ' + d.doctype AS how,
       count(DISTINCT d) AS touches, collect(DISTINCT d.name)[0..3] AS examples
```

### who_knows: commented
```cypher
MATCH (e:`Customer` {name: $name})<-[:ON]-(c:Comment)-[:CREATED_BY]->(u:User)
RETURN u.name AS user, u.full_name AS full_name, 'commented' AS how,
       count(c) AS touches, collect(c.content)[0..2] AS examples
```

### who_knows: assigned tasks
```cypher
MATCH (e:`Customer` {name: $name})<-[:ON]-(t:ToDo)-[:ASSIGNED_TO]->(u:User)
RETURN u.name AS user, u.full_name AS full_name, 'was assigned: ' + coalesce(t.status, 'Open') AS how,
       count(t) AS touches, collect(t.description)[0..2] AS examples
```

### who_knows: sales people
```cypher
MATCH (e:`Customer` {name: $name})-[r:MANAGED_BY|SOLD_TO]-(sp:`Sales Person`)
OPTIONAL MATCH (sp)-[:IS_EMPLOYEE]->(:Employee)-[:IS_USER]->(u:User)
RETURN coalesce(u.name, sp.name) AS user, coalesce(u.full_name, sp.sales_person_name, sp.name) AS full_name,
       CASE type(r) WHEN 'MANAGED_BY' THEN 'is the assigned sales person' ELSE 'sold to them' END AS how,
       coalesce(r.invoices, 1) AS touches, [] AS examples
```

### who_knows: department and designation
```cypher
MATCH (u:User) WHERE u.name IN $users
OPTIONAL MATCH (emp:Employee)-[:IS_USER]->(u)
OPTIONAL MATCH (emp)-[:WORKS_IN]->(d:Department)
OPTIONAL MATCH (emp)-[:HAS_DESIGNATION]->(g:Designation)
RETURN u.name AS user, emp.name AS employee, d.name AS department, g.name AS designation
```

### decision_trail: document flow with creators and notes
```cypher
MATCH (start:`Sales Order` {name: $name})-[:QUOTED_AS|ORDERED_AS|DELIVERED_BY|BILLED_BY|RECEIVED_BY|INSPECTED_BY|PAID_BY|CONVERTED_TO*0..4]-(d:Record)
WITH DISTINCT d LIMIT 40
OPTIONAL MATCH (d)-[:CREATED_BY]->(u:User)
OPTIONAL MATCH (d)<-[:ON]-(n:Record)
OPTIONAL MATCH (d)-[:FOR_CUSTOMER|FROM_SUPPLIER]->(party)
RETURN d.doctype AS doctype, d.name AS name,
       coalesce(d.posting_date, d.transaction_date, substring(d.creation, 0, 10)) AS date,
       d.status AS status, coalesce(d.base_grand_total, d.paid_amount) AS amount, party.name AS party,
       u.full_name AS created_by,
       [x IN collect(DISTINCT n) WHERE x IS NOT NULL | x.doctype + ': ' + coalesce(x.content, x.description, '')][0..5] AS notes
ORDER BY date
```

## Schema and fallback (`agent/src/tools/utility.ts`)

### graph_schema: records per DocType
```cypher
MATCH (n:Record) RETURN n.doctype AS doctype, count(*) AS records ORDER BY records DESC
```

### graph_schema: business relationships
```cypher
MATCH (a)-[r]->(b) WHERE type(r) <> 'LINKS_TO' AND type(r) <> 'HAS_CHILD'
RETURN type(r) AS relationship, a.doctype AS from, b.doctype AS to, count(*) AS edges
ORDER BY edges DESC
```

### describe_doctype (schema graph)
```cypher
MATCH (d:DocType {name: $doctype})
OPTIONAL MATCH (d)-[r:LINKS_TO|HAS_CHILD]->(t:DocType)
RETURN d.name AS doctype, d.module AS module, d.is_submittable AS is_submittable, d.title_field AS title_field,
       d.fields_json AS fields, collect({type: type(r), field: r.fieldname, target: t.name}) AS links
```

## Memory graph (`agent/src/memory.ts`)

### save a turn (after every reply)
```cypher
MERGE (u:User {name: $user}) SET u.full_name = $full_name
MERGE (c:Conversation {id: $chat_id}) ON CREATE SET c.started_at = $now, c.title = $title
SET c.updated_at = $now
MERGE (u)-[:HAD]->(c)
CREATE (c)-[:HAS_MESSAGE]->(:Message {role: 'user', text: $question, at: $now})
CREATE (c)-[:HAS_MESSAGE]->(m:Message {role: 'assistant', text: $answer, at: $now})
WITH u, m
UNWIND $refs AS ref
MERGE (e:EntityRef {key: ref.key}) ON CREATE SET e.doctype = ref.doctype, e.name = ref.name, e.title = ref.title
CREATE (m)-[:MENTIONS]->(e)
MERGE (u)-[a:ASKED_ABOUT]->(e) ON CREATE SET a.count = 0
SET a.count = a.count + 1, a.last_at = $now
```

### memory context (start of every chat)
```cypher
MATCH (u:User {name: $user})
OPTIONAL MATCH (u)-[a:ASKED_ABOUT]->(e:EntityRef)
WITH u, e, a ORDER BY a.count DESC, a.last_at DESC
WITH u, collect(e.doctype + ' ' + e.name + ' (asked ' + toString(a.count) + 'x)')[0..8] AS interests
OPTIONAL MATCH (u)-[:HAD]->(c:Conversation)
WITH u, interests, c ORDER BY c.updated_at DESC
WITH u, interests, collect(c.title + ' [' + substring(c.updated_at, 0, 10) + ']')[0..5] AS recent
OPTIONAL MATCH (u)-[:SAVED]->(i:Insight)
WITH interests, recent, i ORDER BY i.at DESC
RETURN interests, recent, collect(i.text + ' [' + substring(i.at, 0, 10) + ']')[0..5] AS insights
```

### recall: past messages
```cypher
MATCH (u:User {name: $user})-[:HAD]->(c:Conversation)-[:HAS_MESSAGE]->(m:Message)
WHERE $topic = '' OR toLower(m.text) CONTAINS $topic
OPTIONAL MATCH (m)-[:MENTIONS]->(e:EntityRef)
RETURN c.title AS conversation, m.role AS role, m.at AS at, substring(m.text, 0, 400) AS text,
       collect(e.doctype + ' ' + e.name)[0..5] AS mentions
ORDER BY at DESC LIMIT 12
```

### recall: saved insights
```cypher
MATCH (u:User {name: $user})-[:SAVED]->(i:Insight)
WHERE $topic = '' OR toLower(i.text) CONTAINS $topic
OPTIONAL MATCH (i)-[:DERIVED_FROM]->(e:EntityRef)
RETURN i.text AS insight, i.at AS at, collect(e.doctype + ' ' + e.name) AS sources
ORDER BY at DESC LIMIT 10
```

### remember_insight
```cypher
MERGE (u:User {name: $user})
CREATE (u)-[:SAVED]->(i:Insight {text: $text, at: $now})
WITH i
UNWIND $refs AS ref
MERGE (e:EntityRef {key: ref.key}) ON CREATE SET e.doctype = ref.doctype, e.name = ref.name, e.title = ref.title
CREATE (i)-[:DERIVED_FROM]->(e)
```
