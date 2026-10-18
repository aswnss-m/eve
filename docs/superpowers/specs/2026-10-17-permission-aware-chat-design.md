# Permission-aware Eve: answer only from what the user can read

Status: ready for approval · 2026-10-17

## Intent

Eve copies ERP records into FalkorDB and answers questions from that graph. Today every user with a Chat Role reads the whole graph. A Sales Manager can ask about invoices, payments, GL postings or employee details, and `run_cypher` returns anything. Those answers also go to the LLM provider.

**Eve must follow Frappe's permissions. It answers each user only from data that user can read in Frappe.** For example, a sales person must not learn what only the accounts team can see. The rule covers totals, counts, rankings, paths between records, comments and field values, as well as which records appear.

Decided by the product owner:
- Eve has only the permissions the logged-in user has.
- A role-to-proxy-user mapping was considered and **dropped**.
- The approach below is accepted for the written design.

## Goals

1. For any chat user, everything Eve shows or sends to the model is computed from records and fields that user can read in Frappe.
2. Totals and rankings (best customers, best-selling items, purchase totals) are computed only from documents the user can read. Pre-computed graph totals must not leak hidden documents.
3. All existing tools keep working for every user, including the free-form `run_cypher`, on the data that user may see. When a user lacks the documents a tool normally uses, the tool falls back to documents they can see, or says the answer needs access they don't have.
4. No new configuration that admins must keep in step with Frappe. Frappe's roles, DocPerms, User Permissions and field levels are the only source of truth.
5. Permission changes in Frappe (roles added or removed, User Permissions, DocPerm edits) take effect from the next question.

## Non-goals (first version)

In these cases Eve shows **less** than Frappe would, never more:
- Documents a user can read only because they were shared with them (DocShare).
- DocPerms that grant read only "if owner".
- DocTypes whose read access depends on custom permission code (a `permission_query_conditions` or `has_permission` hook) that differs per user. Exceptions are the special cases below. This excludes ToDo, and probably some HRMS DocTypes on sites with HRMS, for users who are not full-access.
- Records a user can read only through an own-record User Permission, such as their own Employee record and the documents linked to it. See the own-record rule in section 2.

Also out of scope:
- A graph per individual user.
- Field masking (a Frappe v16 feature).
- The existing `explain_connection` failure on FalkorDB 4.22 (undirected `shortestPath`). That is a separate fix.

## Anti-goals: failure even if it "works"

- Eve reveals anything the user cannot open in Frappe. That includes through a total, a count, a path, a comment, a field value, or the wording of the model's answer.
- On any error or while data is being prepared, Eve falls back to the full graph.
- Ordinary document saves get slower. The `doc_events` hook must stay as cheap as today, and fan-out work happens in background jobs.
- Admins have to maintain a second permission setup.

## Constraints

- Frappe v15 and ERPNext v15 (HRMS optional), Python 3.10, FalkorDB v4.22.x. No new external services.
- The chat runs in the Frappe web process (`eve_agent.api.chat.stream`). Graph builds run as background jobs.
- Eve must never change Frappe permission settings.
- Record writes (`propose_record` → `eve_agent.api.actions.apply`) already run as the real user with Frappe's own checks. That stays.

## Design

### 1. Access profiles

An **access profile** is the set of things a user can read, as Frappe computes it, for the DocTypes in Eve's data plan (`load_plan`).

