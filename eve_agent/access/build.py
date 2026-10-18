"""Build one access profile's data graph through the regular data sync pipeline."""

from dataclasses import replace

import frappe
from frappe.utils import cint

from eve_agent.access.reader import ProfileReader
from eve_agent.access.scope import AccessScope, filter_plan
from eve_agent.falkor.importer.data_rows import DataPlan, DocTypePlan
from eve_agent.sync.data_sync import base_data_graph, build_data_graph, load_plan


def profile_graph_names(settings, key: str) -> tuple[str, str]:
	"""The two blue/green slots of profile `key`'s graph."""
	name = f"{base_data_graph(settings)}_p{key}"
	return name, f"{name}_next"


def with_live_field_levels(plan: DataPlan) -> DataPlan:
	"""`plan` with each field's permlevel from Frappe's current meta rather than the schema graph, which
	only a schema sync updates: a field just moved to a higher permlevel is filtered out at once. A field
	the meta no longer has is dropped (fail closed). Use it before every filter_plan for a profile."""
	# get_meta memoises in frappe.local.cache for the whole job (RedisWrapper.hget), and a change in another
	# process only clears Redis: a build restarting after a permlevel change would read the old levels.
	frappe.local.cache.pop(frappe.cache.make_key("doctype_meta"), None)
	return DataPlan(
		parents=[_live_levels(p) for p in plan.parents],
		children={name: _live_levels(child) for name, child in plan.children.items()},
	)


def _live_levels(plan: DocTypePlan) -> DocTypePlan:
	try:
		fields = frappe.get_meta(plan.name).fields
	except frappe.DoesNotExistError:
		fields = []  # deleted since the schema sync: no fields (and the reader skips the missing table)
	levels = {df.fieldname: cint(df.permlevel) for df in fields}
	return replace(
		plan,
		value_fields=[f for f in plan.value_fields if f in levels],
		text_fields=[f for f in plan.text_fields if f in levels],
		links={f: target for f, target in plan.links.items() if f in levels},
		dynamic_links={f: source for f, source in plan.dynamic_links.items() if f in levels},
		tables={f: child for f, child in plan.tables.items() if f in levels},
		field_levels={f: levels[f] for f in plan.field_levels if f in levels},
	)


def build_profile_graph(settings, scope: AccessScope, graph_name: str) -> str:
	"""Always passes the filtered plan: that is the only thing narrowing the reader's fields."""
	plan = filter_plan(with_live_field_levels(load_plan(settings)), scope)
	return build_data_graph(settings, graph_name, plan=plan, reader=ProfileReader(scope))
