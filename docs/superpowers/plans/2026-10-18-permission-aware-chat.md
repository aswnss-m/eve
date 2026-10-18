# Permission-aware Eve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eve answers every chat user only from records and fields that user can read in Frappe, totals included.

**Architecture:**
- Each user maps to an *access profile*: the outcome of Frappe's role permissions, permlevels and User Permissions for the DocTypes Eve syncs.
- Users with no restrictions read the existing main graph. Everyone else reads a FalkorDB graph built for their profile, using the existing import pipeline with a filtered plan and a permission-aware record reader.
- The incremental drain fans each change out to every Ready profile graph.
- In the chat, every tool runs on the user's graph, falls back from invoices to orders when invoices are out of scope, and passes each cited source through a `frappe.has_permission` safety net.

**Tech Stack:** Frappe v15 / ERPNext v15, Python 3.10, FalkorDB 4.22 (redis-py), `unittest` for pure tests, `bench run-tests` for Frappe tests.

**Spec:** `docs/superpowers/specs/2026-10-17-permission-aware-chat-design.md`. Read it first; this plan argues from it.

## Global Constraints

- **Where to work:**
  - Use the bench clone `~/eve-bench/apps/eve_agent` (Windows path `\\wsl$\Ubuntu-22.04\home\fahad\eve-bench\apps\eve_agent`).
  - Work on a new branch `feat/permission-aware-chat`, created from `upstream/design/permission-aware-chat`, which is `develop` df81133 plus the spec and this plan. Its remote `upstream` is `C:\Work\eve falkoredb\eve`.
  - Do not touch the uncommitted work in `C:\Work\eve falkoredb\eve`.
- **Site:** `eve.localhost` on bench `~/eve-bench`, with `allow_tests` already on. Seeded users are in `eve-test-env/README.md`.
- **Restarts:**
  - Python changes need `bench start` restarted, because workers don't reload.
  - DocType JSON changes need `bench --site eve.localhost migrate`.
- **No Frappe imports in pure modules:** `eve_agent/falkor/`, `eve_agent/chat/` and the new `eve_agent/access/scope.py` must not import Frappe. Frappe behaviour is injected, as `ToolContext.can_write` already is.
- **Code style:** Python 3.10 syntax only, tabs, double quotes, ruff line length 110. Match the surrounding docstring density.
- **Less, never more:**
  - Never answer from a broader graph than the user's profile, including on error or while preparing.
  - When unsure whether a user may see something, leave it out.
- **The save hook stays as it is:** `queue_record` in `eve_agent/sync/refresh.py` is not changed. All fan-out runs in background jobs.
- **Defaults:** Max access profiles `20`; drop unused access profiles after `14` days.
- **Fixed copy (exact strings):**
  - `PREPARING_MESSAGE = "Eve is preparing data for your access level. This happens the first time and after permission changes, and usually takes under a minute. You'll get a message when it's ready."`
  - `CAPACITY_MESSAGE = "Eve can't prepare data for your access level right now because it has reached its limit of access profiles. Please ask your System Manager to check Eve Access Profiles."`
  - `FAILED_MESSAGE = "Eve couldn't prepare data for your access level. A System Manager can see the error in Eve Access Profiles."`
  - `READY_MESSAGE = "Eve is ready for your questions."`
  - `SALES_ORDERS_NOTE = "Based on submitted sales orders: invoices need access to Sales Invoice, which you don't have."`
  - `PURCHASE_ORDERS_NOTE = "Based on submitted purchase orders: purchase invoices need access to Purchase Invoice, which you don't have."`
  - `OUTSTANDING_NOTE = "Outstanding amounts need access to Sales Invoice, which you don't have; ranked by order value instead."`
  - `HIDDEN_SOURCE_TITLE = "Eve: result outside the user's permissions"`
- **Test commands:**
  - Pure tests, from `~/eve-bench/apps/eve_agent`: `../../env/bin/python -m unittest eve_agent.tests.<module> -v`.
  - Frappe tests, from `~/eve-bench`: `bench --site eve.localhost run-tests --skip-before-tests --module eve_agent.tests.<module>`. Never run without `--skip-before-tests`, because ERPNext's hook deletes all Item Prices.
- **Frappe test hygiene:**
  - Frappe tests that need FalkorDB or the seeded users call `self.skipTest` when those are missing.
  - Every profile they create is removed in `tearDown` with `delete_profile(key)`.

## Review Focus

1. **A profile build fails** (FalkorDB down, or an exception in the import). The user gets `FAILED_MESSAGE`, never the main graph, and the next question retries the build. *Test:* Task 6 `test_failed_build_marks_failed_and_retries`, Task 10 `test_failed_profile_answers_failed_message`.
2. **Many members of a new profile ask at once.** Exactly one Eve Access Profile and one queued build result. *Test:* Task 6 `test_ensure_ready_dedupes_builds`.
3. **A record is deleted in Frappe.** It disappears from every profile graph that had it, along with the totals it fed. *Test:* Task 7 `test_deleted_record_leaves_profile_graph`.
4. **A user with a Chat Role who can read none of the synced DocTypes** gets an answer saying they lack access, with no crash and no data. *Test:* Task 8 `test_empty_scope_hides_data_tools`, Task 10 `test_user_with_no_readable_doctypes`.
5. **A child table used by two parents with different readable permlevels** gets the intersection of those levels. *Test:* Task 2 `test_shared_child_table_takes_intersection`.

