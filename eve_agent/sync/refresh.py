"""Keep the data graph current.

Every save, submit, cancel, rename or delete of a synced record queues its key.
A short background job drains the queue and upserts those records into the
active graph (eve_agent/falkor/importer/incremental.py), so a change shows up
within seconds. A 5-minute sweep queues records whose `modified` moved without a
hook (status and outstanding updates written with db_set), and a nightly full
rebuild heals whatever the incremental path cannot see.

Queue mechanics, all in the bench's Redis (frappe.cache):
  eve_agent:pending            set of keys waiting for the drain job
  eve_agent:changes            sorted set key -> time, read by the full rebuild's catch-up
  eve_agent:drain:scheduled    flag: a drain job is queued. The job clears it before its
                               first pop, so a key added later always triggers a new job.
"""

import time
from datetime import timedelta

import frappe
from frappe.utils import add_to_date, cint, now_datetime
from frappe.utils.file_lock import LockTimeoutError
from frappe.utils.synchronization import filelock

from eve_agent.falkor.importer.data_rows import DEFAULT_MODULES, EXTRA_DOCTYPES, parse_lines
from eve_agent.falkor.importer.incremental import make_key
from eve_agent.sync.data_sync import enqueue_data_sync, load_plan, sync_keys_on_graph
from eve_agent.sync.schema_sync import SETTINGS_DOCTYPE

PENDING_KEY = "eve_agent:pending"
CHANGES_KEY = "eve_agent:changes"
FLAG_KEY = "eve_agent:drain:scheduled"
WATERMARK_KEY = "eve_agent:sweep_watermark"
GRAPH_WRITE_LOCK = "eve_agent_graph_write"

POP_BATCH = 2000
MAX_ITERATIONS = 20
FLAG_TTL_SECONDS = 120
CHANGELOG_DAYS = 2
SWEEP_OVERLAP_SECONDS = 60


def _key(name: str) -> str:
	return frappe.cache.make_key(name)


def _settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def _synced_modules(settings=None) -> set[str]:
	settings = settings or _settings()
	return set(parse_lines(settings.data_modules) or DEFAULT_MODULES)


def _extra_doctypes(settings=None) -> set[str]:
	settings = settings or _settings()
	return set(parse_lines(settings.get("extra_doctypes")))


def is_synced(doc, settings=None) -> bool:
	if doc.doctype == "Comment":
		return doc.get("comment_type") == "Comment"
	if doc.doctype in EXTRA_DOCTYPES:
		return True
	settings = settings or _settings()
	meta = frappe.get_meta(doc.doctype)
	if meta.issingle or meta.istable:
		return False
	return meta.module in _synced_modules(settings) or doc.doctype in _extra_doctypes(settings)


def incremental_enabled(settings=None) -> bool:
	settings = settings or _settings()
	value = settings.get("incremental_sync")
	return value is None or cint(value) == 1  # Unset (site predates the field) means on.


# --- Producers ---------------------------------------------------------------


def queue_record(doc, method=None, *args) -> None:
	"""doc_events hook for every DocType. Cheap: a meta lookup and a list append.

	The keys are published after the transaction commits, once per request, so a
	rolled-back save queues nothing and a submit that touches many records queues one job.
	"""
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return
	try:
		settings = _settings()
		if not incremental_enabled(settings) or not is_synced(doc, settings):
			return
		names = [doc.name]
		if method == "after_rename" and args:
			names.extend(str(a) for a in args[:2] if a)  # old and new name
		pending = getattr(frappe.local, "eve_pending_keys", None)
		if pending is None:
			pending = frappe.local.eve_pending_keys = set()
			frappe.db.after_commit.add(_publish_after_commit)
		pending.update(make_key(doc.doctype, n) for n in names)
	except Exception:
		# Never block a user's save because of the graph.
		frappe.log_error(title="Eve Agent: queue_record failed")


def _publish_after_commit() -> None:
	keys = getattr(frappe.local, "eve_pending_keys", None)
	frappe.local.eve_pending_keys = None
	if not keys:
		return
	try:
		queue_keys(keys)
	except Exception:
		frappe.log_error(title="Eve Agent: could not queue graph changes")


