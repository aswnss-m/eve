"""Build the data graph from the schema graph and ERP records. Frappe-aware glue code."""

from functools import partial

import frappe
from frappe.utils import cint, now_datetime
from frappe.utils.file_lock import LockTimeoutError
from frappe.utils.synchronization import filelock

from eve_agent.falkor.cypher.data_queries import COUNT_SCHEMA_DOCTYPES
from eve_agent.falkor.importer.business_layer import apply_business_rules
from eve_agent.falkor.importer.data_import import import_data, read_schema_nodes
from eve_agent.falkor.importer.data_rows import DEFAULT_MODULES, parse_lines, plan_from_schema
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


def base_data_graph(settings) -> str:
	return settings.data_graph_name or default_graph_name("data")


def active_data_graph(settings) -> str:
	"""The graph readers (the agent) use. Falls back to the base name before the first swap."""
	return settings.active_data_graph or base_data_graph(settings)


def standby_data_graph(settings) -> str:
	"""Blue/green: rebuild into whichever slot readers are not using."""
	base = base_data_graph(settings)
	return base if active_data_graph(settings) != base else base + NEXT_SUFFIX


def build_data_graph(settings, graph_name: str) -> str:
	schema_client = get_graph_client(settings)
	if not int(schema_client.query(COUNT_SCHEMA_DOCTYPES).rows[0][0] or 0):
		raise ValueError(f"Schema graph '{schema_client.graph_name}' is empty. Run the schema sync first.")

	plan = plan_from_schema(
		read_schema_nodes(schema_client),
		modules=parse_lines(settings.data_modules) or DEFAULT_MODULES,
		exclude=parse_lines(settings.exclude_doctypes),
	)
	limit = cint(settings.max_records_per_doctype) or DEFAULT_MAX_RECORDS
	data_client = get_graph_client(settings, graph_name)
	summary = import_data(
		data_client,
		plan,
		fetch_records=partial(record_reader.fetch_records, limit=limit),
		fetch_children=record_reader.fetch_children,
	)
	business = apply_business_rules(data_client)
	return f"{summary.as_text()}, {business.as_text()}"


def run_data_sync() -> str:
	try:
		with filelock("eve_agent_data_sync", timeout=1):
			return _run_data_sync()
	except LockTimeoutError:
		return "Skipped: another data sync is running"


def _run_data_sync() -> str:
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	target = standby_data_graph(settings)
	values = {}
	try:
		status, text = "Success", build_data_graph(settings, target)
		# Readers switch only after the new graph is complete.
		values["active_data_graph"] = target
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