---

### Task 1: Field permlevels in the schema graph and the data plan

**Files:**
- Modify: `eve_agent/sync/meta_reader.py` (`FIELD_KEYS`)
- Modify: `eve_agent/falkor/importer/data_rows.py` (`DocTypePlan`, `doctype_plan`)
- Test: `eve_agent/tests/test_data_rows.py`

**Interfaces:**
- Produces:
  - `DocTypePlan.field_levels: dict[str, int]`, mapping fieldname to permlevel for every field in `value_fields`, `text_fields`, `links`, `dynamic_links` and `tables`. A missing or `None` permlevel is `0`.
  - Schema-graph `fields_json` now carries `permlevel`.

- [ ] **Step 1: Write the failing test** `test_field_levels_come_from_schema`:
  - Build a node whose fields are `{"fieldname": "credit_limit", "fieldtype": "Currency", "permlevel": 1}` and `{"fieldname": "customer_name", "fieldtype": "Data"}`.
  - Call `doctype_plan(node)`.
  - Assert `plan.field_levels == {"credit_limit": 1, "customer_name": 0}`.
- [ ] **Step 2: Run** `../../env/bin/python -m unittest eve_agent.tests.test_data_rows -v`. Expected: FAIL (`AttributeError: field_levels`).
- [ ] **Step 3: Implement.** Add `"permlevel"` to `FIELD_KEYS`. In `doctype_plan`, record `int(f.get("permlevel") or 0)` for every field the plan keeps.
- [ ] **Step 4: Run the same command.** Expected: PASS, with all earlier `test_data_rows` tests still passing.
- [ ] **Step 5: Resync the schema** so the live schema graph carries permlevels. Restart `bench start`, then run `bench --site eve.localhost sync-schema`. Expected: `Schema: DocTypes: 778, …`.
- [ ] **Step 6: Commit** `feat(access): carry field permlevels into the data plan`.

### Task 2: `AccessScope`, profile key and plan filtering (pure)

**Files:**
- Create: `eve_agent/access/__init__.py` (empty)
- Create: `eve_agent/access/scope.py`
- Test: `eve_agent/tests/test_access.py`

**Interfaces:**
- Consumes: `DataPlan`, `DocTypePlan`, `DocTypePlan.field_levels` (Task 1).
- Produces:

```python
SPECIAL_KEEP = ("User", "Comment")   # kept in every profile plan; Comment rows are filtered by the reader
SPECIAL_DROP = ("ToDo",)             # dropped from every non-full profile plan

@dataclass
class AccessScope:
	key: str
	full_access: bool
	readable: dict[str, tuple[int, ...]]          # parent AND child DocType -> readable permlevels (sorted)
	user_permissions: dict[str, tuple[tuple[str, str], ...]]  # allow DocType -> sorted ((doc, applicable_for or ""), ...), own-record ones removed
	own_record_doctypes: tuple[str, ...] = ()     # sorted, e.g. ("Employee",)
	strict: bool = False

	def can_see(self, doctype: str) -> bool: ...  # full_access or doctype in readable or doctype in SPECIAL_KEEP
	def to_json(self) -> str: ...
	@classmethod
	def from_json(cls, text: str) -> "AccessScope": ...

FULL_SCOPE: AccessScope  # key="full", full_access=True, empty readable

def make_key(readable, user_permissions, own_record_doctypes, strict) -> str: ...  # sha1 of canonical JSON, first 12 hex chars
def filter_plan(plan: DataPlan, scope: AccessScope) -> DataPlan: ...
```

**`filter_plan` rules:**
- `full_access` returns `plan` unchanged.
- **Parents** are kept when they are in `readable` and not in `own_record_doctypes`, plus `SPECIAL_KEEP`. `SPECIAL_DROP` is removed.
- **Fields** of a kept parent are dropped when their level is not in that parent's levels. This applies to every list or dict in `DocTypePlan`: `value_fields`, `text_fields`, `links`, `dynamic_links` and `tables`. `SPECIAL_KEEP` DocTypes use levels `(0,)`.
- **Children** are kept only when a kept parent's `tables` uses them **and** they are in `readable`. Their fields are filtered by `readable[child]`.

- [ ] **Step 1: Write the failing tests** in `test_access.py`. Build the plan with `plan_from_schema` over small nodes, as `test_data_rows.NODES` does.
  - `test_key_ignores_order_and_user`: `make_key` gives the same value when `readable` and `user_permissions` are built in a different order. The key is 12 hex characters.
  - `test_key_changes_with_outcome`: adding `("South", "")` to `user_permissions["Territory"]` changes the key.
  - `test_unreadable_doctypes_are_dropped`:
    - With `readable={"Sales Order": (0,), "Sales Order Item": (0,), "Customer": (0,)}`, `"Sales Invoice"` is not in `filter_plan(...).parent_names`.
    - `"User"` and `"Comment"` are in it.
    - `"ToDo"` is not.
  - `test_fields_above_readable_level_are_dropped`: with Customer field `credit_limit` at level 1 and readable `(0,)`, `credit_limit` is not in the kept Customer plan's `value_fields`.
  - `test_own_record_doctypes_are_dropped`: with `own_record_doctypes=("Employee",)` and Employee in `readable`, Employee is not a kept parent.
  - `test_shared_child_table_takes_intersection`:
    - The child `Sales Team` is used by `Customer` and `Sales Order`, and has a field at level 1.
    - `readable["Sales Team"] = (0,)` because the resolver computes the intersection (Task 3).
    - The filtered child plan lacks the level-1 field.
  - `test_full_scope_returns_plan_unchanged`: `filter_plan(plan, FULL_SCOPE) is plan`.
  - `test_scope_json_round_trip`: `AccessScope.from_json(s.to_json()) == s`.
