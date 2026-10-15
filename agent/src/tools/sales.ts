// Sales and buying analytics over the business layer:
//   (Sales Invoice)-[:FOR_CUSTOMER]->(Customer), (Sales Invoice)-[:CONTAINS {qty, amount}]->(Item)
//   (Customer)-[:BOUGHT {qty, amount, invoices}]->(Item), (Supplier)-[:SUPPLIES]->(Item)

import { tool } from "ai";
import { z } from "zod";
import { isoDaysAgo, refs, result, type ToolContext } from "./common.js";

const period = {
  date_from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().describe("Inclusive start date, YYYY-MM-DD"),
  date_to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().describe("Inclusive end date, YYYY-MM-DD"),
};
const limit = z.number().int().min(1).max(50).default(10);

// Submitted, non-return invoices in the period. Dates are stored as ISO strings, so string comparison works.
const INVOICE_FILTER = (v: string) =>
  `${v}.docstatus = 1 AND coalesce(${v}.is_return, 0) = 0 ` +
  `AND ($date_from IS NULL OR ${v}.posting_date >= $date_from) AND ($date_to IS NULL OR ${v}.posting_date <= $date_to)`;

export const TOP_SELLING_ITEMS = (orderBy: "amount" | "qty") => `
MATCH (si:\`Sales Invoice\`)-[k:CONTAINS]->(i:Item)
WHERE ${INVOICE_FILTER("si")} AND ($item_group IS NULL OR i.item_group = $item_group)
OPTIONAL MATCH (si)-[:FOR_CUSTOMER]->(c:Customer)
WITH i, sum(k.qty) AS qty, sum(k.amount) AS amount, count(DISTINCT si) AS invoices, count(DISTINCT c) AS customers
RETURN i.name AS item, i.item_name AS item_name, i.item_group AS item_group, qty, amount, invoices, customers
ORDER BY ${orderBy} DESC LIMIT $limit`;

export const TOP_CUSTOMERS = (orderBy: "revenue" | "invoices" | "outstanding") => `
MATCH (c:Customer)<-[:FOR_CUSTOMER]-(si:\`Sales Invoice\`)
WHERE si.docstatus = 1
  AND ($date_from IS NULL OR si.posting_date >= $date_from) AND ($date_to IS NULL OR si.posting_date <= $date_to)
  AND ($territory IS NULL OR c.territory = $territory) AND ($customer_group IS NULL OR c.customer_group = $customer_group)
WITH c, sum(si.base_grand_total) AS revenue, count(si) AS invoices,
     sum(coalesce(si.outstanding_amount, 0)) AS outstanding, max(si.posting_date) AS last_invoice
OPTIONAL MATCH (c)-[:BOUGHT]->(i:Item)
WITH c, revenue, invoices, outstanding, last_invoice, count(i) AS distinct_items
RETURN c.name AS customer, c.customer_group AS customer_group, c.territory AS territory,
       revenue, invoices, outstanding, last_invoice, distinct_items
ORDER BY ${orderBy} DESC LIMIT $limit`;

export const TOP_SUPPLIERS = (orderBy: "spend" | "invoices") => `
MATCH (s:Supplier)<-[:FROM_SUPPLIER]-(pi:\`Purchase Invoice\`)
WHERE ${INVOICE_FILTER("pi")}
WITH s, sum(pi.base_grand_total) AS spend, count(pi) AS invoices, max(pi.posting_date) AS last_invoice
OPTIONAL MATCH (s)-[:SUPPLIES]->(i:Item)
WITH s, spend, invoices, last_invoice, count(i) AS items_supplied
RETURN s.name AS supplier, s.supplier_group AS supplier_group, spend, invoices, last_invoice, items_supplied
ORDER BY ${orderBy} DESC LIMIT $limit`;

export const ALSO_BOUGHT = `
MATCH (i:Item {name: $item})<-[:BOUGHT]-(c:Customer)-[b:BOUGHT]->(j:Item)
WHERE j <> i
RETURN j.name AS item, j.item_name AS item_name, count(DISTINCT c) AS shared_customers,
       sum(b.amount) AS amount, collect(DISTINCT c.name)[0..5] AS example_customers
ORDER BY shared_customers DESC, amount DESC LIMIT $limit`;

export const DORMANT_CUSTOMERS = `
MATCH (c:Customer)<-[:FOR_CUSTOMER]-(si:\`Sales Invoice\`)
WHERE si.docstatus = 1
WITH c, sum(si.base_grand_total) AS lifetime_revenue, count(si) AS invoices, max(si.posting_date) AS last_invoice
WHERE last_invoice < $cutoff AND lifetime_revenue >= $min_revenue
RETURN c.name AS customer, c.territory AS territory, lifetime_revenue, invoices, last_invoice
ORDER BY lifetime_revenue DESC LIMIT $limit`;

