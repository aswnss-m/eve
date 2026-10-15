"""Company brain: who knows what, and how a decision came about.

People layer (see business_rules.py): (Record)-[:CREATED_BY]->(User),
(Comment|ToDo)-[:ON]->(Record), (ToDo)-[:ASSIGNED_TO]->(User),
(Customer)-[:MANAGED_BY]->(Sales Person), (Sales Person)-[:SOLD_TO]->(Customer),
(Sales Person)-[:IS_EMPLOYEE]->(Employee)-[:IS_USER]->(User).
"""

from pydantic import BaseModel, Field

from eve_agent.chat.common import QueryRun, Tool, ToolContext, label, refs, result

FLOW_EDGES = "QUOTED_AS|ORDERED_AS|DELIVERED_BY|BILLED_BY|RECEIVED_BY|INSPECTED_BY|PAID_BY|CONVERTED_TO"


def who_created(lbl: str) -> str:
	return f"""
MATCH (e:{lbl} {{name: $name}})-[:CREATED_BY]->(u:User)
RETURN u.name AS user, u.full_name AS full_name, 'created this record' AS how, 1 AS touches, [] AS examples"""


def who_worked_on_related(lbl: str) -> str:
	return f"""
MATCH (e:{lbl} {{name: $name}})-[]-(d:Record)-[:CREATED_BY]->(u:User)
WHERE NOT d.doctype IN ['Comment', 'ToDo']
RETURN u.name AS user, u.full_name AS full_name, 'created ' + d.doctype AS how,
       count(DISTINCT d) AS touches, collect(DISTINCT d.name)[0..3] AS examples"""


def who_commented(lbl: str) -> str:
	return f"""
MATCH (e:{lbl} {{name: $name}})<-[:ON]-(c:Comment)-[:CREATED_BY]->(u:User)
RETURN u.name AS user, u.full_name AS full_name, 'commented' AS how,
       count(c) AS touches, collect(c.content)[0..2] AS examples"""


def who_assigned(lbl: str) -> str:
	return f"""
MATCH (e:{lbl} {{name: $name}})<-[:ON]-(t:ToDo)-[:ASSIGNED_TO]->(u:User)
RETURN u.name AS user, u.full_name AS full_name, 'was assigned: ' + coalesce(t.status, 'Open') AS how,
       count(t) AS touches, collect(t.description)[0..2] AS examples"""


def who_sells(lbl: str) -> str:
	return f"""
MATCH (e:{lbl} {{name: $name}})-[r:MANAGED_BY|SOLD_TO]-(sp:`Sales Person`)
OPTIONAL MATCH (sp)-[:IS_EMPLOYEE]->(:Employee)-[:IS_USER]->(u:User)
RETURN coalesce(u.name, sp.name) AS user, coalesce(u.full_name, sp.sales_person_name, sp.name) AS full_name,
       CASE type(r) WHEN 'MANAGED_BY' THEN 'is the assigned sales person' ELSE 'sold to them' END AS how,
       coalesce(r.invoices, 1) AS touches, [] AS examples"""


PEOPLE_DETAILS = """
MATCH (u:User) WHERE u.name IN $users
OPTIONAL MATCH (emp:Employee)-[:IS_USER]->(u)
OPTIONAL MATCH (emp)-[:WORKS_IN]->(d:Department)
OPTIONAL MATCH (emp)-[:HAS_DESIGNATION]->(g:Designation)
RETURN u.name AS user, emp.name AS employee, d.name AS department, g.name AS designation"""


def decision_trail(lbl: str) -> str:
	return f"""
MATCH (start:{lbl} {{name: $name}})-[:{FLOW_EDGES}*0..4]-(d:Record)
WITH DISTINCT d LIMIT 40
OPTIONAL MATCH (d)-[:CREATED_BY]->(u:User)
OPTIONAL MATCH (d)<-[:ON]-(n:Record)
OPTIONAL MATCH (d)-[:FOR_CUSTOMER|FROM_SUPPLIER]->(party)
RETURN d.doctype AS doctype, d.name AS name,
       coalesce(d.posting_date, d.transaction_date, substring(d.creation, 0, 10)) AS date,
       d.status AS status, coalesce(d.base_grand_total, d.paid_amount) AS amount, party.name AS party,
       u.full_name AS created_by,
       [x IN collect(DISTINCT n) WHERE x IS NOT NULL | x.doctype + ': ' + coalesce(x.content, x.description, '')][0..5] AS notes
ORDER BY date"""


class WhoKnowsArgs(BaseModel):
	doctype: str
	name: str
	limit: int = Field(5, ge=1, le=20)


class DecisionTrailArgs(BaseModel):
	doctype: str
	name: str


def merge_people(runs: list[QueryRun]) -> list[dict]:
	people: dict[str, dict] = {}
	for row in (row for run in runs for row in run.rows):
		key = str(row.get("user"))
		person = people.get(key) or {
			"user": key,
			"full_name": row.get("full_name") or key,
			"touches": 0,
			"how": [],
			"examples": [],
		}
		person["touches"] += int(row.get("touches") or 0)
		person["how"].append(f"{row.get('how')} ({row.get('touches')})")
		person["examples"].extend(e for e in (row.get("examples") or []) if e)
		people[key] = person
	return sorted(people.values(), key=lambda p: p["touches"], reverse=True)


def brain_tools(ctx: ToolContext) -> list[Tool]:
	def who_knows(args: WhoKnowsArgs) -> dict:
		lbl = label(ctx, args.doctype)
		params = {"name": args.name}
		runs = [
			ctx.data.ro("Created the record", who_created(lbl), params),
			ctx.data.ro("Created related documents", who_worked_on_related(lbl), params),
			ctx.data.ro("Commented on it", who_commented(lbl), params),
			ctx.data.ro("Assigned tasks on it", who_assigned(lbl), params),
			ctx.data.ro("Sales people", who_sells(lbl), params),
		]
		people = merge_people(runs)[: args.limit]
		if people:
			details = ctx.data.ro("People details", PEOPLE_DETAILS, {"users": [p["user"] for p in people]})
			runs.append(details)
			by_user = {str(row.get("user")): row for row in details.rows}
			for person in people:
				match = by_user.get(person["user"])
				if match:
					person.update(
						employee=match.get("employee"),
						department=match.get("department"),
						designation=match.get("designation"),
					)
		answered = [run for run in runs if run.rows]
		note = None
		if not people:
			note = (
				"No people are linked to this record yet (only system accounts created it, "
				"and there are no comments, assignments or sales team entries)."
			)
		sources = [{"doctype": args.doctype, "name": args.name}, *refs(people, "Employee", "employee")]
		return {"people": people, **result(ctx, answered or runs[:1], sources, note)}

	def decision_trail_tool(args: DecisionTrailArgs) -> dict:
		run = ctx.data.ro("Document flow", decision_trail(label(ctx, args.doctype)), {"name": args.name})
		return result(
			ctx, [run], [{"doctype": str(r.get("doctype")), "name": str(r.get("name"))} for r in run.rows]
		)

	return [
		Tool(
			"who_knows",
			"Who in the company knows a record best (customer, supplier, item, project, any document): people who created it or related documents, commented, were assigned tasks on it, or sell to it. Ranked by number of touches, with their department and designation.",
			WhoKnowsArgs,
			who_knows,
		),
		Tool(
			"decision_trail",
			"Trace how a document came about and what followed: the document flow (lead, quotation, order, delivery, invoice, payment, receipt...) around it, who created each step, and the comments and assignments on each.",
			DecisionTrailArgs,
			decision_trail_tool,
		),
	]