- [ ] **Step 2: Run** `../../env/bin/python -m unittest eve_agent.tests.test_access -v`. Expected: FAIL (`ModuleNotFoundError: eve_agent.access`).
- [ ] **Step 3: Implement `eve_agent/access/scope.py`.** It has no Frappe imports. Build the filtered `DocTypePlan` copies with `dataclasses.replace`.
- [ ] **Step 4: Run the same command.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(access): access scope, profile key and plan filtering`.

### Task 3: Resolve a user's scope from Frappe

**Files:**
- Create: `eve_agent/access/resolve.py`
- Test: `eve_agent/tests/test_access_frappe.py` (class `ResolveScopeTests`)

**Interfaces:**
- Consumes: `AccessScope`, `make_key`, `FULL_SCOPE`, `SPECIAL_KEEP` and `SPECIAL_DROP` (Task 2), and `load_plan(settings)` (existing).
- Produces:
  - `resolve_scope(user: str, plan: DataPlan) -> AccessScope`
  - `is_own_record(allow: str, doc: str, user: str) -> bool`
  - `hook_restricted(doctype: str, user: str) -> bool`

**Rules.** Each rule is one or two calls; the implementer writes them.
- `user == "Administrator"` returns `FULL_SCOPE`.
- **Parent readability:** a parent `p` (not in `SPECIAL_KEEP`/`SPECIAL_DROP`) is readable when all of these hold:
  - `perms = frappe.permissions.get_role_permissions(frappe.get_meta(p.name), user=user)` gives `perms.read`;
  - `"read" not in perms.get("if_owner", {})`, i.e. not owner-only;
  - not `hook_restricted(p.name, user)`.
- **Parent levels:** `tuple(sorted(meta.get_permlevel_access("read", user=user)))`.
- **Child levels:** for each child in `plan.children`, the intersection over readable parents whose `tables` use it of `frappe.get_meta(child).get_permlevel_access("read", parenttype=parent, user=user)`. Leave the child out when the intersection is empty.
- **`hook_restricted`** is true when any of these hold:
  - the DocType has a `has_permission` hook;
  - any `permission_query_conditions` hook returns a non-empty string for the user;
  - a Server Script permission query exists for it (`frappe.model.db_query.get_server_script_map().get("permission_query", {})`).
- **User Permissions:**
  - Start from `frappe.permissions.get_user_permissions(user)`.
  - An entry is own-record when `is_own_record(allow, entry["doc"], user)`: some Link-to-User field of `allow`'s meta holds `user` on that record. Own-record entries are removed, and `allow` is added to `own_record_doctypes`.
  - Keep `(doc, applicable_for or "")` pairs, sorted.
- **Strict mode:** `strict = bool(cint(frappe.get_system_settings("apply_strict_user_permissions")))`.
- **Full access:** every planned parent is readable at every permlevel its meta's fields use, `user_permissions` and `own_record_doctypes` are empty, and nothing is hook-restricted. Then return `FULL_SCOPE`.

- [ ] **Step 1: Write the failing tests** (`ResolveScopeTests`, seeded users, skip if absent):
  - `test_sales_user_cannot_read_accounts`: rahul's `readable` lacks `Sales Invoice`, `Payment Entry`, `Journal Entry`, `GL Entry` and `Purchase Invoice`, and contains `Sales Order` and `Customer`.
  - `test_employee_permission_is_own_record`: rahul's `own_record_doctypes == ("Employee",)`, and `user_permissions` has `Company` but no `Employee`.
  - `test_same_access_same_key`: `resolve_scope(rahul).key == resolve_scope(ananya).key != resolve_scope(meera).key`.
  - `test_administrator_is_full`: `resolve_scope("Administrator", plan).full_access is True`.
  - `test_territory_permission_changes_key`:
    - Insert a Territory "Eve Test South" and a User Permission Territory=that for ananya.
    - Her key now differs from rahul's.
    - `user_permissions["Territory"] == (("Eve Test South", ""),)`.
    - tearDown deletes both records.
- [ ] **Step 2: Run** `bench --site eve.localhost run-tests --skip-before-tests --module eve_agent.tests.test_access_frappe`. Expected: FAIL (import error).
- [ ] **Step 3: Implement `eve_agent/access/resolve.py`.**
- [ ] **Step 4: Run the same command.** Expected: PASS (5 tests).
- [ ] **Step 5: Commit** `feat(access): resolve a user's access scope from Frappe permissions`.

### Task 4: Permission-aware record reader

**Files:**
- Create: `eve_agent/access/reader.py`
- Test: `eve_agent/tests/test_access_frappe.py` (class `ProfileReaderTests`)

**Interfaces:**
- Consumes: `AccessScope` (Task 2), and `record_reader` (existing; its `_existing_fields` is reused).
- Produces:

