// Writes docs/agent-queries.md from the Cypher the agent tools actually run.
// npm run docs:queries

import { writeFileSync } from "node:fs";
import * as memory from "../src/memory.js";
import * as brain from "../src/tools/brain.js";
import * as entities from "../src/tools/entities.js";
import * as sales from "../src/tools/sales.js";
import * as utility from "../src/tools/utility.js";

const L = "`Customer`"; // example label for templated queries

const sections: Array<[string, string, Array<[string, string]>]> = [
  ["Sales and buying", "src/tools/sales.ts", [
    ["top_selling_items (by amount; `by: qty` orders by qty)", sales.TOP_SELLING_ITEMS("amount")],
    ["top_customers (by revenue; also invoices, outstanding)", sales.TOP_CUSTOMERS("revenue")],
    ["top_suppliers (by spend; also invoices)", sales.TOP_SUPPLIERS("spend")],
    ["also_bought: 3-hop cross-sell", sales.ALSO_BOUGHT],
    ["dormant_customers ($cutoff = today minus N days)", sales.DORMANT_CUSTOMERS],
    ["supplier_risk: single-source items", sales.SINGLE_SOURCE_ITEMS],
  ]],
  ["Entities", "src/tools/entities.ts", [
    ["find_entity: full-text index", entities.FIND_FULLTEXT],
    ["find_entity: fallback when no index matches", entities.FIND_CONTAINS(L)],
    ["customer_profile: summary", entities.CUSTOMER_SUMMARY],
    ["customer_profile: items bought", entities.CUSTOMER_ITEMS],
    ["customer_profile: recent documents and payments", entities.CUSTOMER_DOCS],
    ["item_profile: suppliers, stock, BOMs", entities.ITEM_SUPPLY],
    ["item_profile: top buyers", entities.ITEM_BUYERS],
    ["item_profile: monthly sales", entities.ITEM_MONTHLY_SALES],
    ["explain_connection: shortest path (labels are validated against db.labels())", entities.SHORTEST_PATH(L, "`Supplier`")],
  ]],
  ["Company brain", "src/tools/brain.ts", [
    ["who_knows: created the record", brain.WHO_CREATED(L)],
    ["who_knows: created related documents", brain.WHO_WORKED_ON_RELATED(L)],
    ["who_knows: commented", brain.WHO_COMMENTED(L)],
    ["who_knows: assigned tasks", brain.WHO_ASSIGNED(L)],
    ["who_knows: sales people", brain.WHO_SELLS(L)],
    ["who_knows: department and designation", brain.PEOPLE_DETAILS],
    ["decision_trail: document flow with creators and notes", brain.DECISION_TRAIL("`Sales Order`")],
  ]],
  ["Schema and fallback", "src/tools/utility.ts", [
    ["graph_schema: records per DocType", utility.SCHEMA_LABELS],
    ["graph_schema: business relationships", utility.SCHEMA_RELATIONSHIPS],
    ["describe_doctype (schema graph)", utility.DESCRIBE_DOCTYPE],
  ]],
  ["Memory graph", "src/memory.ts", [
    ["save a turn (after every reply)", memory.SAVE_TURN],
    ["memory context (start of every chat)", memory.MEMORY_CONTEXT],
    ["recall: past messages", memory.RECALL],
    ["recall: saved insights", memory.RECALL_INSIGHTS],
    ["remember_insight", memory.SAVE_INSIGHT],
  ]],
];

let md = `# Cypher the Eve agent runs

Generated from the agent source by \`npm run docs:queries\` (in \`agent/\`). Do not edit by hand.

All ERP reads use \`GRAPH.RO_QUERY\` on the active data graph, so FalkorDB refuses any write. Values are passed as query parameters (\`$name\`). DocType labels can't be parameters, so tools check them against \`CALL db.labels()\` before splicing them in. The examples below use \`Customer\`.

\`run_cypher\` is the fallback for questions no tool covers. It runs model-written Cypher after a local write check (\`assertReadOnly\`, the same rules as \`eve_agent/falkor/query/readonly.py\`), adds \`LIMIT 200\` when there is none, and also goes through \`GRAPH.RO_QUERY\`.

The graph these queries read is built by the Python sync. Its rules are in \`eve_agent/falkor/cypher/business_rules.py\`, documented in [graph-guide.md](graph-guide.md).
`;
for (const [title, file, queries] of sections) {
  md += `\n## ${title} (\`agent/${file}\`)\n`;
  for (const [name, cypher] of queries) md += `\n### ${name}\n\`\`\`cypher\n${cypher.trim()}\n\`\`\`\n`;
}
writeFileSync(new URL("../../docs/agent-queries.md", import.meta.url), md);
console.log("wrote docs/agent-queries.md");