export const SINGLE_SOURCE_ITEMS = `
MATCH (s:Supplier)-[:SUPPLIES]->(i:Item)
WITH i, collect(s.name) AS suppliers
WHERE size(suppliers) = 1 AND ($item IS NULL OR i.name = $item)
OPTIONAL MATCH (c:Customer)-[b:BOUGHT]->(i)
RETURN i.name AS item, i.item_name AS item_name, suppliers[0] AS only_supplier,
       sum(coalesce(b.amount, 0)) AS revenue_at_risk, count(c) AS customers
ORDER BY revenue_at_risk DESC LIMIT $limit`;

export function salesTools(ctx: ToolContext) {
  return {
    top_selling_items: tool({
      description:
        "Best-selling items from submitted Sales Invoices (returns excluded), ranked by revenue or quantity. Optional period and item group.",
      inputSchema: z.object({
        ...period,
        by: z.enum(["amount", "qty"]).default("amount"),
        item_group: z.string().optional(),
        limit,
      }),
      execute: async ({ date_from, date_to, by, item_group, limit }) => {
        const run = await ctx.data.ro("Top selling items", TOP_SELLING_ITEMS(by), {
          date_from: date_from ?? null,
          date_to: date_to ?? null,
          item_group: item_group ?? null,
          limit,
        });
        return result(ctx, [run], refs(run.rows, "Item", "item", "item_name"));
      },
    }),

    top_customers: tool({
      description:
        "Best customers from submitted Sales Invoices, ranked by revenue (net of returns), invoice count or outstanding amount. Optional period, territory, customer group.",
      inputSchema: z.object({
        ...period,
        by: z.enum(["revenue", "invoices", "outstanding"]).default("revenue"),
        territory: z.string().optional(),
        customer_group: z.string().optional(),
        limit,
      }),
      execute: async ({ date_from, date_to, by, territory, customer_group, limit }) => {
        const run = await ctx.data.ro("Top customers", TOP_CUSTOMERS(by), {
          date_from: date_from ?? null,
          date_to: date_to ?? null,
          territory: territory ?? null,
          customer_group: customer_group ?? null,
          limit,
        });
        return result(ctx, [run], refs(run.rows, "Customer", "customer"));
      },
    }),

    top_suppliers: tool({
      description: "Biggest suppliers from submitted Purchase Invoices, ranked by spend or invoice count.",
      inputSchema: z.object({ ...period, by: z.enum(["spend", "invoices"]).default("spend"), limit }),
      execute: async ({ date_from, date_to, by, limit }) => {
        const run = await ctx.data.ro("Top suppliers", TOP_SUPPLIERS(by), {
          date_from: date_from ?? null,
          date_to: date_to ?? null,
          limit,
        });
        return result(ctx, [run], refs(run.rows, "Supplier", "supplier"));
      },
    }),

    also_bought: tool({
      description:
        "Cross-sell: items most often bought by the customers who bought the given item (Customer-BOUGHT->Item<-BOUGHT-Customer-BOUGHT->Item). Item is the item code.",
      inputSchema: z.object({ item: z.string().describe("Item code, e.g. SDR-0060"), limit }),
      execute: async ({ item, limit }) => {
        const run = await ctx.data.ro("Customers who bought this also bought", ALSO_BOUGHT, { item, limit });
        return result(ctx, [run], [{ doctype: "Item", name: item }, ...refs(run.rows, "Item", "item", "item_name")]);
      },
    }),

    dormant_customers: tool({
      description:
        "Customers who used to buy but have no invoice in the last N days, ranked by lifetime revenue. Use for churn risk / who to call.",
      inputSchema: z.object({
        days: z.number().int().min(1).max(3650).default(90),
        min_revenue: z.number().min(0).default(0),
        limit,
      }),
      execute: async ({ days, min_revenue, limit }) => {
        const run = await ctx.data.ro("Dormant customers", DORMANT_CUSTOMERS, {
          cutoff: isoDaysAgo(days),
          min_revenue,
          limit,
        });
        return result(ctx, [run], refs(run.rows, "Customer", "customer"));
      },
    }),

    supplier_risk: tool({
      description:
        "Items that only one supplier supplies, ranked by the customer revenue that depends on them. Optionally check one item.",
      inputSchema: z.object({ item: z.string().optional().describe("Item code to check"), limit }),
      execute: async ({ item, limit }) => {
        const run = await ctx.data.ro("Single-source items", SINGLE_SOURCE_ITEMS, { item: item ?? null, limit });
        return result(ctx, [run], [
          ...refs(run.rows, "Item", "item", "item_name"),
          ...refs(run.rows, "Supplier", "only_supplier"),
        ]);
      },
    }),
  };
}