```python
def match_condition(doctype: str, scope: AccessScope) -> str: ...   # SQL condition on `tab{doctype}`; "" = no restriction
class ProfileQuery(DatabaseQuery): ...                              # __init__(doctype, condition); runs as Administrator
class ProfileReader:
	def __init__(self, scope: AccessScope): ...
	def fetch_records(self, plan: DocTypePlan, limit: int) -> list[dict] | None: ...
	def fetch_records_by_names(self, plan: DocTypePlan, names: list[str]) -> list[dict]: ...
	fetch_children = staticmethod(record_reader.fetch_children)
	fetch_children_by_parents = staticmethod(record_reader.fetch_children_by_parents)
```

**`match_condition` — build it with Frappe's own code.** Do not rewrite the rules.
1. `q = DatabaseQuery(doctype); q.match_conditions = []; q.match_filters = []`.
2. `q.add_user_permissions(<scope.user_permissions in Frappe's shape: {allow: [{"doc": d, "applicable_for": a or None}]}>)`.
3. Join `q.match_conditions` with `" and "`.
4. **Own-record rule:** for each Link field of `doctype` whose `options` is in `scope.own_record_doctypes` and that is not `ignore_user_permissions`, AND the field with `ifnull(\`tab{doctype}\`.\`{field}\`, '')=''`. In strict mode, use `1=0` instead.
5. **Comment:** OR, over each `dt` in `scope.readable` that is not a child table (`not frappe.get_meta(dt).istable`), of ``(`tabComment`.reference_doctype = <dt> and `tabComment`.reference_name in (select name from `tab{dt}` where <match_condition(dt) or 1=1>))``. With no parents, the condition is `1=0`.

**`ProfileQuery`:** subclass `frappe.model.db_query.DatabaseQuery`. Pass `user="Administrator"` so the DocType-level check passes, and override `build_match_conditions(self, as_condition=True)` to return the stored condition. `fetch_records` calls `.execute(fields=..., filters=plan.filters or None, order_by="modified desc", limit_page_length=limit, ignore_permissions=False)` and returns `None` when the table is missing, as `record_reader.fetch_records` does.

- [ ] **Step 1: Write the failing tests** (`ProfileReaderTests`):
  - `test_reader_matches_get_list`: for rahul's scope, for each of `Customer`, `Quotation`, `Sales Order` and `Delivery Note`, the record names from `ProfileReader(scope).fetch_records(plan_for(dt), 0)` equal `set(frappe.get_list(dt, user=rahul, pluck="name", limit=0))`.
  - `test_own_record_rule_hides_employee_linked_sales_persons`: no fetched `Sales Person` has a non-empty `employee`.
  - `test_comments_follow_their_reference`: every fetched Comment's `reference_doctype` is in `scope.readable`, and none references a `Sales Invoice`.
  - `test_territory_permission_matches_frappe`:
    - Setup as in Task 3's territory test, plus a Customer and a submitted Sales Order with `territory="Eve Test South"`.
    - Ananya's reader returns exactly `frappe.get_list("Sales Order", user=ananya, pluck="name", limit=0)`.
    - It includes the new order.
  - `test_shared_document_is_not_included`:
    - `frappe.share.add("Sales Order", <an order outside ananya's territory>, ananya)`.
    - Her reader's names do **not** include it, although `frappe.get_list` does.
    - tearDown removes the share.
