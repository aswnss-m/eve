// Company brain: who knows what, and how a decision came about.
//
// People layer (see business_rules.py): (Record)-[:CREATED_BY]->(User),
// (Comment|ToDo)-[:ON]->(Record), (ToDo)-[:ASSIGNED_TO]->(User),
// (Customer)-[:MANAGED_BY]->(Sales Person), (Sales Person)-[:SOLD_TO]->(Customer),
// (Sales Person)-[:IS_EMPLOYEE]->(Employee)-[:IS_USER]->(User).

import { tool } from "ai";
import { z } from "zod";
import type { QueryRun, Row } from "../graph.js";
import { label, refs, result, type ToolContext } from "./common.js";

const FLOW_EDGES = "QUOTED_AS|ORDERED_AS|DELIVERED_BY|BILLED_BY|RECEIVED_BY|INSPECTED_BY|PAID_BY|CONVERTED_TO";

export const WHO_CREATED = (l: string) => `
MATCH (e:${l} {name: $name})-[:CREATED_BY]->(u:User)
RETURN u.name AS user, u.full_name AS full_name, 'created this record' AS how, 1 AS touches, [] AS examples`;

export const WHO_WORKED_ON_RELATED = (l: string) => `
MATCH (e:${l} {name: $name})-[]-(d:Record)-[:CREATED_BY]->(u:User)
WHERE NOT d.doctype IN ['Comment', 'ToDo']
RETURN u.name AS user, u.full_name AS full_name, 'created ' + d.doctype AS how,
       count(DISTINCT d) AS touches, collect(DISTINCT d.name)[0..3] AS examples`;

export const WHO_COMMENTED = (l: string) => `
MATCH (e:${l} {name: $name})<-[:ON]-(c:Comment)-[:CREATED_BY]->(u:User)
RETURN u.name AS user, u.full_name AS full_name, 'commented' AS how,
       count(c) AS touches, collect(c.content)[0..2] AS examples`;

export const WHO_ASSIGNED = (l: string) => `
MATCH (e:${l} {name: $name})<-[:ON]-(t:ToDo)-[:ASSIGNED_TO]->(u:User)
RETURN u.name AS user, u.full_name AS full_name, 'was assigned: ' + coalesce(t.status, 'Open') AS how,
       count(t) AS touches, collect(t.description)[0..2] AS examples`;

export const WHO_SELLS = (l: string) => `
MATCH (e:${l} {name: $name})-[r:MANAGED_BY|SOLD_TO]-(sp:\`Sales Person\`)
OPTIONAL MATCH (sp)-[:IS_EMPLOYEE]->(:Employee)-[:IS_USER]->(u:User)
RETURN coalesce(u.name, sp.name) AS user, coalesce(u.full_name, sp.sales_person_name, sp.name) AS full_name,
       CASE type(r) WHEN 'MANAGED_BY' THEN 'is the assigned sales person' ELSE 'sold to them' END AS how,
       coalesce(r.invoices, 1) AS touches, [] AS examples`;

export const PEOPLE_DETAILS = `
MATCH (u:User) WHERE u.name IN $users
OPTIONAL MATCH (emp:Employee)-[:IS_USER]->(u)
OPTIONAL MATCH (emp)-[:WORKS_IN]->(d:Department)
OPTIONAL MATCH (emp)-[:HAS_DESIGNATION]->(g:Designation)
RETURN u.name AS user, emp.name AS employee, d.name AS department, g.name AS designation`;

export const DECISION_TRAIL = (l: string) => `
MATCH (start:${l} {name: $name})-[:${FLOW_EDGES}*0..4]-(d:Record)
WITH DISTINCT d LIMIT 40
OPTIONAL MATCH (d)-[:CREATED_BY]->(u:User)
OPTIONAL MATCH (d)<-[:ON]-(n:Record)
OPTIONAL MATCH (d)-[:FOR_CUSTOMER|FROM_SUPPLIER]->(party)
RETURN d.doctype AS doctype, d.name AS name,
       coalesce(d.posting_date, d.transaction_date, substring(d.creation, 0, 10)) AS date,
       d.status AS status, coalesce(d.base_grand_total, d.paid_amount) AS amount, party.name AS party,
       u.full_name AS created_by,
       [x IN collect(DISTINCT n) WHERE x IS NOT NULL | x.doctype + ': ' + coalesce(x.content, x.description, '')][0..5] AS notes
ORDER BY date`;

interface Person {
  user: string;
  full_name: string;
  touches: number;
  how: string[];
  examples: unknown[];
  employee?: string;
  department?: string;
  designation?: string;
}

function mergePeople(runs: QueryRun[]): Person[] {
  const people = new Map<string, Person>();
  for (const row of runs.flatMap((r) => r.rows)) {
    const key = String(row.user);
    const p = people.get(key) ?? { user: key, full_name: String(row.full_name ?? key), touches: 0, how: [], examples: [] };
    p.touches += Number(row.touches ?? 0);
    p.how.push(`${row.how} (${row.touches})`);
    p.examples.push(...((row.examples as unknown[]) ?? []).filter(Boolean));
    people.set(key, p);
  }
  return [...people.values()].sort((a, b) => b.touches - a.touches);
}

export function brainTools(ctx: ToolContext) {
  return {
    who_knows: tool({
      description:
        "Who in the company knows a record best (customer, supplier, item, project, any document): people who created it or related documents, commented, were assigned tasks on it, or sell to it. Ranked by number of touches, with their department and designation.",
      inputSchema: z.object({ doctype: z.string(), name: z.string(), limit: z.number().int().min(1).max(20).default(5) }),
      execute: async ({ doctype, name, limit }) => {
        const l = await label(ctx, doctype);
        const runs = await Promise.all([
          ctx.data.ro("Created the record", WHO_CREATED(l), { name }),
          ctx.data.ro("Created related documents", WHO_WORKED_ON_RELATED(l), { name }),
          ctx.data.ro("Commented on it", WHO_COMMENTED(l), { name }),
          ctx.data.ro("Assigned tasks on it", WHO_ASSIGNED(l), { name }),
          ctx.data.ro("Sales people", WHO_SELLS(l), { name }),
        ]);
        const people = mergePeople(runs).slice(0, limit);
        if (people.length) {
          const details = await ctx.data.ro("People details", PEOPLE_DETAILS, { users: people.map((p) => p.user) });
          runs.push(details);
          const byUser = new Map(details.rows.map((r: Row) => [String(r.user), r]));
          for (const p of people) {
            const d = byUser.get(p.user);
            if (d) Object.assign(p, { employee: d.employee, department: d.department, designation: d.designation });
          }
        }
        const answered = runs.filter((r) => r.rows.length);
        return {
          people,
          ...result(
            ctx,
            answered.length ? answered : runs.slice(0, 1),
            [{ doctype, name }, ...refs(people as unknown as Row[], "Employee", "employee")],
            people.length
              ? undefined
              : "No people are linked to this record yet (only system accounts created it, and there are no comments, assignments or sales team entries).",
          ),
        };
      },
    }),

    decision_trail: tool({
      description:
        "Trace how a document came about and what followed: the document flow (lead, quotation, order, delivery, invoice, payment, receipt...) around it, who created each step, and the comments and assignments on each.",
      inputSchema: z.object({ doctype: z.string(), name: z.string() }),
      execute: async ({ doctype, name }) => {
        const run = await ctx.data.ro("Document flow", DECISION_TRAIL(await label(ctx, doctype)), { name });
        return result(
          ctx,
          [run],
          run.rows.map((r) => ({ doctype: String(r.doctype), name: String(r.name) })),
        );
      },
    }),
  };
}
