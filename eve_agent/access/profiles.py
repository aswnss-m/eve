"""Eve Access Profiles: one per AccessScope key, each with its own blue/green data graph.

A profile is created the first time one of its members asks, and built by a background job.
Only a Ready profile serves a graph. Until then its members get a message, never a broader graph.

Build flag in the bench's Redis (frappe.cache): eve_agent:profile_build:<key> is set while a build
of the profile is queued or running, so members asking at once queue one job. A rebuild requested
while a build runs bumps the record's `modified`, and the running build then starts over.
"""

import time
from functools import partial

import frappe
from frappe.utils import add_days, add_to_date, cint, now_datetime
from frappe.utils.synchronization import filelock

from eve_agent.access.build import build_profile_graph, profile_graph_names
from eve_agent.access.reader import ProfileReader
from eve_agent.access.resolve import resolve_scope
from eve_agent.access.scope import AccessScope, filter_plan
from eve_agent.api.agent import chat_allowed
from eve_agent.falkor.importer.data_rows import parse_lines
from eve_agent.sync.data_sync import CATCH_UP_OVERLAP_SECONDS, catch_up, load_plan
from eve_agent.sync.schema_sync import SETTINGS_DOCTYPE, get_graph_client

PROFILE_DOCTYPE = "Eve Access Profile"

PREPARING_MESSAGE = (
	"Eve is preparing data for your access level. This happens the first time and after permission "
	"changes, and usually takes under a minute. You'll get a message when it's ready."
)
CAPACITY_MESSAGE = (
	"Eve can't prepare data for your access level right now because it has reached its limit of "
	"access profiles. Please ask your System Manager to check Eve Access Profiles."
)
FAILED_MESSAGE = (
	"Eve couldn't prepare data for your access level. "
	"A System Manager can see the error in Eve Access Profiles."
)
READY_MESSAGE = "Eve is ready for your questions."

DEFAULT_MAX_PROFILES = 20
DEFAULT_RETENTION_DAYS = 14
EVICT_IDLE_HOURS = 24
BUILD_TIMEOUT_SECONDS = 3600
BUILD_ATTEMPTS = 3
CAPACITY_LOG_FLAG = "eve_agent:profile_capacity_logged"
CAPACITY_LOG_SECONDS = 3600


def build_flag(key: str) -> str:
	"""Cache key set while a build of profile `key` is queued or running."""
	return frappe.cache.make_key(f"eve_agent:profile_build:{key}")


# --- Chat requests -----------------------------------------------------------


def ensure_ready(scope: AccessScope, user: str) -> tuple[str | None, str | None]:
	"""(graph_name, None) when the profile is Ready; otherwise (None, the message for `user`), and a
	build is queued. The profile is created the first time one of its members asks, if the limit allows."""
	fields = ["status", "active_graph", "waiting_users"]
	row = frappe.db.get_value(PROFILE_DOCTYPE, scope.key, fields, as_dict=True)
	if not _ready(row):
		if not row and not _create(scope):
			return None, CAPACITY_MESSAGE
		# A locking read sees a profile another request has just created, and orders concurrent askers.
		row = frappe.db.get_value(PROFILE_DOCTYPE, scope.key, fields, as_dict=True, for_update=True)
	values = {"last_used_at": now_datetime()}
	waiting = parse_lines(row.waiting_users)
	if not _ready(row) and user not in waiting:
		values["waiting_users"] = "\n".join([*waiting, user])
	frappe.db.set_value(PROFILE_DOCTYPE, scope.key, values, update_modified=False)
	if _ready(row):
		return row.active_graph, None
	_request_build(scope.key)
	return None, FAILED_MESSAGE if row.status == "Failed" else PREPARING_MESSAGE


def _ready(row) -> bool:
	return bool(row and row.status == "Ready" and row.active_graph)