- [ ] **Step 2: Run the Frappe test command.** Expected: FAIL.
- [ ] **Step 3: Implement `eve_agent/access/reader.py`.**
- [ ] **Step 4: Run the same command.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(access): permission-aware record reader`.

### Task 5: Build a profile graph with the existing pipeline

**Files:**
- Modify: `eve_agent/sync/data_sync.py`
- Create: `eve_agent/access/build.py`
- Test: `eve_agent/tests/test_access_frappe.py` (class `ProfileGraphTests`, needs FalkorDB)

**Interfaces:**
- Consumes: `filter_plan` (Task 2), `ProfileReader` (Task 4).
- Produces:
  - `data_sync.build_data_graph(settings, graph_name, plan: DataPlan | None = None, reader=record_reader) -> str`. The defaults keep today's behaviour.
  - `data_sync.sync_keys_on_graph(settings, graph_name, keys, plan=None, reader=record_reader)`.
  - `data_sync.catch_up(settings, graph_name, plan, since_datetime, since_timestamp, reader=record_reader)`.
  - `build.profile_graph_names(settings, key: str) -> tuple[str, str]`, returning `(f"{base_data_graph(settings)}_p{key}", f"{base_data_graph(settings)}_p{key}_next")`.
  - `build.build_profile_graph(settings, scope: AccessScope, graph_name: str) -> str`. It runs `build_data_graph` with `filter_plan(load_plan(settings), scope)` and `ProfileReader(scope)`.

- [ ] **Step 1: Write the failing tests** (`ProfileGraphTests`). Build rahul's profile into a throwaway graph named `eve_test_profile`, and delete the graph in tearDown.
  - `test_sales_profile_has_no_accounts_nodes`: ``MATCH (n:Record) WHERE n.doctype IN ['Sales Invoice','Payment Entry','Journal Entry','GL Entry','Purchase Invoice','Employee'] RETURN count(n)`` returns 0.
  - `test_no_invoice_derived_edges`: `MATCH ()-[r:BOUGHT|BILLED_BY|PAID_BY|SOLD_TO]->() RETURN count(r)` returns 0.
  - `test_order_totals_match_sql`: the sum of `base_grand_total` over ``(:`Sales Order` {docstatus: 1})`` equals `frappe.db.sql("select sum(base_grand_total) from `tabSales Order` where docstatus=1")`.
  - `test_users_kept_with_allowlisted_fields`: User node count equals the number of System Users. No User node has any property outside `FIELD_ALLOWLIST["User"]` plus the standard fields.
  - `test_main_graph_build_unchanged`: `build_data_graph(settings, "eve_test_main")` with no plan or reader yields the same `DataImportSummary.records` as the main sync for the same data.
- [ ] **Step 2: Run the Frappe test command.** Expected: FAIL.
- [ ] **Step 3: Implement.** Thread `plan` and `reader` through the three `data_sync` functions. Pass `reader.fetch_records`, `reader.fetch_children`, `reader.fetch_records_by_names` and `reader.fetch_children_by_parents` where the module functions are used today.
- [ ] **Step 4: Run the Frappe tests and the existing pure suite.** Expected: PASS. The pure suite must stay at 74 tests with the known `explain_connection` error only.
- [ ] **Step 5: Commit** `feat(access): build profile graphs through the data sync pipeline`.

### Task 6: Eve Access Profile records, lifecycle and admin surface

**Files:**
- Create: `eve_agent/eve_agent/doctype/eve_access_profile/{__init__.py, eve_access_profile.json, eve_access_profile.py}`
- Create: `eve_agent/access/profiles.py`
- Modify:
  - `eve_agent/eve_agent/doctype/eve_agent_settings/eve_agent_settings.json` and `.js`
  - `eve_agent/api/sync.py`
  - `eve_agent/sync/data_sync.py` (`_run_data_sync`)
  - `eve_agent/hooks.py`
- Test: `eve_agent/tests/test_access_frappe.py` (class `ProfileLifecycleTests`)

**Interfaces:**
- Consumes: `resolve_scope` (Task 3), `build_profile_graph` and `profile_graph_names` (Task 5), `catch_up` (Task 5), `GRAPH_WRITE_LOCK` (existing).
- Produces:
  - **DocType `Eve Access Profile`:**
    - module "Eve Agent", `autoname: "field:key"`, System Manager read/delete only;
    - fields: `key` (Data, unique), `summary` (Small Text), `status` (Select `Pending\nReady\nFailed`), `active_graph` (Data), `scope_json` (Long Text), `last_built_at` (Datetime), `last_used_at` (Datetime), `waiting_users` (Small Text, one per line), `last_error` (Small Text).
  - **Settings fields** (new section "Access Profiles"): `max_access_profiles` (Int, default 20), `access_profile_retention_days` (Int, default 14).
  - **In `profiles.py`:**

```python
def ensure_ready(scope: AccessScope, user: str) -> tuple[str | None, str | None]: ...
	# (graph_name, None) when Ready; (None, PREPARING_MESSAGE | CAPACITY_MESSAGE | FAILED_MESSAGE) otherwise.
	# Creates the profile if new (capacity rule), records `user` in waiting_users, enqueues one build
	# (cache flag f"eve_agent:profile_build:{key}", nx, ttl 3600), touches last_used_at.
def enqueue_profile_build(key: str) -> None: ...   # queue="default", timeout=3600, job_id per key
def run_profile_build(key: str) -> str: ...        # blue/green into the slot not active, catch-up under GRAPH_WRITE_LOCK,
	# then status=Ready, active_graph, last_built_at; publish_realtime("msgprint", READY_MESSAGE, user=u) per waiting user;
	# on exception status=Failed, last_error, frappe.log_error; always clears the build flag.
def ready_profiles() -> list[dict]: ...            # name, key, active_graph, scope_json for status == "Ready"
def mark_all_pending_and_rebuild() -> None: ...    # every profile -> Pending, then enqueue builds
def enqueue_profile_rebuilds() -> None: ...        # Ready profiles rebuilt in place (stay Ready, blue/green)
def delete_profile(key: str) -> None: ...          # drops both graphs, deletes the record
def cleanup_profiles() -> None: ...                # daily: retention days, and profiles no enabled chat user maps to
```

**Rules:**
- **Capacity:** when profiles ≥ `max_access_profiles`, evict the least recently used profile whose `last_used_at` is more than 24 hours old. If there is none, return `CAPACITY_MESSAGE` and `frappe.log_error` once per hour.
- **Unmapped profiles:** `cleanup_profiles` resolves the scope of every enabled user who has a Chat Role, and deletes profiles whose key is not among the results.
- **Triggers:**
  - `_run_data_sync` success → `enqueue_profile_rebuilds()`. That covers the nightly run and manual resyncs.
  - `hooks.py` `scheduler_events["daily"]` → `cleanup_profiles`.
  - A **Rebuild access profiles** button calls `eve_agent.api.sync.rebuild_access_profiles` (`frappe.only_for("System Manager")`), which calls `mark_all_pending_and_rebuild()`.

- [ ] **Step 1: Write the failing tests** (`ProfileLifecycleTests`). Call `run_profile_build` directly; tests don't rely on workers.
  - `test_first_question_returns_preparing`: `ensure_ready(scope, rahul) == (None, PREPARING_MESSAGE)`, and one profile record exists with `status == "Pending"`.
  - `test_ensure_ready_dedupes_builds`: two `ensure_ready` calls queue one job (patch `frappe.enqueue` and count calls) and leave one record.
  - `test_build_makes_profile_ready`: after `run_profile_build(key)`, `ensure_ready` returns `(active_graph, None)` and the graph exists in `GRAPH.LIST`.
  - `test_failed_build_marks_failed_and_retries`:
    - Patch `build_profile_graph` to raise, then run the build.
    - `status == "Failed"` and `ensure_ready` returns `FAILED_MESSAGE`.
    - It also enqueues again.
  - `test_capacity_refuses_when_all_recent`: with `max_access_profiles = 1` and one profile used just now, a second scope gets `CAPACITY_MESSAGE`.
  - `test_cleanup_drops_unmapped_and_stale`: a profile with an unknown key, and one with `last_used_at` 15 days ago, are deleted with their graphs.
  - `test_mark_all_pending`: after `mark_all_pending_and_rebuild`, every profile's status is `Pending`.
- [ ] **Step 2: Run the Frappe test command.** Expected: FAIL.
- [ ] **Step 3: Implement** the DocType, `profiles.py`, the Settings fields and button, the endpoint and the hooks. Then run `bench --site eve.localhost migrate`.
- [ ] **Step 4: Run the Frappe test command.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(access): Eve Access Profile lifecycle, limits and rebuilds`.

