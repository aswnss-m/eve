# Charts in Ask Eve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eve draws Frappe charts in its chat answers for trends, rankings and breakdowns, built only from the current turn's query rows.

**Architecture:**
- Every query a tool runs in a turn gets an id (`q1`, `q2`, …).
- A pure `show_chart` tool turns one of those queries' rows, after the safety net, into a chart spec. The model gives only the query id and column names.
- New `sales_trend` and `purchase_trend` tools return monthly, quarterly or total series, with the invoice → order fallback.
- The Vue widget renders a chart spec with the Desk's global `frappe.Chart`.

**Tech Stack:** Python 3.10 (`eve_agent/chat`, no Frappe imports), Cypher on FalkorDB, Vue 3 + Vite 7 widget, frappe-charts 2.0 via `window.frappe.Chart`.

**Spec:** `docs/superpowers/specs/2026-10-18-chat-charts-design.md`

## Global Constraints

- **Where to work:**
  - Use the Windows worktree `C:\Work\eve falkoredb\eve-charts` (WSL path `/mnt/c/Work/eve falkoredb/eve-charts`), on branch `feat/chat-charts`, which starts from `feat/permission-aware-chat` at 7a812da.
  - Don't touch the bench clone `~/eve-bench/apps/eve_agent`; PR #9's final fixes happen there.
- **Pure tests** run from the worktree with the bench's Python:
  - `cd "/mnt/c/Work/eve falkoredb/eve-charts" && EVE_TEST_LIVE=0 /home/fahad/eve-bench/env/bin/python -m unittest eve_agent.tests.test_charts -v`
  - The full pure suite adds `eve_agent.tests.test_falkor_pure eve_agent.tests.test_data_rows eve_agent.tests.test_business_rules eve_agent.tests.test_agent_api eve_agent.tests.test_chat eve_agent.tests.test_incremental eve_agent.tests.test_access`.
- **Live graph checks** use FalkorDB at `redis://127.0.0.1:6500`, graph `eve_localhost_data`. Run them with the worktree's code; no Frappe is needed.
- **Building the widget:** in Windows, `npm install && npm run build` in `C:\Work\eve falkoredb\eve-charts`, using Node 22. WSL's Node 18 can't run Vite 7. Commit `eve_agent/public/dist/`.
- **Chart library:** only the Desk's global `frappe.Chart`; no new npm dependency.
- **Values in charts:**
  - Only values copied from query rows.
  - The one computed number allowed is the donut's "Other" sum.
  - A chart never comes from a query that isn't from the current turn.
- **Limits:** line at most 60 points; bar at most 20 bars; donut or pie at most 8 parts, with parts past the 7th summed into "Other"; at least 3 values, otherwise refused.
- **Commits:** no Claude attribution of any kind. Python 3.10 syntax, tabs, double quotes, ruff line length 110.
- **Exact copy:**
  - The prompt rule: `Charts: after a data tool returns a time series, a ranking or a breakdown with at least 3 values, call show_chart with that query's id (for example q1), the label column and the value column(s): line for trends, bar for rankings, donut for parts of a whole. One chart per answer unless asked. Still write the one-line summary. Never put numbers in show_chart. If the user asks for just the table, don't chart.`
  - The partial-period caption: `* {label} to date`.

## Review Focus

1. **A chart that cites a query from an earlier turn**, or a made-up id, gets a clear error the model can recover from, never a chart. *Test:* Task 1 `test_unknown_query_id_is_refused`.
2. **A value column with a null in it**, such as order-fallback `outstanding`, is refused, not drawn as 0. *Test:* Task 1 `test_null_values_are_refused`.
3. **A trend with no rows in the period** returns no rows and no chart, and doesn't crash. *Test:* Task 2 `test_empty_period_returns_no_rows`.
4. **A donut with more than 8 groups** keeps the 7 largest, and "Other" equals the exact sum of the rest. *Test:* Task 1 `test_donut_groups_the_rest_into_other`.
5. **An old chat reloaded from history** redraws from the stored spec without re-querying. *Check:* Task 3 manual check 4.

---

### Task 1: Query ids and the `show_chart` tool

**Files:**
- Modify: `eve_agent/chat/common.py`: `QueryRun` gets `id: str = ""`, and `as_dict()` includes `"id"`. `ToolContext` gets `runs: dict[str, QueryRun]`. `result()` assigns ids.
- Create: `eve_agent/chat/tools/charts.py`
- Modify: `eve_agent/chat/tools/__init__.py`, to register `chart_tools(ctx)`.
- Test: `eve_agent/tests/test_charts.py` (new)

