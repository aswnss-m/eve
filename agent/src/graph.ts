// FalkorDB access for the agent.
//
// Every ERP read goes through GRAPH.RO_QUERY, which FalkorDB itself refuses to
// run if the query writes. assertReadOnly (ported from
// eve_agent/falkor/query/readonly.py) rejects writes earlier with a clear
// message the model can act on. Only the memory graph is ever written.

import { FalkorDB } from "falkordb";

const QUERY_TIMEOUT_MS = 15_000;

const STRING_RE = /'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g;
const WRITE_RE = /\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH)\b|db\.idx\.[A-Za-z.]*(create|drop)/i;

export class WriteQueryError extends Error {}

export function assertReadOnly(cypher: string): void {
  // Strip string literals first so a value like 'SET' does not trigger the check.
  const match = cypher.replace(STRING_RE, "''").match(WRITE_RE);
  if (match) throw new WriteQueryError(`Write clause '${match[0]}' is not allowed in a read-only query`);
}

export type Param = null | string | number | boolean | Param[] | { [key: string]: Param };
export type Params = Record<string, Param>;
export type Row = Record<string, unknown>;

export interface QueryRun {
  title: string;
  cypher: string;
  params: Params;
  rows: Row[];
  ms: number;
}

const clients = new Map<string, Promise<FalkorDB>>();

function client(url: string): Promise<FalkorDB> {
  let db = clients.get(url);
  if (!db) {
    db = FalkorDB.connect({ url });
    db.catch(() => clients.delete(url));
    clients.set(url, db);
  }
  return db;
}

/** Turn FalkorDB nodes, edges and paths into plain JSON the model can read. */
function plain(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(plain);
  if (value && typeof value === "object") {
    const v = value as Record<string, unknown>;
    if ("labels" in v && "properties" in v) return v.properties;
    if ("relationshipType" in v && "properties" in v) return { type: v.relationshipType, ...(v.properties as object) };
    if ("nodes" in v && "edges" in v) return { nodes: plain(v.nodes), edges: plain(v.edges) };
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, plain(x)]));
  }
  return value;
}

export class Graph {
  constructor(
    private readonly url: string,
    readonly name: string,
  ) {}

  async ro(title: string, cypher: string, params: Params = {}): Promise<QueryRun> {
    assertReadOnly(cypher);
    const started = Date.now();
    const db = await client(this.url);
    const reply = await db.selectGraph(this.name).roQuery<Row>(cypher, { params, TIMEOUT: QUERY_TIMEOUT_MS });
    const rows = (reply.data ?? []).map((r) => plain(r) as Row);
    return { title, cypher: cypher.trim(), params, rows, ms: Date.now() - started };
  }

  /** Writes, for the memory graph only. */
  async write(cypher: string, params: Params = {}): Promise<Row[]> {
    const db = await client(this.url);
    const reply = await db.selectGraph(this.name).query<Row>(cypher, { params, TIMEOUT: QUERY_TIMEOUT_MS });
    return (reply.data ?? []).map((r) => plain(r) as Row);
  }
}

/** Backtick-quote a DocType name for use as a label (same rule as data_queries.quote_label). */
export function quoteLabel(name: string): string {
  return "`" + name.replaceAll("`", "``") + "`";
}
