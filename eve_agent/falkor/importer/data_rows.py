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

STANDARD_FIELDS = ("name", "docstatus", "owner", "creation", "modified")
CHILD_FIELDS = ("parent", "parentfield", "idx")
MAX_TEXT_LENGTH = 500

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TAG_RE = re.compile(r"<[^>]+>")

# DocTypes outside the module list that the people and knowledge layer needs:
# who created a record, who commented on it, and who it is assigned to.
EXTRA_DOCTYPES = ("User", "Comment", "ToDo")

# Only these value fields are copied for the listed DocTypes. Keeps User
# secrets (reset keys, IPs, API keys) out of the graph.
FIELD_ALLOWLIST = {
	"User": ("full_name", "first_name", "last_name", "email", "enabled", "user_type"),
}

# Long-text fields worth keeping for these DocTypes. HTML tags are stripped.
TEXT_FIELDS = {
	"Comment": ("content",),
	"ToDo": ("description",),
}

# Record filters, so system noise (Info / Label comments, website users) stays out.
RECORD_FILTERS = {
	"Comment": {"comment_type": "Comment"},
	"User": {"user_type": "System User"},
}


@dataclass
class DocTypePlan:
	name: str
	title_field: str = ""
	value_fields: list[str] = field(default_factory=list)
	links: dict[str, str] = field(default_factory=dict)  # fieldname -> target DocType
	dynamic_links: dict[str, str] = field(default_factory=dict)  # fieldname -> field holding the DocType
	tables: dict[str, str] = field(default_factory=dict)  # fieldname -> child DocType
	text_fields: list[str] = field(default_factory=list)  # long text, HTML stripped
	filters: dict = field(default_factory=dict)
	is_child: bool = False

	@property
	def fetch_fields(self) -> list[str]:
		extra = CHILD_FIELDS if self.is_child else ()
		return list(dict.fromkeys([*STANDARD_FIELDS, *extra, *self.value_fields, *self.text_fields]))


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
	name = node["name"]
	plan = DocTypePlan(
		name=name,
		title_field=node.get("title_field") or "",
		text_fields=list(TEXT_FIELDS.get(name, ())),
		filters=dict(RECORD_FILTERS.get(name, {})),
		is_child=is_child,
	)
	allowlist = FIELD_ALLOWLIST.get(name)
	for f in node.get("fields", []):
		fieldname, fieldtype, options = f.get("fieldname"), f.get("fieldtype"), f.get("options")
		if not fieldname or not _KEY_RE.match(fieldname):
			continue
		if allowlist is not None and fieldname not in allowlist:
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


def plan_from_schema(nodes: list[dict], modules=DEFAULT_MODULES, exclude=(), extra=()) -> DataPlan:
	"""Pick top-level DocTypes in `modules` (plus EXTRA_DOCTYPES and the `extra` names from Settings),
	and the child DocTypes their tables use.

	Each schema node is visited once, so a DocType named in `extra` that a module already
	covers is simply planned once. `exclude` wins over everything.
	"""
	modules, exclude, extra = set(modules), set(exclude), set(extra)
	by_name = {n["name"]: n for n in nodes}

	def wanted(n: dict) -> bool:
		in_scope = n.get("module") in modules or n["name"] in EXTRA_DOCTYPES or n["name"] in extra
		return in_scope and not n.get("is_child") and not n.get("is_single") and n["name"] not in exclude

	parents = [doctype_plan(n) for n in sorted(nodes, key=lambda n: n["name"]) if wanted(n)]

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


def strip_html(value):
	if not isinstance(value, str):
		return value
	return " ".join(_TAG_RE.sub(" ", value).split())


def build_record_row(plan: DocTypePlan, record: dict) -> dict:
	row = {"doctype": plan.name}
	for key in plan.fetch_fields:
		raw = record.get(key)
		value = coerce(strip_html(raw) if key in plan.text_fields else raw)
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
