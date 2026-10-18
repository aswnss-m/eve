"""Keep Ready access profile graphs current: the incremental drain applies each batch of changed records
to every Ready profile graph, after the main graph.

A profile reads the records through its own visibility (ProfileReader), so a record it can no longer
see is treated as missing and removed, and one it can now see is added (incremental.sync_keys).
"""

import frappe

from eve_agent.access.profiles import PROFILE_DOCTYPE, mark_pending_and_rebuild, ready_profiles
from eve_agent.access.reader import ProfileReader
from eve_agent.access.scope import AccessScope, filter_plan
from eve_agent.falkor.importer.data_rows import DataPlan
from eve_agent.sync.data_sync import sync_keys_on_graph


def apply_keys_to_profiles(settings, keys: list[str], plan: DataPlan) -> list[str]:
	"""Upsert the records behind `keys` into every Ready profile graph. Returns the profiles that failed.

	A failed update may leave a graph showing too much (a record that left the profile's territory, say),
	so that profile goes Pending at once and is rebuilt. Its keys are not queued again: a broken profile
	never keeps the drain busy, and the other profiles still get the batch."""
	failed = []
	for profile in ready_profiles():
		if not profile.active_graph:
			continue  # never served; and sync_keys_on_graph would fall back to the main graph
		try:
			scope = AccessScope.from_json(profile.scope_json)
			reader = ProfileReader(scope)
			sync_keys_on_graph(settings, profile.active_graph, keys, filter_plan(plan, scope), reader)
		except Exception as exc:
			failed.append(profile.name)
			mark_pending_and_rebuild(profile.name, last_error=f"{type(exc).__name__}: {exc}")
			frappe.db.commit()  # Unusable from now on, not from the end of the drain; also sends the build.
			frappe.log_error(
				title="Eve Agent: access profile update failed",
				reference_doctype=PROFILE_DOCTYPE,
				reference_name=profile.name,
			)
	return failed
