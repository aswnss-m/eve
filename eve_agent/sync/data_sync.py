"""Build the data graph from the schema graph and ERP records. Frappe-aware glue code."""

import time
from functools import partial

import frappe
from frappe.utils import add_to_date, cint, now_datetime
from frappe.utils.file_lock import LockTimeoutError
from frappe.utils.synchronization import filelock

from eve_agent.falkor.cypher.data_queries import COUNT_SCHEMA_DOCTYPES, FULLTEXT_LABELS
from eve_agent.falkor.importer.business_layer import apply_business_rules
from eve_agent.falkor.importer.data_import import import_data, read_schema_nodes
from eve_agent.falkor.importer.data_rows import DEFAULT_MODULES, DataPlan, parse_lines, plan_from_schema
from eve_agent.falkor.importer.incremental import IncrementalSummary, make_key, sync_keys
from eve_agent.sync import record_reader
from eve_agent.sync.schema_sync import (
	SETTINGS_DOCTYPE,
	default_graph_name,
	get_graph_client,
	publish_sync_done,
	run_schema_sync,
)

DEFAULT_MAX_RECORDS = 5000
NEXT_SUFFIX = "_next"
CATCH_UP_OVERLAP_SECONDS = 60


def base_data_graph(settings) -> str:
	return settings.data_graph_name or default_graph_name("data")


def active_data_graph(settings) -> str:
	"""The graph readers (the agent) use. Falls back to the base name before the first swap."""
	return settings.active_data_graph or base_data_graph(settings)


def standby_data_graph(settings) -> str:
	"""Blue/green: rebuild into whichever slot readers are not using."""
	base = base_data_graph(settings)
	return base if active_data_graph(settings) != base else base + NEXT_SUFFIX


def extra_doctypes(settings) -> list[str]:
	# .get: a Settings doc cached before the field existed has no attribute for it.
	return parse_lines(settings.get("extra_doctypes"))


def load_plan(settings) -> DataPlan:
	"""The DocTypes to load, from the schema graph and the Settings lists."""
	schema_client = get_graph_client(settings)
	if not int(schema_client.query(COUNT_SCHEMA_DOCTYPES).rows[0][0] or 0):
		raise ValueError(f"Schema graph '{schema_client.graph_name}' is empty. Run the schema sync first.")
	return plan_from_schema(
		read_schema_nodes(schema_client),
		modules=parse_lines(settings.data_modules) or DEFAULT_MODULES,
		exclude=parse_lines(settings.exclude_doctypes),
		extra=extra_doctypes(settings),
	)


def build_data_graph(settings, graph_name: str, plan: DataPlan | None = None, reader=record_reader) -> str:
	"""Build `graph_name`. A profile build passes its filtered `plan` and a `reader` for the profile."""
	plan = plan or load_plan(settings)
	limit = cint(settings.max_records_per_doctype) or DEFAULT_MAX_RECORDS
	data_client = get_graph_client(settings, graph_name)
	summary = import_data(
		data_client,
		plan,
		fetch_records=partial(reader.fetch_records, limit=limit),
		fetch_children=reader.fetch_children,
		fulltext_labels=(*FULLTEXT_LABELS, *extra_doctypes(settings)),
	)
	business = apply_business_rules(data_client)
	text = f"{summary.as_text()}, {business.as_text()}"
	missing = [name for name in extra_doctypes(settings) if name not in plan.parent_names]
	if missing:
		text += f", not found: {', '.join(missing)}"
	return text


def sync_keys_on_graph(
	settings, graph_name: str | None, keys, plan: DataPlan | None = None, reader=record_reader
) -> IncrementalSummary:
	"""Upsert the records behind `keys` into `graph_name` (default: the active graph)."""
	plan = plan or load_plan(settings)
	client = get_graph_client(settings, graph_name or active_data_graph(settings))
	return sync_keys(
		client,
		plan,
		keys,
		fetch_by_names=reader.fetch_records_by_names,
		fetch_children_by_parents=reader.fetch_children_by_parents,
	)


def catch_up(
	settings, graph_name: str, plan: DataPlan, since_datetime, since_timestamp: float, reader=record_reader
) -> IncrementalSummary:
	"""Apply what changed while a rebuild ran: records modified since, plus queued deletes and renames."""
	from eve_agent.sync.refresh import changelog_since, modified_keys_since

	keys = set(modified_keys_since(settings, since_datetime))
	keys.update(changelog_since(since_timestamp))
	return sync_keys_on_graph(settings, graph_name, sorted(keys), plan, reader)


def run_data_sync() -> str:
	try:
		with filelock("eve_agent_data_sync", timeout=1):
			return _run_data_sync()
	except LockTimeoutError:
		return "Skipped: another data sync is running"


def _run_data_sync() -> str:
	from eve_agent.sync.refresh import GRAPH_WRITE_LOCK

	settings = frappe.get_single(SETTINGS_DOCTYPE)
	target = standby_data_graph(settings)
	values = {}
	started_at = add_to_date(now_datetime(), seconds=-CATCH_UP_OVERLAP_SECONDS)
	started_ts = time.time() - CATCH_UP_OVERLAP_SECONDS
	try:
		text = build_data_graph(settings, target)
		# Records that changed during the build go into the new graph before readers switch to it.
		# The lock keeps the incremental drain from writing to the graph that is about to go standby.
		with filelock(GRAPH_WRITE_LOCK, timeout=300):
			frappe.db.commit()  # Fresh snapshot: see everything committed during the build.
			catch = catch_up(settings, target, load_plan(settings), started_at, started_ts)
			values["active_data_graph"] = target
			frappe.db.set_single_value(SETTINGS_DOCTYPE, values)
			frappe.db.commit()
		status = "Success"
		if catch.upserted or catch.deleted:
			text += f"; catch-up: {catch.as_text()}"
	except Exception as exc:
		status, text = "Failed", f"{type(exc).__name__}: {exc}"
		frappe.log_error(title="Eve Agent data sync failed")

	values.update(
		{"last_data_synced_at": now_datetime(), "last_data_sync_status": status, "last_data_sync_summary": text}
	)
	frappe.db.set_single_value(SETTINGS_DOCTYPE, values)
	frappe.db.commit()
	publish_sync_done("data", status, text)
	return text


def run_full_sync() -> str:
	return f"Schema: {run_schema_sync()}\nData: {run_data_sync()}"


def enqueue_data_sync(full: bool = False) -> None:
	method = "run_full_sync" if full else "run_data_sync"
	frappe.enqueue(
		f"eve_agent.sync.data_sync.{method}",
		queue="long",
		timeout=3600,
		enqueue_after_commit=True,
	)