**Interfaces:**
- Produces:
  - **Query ids:** `result()` gives every query passed in the next `q{n}` for this turn (n counts from 1 per ToolContext) and stores it in `ctx.runs[id]`.
    - An old id is overwritten, because `graph_schema` reuses cached runs across turns.
    - This happens after the safety net has filtered the query's rows.
  - **The builder:** `build_chart(run: QueryRun, type: str, x: str, y: list[str], title: str, today: date) -> dict` returns `{"type", "title", "labels", "datasets": [{"name", "values"}], "unit", "partial_last"}`. It raises `ValueError` with a message the model can act on.
  - **Currency detection:** `unit` is `"currency"` when every `y` name matches `amount|revenue|spend|outstanding|total|paid|value`, otherwise `"number"`.
  - **Partial period:** `partial_last` is true when the last label equals today's `YYYY-MM` or `YYYY-Qn`.
  - **The tool:** `show_chart(query: str, type: Literal["line","bar","donut","pie"], x: str, y: list[str] (1–4 items), title: str)`. It returns `{"chart": <spec>, "queries": [], "sources": [], "as_of": ...}` and is always offered (not in `TOOL_NEEDS`).

- [ ] **Step 1: Write the failing tests** in `test_charts.py`. Use `make_ctx` and `FakeGraph` from `test_chat.py`, and runs built as `QueryRun(title, cypher, params, rows, ms)`.
  - `test_result_assigns_query_ids_in_order`: two `result()` calls with one run each give ids `q1` and `q2`, each in `ctx.runs` and in the output's `queries[i]["id"]`.
  - `test_line_chart_copies_rows`: rows `[{"period": "2026-01", "amount": 543153}, {"period": "2026-02", "amount": 759040}, {"period": "2026-03", "amount": 825748}]` give `labels == ["2026-01", "2026-02", "2026-03"]`, `datasets == [{"name": "amount", "values": [543153, 759040, 825748]}]` and `unit == "currency"`.
  - `test_unknown_query_id_is_refused`: `show_chart("q9", …)` raises `ValueError` mentioning `q9`.
  - `test_unknown_column_is_refused`: `y=["nope"]` raises `ValueError`.
  - `test_null_values_are_refused`: one row with `"amount": None` raises `ValueError`.
  - `test_too_few_values_are_refused`: 2 rows raise `ValueError`.
  - `test_donut_groups_the_rest_into_other`: 12 parts with values 12…1 give 8 labels, the last being `"Other"` with value `5+4+3+2+1 = 15`.
  - `test_partial_last_period`: with `today = date(2026, 10, 18)`, a last label of `"2026-10"` gives `partial_last is True`, and `"2026-09"` gives `False`.
  - `test_rows_dropped_by_the_safety_net_are_not_charted`: `can_read` rejects `("Customer", "Apex")`. A ranking run with a row naming Apex goes through `result()`, and `show_chart` on it has no `"Apex"` label.
- [ ] **Step 2: Run them.** Expected: FAIL (import error for `eve_agent.chat.tools.charts`).
- [ ] **Step 3: Implement.** `show_chart` reads `ctx.runs` and passes `today` from `datetime.now(timezone.utc).date()`.
- [ ] **Step 4: Run** `test_charts` and the full pure suite. Expected: PASS, and existing tests unchanged apart from queries now carrying `id`.
- [ ] **Step 5: Commit** `feat(chat): query ids and a show_chart tool built from query rows`.

### Task 2: Trend tools and the prompt rule

**Files:**
- Create: `eve_agent/chat/tools/trends.py`
- Modify: `eve_agent/chat/tools/__init__.py`, for `TOOL_NEEDS` and registration.
- Modify: `eve_agent/chat/prompt.py`, for the charts rule.
- Test: `eve_agent/tests/test_charts.py`

**Interfaces:**
- Consumes:
  - `invoice_filter(v)`, `order_filter(v)`, `SALES_ORDERS_NOTE` and `PURCHASE_ORDERS_NOTE` from `eve_agent/chat/tools/sales.py`;
  - `visible(ctx, doctype)` and `result()` from `common.py`.
- Produces:
  - **`sales_trend`:** `sales_trend(date_from: str | None, date_to: str | None, interval: Literal["month","quarter","total"]="month", group_by: Literal["item_group","territory","customer_group"] | None = None)`.
    - Columns: `period` and `amount`, plus `documents` without `group_by`, or `group` with it.
    - Rows are ordered by period, then amount descending.
    - It reads Sales Invoices when visible, otherwise Sales Orders with `SALES_ORDERS_NOTE`.
    - `item_group` sums `CONTAINS.amount` over `Item.item_group`. The other two group on the document's own field.
  - **`purchase_trend`:** the same arguments, except `group_by` is `"item_group"` or `"supplier_group"`. It reads Purchase Invoices, falling back to Purchase Orders with `PURCHASE_ORDERS_NOTE`.
  - **Periods:** month is `substring(date, 0, 7)`; quarter is `YYYY-Qn`; total is the literal `"total"`.
  - **`TOOL_NEEDS`:** `sales_trend` uses the customer sales gate; `purchase_trend` uses `({"Supplier"}, {"Purchase Invoice", "Purchase Order"})`.
  - **The prompt:** `system_prompt` appends the exact charts rule from Global Constraints.