def _create(scope: AccessScope) -> bool:
	"""Insert the profile, first evicting idle ones at the limit. False when all are in recent use."""
	limit = cint(frappe.get_cached_doc(SETTINGS_DOCTYPE).get("max_access_profiles")) or DEFAULT_MAX_PROFILES
	excess = frappe.db.count(PROFILE_DOCTYPE) - limit + 1
	if excess > 0:
		idle_since = add_to_date(now_datetime(), hours=-EVICT_IDLE_HOURS)
		idle = frappe.get_all(
			PROFILE_DOCTYPE,
			{"last_used_at": ("<", idle_since)},
			pluck="name",
			order_by="last_used_at asc",
			limit=excess,
		)
		if len(idle) < excess:
			_log_capacity(limit)
			return False
		for key in idle:
			delete_profile(key)
	profile = {
		"doctype": PROFILE_DOCTYPE,
		"key": scope.key,
		"status": "Pending",
		"summary": _summary(scope),
		"scope_json": scope.to_json(),
	}
	try:
		frappe.get_doc(profile).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		pass  # another member's request created it a moment ago
	return True


def _log_capacity(limit: int) -> None:
	"""Tell admins once an hour, not at every refused question."""
	if frappe.cache.set(frappe.cache.make_key(CAPACITY_LOG_FLAG), 1, nx=True, ex=CAPACITY_LOG_SECONDS):
		frappe.log_error(
			title="Eve Agent: access profile limit reached",
			message=f"All {limit} Eve Access Profiles were used in the last {EVICT_IDLE_HOURS} hours, so a "
			"user with different access could not get one. Raise Max Access Profiles in Eve Agent Settings.",
		)


def _summary(scope: AccessScope) -> str:
	"""For admins: what the profile reads, and what narrows it."""
	own = scope.own_record_doctypes
	reads = [p for p in scope.parents if p not in own]
	lines = [f"Reads: {', '.join(reads) or 'none of the synced DocTypes'}"]
	for allow, entries in sorted(scope.user_permissions.items()):
		lines.append(f"Only {allow}: {', '.join(sorted({doc for doc, _ in entries}))}")
	if own:
		lines.append(f"Own record only: {', '.join(own)}")
	if scope.strict:
		lines.append("Strict user permissions")
	return "\n".join(lines)


# --- Builds ------------------------------------------------------------------


def _request_build(key: str) -> None:
	"""Queue a build unless one is queued or running. The job is queued when the transaction commits."""
	flag = build_flag(key)
	if frappe.cache.set(flag, 1, nx=True, ex=BUILD_TIMEOUT_SECONDS):
		frappe.db.after_rollback.add(partial(frappe.cache.delete, flag))
		enqueue_profile_build(key)


def enqueue_profile_build(key: str) -> None:
	frappe.enqueue(
		"eve_agent.access.profiles.run_profile_build",
		queue="default",
		timeout=BUILD_TIMEOUT_SECONDS,
		job_id=f"eve_agent_profile_build:{key}",
		deduplicate=True,
		enqueue_after_commit=True,
		key=key,
	)


def run_profile_build(key: str) -> str:
	"""Background job: build the profile's graph into the slot readers are not using, then switch."""
	try:
		for _ in range(BUILD_ATTEMPTS):
			text = _build_once(key)
			if text is not None:
				return text
		return "Skipped: the profile changed during every attempt"
	except Exception as exc:
		text = f"{type(exc).__name__}: {exc}"
		failed = {"status": "Failed", "last_error": text}
		frappe.db.set_value(PROFILE_DOCTYPE, key, failed, update_modified=False)
		frappe.log_error(
			title="Eve Agent: access profile build failed",
			reference_doctype=PROFILE_DOCTYPE,
			reference_name=key,
		)
		frappe.db.commit()
		return text
	finally:
		frappe.cache.delete(build_flag(key))


