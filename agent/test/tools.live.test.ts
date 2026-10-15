// Runs every tool against a real data graph. Skips when FalkorDB or the graph is missing.
// EVE_TEST_REDIS (default redis://127.0.0.1:6500), EVE_TEST_GRAPH (default eve_local_data).

import { FalkorDB } from "falkordb";
import { beforeAll, describe, expect, it } from "vitest";
import type { SiteConfig } from "../src/auth.js";
import { buildTools } from "../src/tools/index.js";
import { makeContext } from "../src/tools/common.js";

const url = process.env.EVE_TEST_REDIS ?? "redis://127.0.0.1:6500";
const graph = process.env.EVE_TEST_GRAPH ?? "eve_local_data";

const config: SiteConfig = {
  site: "test",
  app_url: "",
  model: "",
  api_key: "",
  base_url: "",
  redis_url: url,
  data_graph: graph,
  schema_graph: graph.replace(/_data(_next)?$/, "_schema"),
  memory_graph: "eve_test_memory",
  data_as_of: "",
  currency: "USD",
  company: "",
};
const ctx = makeContext(config, { user: "test@example.com", name: "Test", site: "test", roles: [] });
type AnyTool = { inputSchema: { parse: (v: unknown) => unknown }; execute: (input: any, opts: any) => Promise<any> };
const tools = buildTools(ctx) as unknown as Record<string, AnyTool>;
// Parse like the AI SDK does, so schema defaults apply.
const run = (name: string, input: object) =>
  tools[name].execute(tools[name].inputSchema.parse(input), { toolCallId: "t", messages: [] });

let available = false;
beforeAll(async () => {
  try {
    const db = await FalkorDB.connect({ url });
    available = (await db.list()).includes(graph);
    await db.close();
  } catch {
    available = false;
  }
});

describe.runIf(process.env.EVE_LIVE !== "0")("tools against a live graph", () => {
  it("ranks best-selling items and best customers", async (t) => {
    if (!available) return t.skip();
    const items = await run("top_selling_items", { by: "amount", limit: 5 });
    expect(items.queries[0].rows.length).toBeGreaterThan(0);
    const amounts = items.queries[0].rows.map((r: any) => r.amount);
    expect([...amounts].sort((a, b) => b - a)).toEqual(amounts);
    expect(items.sources[0].doctype).toBe("Item");

    const customers = await run("top_customers", { by: "revenue", limit: 5 });
    expect(customers.queries[0].rows[0]).toHaveProperty("revenue");
  });

  it("finds, profiles and connects entities", async (t) => {
    if (!available) return t.skip();
    const top = await run("top_customers", { by: "revenue", limit: 1 });
    const name = top.queries[0].rows[0].customer as string;
    const found = await run("find_entity", { text: name.split(" ")[0], doctype: "Customer" });
    expect(found.sources.map((s: any) => s.name)).toContain(name);

    const profile = await run("customer_profile", { customer: name });
    expect(profile.queries).toHaveLength(3);
    const item = profile.queries[1].rows[0]?.item as string;
    if (item) {
      const also = await run("also_bought", { item, limit: 5 });
      expect(also.queries[0].cypher).toContain("BOUGHT");
      const itemProfile = await run("item_profile", { item });
      expect(itemProfile.queries[0].rows[0].item).toBe(item);
    }
    const supplier = await run("top_suppliers", { limit: 1 });
    const path = await run("explain_connection", {
      from_doctype: "Customer",
      from_name: name,
      to_doctype: "Supplier",
      to_name: supplier.queries[0].rows[0].supplier,
    });
    expect(path.queries[0].rows.length).toBe(1);
  });

  it("runs the company-brain and insight tools", async (t) => {
    if (!available) return t.skip();
    const top = await run("top_customers", { limit: 1 });
    const name = top.queries[0].rows[0].customer as string;
    const who = await run("who_knows", { doctype: "Customer", name });
    expect(Array.isArray(who.people)).toBe(true);
    const so = await run("run_cypher", {
      cypher: "MATCH (s:`Sales Order`) RETURN s.doctype AS doctype, s.name AS name LIMIT 1",
      purpose: "one order",
    });
    const trail = await run("decision_trail", { doctype: "Sales Order", name: so.queries[0].rows[0].name });
    expect(trail.queries[0].rows.length).toBeGreaterThan(0);
    await run("dormant_customers", { days: 30, limit: 5 });
    await run("supplier_risk", { limit: 5 });
    const schema = await run("graph_schema", {});
    expect(schema.queries[1].rows.length).toBeGreaterThan(0);
    const desc = await run("describe_doctype", { doctype: "Sales Invoice" });
    expect(desc.queries[0].rows[0].fields).toContain("posting_date:Date");
  });

  it("refuses writes in run_cypher", async (t) => {
    if (!available) return t.skip();
    await expect(run("run_cypher", { cypher: "MATCH (n) DETACH DELETE n", purpose: "x" })).rejects.toThrow(/not allowed/);
  });
});