- [ ] **Step 1: Write the failing tests**, using `FakeGraph` and `make_ctx` with scopes, as in `test_chat.py`.
  - `test_sales_trend_reads_invoices_by_month`: the Cypher contains `` `Sales Invoice` `` and `substring(si.posting_date, 0, 7)`, and the note is absent.
  - `test_sales_trend_falls_back_to_orders`: with a scope without Sales Invoice, the Cypher has `` `Sales Order` ``, no `Sales Invoice`, and `out["note"] == SALES_ORDERS_NOTE`.
  - `test_total_by_item_group_feeds_a_donut`: with `interval="total", group_by="item_group"`, the Cypher returns `group` and has no month substring.
  - `test_purchase_trend_falls_back_to_purchase_orders`: as the sales fallback test, with `PURCHASE_ORDERS_NOTE`.
  - `test_empty_period_returns_no_rows`: `FakeGraph` returns no rows, and `out["queries"][0]["rows"] == []` with no exception.
  - `test_prompt_has_charts_rule`: `system_prompt(...)` contains the exact rule string.
  - `test_trend_tools_gated`: with a scope reading neither Supplier nor any purchase document, `purchase_trend` isn't offered.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `test_charts` and the full pure suite. Expected: PASS.
- [ ] **Step 5: Live check.** A small script in the worktree builds a real `Graph(redis://127.0.0.1:6500, "eve_localhost_data")` ctx (`scope=None`).
  - Run `sales_trend("2026-01-01", "2026-12-31")`.
  - Its `amount` per month must equal the spec's numbers: Jan 543,153; Feb 759,040; Mar 825,748; Apr 571,993; May 695,875; Jun 790,885; Jul 525,785; Aug 1,206,627; Sep 820,507; Oct 111,055. Use those, or the current graph's values if data has changed, and say so.
  - Then run `show_chart` on its query (`line`, x=`period`, y=`["amount"]`).
  - Record the output in the commit message body.
- [ ] **Step 6: Commit** `feat(chat): sales and purchase trend tools; prompt asks for charts`.

### Task 3: The chart card in the widget

**Files:**
- Create: `frontend/eve_chat/ChartCard.vue`
- Modify: `frontend/eve_chat/Message.vue`, to render `ChartCard` for tool parts whose output has `chart`, before the `ToolStep` branch.
- Build: `eve_agent/public/dist/eve_chat.js` and `eve_chat.css` (`npm run build`).

**Interfaces:**
- Consumes: the chart spec from Task 1, `output.chart = {type, title, labels, datasets, unit, partial_last}`.
- Produces: `ChartCard.vue` with prop `part`.
  - **On mount:** `new frappe.Chart(el, {type, title, height: 230, data: {labels, datasets}, ...})`.
  - **Line charts:** `lineOptions: {regionFill: 1, dotSize: 4}`.
  - **Axes:** `axisOptions: {xIsSeries: type === "line" ? 1 : 0, shortenYAxisNumbers: 1}`.
  - **Tooltips:** `tooltipOptions.formatTooltipY` uses `format_currency(v, frappe.boot.sysdefaults.currency, 0)` for `unit === "currency"`, and `format_number(v)` otherwise.
  - **Donut and pie:** `truncateLegends: 1`.
  - **Partial period:** when `partial_last` is set, append `*` to the last label and show the caption `* {label} to date`.
  - **Buttons:** "Show data" toggles a small labels/values table; "Download" calls `chart.export()`.
  - **On unmount:** the chart is destroyed.

- [ ] **Step 1: Implement** `ChartCard.vue` and the `Message.vue` branch. Follow `ActionCard.vue`'s style conventions: scoped CSS with `eve-` class prefixes and Desk CSS variables.
- [ ] **Step 2: Build** in Windows: `npm install && npm run build` in the worktree. Expected: `eve_agent/public/dist/eve_chat.js` is rebuilt with no errors.
- [ ] **Step 3: Commit** `feat(chat): render charts in the Ask Eve panel with Frappe charts`, including the rebuilt `dist`.
- [ ] **Step 4: Manual check.** This runs after PR #9's fixes land; the controller pulls `feat/chat-charts` into the bench and restarts `bench start`. With an LLM key set, record the following with screenshots:
  1. "Sales trend for this year" → line chart; the hover total matches "Show data"; Download saves an SVG.
  2. "Top 10 customers this year" → bar chart.
  3. "Sales by item group this year" → donut, with the legend inside the panel.
  4. Reloading the Desk redraws the charts from history.
  5. "Total sales in August" → no chart.
  6. Dark theme stays readable.
