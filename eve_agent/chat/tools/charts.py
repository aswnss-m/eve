"""Charts in Ask Eve: draw one query the model already ran this turn.

The model names the query (q1, q2, ... from the tool results) and its columns, never the numbers: every
value is copied from that query's rows as they stand after the safety net in chat/common.py:result(). The
only computed number is a donut's "Other" slice. The widget draws the spec with the Desk's frappe.Chart
(frontend/eve_chat/ChartCard.vue).
"""

import re
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from eve_agent.chat.common import QueryRun, Tool, ToolContext

MIN_VALUES = 3
MAX_POINTS = {"line": 60, "bar": 20}
MAX_PARTS = 8  # donut and pie: the 7 largest parts, the rest summed into "Other"
CURRENCY = re.compile(r"amount|revenue|spend|outstanding|total|paid|value", re.IGNORECASE)


class ShowChartArgs(BaseModel):
	query: str = Field(description="Id of a query from this turn, e.g. q1 (each tool result lists query ids)")
	type: Literal["line", "bar", "donut", "pie"]
	x: str = Field(description="Column for the labels: the period, name or group")
	y: list[str] = Field(
		min_length=1, max_length=4, description="Numeric column(s) to plot; exactly one for donut and pie"
	)
	title: str


def _is_number(value) -> bool:
	return isinstance(value, int | float) and not isinstance(value, bool)


def _current_periods(today: date) -> set[str]:
	return {today.strftime("%Y-%m"), f"{today.year}-Q{(today.month - 1) // 3 + 1}"}


def build_chart(run: QueryRun, type: str, x: str, y: list[str], title: str, today: date) -> dict:
	"""The chart spec for `run`, or ValueError with a message the model can act on."""
	rows = run.rows
	name = run.id or run.title
	if len(rows) < MIN_VALUES:
		raise ValueError(
			f"Query {name} has {len(rows)} rows; a chart needs at least {MIN_VALUES}. "
			"Answer with the numbers."
		)
	columns = set(rows[0])
	missing = [c for c in (x, *y) if c not in columns]
	if missing:
		raise ValueError(
			f"Column {', '.join(missing)} is not in query {name}; "
			f"its columns are {', '.join(sorted(columns))}."
		)
	if type in ("donut", "pie") and len(y) != 1:
		raise ValueError("A donut or pie chart takes exactly one value column.")
	for col in y:
		bad = next((r for r in rows if not _is_number(r.get(col))), None)
		if bad is not None:
			raise ValueError(
				f"Column {col} has an empty or non-numeric value (for {bad.get(x)}). "
				"Chart a numeric column, or answer with a table."
			)

	labels = [str(r.get(x)) for r in rows]
	datasets = [{"name": col, "values": [r[col] for r in rows]} for col in y]
	if type in ("donut", "pie"):
		parts = sorted(zip(labels, datasets[0]["values"]), key=lambda p: p[1], reverse=True)
		if len(parts) > MAX_PARTS:
			parts = [*parts[: MAX_PARTS - 1], ("Other", sum(v for _, v in parts[MAX_PARTS - 1 :]))]
		labels = [label for label, _ in parts]
		datasets = [{"name": y[0], "values": [value for _, value in parts]}]
	elif len(rows) > MAX_POINTS[type]:
		raise ValueError(
			f"Query {name} has {len(rows)} rows; a {type} chart shows at most {MAX_POINTS[type]}. "
			"Narrow the period or lower the limit."
		)

	return {
		"type": type,
		"title": title,
		"labels": labels,
		"datasets": datasets,
		"unit": "currency" if all(CURRENCY.search(col) for col in y) else "number",
		"partial_last": type in ("line", "bar") and labels[-1] in _current_periods(today),
	}


def chart_tools(ctx: ToolContext) -> list[Tool]:
	def show_chart(args: ShowChartArgs) -> dict:
		run = ctx.runs.get(args.query)
		if run is None:
			known = ", ".join(ctx.runs) or "none yet"
			raise ValueError(
				f"No query {args.query} in this turn (known: {known}). "
				"Run a data tool first, then chart its query."
			)
		today = datetime.now(timezone.utc).date()
		chart = build_chart(run, args.type, args.x, args.y, args.title, today)
		return {"chart": chart, "queries": [], "sources": [], "as_of": ctx.config.data_as_of}

	return [
		Tool(
			"show_chart",
			"Draw one query from this turn as a chart in the answer: line for a trend over time, bar for a "
			"ranking, donut for parts of a whole. Pass the query id (q1, q2, ... from the tool results), the "
			"label column and the value column(s), never the numbers. Needs at least 3 rows.",
			ShowChartArgs,
			show_chart,
		)
	]
