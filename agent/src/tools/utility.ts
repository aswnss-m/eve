// Schema discovery and the guarded text-to-Cypher fallback.

import { tool } from "ai";
import { z } from "zod";
import { assertReadOnly, type QueryRun } from "../graph.js";
import { result, type Source, type ToolContext } from "./common.js";

export const SCHEMA_LABELS = `
MATCH (n:Record) RETURN n.doctype AS doctype, count(*) AS records ORDER BY records DESC`;

export const SCHEMA_RELATIONSHIPS = `
MATCH (a)-[r]->(b) WHERE type(r) <> 'LINKS_TO' AND type(r) <> 'HAS_CHILD'
RETURN type(r) AS relationship, a.doctype AS from, b.doctype AS to, count(*) AS edges
ORDER BY edges DESC`;

export const DESCRIBE_DOCTYPE = `
MATCH (d:DocType {name: $doctype})
OPTIONAL MATCH (d)-[r:LINKS_TO|HAS_CHILD]->(t:DocType)
RETURN d.name AS doctype, d.module AS module, d.is_submittable AS is_submittable, d.title_field AS title_field,
       d.fields_json AS fields, collect({type: type(r), field: r.fieldname, target: t.name}) AS links`;

const ROW_LIMIT = 200;
const MODEL_ROW_LIMIT = 100;

const schemaCache = new Map<string, { at: number; runs: QueryRun[] }>();

export function utilityTools(ctx: ToolContext) {
  return {
    graph_schema: tool({
      description:
        "What is in the ERP graph: every DocType label with its record count, and every business relationship (type, from, to, count). Call before writing Cypher with run_cypher.",
      inputSchema: z.object({}),
      execute: async () => {
        const cached = schemaCache.get(ctx.data.name);
        let runs = cached && Date.now() - cached.at < 5 * 60_000 ? cached.runs : undefined;
        if (!runs) {
          runs = await Promise.all([
            ctx.data.ro("Records per DocType", SCHEMA_LABELS),
            ctx.data.ro("Business relationships", SCHEMA_RELATIONSHIPS),
          ]);
          schemaCache.set(ctx.data.name, { at: Date.now(), runs });
        }
        return result(ctx, runs, []);
      },
    }),

    describe_doctype: tool({
      description:
        "Fields of one DocType (property names and types on its graph nodes) and the DocTypes it links to. Use before writing Cypher that filters or returns specific properties.",
      inputSchema: z.object({ doctype: z.string() }),
      execute: async ({ doctype }) => {
        const run = await ctx.schema.ro(`Schema of ${doctype}`, DESCRIBE_DOCTYPE, { doctype });
        // Keep only what a query needs: fieldname, type and link target.
        for (const row of run.rows) {
          const fields = JSON.parse(String(row.fields ?? "[]")) as Array<Record<string, string>>;
          row.fields = fields
            .filter((f) => f.fieldname && !/Break|Button|HTML|Heading|Image/.test(f.fieldtype))
            .map((f) => `${f.fieldname}:${f.fieldtype}${/Link|Table/.test(f.fieldtype) && f.options ? `→${f.options}` : ""}`);
        }
        if (!run.rows.length) throw new Error(`Unknown DocType '${doctype}'.`);
        return result(ctx, [run], []);
      },
    }),

    run_cypher: tool({
      description: [
        "Run your own read-only Cypher on the ERP data graph when no other tool fits.",
        "Nodes have two labels: the DocType (backtick-quoted if it has spaces, e.g. :`Sales Invoice`) and :Record.",
        "Every node has name, doctype, title, docstatus (1 = submitted), owner, creation, modified, plus its DocType's fields as properties.",
        "Dates are ISO strings ('2026-10-14'), compare them as strings. Prefer business relationships over LINKS_TO.",
        "Writes are rejected. Always RETURN named columns; include name/doctype columns so sources can be cited.",
      ].join(" "),
      inputSchema: z.object({
        cypher: z.string(),
        purpose: z.string().describe("One line: what this query answers. Shown to the user."),
      }),
      execute: async ({ cypher, purpose }) => {
        assertReadOnly(cypher);
        const limited = /\bLIMIT\b/i.test(cypher) ? cypher : `${cypher.trim()} LIMIT ${ROW_LIMIT}`;
        const run = await ctx.data.ro(purpose, limited);
        const truncated = run.rows.length > MODEL_ROW_LIMIT;
        run.rows = run.rows.slice(0, MODEL_ROW_LIMIT);
        const sources: Source[] = run.rows
          .filter((r) => typeof r.doctype === "string" && typeof r.name === "string")
          .map((r) => ({ doctype: r.doctype as string, name: r.name as string }));
        return result(ctx, [run], sources, truncated ? `Showing the first ${MODEL_ROW_LIMIT} rows.` : undefined);
      },
    }),
  };
}
