// Conversation memory in its own graph (<site>_memory), one subgraph per user:
//
//   (:User)-[:HAD]->(:Conversation)-[:HAS_MESSAGE]->(:Message)
//   (:Message)-[:MENTIONS]->(:EntityRef {doctype, name})
//   (:User)-[:ASKED_ABOUT {count, last_at}]->(:EntityRef)
//   (:User)-[:SAVED]->(:Insight)-[:DERIVED_FROM]->(:EntityRef)
//
// EntityRef points back at a record in the data graph by (doctype, name), so
// memory survives the data graph being rebuilt.

import { tool } from "ai";
import { z } from "zod";
import type { Graph, Param } from "./graph.js";
import { result, type Source, type ToolContext } from "./tools/common.js";

const MAX_TEXT = 2000;

const indexed = new Set<string>();

async function ensureIndexes(memory: Graph): Promise<void> {
  if (indexed.has(memory.name)) return;
  for (const [lbl, prop] of [
    ["User", "name"],
    ["Conversation", "id"],
    ["EntityRef", "key"],
  ]) {
    try {
      await memory.write(`CREATE INDEX FOR (n:${lbl}) ON (n.${prop})`);
    } catch {
      // Already indexed.
    }
  }
  indexed.add(memory.name);
}

function entityRefs(sources: Source[]): Param[] {
  const seen = new Set<string>();
  const out: Param[] = [];
  for (const s of sources) {
    const key = `${s.doctype}|${s.name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ key, doctype: s.doctype, name: s.name, title: s.title ?? "" });
  }
  return out.slice(0, 50);
}

export const SAVE_TURN = `
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
SET a.count = a.count + 1, a.last_at = $now`;

export async function saveTurn(ctx: ToolContext, chatId: string, question: string, answer: string): Promise<void> {
  await ensureIndexes(ctx.memory);
  await ctx.memory.write(SAVE_TURN, {
    user: ctx.user.user,
    full_name: ctx.user.name,
    chat_id: `${ctx.user.user}:${chatId}`,
    title: question.slice(0, 120),
    question: question.slice(0, MAX_TEXT),
    answer: answer.slice(0, MAX_TEXT),
    now: new Date().toISOString(),
    refs: entityRefs(ctx.cited),
  });
}

export const MEMORY_CONTEXT = `
MATCH (u:User {name: $user})
OPTIONAL MATCH (u)-[a:ASKED_ABOUT]->(e:EntityRef)
WITH u, e, a ORDER BY a.count DESC, a.last_at DESC
WITH u, collect(e.doctype + ' ' + e.name + ' (asked ' + toString(a.count) + 'x)')[0..8] AS interests
OPTIONAL MATCH (u)-[:HAD]->(c:Conversation)
WITH u, interests, c ORDER BY c.updated_at DESC
WITH u, interests, collect(c.title + ' [' + substring(c.updated_at, 0, 10) + ']')[0..5] AS recent
OPTIONAL MATCH (u)-[:SAVED]->(i:Insight)
WITH interests, recent, i ORDER BY i.at DESC
RETURN interests, recent, collect(i.text + ' [' + substring(i.at, 0, 10) + ']')[0..5] AS insights`;

/** A short summary of what this user cares about, for the system prompt. */
export async function memoryContext(ctx: ToolContext): Promise<string> {
  try {
    const run = await ctx.memory.ro("Memory context", MEMORY_CONTEXT, { user: ctx.user.user });
    const row = run.rows[0];
    if (!row) return "";
    const parts: string[] = [];
    const list = (v: unknown) => ((v as string[]) ?? []).filter(Boolean);
    if (list(row.interests).length) parts.push(`Records they ask about most: ${list(row.interests).join("; ")}`);
    if (list(row.recent).length) parts.push(`Recent conversations: ${list(row.recent).join("; ")}`);
    if (list(row.insights).length) parts.push(`Insights they saved: ${list(row.insights).join("; ")}`);
    return parts.join("\n");
  } catch {
    return ""; // Memory graph does not exist yet.
  }
}

export const RECALL = `
MATCH (u:User {name: $user})-[:HAD]->(c:Conversation)-[:HAS_MESSAGE]->(m:Message)
WHERE $topic = '' OR toLower(m.text) CONTAINS $topic
OPTIONAL MATCH (m)-[:MENTIONS]->(e:EntityRef)
RETURN c.title AS conversation, m.role AS role, m.at AS at, substring(m.text, 0, 400) AS text,
       collect(e.doctype + ' ' + e.name)[0..5] AS mentions
ORDER BY at DESC LIMIT 12`;

export const RECALL_INSIGHTS = `
MATCH (u:User {name: $user})-[:SAVED]->(i:Insight)
WHERE $topic = '' OR toLower(i.text) CONTAINS $topic
OPTIONAL MATCH (i)-[:DERIVED_FROM]->(e:EntityRef)
RETURN i.text AS insight, i.at AS at, collect(e.doctype + ' ' + e.name) AS sources
ORDER BY at DESC LIMIT 10`;

export const SAVE_INSIGHT = `
MERGE (u:User {name: $user})
CREATE (u)-[:SAVED]->(i:Insight {text: $text, at: $now})
WITH i
UNWIND $refs AS ref
MERGE (e:EntityRef {key: ref.key}) ON CREATE SET e.doctype = ref.doctype, e.name = ref.name, e.title = ref.title
CREATE (i)-[:DERIVED_FROM]->(e)`;

export function memoryTools(ctx: ToolContext) {
  return {
    recall: tool({
      description:
        "Search this user's past conversations with Eve and the insights they saved. Use when they refer to earlier chats ('last time', 'what did we find about...').",
      inputSchema: z.object({ topic: z.string().default("").describe("Word or name to search for; empty for most recent") }),
      execute: async ({ topic }) => {
        await ensureIndexes(ctx.memory);
        const params = { user: ctx.user.user, topic: topic.toLowerCase() };
        const runs = await Promise.all([
          ctx.memory.ro("Past messages", RECALL, params),
          ctx.memory.ro("Saved insights", RECALL_INSIGHTS, params),
        ]);
        return result(ctx, runs, []);
      },
    }),

    remember_insight: tool({
      description:
        "Save a finding for this user so it can be recalled in later sessions, linked to the records it came from. Use when the user asks you to remember something, or after an important conclusion they confirm.",
      inputSchema: z.object({
        text: z.string().min(5).max(500),
        sources: z.array(z.object({ doctype: z.string(), name: z.string() })).max(20).default([]),
      }),
      execute: async ({ text, sources }) => {
        await ensureIndexes(ctx.memory);
        await ctx.memory.write(SAVE_INSIGHT, {
          user: ctx.user.user,
          text,
          now: new Date().toISOString(),
          refs: entityRefs(sources),
        });
        return { saved: true, text };
      },
    }),
  };
}
