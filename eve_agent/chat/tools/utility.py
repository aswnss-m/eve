"""Schema discovery and the guarded text-to-Cypher fallback."""

import json
import re
import time

from pydantic import BaseModel, Field

from eve_agent.chat.common import QueryRun, Tool, ToolContext, result
from eve_agent.falkor.query.readonly import assert_read_only

SCHEMA_LABELS = """
MATCH (n:Record) RETURN n.doctype AS doctype, count(*) AS records ORDER BY records DESC"""

SCHEMA_RELATIONSHIPS = """
MATCH (a)-[r]->(b) WHERE type(r) <> 'LINKS_TO' AND type(r) <> 'HAS_CHILD'
RETURN type(r) AS relationship, a.doctype AS from, b.doctype AS to, count(*) AS edges
ORDER BY edges DESC"""

DESCRIBE_DOCTYPE = """
MATCH (d:DocType {name: $doctype})
OPTIONAL MATCH (d)-[r:LINKS_TO|HAS_CHILD]->(t:DocType)
RETURN d.name AS doctype, d.module AS module, d.is_submittable AS is_submittable, d.title_field AS title_field,
       d.fields_json AS fields, collect({type: type(r), field: r.fieldname, target: t.name}) AS links"""

ROW_LIMIT = 200
MODEL_ROW_LIMIT = 100
SCHEMA_TTL_SECONDS = 5 * 60

_schema_cache: dict[str, tuple[float, list[QueryRun]]] = {}


class _NoArgs(BaseModel):
	pass


class DescribeDoctypeArgs(BaseModel):
	doctype: str


class RunCypherArgs(BaseModel):
	cypher: str
	purpose: str = Field(description="One line: what this query answers. Shown to the user.")


def _describe_fields(fields_json: str | None) -> list[str]:
	"""Keep only what a query needs: fieldname, type and link target."""
	out = []
	for field in json.loads(fields_json or "[]"):
		fieldname, fieldtype = field.get("fieldname"), field.get("fieldtype", "")
		if not fieldname or re.search(r"Break|Button|HTML|Heading|Image", fieldtype):
			continue
		target = (
			f"→{field['options']}" if re.search(r"Link|Table", fieldtype) and field.get("options") else ""
		)
		out.append(f"{fieldname}:{fieldtype}{target}")
	return out


def utility_tools(ctx: ToolContext) -> list[Tool]:
	def graph_schema(_args: BaseModel) -> dict:
		cached = _schema_cache.get(ctx.data.name)
		if cached and time.monotonic() - cached[0] < SCHEMA_TTL_SECONDS:
			runs = cached[1]
		else:
			runs = [
				ctx.data.ro("Records per DocType", SCHEMA_LABELS),
				ctx.data.ro("Business relationships", SCHEMA_RELATIONSHIPS),
			]
			_schema_cache[ctx.data.name] = (time.monotonic(), runs)
		return result(ctx, runs, [])

	def describe_doctype(args: DescribeDoctypeArgs) -> dict:
		run = ctx.schema.ro(f"Schema of {args.doctype}", DESCRIBE_DOCTYPE, {"doctype": args.doctype})
		if not run.rows:
			raise ValueError(f"Unknown DocType '{args.doctype}'.")
		for row in run.rows:
			row["fields"] = _describe_fields(row.get("fields"))
		return result(ctx, [run], [])

	def run_cypher(args: RunCypherArgs) -> dict:
		assert_read_only(args.cypher)
		has_limit = re.search(r"\bLIMIT\b", args.cypher, re.IGNORECASE)
		limited = args.cypher if has_limit else f"{args.cypher.strip()} LIMIT {ROW_LIMIT}"
		run = ctx.data.ro(args.purpose, limited)
		truncated = len(run.rows) > MODEL_ROW_LIMIT
		run.rows = run.rows[:MODEL_ROW_LIMIT]
		sources = [
			{"doctype": row["doctype"], "name": row["name"]}
			for row in run.rows
			if isinstance(row.get("doctype"), str) and isinstance(row.get("name"), str)
		]
		note = f"Showing the first {MODEL_ROW_LIMIT} rows." if truncated else None
		return result(ctx, [run], sources, note)

	return [
		Tool(
			"graph_schema",
			"What is in the ERP graph: every DocType label with its record count, and every business relationship (type, from, to, count). Call before writing Cypher with run_cypher.",
			_NoArgs,
			graph_schema,
		),
		Tool(
			"describe_doctype",
			"Fields of one DocType (property names and types on its graph nodes) and the DocTypes it links to. Use before writing Cypher that filters or returns specific properties.",
			DescribeDoctypeArgs,
			describe_doctype,
		),
		Tool(
			"run_cypher",
			" ".join(
				[
					"Run your own read-only Cypher on the ERP data graph when no other tool fits.",
					"Nodes have two labels: the DocType (backtick-quoted if it has spaces, e.g. :`Sales Invoice`) and :Record.",
					"Every node has name, doctype, title, docstatus (1 = submitted), owner, creation, modified, plus its DocType's fields as properties.",
					"Dates are ISO strings ('2026-10-14'), compare them as strings. Prefer business relationships over LINKS_TO.",
					"Writes are rejected. Always RETURN named columns; include name/doctype columns so sources can be cited.",
				]
			),
			RunCypherArgs,
			run_cypher,
		),
	]
