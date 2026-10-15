// Entity lookup and 360° views: find a record by fuzzy name, profile a
// customer or item across every relationship, and explain how two records connect.

import { tool } from "ai";
import { z } from "zod";
import type { QueryRun } from "../graph.js";
import { label, refs, result, type Source, type ToolContext } from "./common.js";

// Same list as FULLTEXT_LABELS in eve_agent/falkor/cypher/data_queries.py.
export const FULLTEXT_LABELS = ["Customer", "Supplier", "Item", "Employee", "Project", "Sales Person", "User", "Lead"];

export const FIND_FULLTEXT = `
CALL db.idx.fulltext.queryNodes($label, $query) YIELD node, score
RETURN node.doctype AS doctype, node.name AS name, node.title AS title, score
ORDER BY score DESC LIMIT 5`;

export const FIND_CONTAINS = (lbl: string) => `
MATCH (n:${lbl})
WHERE toLower(n.name) CONTAINS $text OR toLower(coalesce(n.title, '')) CONTAINS $text
RETURN n.doctype AS doctype, n.name AS name, n.title AS title
LIMIT 10`;

export const CUSTOMER_SUMMARY = `
MATCH (c:Customer {name: $name})
OPTIONAL MATCH (c)<-[:FOR_CUSTOMER]-(si:\`Sales Invoice\`) WHERE si.docstatus = 1
WITH c, sum(si.base_grand_total) AS revenue, count(si) AS invoices,
     sum(coalesce(si.outstanding_amount, 0)) AS outstanding, min(si.posting_date) AS first_invoice, max(si.posting_date) AS last_invoice
OPTIONAL MATCH (c)-[:IN_TERRITORY]->(t:Territory)
OPTIONAL MATCH (c)-[:IN_GROUP]->(g:\`Customer Group\`)
OPTIONAL MATCH (l:Lead)-[:CONVERTED_TO]->(c)
RETURN c.name AS customer, c.customer_type AS type, t.name AS territory, g.name AS customer_group,
       l.name AS from_lead, revenue, invoices, outstanding, first_invoice, last_invoice`;

export const CUSTOMER_ITEMS = `
MATCH (c:Customer {name: $name})-[b:BOUGHT]->(i:Item)
RETURN i.name AS item, i.item_name AS item_name, b.qty AS qty, b.amount AS amount, b.invoices AS invoices
ORDER BY amount DESC LIMIT 10`;

export const CUSTOMER_DOCS = `
MATCH (c:Customer {name: $name})<-[:FOR_CUSTOMER]-(d:Record)
WHERE d.doctype IN ['Quotation', 'Sales Order', 'Sales Invoice']
OPTIONAL MATCH (d)-[p:PAID_BY]->(pe:\`Payment Entry\`)
RETURN d.doctype AS doctype, d.name AS name, coalesce(d.posting_date, d.transaction_date) AS date,
       d.status AS status, d.base_grand_total AS amount, sum(coalesce(p.amount, 0)) AS paid
ORDER BY date DESC LIMIT 10`;

export const ITEM_BUYERS = `
MATCH (i:Item {name: $name})<-[b:BOUGHT]-(c:Customer)
RETURN c.name AS customer, b.qty AS qty, b.amount AS amount, b.invoices AS invoices
ORDER BY amount DESC LIMIT 10`;

export const ITEM_SUPPLY = `
MATCH (i:Item {name: $name})
OPTIONAL MATCH (s:Supplier)-[sp:SUPPLIES]->(i)
WITH i, collect({supplier: s.name, qty: sp.qty, amount: sp.amount}) AS suppliers
OPTIONAL MATCH (i)-[st:STOCKED_IN]->(w:Warehouse)
WITH i, suppliers, collect({warehouse: w.name, actual_qty: st.actual_qty, projected_qty: st.projected_qty}) AS stock
OPTIONAL MATCH (bom:BOM)-[:PRODUCES]->(i)
OPTIONAL MATCH (used_in:BOM)-[:CONSUMES]->(i)
RETURN i.name AS item, i.item_name AS item_name, i.item_group AS item_group, suppliers, stock,
       collect(DISTINCT bom.name) AS made_by_boms, collect(DISTINCT used_in.name) AS used_in_boms`;

export const ITEM_MONTHLY_SALES = `
MATCH (si:\`Sales Invoice\`)-[k:CONTAINS]->(i:Item {name: $name})
WHERE si.docstatus = 1 AND coalesce(si.is_return, 0) = 0
RETURN substring(si.posting_date, 0, 7) AS month, sum(k.qty) AS qty, sum(k.amount) AS amount
ORDER BY month DESC LIMIT 12`;

