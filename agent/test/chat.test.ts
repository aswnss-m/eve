// End-to-end /chat with a scripted model: the model calls top_customers, the
// tool queries the live graph, and the reply streams back with the Cypher and
// sources the widget renders. The turn is then saved to a throwaway memory graph.

import { MockLanguageModelV4 } from "ai/test";
import { FalkorDB } from "falkordb";
import { SignJWT } from "jose";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApp, defaultDeps } from "../src/app.js";
import type { SiteConfig } from "../src/auth.js";
import { memoryContext } from "../src/memory.js";
import { makeContext } from "../src/tools/common.js";

const url = process.env.EVE_TEST_REDIS ?? "redis://127.0.0.1:6500";
const graph = process.env.EVE_TEST_GRAPH ?? "eve_local_data";
const MEMORY = "eve_test_memory";

const config: SiteConfig = {
  site: "test", app_url: "", model: "", api_key: "", base_url: "", redis_url: url,
  data_graph: graph, schema_graph: "eve_local_schema", memory_graph: MEMORY,
  data_as_of: "2026-10-15 19:00:00", currency: "USD", company: "Test Co",
};
const user = { user: "tester@example.com", name: "Tester", site: "test", roles: ["Sales Manager"] };

const usage = {
  inputTokens: { total: 10, noCache: 10, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 5, text: 5, reasoning: 0 },
};
const stream = (parts: object[]) => ({
  stream: new ReadableStream({
    start(controller) {
      for (const p of parts) controller.enqueue(p);
      controller.close();
    },
  }),
});

function scriptedModel() {
  let step = 0;
  return new MockLanguageModelV4({
    doStream: async () =>
      step++ === 0
        ? stream([
            { type: "stream-start", warnings: [] },
            { type: "tool-call", toolCallId: "call-1", toolName: "top_customers", input: JSON.stringify({ limit: 3 }) },
            { type: "finish", finishReason: { unified: "tool-calls", raw: "tool_use" }, usage },
          ])
        : stream([
            { type: "stream-start", warnings: [] },
            { type: "text-start", id: "t1" },
            { type: "text-delta", id: "t1", delta: "Your best customer is at the top of the table." },
            { type: "text-end", id: "t1" },
            { type: "finish", finishReason: { unified: "stop", raw: "end_turn" }, usage },
          ]),
  } as any);
}

let available = false;
let db: FalkorDB | undefined;
beforeAll(async () => {
  try {
    db = await FalkorDB.connect({ url });
    available = (await db.list()).includes(graph);
  } catch {
    available = false;
  }
});
afterAll(async () => {
  if (db && (await db.list()).includes(MEMORY)) await db.selectGraph(MEMORY).delete();
  await db?.close();
});

describe("POST /chat", () => {
  it("rejects requests without a valid token", async () => {
    process.env.EVE_SHARED_SECRET = "test-secret";
    const app = createApp(defaultDeps);
    const missing = await app.request("/chat", { method: "POST", body: "{}" });
    expect(missing.status).toBe(401);
    const forged = await new SignJWT({ site: "test" })
      .setProtectedHeader({ alg: "HS256" })
      .setSubject("x")
      .setExpirationTime("5m")
      .sign(new TextEncoder().encode("wrong-secret"));
    const bad = await app.request("/chat", { method: "POST", body: "{}", headers: { Authorization: `Bearer ${forged}` } });
    expect(bad.status).toBe(401);
  });

  it("streams tool calls, Cypher and sources, then remembers the turn", async (t) => {
    if (!available) return t.skip();
    const app = createApp({ authorize: async () => makeContext(config, user), model: scriptedModel });
    const res = await app.request("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: "chat-1",
        messages: [{ id: "m1", role: "user", parts: [{ type: "text", text: "Who are our best customers?" }] }],
      }),
    });
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain('"type":"tool-output-available"');
    expect(body).toContain("FOR_CUSTOMER");
    expect(body).toContain('"doctype":"Customer"');
    expect(body).toContain("Your best customer");

    // onEnd runs after the stream closes; give it a moment.
    await new Promise((r) => setTimeout(r, 300));
    const summary = await memoryContext(makeContext(config, user));
    expect(summary).toContain("Who are our best customers?");
    expect(summary).toMatch(/Customer .+asked 1x/);
  });
});