def queue_keys(keys) -> None:
	keys = list(keys)
	if not keys:
		return
	now = time.time()
	frappe.cache.sadd(PENDING_KEY, *keys)
	frappe.cache.zadd(_key(CHANGES_KEY), {k: now for k in keys})
	trigger_drain()


def trigger_drain() -> None:
	"""Queue one drain job unless one is already waiting to start."""
	if frappe.cache.set(_key(FLAG_KEY), 1, nx=True, ex=FLAG_TTL_SECONDS):
		frappe.enqueue("eve_agent.sync.refresh.drain_pending", queue="short", timeout=600)


# --- Consumer ----------------------------------------------------------------


def _pop_pending(count: int) -> list[str]:
	popped = frappe.cache.execute_command("SPOP", _key(PENDING_KEY), count) or []
	return [p.decode() if isinstance(p, bytes) else str(p) for p in popped]


def drain_pending() -> str:
	"""Background job: upsert every queued record into the active graph."""
	try:
		with filelock(GRAPH_WRITE_LOCK, timeout=120):
			return _drain()
	except LockTimeoutError:
		frappe.cache.delete(_key(FLAG_KEY))
		trigger_drain()
		return "Skipped: graph is locked by another sync, requeued"


def _drain() -> str:
	frappe.cache.delete(_key(FLAG_KEY))
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	plan = load_plan(settings)
	texts = []
	for _ in range(MAX_ITERATIONS):
		keys = _pop_pending(POP_BATCH)
		if not keys:
			break
		try:
			summary = sync_keys_on_graph(settings, settings.active_data_graph or None, keys, plan)
		except Exception:
			frappe.cache.sadd(PENDING_KEY, *keys)
			frappe.log_error(title="Eve Agent incremental sync failed")
			raise
		texts.append(summary.as_text())
		frappe.db.commit()  # Release the read snapshot so the next iteration sees newer commits.

	if frappe.cache.scard(_key(PENDING_KEY)):
		trigger_drain()
	if texts:
		frappe.db.set_single_value(
			SETTINGS_DOCTYPE,
			{"last_incremental_sync_at": now_datetime(), "last_incremental_summary": texts[-1]},
		)
		frappe.db.commit()
	return "\n".join(texts) or "Nothing pending"


# --- Scheduled ---------------------------------------------------------------


def sweep() -> None:
	"""Every 5 minutes: queue records whose `modified` moved since the last sweep.

	Catches db_set / frappe.db.set_value writes (invoice status, outstanding amounts,
	bins), which bump `modified` but fire no document hook.
	"""
	settings = _settings()
	if not incremental_enabled(settings):
		return
	started = now_datetime()
	watermark = frappe.cache.get_value(WATERMARK_KEY)
	since = (
		add_to_date(watermark, seconds=-SWEEP_OVERLAP_SECONDS)
		if watermark
		else started - timedelta(minutes=10)
	)
	keys = modified_keys_since(settings, since)
	frappe.cache.set_value(WATERMARK_KEY, started)
	if keys:
		queue_keys(keys)
	trim_changelog()


def modified_keys_since(settings, since) -> list[str]:
	from eve_agent.sync import record_reader

	plan = load_plan(settings)
	keys = []
	for parent in plan.parents:
		for name in record_reader.names_modified_since(parent, plan.children, since):
			keys.append(make_key(parent.name, name))
	return keys


def changelog_since(since_timestamp: float) -> list[str]:
	raw = frappe.cache.zrangebyscore(_key(CHANGES_KEY), since_timestamp, "+inf") or []
	return [k.decode() if isinstance(k, bytes) else str(k) for k in raw]


def trim_changelog() -> None:
	frappe.cache.zremrangebyscore(_key(CHANGES_KEY), "-inf", time.time() - CHANGELOG_DAYS * 86400)


def nightly_rebuild() -> None:
	"""Full blue/green rebuild: heals link targets that were missing, renamed masters, raw SQL writes."""
	enqueue_data_sync()