def _build_once(key: str) -> str | None:
	"""One attempt, as _run_data_sync does it. None when the profile was marked for rebuild meanwhile."""
	from eve_agent.sync.refresh import GRAPH_WRITE_LOCK

	row = frappe.db.get_value(PROFILE_DOCTYPE, key, ["scope_json", "active_graph", "modified"], as_dict=True)
	if not row:
		return "Skipped: the profile no longer exists"
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	scope = AccessScope.from_json(row.scope_json)
	first, second = profile_graph_names(settings, key)
	target = second if row.active_graph == first else first
	started_at = add_to_date(now_datetime(), seconds=-CATCH_UP_OVERLAP_SECONDS)
	started_ts = time.time() - CATCH_UP_OVERLAP_SECONDS
	text = build_profile_graph(settings, scope, target)
	with filelock(GRAPH_WRITE_LOCK, timeout=300):
		frappe.db.commit()  # Fresh snapshot: see everything committed during the build.
		plan = filter_plan(load_plan(settings), scope)
		catch_up(settings, target, plan, started_at, started_ts, reader=ProfileReader(scope))
		# Locks the row: a member asking now waits for the switch, then gets the new graph.
		fields = ["modified", "waiting_users"]
		current = frappe.db.get_value(PROFILE_DOCTYPE, key, fields, as_dict=True, for_update=True)
		unchanged = bool(current) and current.modified == row.modified
		if unchanged:
			values = {
				"status": "Ready",
				"active_graph": target,
				"last_built_at": now_datetime(),
				"last_error": None,
				"waiting_users": None,
			}
			frappe.db.set_value(PROFILE_DOCTYPE, key, values, update_modified=False)
		frappe.db.commit()
	if not current:
		get_graph_client(settings, target).delete_graph()
		return "Skipped: the profile was deleted during the build"
	if not unchanged:
		return None
	for user in parse_lines(current.waiting_users):
		frappe.publish_realtime("msgprint", READY_MESSAGE, user=user)
	return text


def ready_profiles() -> list[dict]:
	return frappe.get_all(
		PROFILE_DOCTYPE, {"status": "Ready"}, ["name", "key", "active_graph", "scope_json"], order_by="name"
	)


def mark_all_pending_and_rebuild() -> None:
	"""After changes to field layouts or Eve's data settings: no profile graph is used until rebuilt.

	Setting the status bumps `modified`, so a build that is already running starts over."""
	for key in frappe.get_all(PROFILE_DOCTYPE, pluck="name"):
		frappe.db.set_value(PROFILE_DOCTYPE, key, "status", "Pending")
		_request_build(key)


def enqueue_profile_rebuilds() -> None:
	"""After a main graph rebuild: Ready profiles rebuild into their other slot and stay Ready meanwhile."""
	for key in frappe.get_all(PROFILE_DOCTYPE, {"status": "Ready"}, pluck="name"):
		_request_build(key)


# --- Clean-up ----------------------------------------------------------------


def delete_profile(key: str) -> None:
	"""Delete the record; EveAccessProfile.on_trash drops both of its graphs."""
	frappe.delete_doc(
		PROFILE_DOCTYPE,
		key,
		force=True,
		ignore_permissions=True,
		ignore_missing=True,
		delete_permanently=True,  # no Deleted Document copy: the profile is rebuilt when needed
	)


def drop_graphs(key: str, active_graph: str | None = None) -> None:
	"""Both slots of profile `key`, and its active graph if the data graph name has changed since."""
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	for name in {*profile_graph_names(settings, key), active_graph} - {None, ""}:
		get_graph_client(settings, name).delete_graph()


def cleanup_profiles() -> None:
	"""Daily: delete profiles unused for the retention period, then those no enabled chat user maps to."""
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	days = cint(settings.get("access_profile_retention_days")) or DEFAULT_RETENTION_DAYS
	unused_since = add_days(now_datetime(), -days)
	_delete_each(frappe.get_all(PROFILE_DOCTYPE, {"last_used_at": ("<", unused_since)}, pluck="name"))
	plan = load_plan(settings)
	users = frappe.get_all("User", {"enabled": 1}, pluck="name")
	mapped = {resolve_scope(user, plan).key for user in users if chat_allowed(settings, user)}
	_delete_each(key for key in frappe.get_all(PROFILE_DOCTYPE, pluck="name") if key not in mapped)


def _delete_each(keys) -> None:
	for key in keys:
		delete_profile(key)
		frappe.db.commit()  # Graphs are dropped at once: keep each record deletion with them.
