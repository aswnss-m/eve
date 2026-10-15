import type { ChatUser, SiteConfig } from "../auth.js";
import { Graph, quoteLabel, type QueryRun, type Row } from "../graph.js";

export interface Source {
  doctype: string;
  name: string;
  title?: string;
}

/** What every graph tool returns: the queries it ran (shown in the UI), the ERP records behind the answer, and data freshness. */
export interface ToolResult {
  queries: QueryRun[];
  sources: Source[];
  as_of: string;
  note?: string;
}

export interface ToolContext {
  config: SiteConfig;
  user: ChatUser;
  data: Graph;
  schema: Graph;
  memory: Graph;
  /** Every source cited this turn; memory links the conversation to these. */
  cited: Source[];
}

export function makeContext(config: SiteConfig, user: ChatUser): ToolContext {
  return {
    config,
    user,
    data: new Graph(config.redis_url, config.data_graph),
    schema: new Graph(config.redis_url, config.schema_graph),
    memory: new Graph(config.redis_url, config.memory_graph),
    cited: [],
  };
}

const MAX_SOURCES = 25;

export function refs(rows: Row[], doctype: string, nameKey: string, titleKey?: string): Source[] {
  return rows
    .filter((r) => r[nameKey] != null && r[nameKey] !== "")
    .map((r) => ({
      doctype,
      name: String(r[nameKey]),
      ...(titleKey && r[titleKey] ? { title: String(r[titleKey]) } : {}),
    }));
}

export function result(ctx: ToolContext, queries: QueryRun[], sources: Source[], note?: string): ToolResult {
  const seen = new Set<string>();
  const unique = sources.filter((s) => {
    const key = `${s.doctype}|${s.name}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  ctx.cited.push(...unique);
  return { queries, sources: unique.slice(0, MAX_SOURCES), as_of: ctx.config.data_as_of, ...(note ? { note } : {}) };
}

const labelCache = new Map<string, { at: number; labels: Set<string> }>();
const LABEL_TTL_MS = 5 * 60_000;

export async function knownLabels(graph: Graph): Promise<Set<string>> {
  const cached = labelCache.get(graph.name);
  if (cached && Date.now() - cached.at < LABEL_TTL_MS) return cached.labels;
  const run = await graph.ro("labels", "CALL db.labels() YIELD label RETURN label");
  const labels = new Set(run.rows.map((r) => String(r.label)));
  labelCache.set(graph.name, { at: Date.now(), labels });
  return labels;
}

/** A DocType name checked against the graph's labels, so it is safe to splice into Cypher. */
export async function label(ctx: ToolContext, doctype: string): Promise<string> {
  const labels = await knownLabels(ctx.data);
  if (!labels.has(doctype) || doctype === "Record") {
    throw new Error(`DocType '${doctype}' has no records in the graph. Use graph_schema to see what exists.`);
  }
  return quoteLabel(doctype);
}

export function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}