### Task 7: Keep profile graphs current

**Files:**
- Modify: `eve_agent/sync/refresh.py` (`_drain`)
- Modify: `eve_agent/hooks.py` (`doc_events` for layout changes)
- Modify: `eve_agent/access/profiles.py` (`on_field_layout_change`)
- Test: `eve_agent/tests/test_access_frappe.py` (class `ProfileFanOutTests`)

**Interfaces:**
- Consumes: `ready_profiles`, `mark_all_pending_and_rebuild` (Task 6); `sync_keys_on_graph(..., plan, reader)` (Task 5).
- Produces:
  - `profiles.apply_keys_to_profiles(settings, keys: list[str], plan: DataPlan) -> list[str]`, which returns the keys of profiles that failed.
  - `profiles.on_field_layout_change(doc, method=None) -> None`.

**Rules:**
- `_drain` calls `apply_keys_to_profiles` after each successful main `sync_keys_on_graph`, inside the same lock.
- If a profile's update raises, that profile is set to `Pending` (unusable, since its graph may now show too much), `frappe.log_error` is written, and its build is enqueued. Other profiles continue.
- `on_field_layout_change` is wired as `on_update` and `on_trash` for `Property Setter` (only when `doc.property == "permlevel"`), `Custom Field` (when `permlevel` is set or changed) and `DocType`. It calls `mark_all_pending_and_rebuild()` when `frappe.flags.in_install`, `in_migrate` and `in_patch` are all off. `run_schema_sync` success after migrate also calls it.

- [ ] **Step 1: Write the failing tests** (`ProfileFanOutTests`). Ready profiles exist for rahul and meera (built in `setUpClass`). Call `_drain()` directly after `queue_keys(...)`.
  - `test_new_order_reaches_sales_profile`: a submitted Sales Order for a visible customer appears in rahul's graph.
  - `test_invoice_reaches_accounts_not_sales`: a submitted Sales Invoice appears in meera's profile graph and the main graph, but not in rahul's.
  - `test_deleted_record_leaves_profile_graph`:
    - Delete a draft Sales Order created in the test, after it was drained in.
    - It is gone from rahul's graph.
    - The order-value sum in his graph is back to its earlier value.
  - `test_failure_marks_profile_pending`: patch `sync_keys_on_graph` to raise for rahul's graph only. Rahul's profile becomes `Pending`, and meera's still gets the change.
  - `test_permlevel_change_marks_pending`: saving a Property Setter with `property="permlevel"` on a Sales Order field sets every profile to `Pending`. tearDown deletes the Property Setter and rebuilds.