export const SHORTEST_PATH = (a: string, b: string) => `
MATCH (a:${a} {name: $a}), (b:${b} {name: $b})
WITH shortestPath((a)-[*..6]-(b)) AS p
WHERE p IS NOT NULL
RETURN [n IN nodes(p) | {doctype: n.doctype, name: n.name, title: n.title}] AS nodes,
       [r IN relationships(p) | type(r)] AS relationships`;

function ftQuery(text: string): string {
  // RediSearch syntax: prefix-match every word. Strip operators so user text cannot change the query.
  const words = text.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
  return words.map((w) => `${w}*`).join(" ");
}

export function entityTools(ctx: ToolContext) {
  return {
    find_entity: tool({
      description:
        "Resolve a name the user typed (customer, supplier, item, employee, project, sales person, user, lead, or any DocType) to exact record names. Call this before other tools when the user gives a partial or fuzzy name.",
      inputSchema: z.object({
        text: z.string().min(1),
        doctype: z.string().optional().describe("Restrict to one DocType, e.g. Customer"),
      }),
      execute: async ({ text, doctype }) => {
        const query = ftQuery(text);
        const runs: QueryRun[] = [];
        const labels = doctype ? [doctype] : FULLTEXT_LABELS;
        for (const lbl of labels) {
          if (!FULLTEXT_LABELS.includes(lbl) || !query) continue;
          try {
            const run = await ctx.data.ro(`Full-text search in ${lbl}`, FIND_FULLTEXT, { label: lbl, query });
            if (run.rows.length) runs.push(run);
          } catch {
            // No full-text index for this label yet (graph built before indexes existed).
          }
        }
        if (!runs.length) {
          for (const lbl of labels) {
            try {
              const run = await ctx.data.ro(`Name contains, ${lbl}`, FIND_CONTAINS(await label(ctx, lbl)), {
                text: text.toLowerCase(),
              });
              if (run.rows.length) runs.push(run);
            } catch {
              // Unknown label: skip.
            }
          }
        }
        const sources: Source[] = runs.flatMap((r) =>
          r.rows.map((row) => ({ doctype: String(row.doctype), name: String(row.name), title: String(row.title ?? "") })),
        );
        return result(ctx, runs, sources, sources.length ? undefined : `Nothing matched '${text}'.`);
      },
    }),

    customer_profile: tool({
      description:
        "Everything about one customer: revenue, invoices, outstanding, territory, group, originating lead, top items bought, and recent documents with payments.",
      inputSchema: z.object({ customer: z.string().describe("Exact Customer name (use find_entity first if unsure)") }),
      execute: async ({ customer }) => {
        const p = { name: customer };
        const runs = await Promise.all([
          ctx.data.ro("Customer summary", CUSTOMER_SUMMARY, p),
          ctx.data.ro("Top items bought", CUSTOMER_ITEMS, p),
          ctx.data.ro("Recent documents", CUSTOMER_DOCS, p),
        ]);
        if (!runs[0].rows.length) throw new Error(`No Customer named '${customer}'. Try find_entity.`);
        return result(ctx, runs, [
          { doctype: "Customer", name: customer },
          ...refs(runs[1].rows, "Item", "item", "item_name"),
          ...runs[2].rows.map((r) => ({ doctype: String(r.doctype), name: String(r.name) })),
        ]);
      },
    }),

    item_profile: tool({
      description:
        "Everything about one item: top buyers, suppliers, stock by warehouse, BOMs that make or use it, and monthly sales for the last 12 months.",
      inputSchema: z.object({ item: z.string().describe("Item code (use find_entity first if unsure)") }),
      execute: async ({ item }) => {
        const p = { name: item };
        const runs = await Promise.all([
          ctx.data.ro("Item, suppliers, stock and BOMs", ITEM_SUPPLY, p),
          ctx.data.ro("Top buyers", ITEM_BUYERS, p),
          ctx.data.ro("Monthly sales", ITEM_MONTHLY_SALES, p),
        ]);
        if (!runs[0].rows.length) throw new Error(`No Item with code '${item}'. Try find_entity.`);
        return result(ctx, runs, [{ doctype: "Item", name: item }, ...refs(runs[1].rows, "Customer", "customer")]);
      },
    }),

    explain_connection: tool({
      description:
        "How two records are connected: the shortest path between them in the graph (up to 6 hops), e.g. a customer and a supplier through the items they share.",
      inputSchema: z.object({
        from_doctype: z.string(),
        from_name: z.string(),
        to_doctype: z.string(),
        to_name: z.string(),
      }),
      execute: async ({ from_doctype, from_name, to_doctype, to_name }) => {
        const cypher = SHORTEST_PATH(await label(ctx, from_doctype), await label(ctx, to_doctype));
        const run = await ctx.data.ro("Shortest path", cypher, { a: from_name, b: to_name });
        const nodes = (run.rows[0]?.nodes as Source[] | undefined) ?? [];
        return result(ctx, [run], nodes, nodes.length ? undefined : "No path within 6 hops.");
      },
    }),
  };
}
