"""Plan the data graph from the schema graph and turn records into rows. No Frappe imports here.

The plan is built from DocType nodes read back out of the schema graph (see
schema_queries.EXPORT_DOCTYPES), so the data graph follows whatever the last
schema sync wrote.
"""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from eve_agent.falkor.importer.schema_rows import DYNAMIC_FIELDTYPES, LINK_FIELDTYPES, TABLE_FIELDTYPES

DEFAULT_MODULES = (
	"Accounts",
	"Selling",
	"Buying",
	"Stock",
	"Setup",
	"CRM",
	"Projects",
	"Assets",
	"Manufacturing",
	"HR",
	"Payroll",
)

# Field types copied onto nodes as properties. Link values are kept too, so
# queries can filter on them without walking the edge.
SCALAR_FIELDTYPES = {
	"Data",
	"Select",
	"Int",
	"Float",
	"Currency",
	"Percent",
	"Check",
	"Date",
	"Datetime",
	"Time",
	"Duration",
	"Rating",
	"Small Text",
	"Read Only",
	"Autocomplete",
	"Phone",
}
VALUE_FIELDTYPES = SCALAR_FIELDTYPES | LINK_FIELDTYPES | DYNAMIC_FIELDTYPES

STANDARD_FIELDS = ("name", "docstatus", "creation", "modified")
CHILD_FIELDS = ("parent", "parentfield", "idx")
MAX_TEXT_LENGTH = 500

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class DocTypePlan:
	name: str
	title_field: str = ""
	value_fields: list[str] = field(default_factory=list)
	links: dict[str, str] = field(default_factory=dict)  # fieldname -> target DocType
	dynamic_links: dict[str, str] = field(default_factory=dict)  # fieldname -> field holding the DocType
	tables: dict[str, str] = field(default_factory=dict)  # fieldname -> child DocType
	is_child: bool = False

	@property
	def fetch_fields(self) -> list[str]:
		extra = CHILD_FIELDS if self.is_child else ()
		return list(dict.fromkeys([*STANDARD_FIELDS, *extra, *self.value_fields]))


@dataclass
class DataPlan:
	parents: list[DocTypePlan]
	children: dict[str, DocTypePlan]

	@property
	def parent_names(self) -> set[str]:
		return {p.name for p in self.parents}


def parse_lines(text: str | None) -> list[str]:
	return [line.strip() for line in (text or "").splitlines() if line.strip()]


def as_bool(value) -> bool:
	"""GRAPH.QUERY replies carry booleans as the strings 'true' / 'false'."""
	if isinstance(value, str):
		return value.lower() == "true"
	return bool(value)


def schema_node_from_export_row(row: list) -> dict:
	"""Map a row of schema_queries.EXPORT_DOCTYPES to the keys used here."""
	return {
		"name": row[0],
		"module": row[1],
		"is_single": as_bool(row[3]),
		"is_child": as_bool(row[4]),
		"title_field": row[6],
		"fields": json.loads(row[11] or "[]"),
	}


def doctype_plan(node: dict, is_child: bool = False) -> DocTypePlan:
	plan = DocTypePlan(name=node["name"], title_field=node.get("title_field") or "", is_child=is_child)
	for f in node.get("fields", []):
		fieldname, fieldtype, options = f.get("fieldname"), f.get("fieldtype"), f.get("options")
		if not fieldname or not _KEY_RE.match(fieldname):
			continue
		if fieldtype in VALUE_FIELDTYPES:
			plan.value_fields.append(fieldname)
		if fieldtype in LINK_FIELDTYPES and options:
			plan.links[fieldname] = options
		elif fieldtype in DYNAMIC_FIELDTYPES and options:
			plan.dynamic_links[fieldname] = options
		elif fieldtype in TABLE_FIELDTYPES and options:
			plan.tables[fieldname] = options
	return plan


def plan_from_schema(nodes: list[dict], modules=DEFAULT_MODULES, exclude=()) -> DataPlan:
	"""Pick top-level DocTypes in `modules`, plus the child DocTypes their tables use."""
	modules, exclude = set(modules), set(exclude)
	by_name = {n["name"]: n for n in nodes}

	parents = [
		doctype_plan(n)
		for n in sorted(nodes, key=lambda n: n["name"])
		if n.get("module") in modules and not n.get("is_child") and not n.get("is_single") and n["name"] not in exclude
	]

	children = {}
	for parent in parents:
		for child_name in parent.tables.values():
			child = by_name.get(child_name)
			if child and child.get("is_child") and child_name not in exclude and child_name not in children:
				children[child_name] = doctype_plan(child, is_child=True)

	return DataPlan(parents=parents, children=children)


def coerce(value):
	"""Make a DB value safe for a Cypher literal. Returns None for values to drop."""
	if value is None or value == "":
		return None
	if isinstance(value, bool | int):
		return value
	if isinstance(value, Decimal):
		value = float(value)
	if isinstance(value, float):
		return None if math.isnan(value) or math.isinf(value) else value
	if isinstance(value, datetime | date | time | timedelta):
		return str(value)
	text = str(value)
	return text[:MAX_TEXT_LENGTH]


def build_record_row(plan: DocTypePlan, record: dict) -> dict:
	row = {"doctype": plan.name}
	for key in plan.fetch_fields:
		value = coerce(record.get(key))
		if value is not None:
			row[key] = value
	title = coerce(record.get(plan.title_field)) if plan.title_field else None
	row["title"] = title if title is not None else row.get("name")
	return row


def build_link_edges(plan: DocTypePlan, record: dict, targets: set[str]) -> list[tuple[str, dict]]:
	"""Return (target DocType, edge row) pairs for Link and Dynamic Link values.

	Targets that are not loaded DocTypes are skipped.
	"""
	edges = []
	for fieldname, target in plan.links.items():
		value = record.get(fieldname)
		if value and target in targets:
			edges.append((target, {"src": record["name"], "dst": str(value), "field": fieldname}))
	for fieldname, doctype_field in plan.dynamic_links.items():
		value, target = record.get(fieldname), record.get(doctype_field)
		if value and target in targets:
			edges.append((target, {"src": record["name"], "dst": str(value), "field": fieldname}))
	return edges