- [ ] **Step 2: Run the Frappe test command.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the Frappe tests plus `eve_agent.tests.test_incremental`.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(access): fan incremental changes out to profile graphs`.

### Task 8: Scope-aware chat core (pure)

**Files:**
- Modify: `eve_agent/chat/common.py`
- Modify: `eve_agent/chat/prompt.py`
- Modify: `eve_agent/chat/memory.py`
- Modify: `eve_agent/chat/tools/__init__.py`
- Modify: `eve_agent/chat/tools/utility.py` (`describe_doctype`)
- Modify: `eve_agent/chat/tools/actions.py` (`record_form`)
- Test: `eve_agent/tests/test_chat.py`

**Interfaces:**
- Consumes: `AccessScope` and `AccessScope.can_see` (Task 2).
- Produces:
  - `ToolContext` gains three fields, all defaulting to `None` so existing tests keep working:
    - `scope: AccessScope | None` (`None` means full access);
    - `can_read: Callable[[str, str], bool] | None`;
    - `on_hidden_source: Callable[[dict], None] | None`.
  - `common.visible(ctx, doctype: str) -> bool`.
  - `result()` drops sources that fail `ctx.can_read`. It also drops rows, in every query, that contain the failed source's `name` as a value. It calls `ctx.on_hidden_source({"doctype", "name", "tool"})` for each failure.
  - `tools.TOOL_NEEDS: dict[str, tuple[frozenset[str], ...]]`. A tool is offered only when every group has at least one visible DocType:
    - `top_customers` and `dormant_customers`: `{Customer}`, `{Sales Invoice, Sales Order}`;
    - `customer_profile`: `{Customer}`;
    - `top_selling_items` and `also_bought`: `{Item}`, `{Sales Invoice, Sales Order}`;
    - `item_profile`: `{Item}`;
    - `top_suppliers`: `{Supplier}`, `{Purchase Invoice, Purchase Order}`;
    - `supplier_risk`: `{Item}`, `{Supplier}`, `{Purchase Invoice, Purchase Order}`;
    - every other tool is always offered.
  - `system_prompt(config, user, memory, scope: AccessScope | None = None)`. When `scope` is set and not full, it appends exactly: `You can only see records this user may read in the ERP. Visible DocTypes: {comma-separated sorted parents}. If a question needs data outside this list, say that answering needs access to that DocType, which their role doesn't have. Never say the data doesn't exist and never guess it.`
  - `describe_doctype` and `record_form` raise `ValueError(f"You don't have access to {doctype}.")` when `not visible(ctx, doctype)`. They omit fields whose `permlevel` is not in `scope.readable[doctype]`.
  - `memory_context` and `recall` drop mentioned or derived-from records for which `ctx.can_read` is false. To filter, change their Cypher to return `{doctype, name}` maps instead of joined strings.

- [ ] **Step 1: Write the failing tests** in `test_chat.py`:
  - `test_tools_hidden_without_visible_doctypes`: with a scope reading only `Customer`, `Sales Order` and `Item`, the tool names exclude `top_suppliers` and `supplier_risk` and include `top_customers`.
  - `test_empty_scope_hides_data_tools`: with `readable={}`, every key of `TOOL_NEEDS` is absent, and `find_entity`, `run_cypher` and `recall` are present.
  - `test_result_drops_unreadable_sources_and_rows`:
    - `can_read` rejects `("Sales Invoice", "SI-1")`.
    - A `result()` call with that source and a row `{"name": "SI-1"}` returns neither.
    - `on_hidden_source` was called once.
  - `test_prompt_lists_visible_doctypes`: the prompt contains `Visible DocTypes: Customer, Item, Sales Order` and the "Never say the data doesn't exist" sentence.
  - `test_describe_refuses_and_filters_levels`: `describe_doctype("Sales Invoice")` raises `You don't have access to Sales Invoice.`. A level-1 Customer field is not in the described fields when readable is `(0,)`.
  - `test_memory_hides_unreadable_mentions`: a recall row mentioning a `Sales Invoice` that `can_read` rejects shows no such mention.
  - Existing `EXPECTED_TOOLS` tests still pass with `scope=None`.
- [ ] **Step 2: Run** `../../env/bin/python -m unittest eve_agent.tests.test_chat -v`. Expected: FAIL on the new tests.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the same command.** Expected: PASS, apart from the known live `explain_connection` error.
- [ ] **Step 5: Commit** `feat(chat): scope-aware tools, prompt, memory and safety net`.

### Task 9: Order-based fallbacks when invoices are out of scope (pure)

**Files:**
- Modify: `eve_agent/chat/tools/sales.py`
- Modify: `eve_agent/chat/tools/entities.py`
- Test: `eve_agent/tests/test_chat.py`

**Interfaces:**
- Consumes: `visible` (Task 8); the `*_NOTE` copy (Global Constraints).
- Produces: each tool chooses its Cypher by `visible(ctx, "Sales Invoice")` or `visible(ctx, "Purchase Invoice")` and adds the note when it falls back.

| Tool | Invoice Cypher (today) | Fallback when not visible |
|---|---|---|
| `top_customers` | `top_customers_cypher` | Sales Order `FOR_CUSTOMER`, `docstatus = 1`, `base_grand_total`, `transaction_date` period. `by="invoices"` counts orders. `by="outstanding"` ranks by order value and adds `OUTSTANDING_NOTE`. |
| `dormant_customers` | `DORMANT_CUSTOMERS` | last submitted order `transaction_date` |
| `customer_profile` | `CUSTOMER_SUMMARY`, `CUSTOMER_ITEMS` | order totals; items from Sales Order `CONTAINS` |
| `top_selling_items` | `top_selling_items_cypher` | Sales Order `CONTAINS`, `docstatus = 1` |
| `item_profile` | `ITEM_BUYERS`, `ITEM_MONTHLY_SALES` | buyers and months from Sales Orders |
| `also_bought` | `ALSO_BOUGHT` (`BOUGHT`) | co-ordered items via Sales Order `CONTAINS` / `FOR_CUSTOMER` |
| `top_suppliers` | `top_suppliers_cypher` | Purchase Order `FROM_SUPPLIER`, `docstatus = 1` |
| `supplier_risk` | `SINGLE_SOURCE_ITEMS` (`SUPPLIES`, `BOUGHT`) | suppliers per item from Purchase Order `CONTAINS`; revenue at risk from Sales Order `CONTAINS` when visible, else 0 |

Each fallback query keeps the same output column names as the invoice query, so the widget renders it unchanged. The one exception is `invoices`, which becomes `orders`.

- [ ] **Step 1: Write the failing tests** (`FakeGraph` with a scope without `Sales Invoice`/`Purchase Invoice`):
  - `test_top_customers_falls_back_to_orders`: the Cypher run contains `` `Sales Order` `` and not `` `Sales Invoice` ``, and `out["note"] == SALES_ORDERS_NOTE`.
  - `test_outstanding_ranking_falls_back_with_note`: with `by="outstanding"`, the note is `OUTSTANDING_NOTE`.
  - `test_every_fallback_avoids_invoices`: run each table row's tool. No Cypher passed to `FakeGraph.ro` mentions `Sales Invoice` or `Purchase Invoice`.
  - `test_invoice_scope_unchanged`: with `scope=None`, `top_customers` runs today's exact `top_customers_cypher("revenue")`.
