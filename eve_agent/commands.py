import click
import frappe
from frappe.commands import pass_context


@click.command("sync-schema")
@pass_context
def sync_schema(context):
	"""Run a full DocType schema sync into FalkorDB (synchronous)."""
	from eve_agent.sync.schema_sync import run_schema_sync

	site = context.sites[0]
	frappe.init(site=site)
	frappe.connect()
	try:
		print(run_schema_sync())
	finally:
		frappe.destroy()


commands = [sync_schema]
