"""Keep the data graph current.

Saving, submitting, cancelling or deleting a synced record marks the graph
dirty. A scheduled job (every 5 minutes) queues a blue/green rebuild when it
is dirty, so bursts of edits cost one rebuild and readers never see a
half-built graph.
"""

import frappe

from eve_agent.falkor.importer.data_rows import DEFAULT_MODULES, EXTRA_DOCTYPES, parse_lines
from eve_agent.sync.data_sync import enqueue_data_sync
from eve_agent.sync.schema_sync import SETTINGS_DOCTYPE

DIRTY_KEY = "eve_agent:data_graph_dirty"


def _synced_modules() -> set[str]:
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	return set(parse_lines(settings.data_modules) or DEFAULT_MODULES)


def is_synced(doc) -> bool:
	if doc.doctype == "Comment":
		return doc.get("comment_type") == "Comment"
	if doc.doctype in EXTRA_DOCTYPES:
		return True
	meta = frappe.get_meta(doc.doctype)
	return not meta.issingle and not meta.istable and meta.module in _synced_modules()


def mark_dirty(doc, method=None) -> None:
	"""doc_events hook for every DocType. Cheap: a meta lookup and a cache write."""
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return
	try:
		if is_synced(doc):
			frappe.cache.set_value(DIRTY_KEY, 1)
	except Exception:
		# Never block a user's save because of the graph.
		frappe.log_error(title="Eve Agent: mark_dirty failed")


def refresh_if_dirty() -> None:
	"""Scheduled job. Queues one rebuild when anything changed since the last one."""
	if not frappe.cache.get_value(DIRTY_KEY):
		return
	frappe.cache.delete_value(DIRTY_KEY)
	enqueue_data_sync()