- [ ] **Step 2: Run** `../../env/bin/python -m unittest eve_agent.tests.test_chat -v`. Expected: FAIL on the new tests.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the same command, plus a live check.** Expected: PASS. Then run the live class against rahul's profile graph from Task 6 (`EVE_TEST_GRAPH=<that graph>`): every live test except the known `explain_connection` error passes.
- [ ] **Step 5: Commit** `feat(chat): fall back from invoices to orders outside the user's scope`.

### Task 10: Wire scope into the chat request

**Files:**
- Modify: `eve_agent/api/chat.py` (`build_context`, `stream`, `locate`)
- Create: `eve_agent/access/guard.py`
- Test: `eve_agent/tests/test_access_frappe.py` (class `ChatRequestTests`)

**Interfaces:**
- Consumes:
  - `resolve_scope` (Task 3);
  - `ensure_ready` (Task 6);
  - `ToolContext.scope`, `can_read` and `on_hidden_source` (Task 8);
  - `system_prompt(..., scope)` (Task 8).
- Produces:
  - `guard.make_can_read(user: str) -> Callable[[str, str], bool]`. It is cached per turn. `User` always passes. A child DocType is checked through its `parenttype` and `parent`. Everything else is checked with `frappe.has_permission(doctype, "read", doc=name, user=user)`.
  - `guard.log_hidden_source(user: str) -> Callable[[dict], None]`, which calls `frappe.log_error(title=HIDDEN_SOURCE_TITLE, message=json.dumps({...source, "user": user}))`.
  - `chat.resolve_chat_graph(settings, user) -> tuple[AccessScope, str | None, str | None]`, returning `(scope, graph_name, message)`. A full scope gets `active_data_graph(settings)`. Any other scope goes through `ensure_ready`.
  - `stream`: when `graph_name` is `None`, it returns an event stream of one `text-delta` with the message, then `finish`. It never builds tools on another graph.
  - `locate`: returns `{}` when not ready. Otherwise it filters results through `make_can_read`.

- [ ] **Step 1: Write the failing tests** (`ChatRequestTests`, logged in with `frappe.set_user`):
  - `test_new_profile_streams_preparing_message`: for rahul with no profile, `b"".join(stream(...).response)` contains `PREPARING_MESSAGE` and `"finish"`.
  - `test_ready_profile_uses_profile_graph`: after a build, `build_context(...)` for rahul has `ctx.data.name == <rahul's active_graph>` and `ctx.scope.key == rahul_key`.
  - `test_admin_uses_main_graph`: for Administrator, `ctx.data.name == active_data_graph(settings)` and `ctx.scope.full_access`.
  - `test_failed_profile_answers_failed_message`: with the profile set to `Failed`, the stream contains `FAILED_MESSAGE`.
  - `test_locate_hides_invoices_for_sales_user`: `locate(["<an invoice name>"])` as rahul returns `{}`.
  - `test_guard_logs_hidden_source`:
    - Build a ctx for rahul, then call `result()` with a forged `("Sales Invoice", <name>)` source.
    - The source is removed.
    - An Error Log titled `HIDDEN_SOURCE_TITLE` exists.
  - `test_user_with_no_readable_doctypes`:
    - A test user with only a Chat Role (Sales Manager) and none of the synced DocTypes.
    - `build_context` succeeds after a build, and the tool list contains none of `TOOL_NEEDS`.
- [ ] **Step 2: Run the Frappe test command.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the Frappe test command.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(chat): answer each user from their access profile graph`.

### Task 11: Acceptance pass, docs and spec status

**Files:**
- Create: `eve_agent/tests/test_permission_acceptance.py`
- Modify: `README.md` (new section "Permissions", after "6. Chat with Eve")
- Modify: `docs/superpowers/specs/2026-10-17-permission-aware-chat-design.md` (status line only)

**Interfaces:**
- Consumes: everything above. No new production code. If a test fails, fix the owning task's code in this task.

- [ ] **Step 1: Write the acceptance tests.** Map spec tests 1–11 and 13 onto the seeded users, one method each, named `test_spec_01_sales_user_sees_no_accounts_data` … `test_spec_13_existing_tests`. Reuse the helpers from the earlier test classes.
  - **Test 4:** compare to `frappe.get_list` as written in the spec.
  - **Test 7:** assert no Employee nodes, no employee-linked Sales Person nodes, and that `sales_person` is still present on Customer `Sales Team` rows.
  - **Test 13:** run the pure suite with `subprocess` and assert only the known `explain_connection` error.
- [ ] **Step 2: Run** `bench --site eve.localhost run-tests --skip-before-tests --module eve_agent.tests.test_permission_acceptance`. Expected: PASS.
- [ ] **Step 3: Measure spec test 12 (no slower saves).** On a fresh site, rebuild it as in `eve-test-env/README.md` → "Rebuild from scratch", with rahul's and meera's profiles Ready before seeding. `seed_business.py` must finish within 10% of today's 12 minutes. Record the time in the commit message.
- [ ] **Step 4: Write the README section.** It covers:
  - access profiles, and who reads the main graph;
  - what Eve leaves out: shares, owner-only access, ToDos and own-employee records;
  - the "preparing" message;
  - the two new Settings and the Rebuild button;
  - "Eve shows less, never more".

  Then set the spec status to `Status: implemented · <date>`.
- [ ] **Step 5: Commit** `test(access): spec acceptance tests; docs: permissions`.
