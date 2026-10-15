import click
import frappe
from frappe.commands import pass_context


def _run(context, method):
	site = context.sites[0]
	frappe.init(site=site)
	frappe.connect()
	try:
		print(method())
	finally:
		frappe.destroy()


@click.command("sync-schema")
@pass_context
def sync_schema(context):
	"""Run a full DocType schema sync into FalkorDB (synchronous)."""
	from eve_agent.sync.schema_sync import run_schema_sync

	_run(context, run_schema_sync)


@click.command("sync-data")
@click.option("--full", is_flag=True, help="Sync the schema first, then rebuild the data graph.")
@pass_context
def sync_data(context, full=False):
	"""Rebuild the FalkorDB data graph from ERP records (synchronous)."""
	from eve_agent.sync.data_sync import run_data_sync, run_full_sync

	_run(context, run_full_sync if full else run_data_sync)


commands = [sync_schema, sync_data]
