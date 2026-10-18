# Charts in Ask Eve

Status: approved · 2026-10-18 ("build charts in the chat, use Frappe default charts so less work is required")

**Lean scope** (designer's calls after approval, following "less work"):
- Drop `compare_previous_year`; the test data covers only one year.
- The trend tools take `interval = month | quarter | total`, where `total` means one row per group, for donuts.
- Download is SVG only, via `chart.export()`.
- The chart card has no "Show queries" button, because the data tool's step already shows its queries.
- These Frappe Charts limits, seen in the rendered sample on 2026-10-18, are accepted as they are:
  - Y-axis numbers are shortened to K/M, not lakh. Tooltips use the site's currency formatting.
  - Long bar labels are truncated by Frappe Charts.
  - The current partial month is marked with a `*` and a caption, not a dashed segment.
- The donut legend is kept inside the panel with Frappe Charts' legend truncation.

## Intent

When an answer is a trend, a ranking, a breakdown or a comparison, Eve should draw a chart in the chat answer instead of showing only a table.

The reference case is "Sales trend for this year". Eve answers with one or two sentences of headline numbers ("₹68.5 lakh so far, peaking in August"), then a line chart:
- one point per month, with a light shaded area under the line;
- hover shows the month's total;
- the unfinished current month is marked;
- below the chart: "Show data", "Show queries" and "Download".

This is mockup **A** in the brainstorm (`sales-trend-chart.html`).

Decided by the product owner:
- Charts for all kinds of trends and other kinds of reports.
- Option A is the look for trends.
- The chart library is **Frappe's own charts** (`frappe.Chart`, frappe-charts 2.0, already loaded on every Desk page), not shadcn.

## Goals

1. Each kind of answer gets its chart:

   | Answer | Chart |
   |---|---|
   | Trends (any value over time) | line, with the area shaded |
   | Rankings | bar |
   | Parts of a whole | donut |
   | Comparisons | two or more lines, or grouped bars |

2. Every number in a chart comes from a query Eve ran in the same turn. The model never types the numbers. Charts therefore follow the user's Frappe permissions automatically, through the permission-aware graphs and the safety net.
3. Eve decides on its own when a chart helps (the data has a fitting shape and at least 3 values). Users can also ask for "a chart" or "just the table".
4. Eve gets trend tools for sales and purchases over time, with the same invoice → order fallback as the other tools.

## Non-goals (v1)

- Other chart libraries (shadcn, Unovis, Chart.js, ECharts).
- Drill-down (clicking a bar to list its invoices).
- Dashboards, or pinning a chart outside the chat.
- Real-time updates.
- Charts the user can edit.
- Horizontal bars (frappe-charts has none).

## Anti-goals: failure even if it "works"

- A chart shows a number that differs from the query's rows, or any number the user cannot see in Frappe.
- The model invents, rounds or re-types numbers into a chart.
- Charts break the Desk's styling or make the chat noticeably slower.
- Charts appear where they add nothing, such as one or two values.

## Constraints

- **Widget:** the chat widget is Vue 3 (`frontend/eve_chat/`), built with Vite 7 into a committed `eve_agent/public/dist/`.
- **Building it:** Vite 7 needs Node 20.19 or newer.
  - This machine's WSL has Node 18, so it cannot build the widget.
  - Windows has Node 22, so build there (`npm install && npm run build` in the app folder). `npm run build` touches `hooks.py` so the dev server reloads.
- **Library:** only `window.frappe.Chart`, with no new npm dependency. Usable types are line (`regionFill`, `spline`), bar (also stacked), donut, pie and percentage. `chart.export()` saves an SVG.
- **Base branch:** this builds on the permission work (`feat/permission-aware-chat`).
  - Tool results pass the safety net in `chat/common.py:result()`.
  - Sales and purchase tools fall back from invoices to orders when invoices are outside the user's scope.
- **Code:** Python 3.10, and `eve_agent/chat/` has no Frappe imports.
- **History:** rows from earlier turns are stripped before they go back to the model (`chat/loop.py`), so a chart can only use queries from the current turn.

## Design

### 1. Query ids

Every query run in a turn gets a short id in execution order (`q1`, `q2`, …). The id is shown in that tool's output next to the query, so the model can refer to it.

### 2. A `show_chart` tool

The model calls `show_chart` after the data tool:

```
show_chart(query: str, type: "line" | "bar" | "donut" | "pie", x: str, y: list[str], title: str)
```

- **Inputs:** `query` is an id from this turn. `x` is the label column. `y` is one or more numeric columns for line and bar, and exactly one for donut and pie.
- **How the chart is built:** the server builds it from that query's rows **as they stand after the safety net**, so rows naming a hidden record are already gone.
  - The labels are the `x` values.
  - There is one dataset per `y` column.
  - Nothing else goes in. The model passes column names only, never values.
- **Refused calls:** each of these returns a tool error the model can recover from, never a partial chart:
  - an unknown query id or column;
  - a non-numeric `y` value;
  - too many values to read (the builder sets the limits, e.g. about 60 points on a line or 20 bars);
  - a donut or pie with more than 8 parts. Parts past the 7th are summed into "Other", which is the only computed number allowed.
- **Output:** `{"chart": {"type", "title", "labels", "datasets": [{"name", "values"}], "unit": "currency" | "number", "partial_last": bool}, "queries": [], "sources": []}`.
- **Partial period:** `partial_last` is true when the last label is the current month or quarter, so the widget can mark it as "to date".

### 3. Trend tools

- **`sales_trend(date_from?, date_to?, interval="month" | "quarter" | "total", group_by=None)`**
  - Sums submitted, non-return Sales Invoices (`base_grand_total`, by `posting_date`) per period.
  - Without Sales Invoice access, it falls back to submitted Sales Orders (`transaction_date`) with the existing `SALES_ORDERS_NOTE`.
  - Columns: `period` (YYYY-MM or YYYY-Qn, or "total"), `amount` and `documents`.
  - `group_by` (`item_group`, `territory` or `customer_group`) adds a `group` column: one row per period and group. With `interval="total"`, that gives one row per group, which feeds a donut.
- **`purchase_trend(...)`** is the same over Purchase Invoices, falling back to Purchase Orders.
- **Gating:** both are gated in `TOOL_NEEDS` like the existing tools. They need the party DocType and an invoice or order DocType.

### 4. Prompt

The system prompt gains a short rule:
- After a data tool whose result is a series over time, a ranking or a breakdown with at least 3 values, call `show_chart` on that query.
- Use one chart per answer unless asked for more.
- Still write the one-line summary.
- Never put numbers in `show_chart`.
- If the user asks for "just the table", don't chart.

### 5. Widget

- **Where it renders:** a new `ChartCard.vue`, rendered by `Message.vue` for tool parts whose output has `chart`, the same way `ActionCard` handles `action`.
- **Drawing:** `new frappe.Chart(element, {...})`.
  - Line charts use `lineOptions: {regionFill: 1}`, which is option A's look.
  - Values are formatted with Frappe's currency formatting (`format_currency` with the site currency) for `unit: "currency"`, including in tooltips.
  - When `partial_last` is set, the last label gets a "(to date)" marker and a short caption under the chart.
- **Buttons under the chart:**
  - "Show data": a small table of labels and values.
  - "Show queries": the existing query detail.
  - "Download": `chart.export()`, which saves an SVG.
- **Behaviour:**
  - The chart resizes with the panel and is destroyed on unmount.
  - It is readable in the Desk's light and dark themes.
  - Reloaded chat history redraws charts from the stored tool output, without re-querying.

### 6. Permissions

Charts are built only from rows that already passed the safety net, and only from queries in the current turn. There is no other path for data into a chart. A user without invoice access gets order-based trends with the note, exactly as the tables do today.

## Acceptance tests

**Pure tests**
1. `show_chart` (line) on a `sales_trend` run: labels and values equal the run's rows exactly.
2. `show_chart` refusals: an unknown query id, an unknown column and a non-numeric `y` are each refused. A 12-part donut becomes 7 parts plus "Other", where "Other" equals the sum of the rest.
3. A row dropped by the safety net does not appear in the chart built from that query.
4. `sales_trend`:
   - groups by month;
   - `interval="total"` with `group_by` gives one row per group;
   - without Sales Invoice in scope, it uses Sales Orders and returns `SALES_ORDERS_NOTE`.

**Live tests, on `eve.localhost`**

5. `sales_trend` for 2026 by month equals SQL sums of submitted Sales Invoices per month. At the time of writing these were: Jan 543,153; Feb 759,040; Mar 825,748; Apr 571,993; May 695,875; Jun 790,885; Jul 525,785; Aug 1,206,627; Sep 820,507; Oct 111,055 (to date).
6. As priya (no Sales Invoice access), the trend comes from Sales Orders, with the note.

**Manual, in the browser** (record a short check list)

7. "Sales trend for this year" shows option A:
   - hover shows the totals;
   - "Show data" matches the query rows;
   - Download saves an SVG;
   - the chart is readable in the dark theme.
8. These questions get the matching chart:
   - "Top 10 customers this year" gives a bar chart;
   - "Sales by item group this year" gives a donut.
9. A one-value answer ("Total sales in August") shows no chart.

## Left to the builder

- The exact limits on points, bars and parts.
- Colours, from Frappe's palette.
- Tooltip and number formatting details.
- Whether the trend tools live in `chat/tools/sales.py` or a new `chat/tools/trends.py`.
- The query id format.
- Whether to offer PNG as well as SVG.

Calls made by the designer, which the builder may revisit if the code shows a better way:
- Charts come from a `show_chart` tool that takes query ids and column names, not from a block of markdown in the answer.
- Mapping: trend → line, ranking → bar, breakdown → donut, comparison → several lines.
- Grouping parts past the 7th into "Other".
- No chart for fewer than 3 values.
- Download is SVG first.
- The trend tools get `group_by` and an `interval="total"` option (no year-over-year comparison in v1).
- Work starts on a new branch `feat/chat-charts` from `feat/permission-aware-chat`.