Two users have the same profile when, for every planned DocType, all of these match:
- whether they can read it through a role permission that is not "if owner";
- which permlevels they can read (`meta.get_permlevel_access("read")`; child tables follow the parent's access);
- which User Permissions apply to it (allow, for_value, and the applicable_for / apply_to_all_doctypes scope);
- for DocTypes with a `permission_query_conditions` hook, the condition the hook returns for them.

The profile **key** is a stable hash of those outcomes, computed from Frappe at question time. It is not computed from role names. Different role combinations that read the same things share one profile. Any change in Frappe permissions that changes what a user can read changes their key, so the next question uses a different profile.

**Own-record User Permissions are left out of the key** (decided by the product owner).
- ERPNext gives every user linked to an Employee a User Permission for their own Employee record (`Employee = <their employee>`, apply to all DocTypes). It does this automatically when `create_user_permission` is on, which is the default.
- More generally, this covers any User Permission whose value is a record linked to that user, such as the Employee whose `user_id` is them.
- These differ for every user. Keying on them would give each person their own graph.
- Instead, the DocTypes they narrow are handled by the own-record rule in section 2, and Company or Territory User Permissions stay in the key as normal.

The key covers permission outcomes only, not the field layout. Two kinds of change keep keys the same but rebuild the affected profiles in place:
- a field moving to another permlevel, or a field being added or removed (after `bench migrate` or a Customize Form / Custom Field / Property Setter change);
- a change to Eve's own data settings.

The existing schema sync, which runs after migrate, is the natural trigger. When a change in the plan does change keys, profiles that no user maps to any more are dropped (section 3).

A user is **full-access** when their profile can read every planned DocType at every permlevel, with no restricting hook conditions and no User Permissions at all. Own-record ones count here, even though they are left out of the key. Administrator always is. Full-access users read the existing main data graph. Everyone else reads their profile's graph.

### 2. What goes into a profile graph

A profile graph is built by the existing pipeline (`build_data_graph` / `import_data` / `apply_business_rules`) with a filtered plan and permission-aware fetchers.

- **DocTypes:** only those the profile can read (role permission, not owner-only). DocTypes with a user-specific hook condition are left out, unless a special case below applies.
- **Fields:** only fields at readable permlevels. The standard fields (`name`, `doctype`, `docstatus`, `owner`, `creation`, `modified`, `title`) are always kept for records in the graph.
- **Records:** only records the profile can read through role permissions **and** its User Permissions. Shares and owner-only access must not count, even if the member used to evaluate the profile has shares. The cap of the newest N records per DocType applies to the visible records.
  - User Permissions follow **Frappe's own semantics exactly**. Each document is filtered by its *own* Link fields, as `DatabaseQuery` does, with System Settings' strict-user-permissions mode respected.
  - Filtering is not transitive. A Sales Order is in the graph if its own `territory` is permitted, whether or not its customer is.
  - The reference is what `frappe.get_list` returns for the user, minus records visible only through shares or owner-only access, and minus anything section 2's exclusions leave out.
- **Own-record rule** (decided by the product owner). Suppose a profile's members have own-record User Permissions on a DocType X, for example Employee:
  - X itself is left out of the shared profile graph, because each member would see only their own record.
  - Every DocType that the permission narrows (one with a Link to X that the permission applies to, e.g. Sales Person and Timesheet for Employee) keeps only records whose links to X are all empty. Under strict user permissions, it keeps none.
  - Eve therefore shows less, never more. A sales person does not see their own Employee record through Eve, nor Sales Person records tied to employees, so `MANAGED_BY` / `SOLD_TO` edges to those are missing. The `sales_person` name on a Customer's Sales Team child rows is still there.
- **Child rows:** only those of parents in the graph, with the parent's field rules applied.
- **Links:** edges only between records that are both in the graph (`import_data` already drops edges to records it did not load). Link values stay as properties, as the user sees them on the form.
- **Business relationships and totals:** the business rules run inside each profile graph on its own contents. A profile without Sales Invoices therefore gets no invoice-derived `BOUGHT`, `SOLD_TO`, `BILLED_BY` or `PAID_BY` edges.
- **Special cases** among Eve's extra DocTypes:
  - **Comment:** included only when its reference document is in the profile graph. This matches Frappe's Comment permission rule.
  - **User:** every System User, with only the allow-listed fields (`FIELD_ALLOWLIST`).
    - Frappe already shows any desk user every enabled user's email and full name, through the @mention search (`frappe.desk.search.get_names_for_mentions`). That holds even though only System Manager can read the User DocType.
    - Keeping all of them, rather than only owners and comment authors, also keeps `CREATED_BY` edges correct during incremental updates.
    - *(Designer's call; amended 2026-10-18 while planning. See section 7.)*
  - **ToDo:** left out of every non-full profile in v1, because Frappe shows each person only their own ToDos.

### 3. Building and lifecycle

- **Profile records:** each profile is stored as a record that admins can see, e.g. a new DocType **Eve Access Profile**. It holds:
  - the key, plus a readable summary (visible DocTypes and any User Permissions);
  - the active and standby graph names (blue/green, as the main graph);
  - status (Building / Ready / Failed), last built, last used, size;
  - member count, and last error.
- **Lazy build:** a profile is built the first time one of its members asks a question. It runs as a background job on the `long` queue. Until it is Ready, that user's chat answers with a short "Eve is preparing data for your access level (first time only)" message. It never falls back to the main graph. When the build finishes, the user is notified in real time so they can ask again.
- **Rebuilds:** the nightly rebuild also rebuilds every profile used in the last *N* days. Changing Eve's data settings (modules, exclusions, extra DocTypes, record cap) marks every profile for rebuild, as for the main graph.
- **Clean-up and limits:**
  - Profiles unused for *N* days (Setting, default 14) are deleted, together with their graphs.
  - A profile that no enabled user with a Chat Role maps to any more is deleted at once, whatever its last-used time. It can no longer be reached. This is checked when a profile is built and in the daily clean-up.
  - There is a maximum number of profiles (Setting, default 20).
  - When a new profile would exceed the maximum, the least recently used profile that has been idle for at least 24 hours is evicted.
  - If none can be evicted, the user is told that Eve is at capacity, and an Error Log tells the admin.
- **After a permission change:** an edit to a DocPerm or role can give many users a new key at once. Those users see the "preparing" reply until their new profiles are built. That is acceptable in v1. Builds should not wait behind long jobs; for example, they can use their own queue. *(Designer's call.)*

### 4. Keeping profile graphs current

- The `doc_events` hook (`queue_record`) does not change.
- The drain job applies each batch of changed keys to the main graph as today, then to every Ready profile graph.
- For a profile graph, the "fetch by names" step uses that profile's visibility. A record the profile can no longer see is treated as missing and removed. This is the existing delete path in `sync_keys`. A newly visible record is added.
- The scoped business rules run per graph.
- The 5-minute sweep and the catch-up after a rebuild work for profile graphs as they do for the main graph.
- A profile rebuild must not lose changes made during the build, just as the main rebuild doesn't.

### 5. The chat request

In `eve_agent/api/chat.py` (`stream`, `locate`) and `build_context`:

1. **Pick the graph:** resolve the user's profile. Full-access users get the main graph, everyone else gets the profile graph. If that graph isn't Ready, start or await its build and answer with the preparing message. Never use a broader graph.
2. **Give the tools the user's scope:** `ToolContext` carries the user's scope (visible DocTypes, and visible fields per DocType). Every tool reads only the user's graph.
3. **Schema tools:** `graph_schema` already reads labels and relationships from the data graph, so it reflects the profile automatically. `describe_doctype` and `record_form` read the schema graph, so they must limit results to DocTypes in scope and fields at readable permlevels. `record_form` additionally keeps today's write-permission check.
4. **Tools:** `build_tools(ctx)` hides tools whose every DocType is outside the scope. For example, supplier tools are hidden from a user who can't read Supplier or any buying document. The remaining tools follow the fallback rules in section 6.
5. **System prompt:** it lists the DocTypes the user can see. It tells the model that when a question needs data outside that list, it should say the answer needs access the user doesn't have, and name the DocType. It must not say the data doesn't exist, and it must not guess.
6. **Safety net:** before any tool result goes to the model or the widget, every cited source is checked.
   - A document is checked with Frappe's own `frappe.has_permission(doctype, "read", doc=name, user=user)`. This check is broader than the profile rules (it also counts shares and owner access), so it never rejects a correct record. It only catches records that should not be in the graph.
   - A child-table row is checked through its parent document.
   - A User source always passes, per the section 2 rule.
   - A failing source and the result rows naming it are removed, and an Error Log ("Eve: result outside the user's permissions") is written, because that means a profile graph bug.
   - `locate` uses the user's graph and the same check.
7. **Memory:** `recall` and `memory_context` drop mentioned records the user can't currently read. Saved conversation text and insights stay, since that user was shown them before. *(Builder's call.)*
8. **Cypher log:** **Eve Cypher Log** records which graph (profile) answered.

### 6. Tools when the usual documents are missing

The general rule is to fall back to the nearest document the user can see and say so in the tool's `note`. When nothing suitable is visible, return a note naming the DocType the answer needs. Never raise an error just because a DocType is missing.

| Tool | Normally uses | Without Sales Invoice / Purchase Invoice |
|---|---|---|
| `top_customers`, `dormant_customers`, `customer_profile` | Sales Invoice (revenue, outstanding, last invoice) | Submitted Sales Orders (order value, last order). Outstanding is omitted. Note: "Based on sales orders; invoices need Accounts access." |
| `top_selling_items`, `item_profile`, `also_bought` | Sales Invoice `CONTAINS`, `BOUGHT` | Sales Order `CONTAINS` (already in the graph). Co-purchase is computed from orders. |
| `top_suppliers`, `supplier_risk` | Purchase Invoice | Submitted Purchase Orders |
| `who_knows`, `decision_trail`, `find_entity`, `explain_connection`, `run_cypher` | anything | unchanged; they see only the profile graph |

Fallbacks apply only when the invoice DocType is outside the user's scope. They do not apply when it is in scope but has no matching records.

### 7. Admin surface

Eve Agent Settings gains:
- **Max access profiles** (default 20);
- **Drop unused access profiles after (days)** (default 14);
- a link to the Eve Access Profile list;
- a **Rebuild access profiles** button.

The existing **Chat Roles** still decide who may chat at all.

Calls made by the designer, which the builder may revisit if the code shows a better way:
- the profile record is a DocType;
- profiles are built lazily, with a "preparing" reply;
- defaults of 14 days and 20 profiles;
- all System Users are included, with allow-listed fields only, matching Frappe's @mention list;
- memory keeps text but filters record mentions;
- Eve says "needs access" rather than pretending data doesn't exist;
- User Permissions follow Frappe's non-transitive, own-field semantics;
- the key covers permission outcomes only, and field-layout or plan changes rebuild profiles in place;
- profiles no user maps to are dropped at once;
- a "preparing" window after DocPerm edits is acceptable in v1;
- the safety net uses `has_permission`, with a User exception and child rows checked through their parent.

Decided by the product owner:
- follow the logged-in user's Frappe permissions;
- no proxy users;
- shared profile graphs, leaving out own-record data (Employee, and documents tied to an employee) instead of building per-user graphs.

## Acceptance tests

These use the test site at `eve.localhost` (WSL `~/eve-bench`), whose seeded users are:
- `priya` — Sales Manager + Sales User
- `rahul` and `ananya` — Sales User
- `vikram` — Purchase Manager + Purchase User + Stock User
- `meera` — Accounts Manager + Accounts User
- `arjun` — Stock Manager + Stock User

All are `@eve-demo.local`.

1. **Sales user sees no accounts data** (rahul):
   - The profile graph has no nodes labelled Sales Invoice, Payment Entry, Journal Entry, GL Entry, Purchase Invoice or Employee.
   - It has no Comment referencing those DocTypes, and no edge derived from them.
   - `run_cypher` with ``MATCH (n:`Sales Invoice`) RETURN count(n)`` returns 0.
   - `locate` of an invoice name returns nothing.
   - `top_customers` returns an order-based ranking with the note.
2. **Accounts user sees invoices** (meera): invoice-based answers match the main graph's numbers for the same query.
3. **Shared profile:** rahul and ananya, with identical access, share one profile and one build. Their own-employee User Permissions differ, but those are left out of the key.
4. **User Permission:**
   - Create a second Territory, "South", and create new customers in it with orders placed after the change.
   - Give ananya a User Permission Territory = South.
   - For every DocType in her scope, the record names in her profile graph equal `frappe.get_list` for her. Documents whose own `territory` is South, or empty under non-strict mode, are included; others are not.
   - `top_customers` totals equal a SQL sum over the same `get_list` result.
   - rahul's answers are unchanged.
5. **Field level:**
   - Move a Sales Order field to permlevel 1, without read for Sales User.
   - After the automatic in-place rebuild, the field is absent from the sales profile graph and present in the main graph.
   - The profile key is unchanged.
6. **Matches Frappe in general:** for each seeded user and each DocType in their scope, the record names in their graph equal `frappe.get_list` for that user, adjusted as described in section 2.
7. **Own-record rule:** the sales profile graph has:
   - no Employee nodes;
   - no Sales Person nodes linked to an employee, and no `MANAGED_BY` / `SOLD_TO` edges to them;
   - Sales Person records with no employee, if any exist.
   
   Customers' Sales Team child rows still carry `sales_person`. The main graph is unchanged.
8. **Incremental:**
   - A new Sales Order for a visible customer appears in the sales profile graph within seconds.
   - A new Sales Invoice appears in meera's profile and the main graph, but not in rahul's.
   - Moving a customer out of ananya's permitted territory removes it from her graph.
9. **Role change:** giving rahul Accounts User makes his next question use a different profile that includes invoices. Removing it switches him back.
10. **Shares don't leak:** sharing one Sales Invoice with rahul does not put it into any sales profile graph. It is still openable in the Desk.
11. **Safety net:** a forged out-of-scope source injected into a tool result is removed before it reaches the model, and an Error Log is written.
12. **No slower saves:** `queue_record` is unchanged. On a fresh test site, `eve-test-env/scripts/seed_business.py` (842 submitted documents, about 12 minutes today) finishes within 10% of today's time, with the worker running and two profile graphs Ready.
13. **Existing tests:** the existing unit and Frappe tests still pass, plus new pure tests for the profile key and plan filtering.

## Left to the builder

- Code layout, and how profile records and graph names are stored and named.
- How visibility is evaluated without counting shares or owner-only access. One option is to build Frappe's User Permission match conditions for the profile directly instead of calling `frappe.get_list` as a member with shares.
- Locking granularity between the drain job and profile rebuilds.
- Job queues and the exact real-time / widget behaviour for "preparing".
- The exact fallback Cypher.
- Performance tuning of the fan-out, provided anti-goal 3 holds.
