# Eve graph guide: model and useful Cypher

The data sync writes two graphs to FalkorDB:

| Graph | Contains | Use it for |
|---|---|---|
| `eve_local_schema` | One `DocType` node per DocType, with `LINKS_TO` / `HAS_CHILD` edges between DocTypes | "What links to what?", field lists, finding the path between two DocTypes |
| `eve_local_data` | One node per ERP record and per child-table row, plus the ERPNext / HRMS business relationships | Questions about real customers, orders, stock, accounts, employees |

The names come from the site (`eve.local` → `eve_local_*`) unless they are set in **Eve Agent Settings**.

## Why `MATCH (n:`X`) RETURN n` shows only dots

The browser draws **only what the query returns**. `RETURN n` returns nodes, so no edges are drawn ("Edges: 0"), and 300 unconnected dots tell you nothing. To see structure, return a **path** or the relationship:

```cypher
MATCH p = (c:Customer)<-[:FOR_CUSTOMER]-(:`Sales Order`) RETURN p LIMIT 100
```

Rules of thumb:
- **Return paths** (`MATCH p = ... RETURN p`) when you want a picture. Return columns (`RETURN c.name, sum(...)`) when you want numbers; switch the browser to its table view for those.
- **Start from one thing** (one customer, one order, one employee) and walk out. A whole label at once is noise.
- **Use the business relationships** (`FOR_CUSTOMER`, `BILLED_BY`, `WORKS_IN`, …), not `LINKS_TO`. `LINKS_TO` is the raw "some Link field points here" edge.
- Click a node to see its properties. Every record node has `name`, `title` (the DocType's title field, e.g. the customer name), `doctype`, `docstatus`, plus the DocType's plain fields (dates, amounts, statuses, Link values).
- Labels with spaces need backticks: `` (:`Sales Invoice`) ``.
- `docstatus`: 0 = draft, 1 = submitted, 2 = cancelled. Most totals should filter on `docstatus = 1`.

## The data graph model

Every record node has two labels: its DocType (`` :`Sales Order` ``) and `:Record`.

**Raw edges**, loaded straight from the DocType definitions:
- `HAS_CHILD {field}`: a parent record to its child-table rows (Sales Order → Sales Order Item).
- `LINKS_TO {field}`: a Link or Dynamic Link field to the record it names. These stay for ledgers (GL Entry, Stock Ledger Entry, Payment Ledger Entry) and anything no rule covers.

Edges to very common masters (Company, Currency, UOM, Price List, Fiscal Year, Cost Center, Mode of Payment, …) are dropped so the graph doesn't collapse into a few giant hubs. The values are still on the nodes, e.g. `so.company`, `si.currency`.

**Business relationships** are added by the rules in `eve_agent/falkor/cypher/business_rules.py` after every sync:

### Parties
| Relationship | From → To | Built from |
|---|---|---|
| `FOR_CUSTOMER` | Quotation, Sales Order, Delivery Note, Sales Invoice, Payment Entry, Project → Customer | `customer` / `party_name` / `party` |
| `FROM_SUPPLIER` | Supplier Quotation, Purchase Order, Purchase Receipt, Purchase Invoice, Payment Entry → Supplier | `supplier` / `party` |
| `FOR_LEAD` | Opportunity, Quotation → Lead | `party_name` |
| `CONVERTED_TO` | Lead → Customer | `Customer.lead_name` |
| `IN_GROUP` | Customer → Customer Group, Supplier → Supplier Group, Item → Item Group | group fields |
| `IN_TERRITORY` | Customer → Territory | `territory` |

### Document flow
```
Lead ─FOR_LEAD─ Opportunity ─QUOTED_AS→ Quotation ─ORDERED_AS→ Sales Order ─DELIVERED_BY→ Delivery Note ─BILLED_BY→ Sales Invoice ─PAID_BY→ Payment Entry
                                                                Sales Order ─BILLED_BY→ Sales Invoice

Material Request ─QUOTED_AS→ Supplier Quotation ─ORDERED_AS→ Purchase Order ─RECEIVED_BY→ Purchase Receipt ─BILLED_BY→ Purchase Invoice ─PAID_BY→ Payment Entry
Material Request ─ORDERED_AS→ Purchase Order                 Purchase Order ─BILLED_BY→ Purchase Invoice
                                                              Purchase Receipt ─INSPECTED_BY→ Quality Inspection
```
- The `*_BY` / `*_AS` edges are worked out from the child rows (e.g. `Delivery Note Item.against_sales_order`).
- `PAID_BY {amount}` sums `Payment Entry Reference.allocated_amount`.
- Steps with no data on this site (e.g. no Quotation became a Sales Order) simply have no edges.

### Items, stock, manufacturing
| Relationship | From → To | Properties |
|---|---|---|
| `CONTAINS` | any buying/selling/stock document → Item | `qty`, `amount`, `rate` (summed over the document's rows) |
| `BOUGHT` | Customer → Item (submitted Sales Invoices) | `qty`, `amount`, `invoices` |
| `SUPPLIES` | Supplier → Item (submitted Purchase Invoices) | `qty`, `amount`, `invoices` |
| `STOCKED_IN` | Item → Warehouse (from Bin) | `actual_qty`, `projected_qty`, `reserved_qty`, `ordered_qty` |
| `PRODUCES` / `CONSUMES` | BOM → Item | `CONSUMES.qty` |
| `CHILD_OF` | Item Group, Warehouse, Account, Department, Task → their parent | |

### Accounting
| Relationship | From → To | Properties |
|---|---|---|
| `POSTED_TO` | any voucher (Sales Invoice, Payment Entry, Journal Entry, Purchase Receipt, …) → Account | `debit`, `credit` from non-cancelled GL Entries |

### HR and payroll
| Relationship | From → To | Properties |
|---|---|---|
| `WORKS_IN` | Employee → Department | |
| `HAS_DESIGNATION` | Employee → Designation | |
| `REPORTS_TO` | Employee → Employee | |
| `AT_BRANCH` | Employee → Branch | |
| `FOR_EMPLOYEE` | Attendance, Leave Application/Allocation/Ledger Entry, Salary Slip, Salary Structure Assignment, Expense Claim, Timesheet, … → Employee | |
| `OF_TYPE` | leave documents → Leave Type | |
| `ON_STRUCTURE` | Employee → Salary Structure | `from_date`, `base` |
| `EARNS` / `DEDUCTS` | Employee → Salary Component (submitted slips) | `total`, `slips` |
| `TOOK_LEAVE` | Employee → Leave Type (approved applications) | `days`, `applications` |

The attendance totals are also written onto each Employee node: `days_present`, `days_absent`, `days_on_leave`, `days_half_day`, `days_wfh`.

### Projects and assets
`Task -PART_OF→ Project -FOR_CUSTOMER→ Customer`, `Asset -LOCATED_AT→ Location`, `Asset -OF_ITEM→ Item`, `Asset -IN_CATEGORY→ Asset Category`, and asset records (`Asset Activity`, `Asset Depreciation Schedule`, …) `-FOR_ASSET→ Asset`.

---

## Recipes: pictures (graph view)

Replace the example names with your own. They are real records on the seeded `eve.local`.

**One customer's order-to-cash**
```cypher
MATCH p = (c:Customer {name: 'Apex Industries'})<-[:FOR_CUSTOMER]-(:`Sales Order`)-[:DELIVERED_BY|BILLED_BY|PAID_BY*1..3]->()
RETURN p LIMIT 150
```

**The full story of one sales order**
```cypher
MATCH p = (so:`Sales Order` {name: 'SAL-ORD-2026-00185'})-[:DELIVERED_BY|BILLED_BY|PAID_BY*1..3]->()
RETURN p
```

**What is in that order**
```cypher
MATCH p = (:`Sales Order` {name: 'SAL-ORD-2026-00185'})-[:CONTAINS|FOR_CUSTOMER]->()
RETURN p
```

**One supplier's procure-to-pay**
```cypher
MATCH p = (s:Supplier {name: 'Pinnacle Works'})<-[:FROM_SUPPLIER]-(:`Purchase Order`)-[:RECEIVED_BY|BILLED_BY|PAID_BY|INSPECTED_BY*1..3]->()
RETURN p LIMIT 150
```

**Who buys what (customer ↔ item network)**
```cypher
MATCH p = (:Customer)-[:BOUGHT]->(:Item)
RETURN p LIMIT 300
```

**Everything around one item**: who buys it, who supplies it, and where it is stocked
```cypher
MATCH p = (i:Item {name: 'SDR-0060'})-[:STOCKED_IN]->(:Warehouse)
RETURN p
UNION
MATCH p = (i:Item {name: 'SDR-0060'})<-[:BOUGHT|SUPPLIES|PRODUCES|CONSUMES]-()
RETURN p
```

**How one invoice hit the books**
```cypher
MATCH p = (:`Sales Invoice` {name: 'ACC-SINV-2026-00001'})-[:POSTED_TO|PAID_BY|FOR_CUSTOMER]->()
RETURN p
```

**Chart of accounts tree**
```cypher
MATCH p = (:Account)-[:CHILD_OF]->(:Account)
RETURN p
```

**Org chart: employees, departments, designations**
```cypher
MATCH p = (:Employee)-[:WORKS_IN|HAS_DESIGNATION|REPORTS_TO]->()
RETURN p
UNION
MATCH p = (:Department)-[:CHILD_OF]->(:Department)
RETURN p
```

**Employee 360: pay, leave, structure, department**
```cypher
MATCH p = (:Employee {name: 'HR-EMP-00001'})-[:WORKS_IN|HAS_DESIGNATION|EARNS|DEDUCTS|TOOK_LEAVE|ON_STRUCTURE]->()
RETURN p
```

**CRM: leads and their opportunities**
```cypher
MATCH p = (:Lead)<-[:FOR_LEAD]-(:Opportunity)
RETURN p LIMIT 200
```

**Projects with tasks and customers**
```cypher
MATCH p = (:Task)-[:PART_OF]->(:Project)-[:FOR_CUSTOMER]->(:Customer)
RETURN p LIMIT 200
```

**Assets: location, category, item**
```cypher
MATCH p = (:Asset)-[:LOCATED_AT|IN_CATEGORY|OF_ITEM]->()
RETURN p
```

## Recipes: answers (table view)

Some of these are exception reports ("not delivered", "below safety stock"). On clean seeded data they return no rows, which means nothing is wrong.

**Top customers by billing, with what they still owe**
```cypher
MATCH (c:Customer)<-[:FOR_CUSTOMER]-(si:`Sales Invoice`)
WHERE si.docstatus = 1
RETURN c.name, count(si) AS invoices, sum(si.grand_total) AS billed, sum(si.outstanding_amount) AS outstanding
ORDER BY billed DESC LIMIT 10
```

**Receivables by invoice status**
```cypher
MATCH (si:`Sales Invoice`)
WHERE si.docstatus = 1 AND si.outstanding_amount > 0
RETURN si.status, count(si) AS invoices, sum(si.outstanding_amount) AS outstanding
```

**Oldest unpaid sales invoices** (dates are `YYYY-MM-DD` text, so they compare and sort correctly; add `AND si.due_date < '2026-10-15'` for overdue only)
```cypher
MATCH (si:`Sales Invoice`)-[:FOR_CUSTOMER]->(c)
WHERE si.docstatus = 1 AND si.outstanding_amount > 0
RETURN si.name, c.name, si.posting_date, si.due_date, si.outstanding_amount
ORDER BY si.posting_date LIMIT 20
```

**What we owe suppliers**
```cypher
MATCH (pi:`Purchase Invoice`)-[:FROM_SUPPLIER]->(s)
WHERE pi.docstatus = 1 AND pi.outstanding_amount > 0
RETURN s.name, count(pi) AS invoices, sum(pi.outstanding_amount) AS due
ORDER BY due DESC LIMIT 10
```

**Sales orders not yet delivered or billed**
```cypher
MATCH (so:`Sales Order`)-[:FOR_CUSTOMER]->(c)
WHERE so.docstatus = 1 AND (NOT (so)-[:DELIVERED_BY]->() OR NOT (so)-[:BILLED_BY]->())
RETURN so.name, c.name, so.status, so.grand_total
```

**Delivered but never invoiced**
```cypher
MATCH (dn:`Delivery Note`)
WHERE dn.docstatus = 1 AND NOT (dn)-[:BILLED_BY]->()
RETURN dn.name, dn.customer, dn.grand_total
```

**Customers who buy like Apex Industries** (simple recommendation)
```cypher
MATCH (c1:Customer {name: 'Apex Industries'})-[:BOUGHT]->(i:Item)<-[:BOUGHT]-(c2:Customer)
RETURN c2.name, count(i) AS shared_items
ORDER BY shared_items DESC LIMIT 10
```

**Items Apex Industries hasn't bought but similar customers have**
```cypher
MATCH (c1:Customer {name: 'Apex Industries'})-[:BOUGHT]->(:Item)<-[:BOUGHT]-(c2:Customer)-[:BOUGHT]->(rec:Item)
WHERE NOT (c1)-[:BOUGHT]->(rec)
RETURN rec.name, rec.title, count(DISTINCT c2) AS bought_by
ORDER BY bought_by DESC LIMIT 10
```

**Supplier concentration: items with the fewest suppliers** (1 = single-source risk)
```cypher
MATCH (s:Supplier)-[x:SUPPLIES]->(i:Item)
WITH i, count(s) AS suppliers, sum(x.amount) AS spend, collect(s.name) AS names
RETURN i.name, i.title, suppliers, spend, names
ORDER BY suppliers, spend DESC LIMIT 10
```

**Best-selling items**
```cypher
MATCH (:Customer)-[b:BOUGHT]->(i:Item)
RETURN i.name, i.title, sum(b.qty) AS qty, sum(b.amount) AS revenue
ORDER BY revenue DESC LIMIT 10
```

**Stock by warehouse**
```cypher
MATCH (i:Item)-[s:STOCKED_IN]->(w:Warehouse)
RETURN w.name, count(i) AS items, sum(s.actual_qty) AS qty
```

**Items below safety stock**
```cypher
MATCH (i:Item)-[s:STOCKED_IN]->(w:Warehouse)
WHERE i.safety_stock > 0 AND s.actual_qty < i.safety_stock
RETURN i.name, w.name, s.actual_qty, i.safety_stock
```

**Account balances (from GL)**
```cypher
MATCH (a:Account)<-[p:POSTED_TO]-()
WITH a, sum(p.debit) AS debit, sum(p.credit) AS credit
RETURN a.name, a.root_type, debit, credit, debit - credit AS balance
ORDER BY abs(debit - credit) DESC LIMIT 15
```

**GL lines for one voucher**
```cypher
MATCH (:`Sales Invoice` {name: 'ACC-SINV-2026-00001'})-[p:POSTED_TO]->(a:Account)
RETURN a.name, p.debit, p.credit
```

**Payroll cost per employee**
```cypher
MATCH (e:Employee)-[x:EARNS]->(c:`Salary Component`)
RETURN e.title, sum(x.total) AS gross, max(x.slips) AS slips
ORDER BY gross DESC
```

**Leave taken by type**
```cypher
MATCH (e:Employee)-[t:TOOK_LEAVE]->(lt:`Leave Type`)
RETURN e.title, lt.name, t.days, t.applications
ORDER BY e.title
```

**Attendance summary**
```cypher
MATCH (e:Employee)-[:WORKS_IN]->(d)
RETURN e.title, d.name, e.days_present, e.days_absent, e.days_on_leave, e.days_wfh
```

**Department headcount**
```cypher
MATCH (e:Employee)-[:WORKS_IN]->(d:Department)
WHERE e.status = 'Active'
RETURN d.name, count(e) AS headcount
ORDER BY headcount DESC
```

**Lead → opportunity outcomes**
```cypher
MATCH (l:Lead)<-[:FOR_LEAD]-(o:Opportunity)
RETURN o.status, count(*) AS opportunities
ORDER BY opportunities DESC
```

**What has this site actually got?**
```cypher
MATCH (n:Record)
RETURN n.doctype, count(*) AS records
ORDER BY records DESC
```

```cypher
MATCH ()-[r]->()
RETURN type(r), count(*) AS edges
ORDER BY edges DESC
```

## Recipes: the schema graph (`eve_local_schema`)

Select `eve_local_schema` in the browser first.

**What links to Sales Order (and through which field)**
```cypher
MATCH p = (:DocType)-[:LINKS_TO]->(:DocType {name: 'Sales Order'})
RETURN p
```

**Child tables of a DocType**
```cypher
MATCH p = (:DocType {name: 'Sales Invoice'})-[:HAS_CHILD]->(:DocType)
RETURN p
```

**How two DocTypes connect**
```cypher
MATCH (a:DocType {name: 'Sales Invoice'}), (b:DocType {name: 'Employee'})
RETURN shortestPath((a)-[*..4]->(b))
```

**A module's DocTypes and their links**
```cypher
MATCH p = (a:DocType {module: 'Payroll'})-[:LINKS_TO|HAS_CHILD]->(:DocType)
RETURN p
```

**Fields of a DocType** (stored as JSON)
```cypher
MATCH (d:DocType {name: 'Employee'})
RETURN d.field_count, d.field_names, d.link_targets
```

---

## Adding your own relationships

Add a rule to `RULES` in `eve_agent/falkor/cypher/business_rules.py`; it runs after every data sync, in list order. There are three shapes:

- `rename(name, NEW_TYPE, [fields], "Target DocType", ["Source DocTypes"], reverse=False)` turns a Link field into a named edge and removes the `LINKS_TO`.
- `flow(name, NEW_TYPE, "Previous Doc", "Child Row DocType", "link_field", "Next Doc")` builds a document-to-document step from child rows.
- `Rule(name, "<cypher> RETURN count(*) AS n")` is for anything else (aggregates, node properties). It must return one count.

Then run **Eve Agent Settings → Sync → Sync Data Graph Now**, or `bench --site eve.local sync-data`. The summary shows how many rules matched something.
