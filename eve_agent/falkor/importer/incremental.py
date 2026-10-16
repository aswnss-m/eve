"""Upsert a handful of records into an existing data graph. No Frappe imports here.

The full import (data_import.py) rebuilds everything. This module brings single
records up to date in place, in the order described in the business rules module:

  1. capture the masters next to the records (before anything is removed, so a
     cancelled or deleted document still points at the customer whose totals change)
  2. delete the records' child-row nodes and the edges they own (`field` property)
  3. delete records that no longer exist in the database
  4. MERGE the records (keeps incoming edges), recreate child rows, HAS_CHILD and LINKS_TO
  5. run every business rule's scoped statements with $names and $pivots

Keys name a record as a JSON pair ["DocType", "name"] (names may contain anything).
"""

import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from eve_agent.falkor.cypher import data_queries as dq
from eve_agent.falkor.cypher import incremental_queries as iq
from eve_agent.falkor.cypher.business_rules import RULES, Rule
from eve_agent.falkor.importer.batch_writer import chunked, write_batches
from eve_agent.falkor.importer.business_layer import apply_scoped_rules
from eve_agent.falkor.importer.data_rows import DataPlan, DocTypePlan, build_link_edges, build_record_row

# fetch_by_names(plan, names) -> the records that still exist, as dicts.
FetchByNames = Callable[[DocTypePlan, list[str]], list[dict]]
# fetch_children_by_parents(child_plan, parent_doctype, parentfield, parents) -> child rows.
FetchChildrenByParents = Callable[[DocTypePlan, str, str, list[str]], list[dict]]

NAME_BATCH = 500


def make_key(doctype: str, name: str) -> str:
	return json.dumps([doctype, str(name)])


def parse_key(key: str) -> tuple[str, str]:
	doctype, name = json.loads(key)
	return str(doctype), str(name)


def group_keys(keys: Iterable[str], plan: DataPlan) -> tuple[dict[str, list[str]], list[str]]:
	"""Names per planned DocType, and the keys for DocTypes the plan does not load."""
	wanted = plan.parent_names
	by_doctype: dict[str, list[str]] = defaultdict(list)
	ignored: list[str] = []
	for key in dict.fromkeys(keys):
		try:
			doctype, name = parse_key(key)
		except (ValueError, TypeError):
			ignored.append(key)
			continue
		if doctype in wanted:
			by_doctype[doctype].append(name)
		else:
			ignored.append(key)
	return dict(by_doctype), ignored


def ensure_index(client, label: str | None = None) -> None:
	"""Create the name index for `label` (or the shared :Record index). Existing indexes are fine."""
	cypher = dq.CREATE_INDEX.format(label=dq.quote_label(label)) if label else dq.CREATE_RECORD_INDEX
	try:
		client.query(cypher)
	except Exception as exc:
		if "already indexed" not in str(exc).lower():
			raise


def capture_pivots(client, by_doctype: dict[str, list[str]]) -> set[str]:
	found: set[str] = set()
	for doctype, names in by_doctype.items():
		label = dq.quote_label(doctype)
		for template in (iq.CAPTURE_PIVOTS_DIRECT, iq.CAPTURE_PIVOTS_VIA_CHILDREN):
			for batch in chunked(names, NAME_BATCH):
				for row in client.query(template.format(label=label), {"names": batch}).rows:
					if row and row[0] not in (None, ""):
						found.add(str(row[0]))
	return found


def _run_names(client, template: str, doctype: str, names: list[str]) -> int:
	total = 0
	label = dq.quote_label(doctype)
	for batch in chunked(names, NAME_BATCH):
		result = client.query(template.format(label=label), {"names": batch})
		if result.rows:
			total += int(result.rows[0][0] or 0)
	return total


@dataclass
class IncrementalSummary:
	upserted: int = 0
	deleted: int = 0
	children: int = 0
	child_edges: int = 0
	links: int = 0
	rule_changes: int = 0
	ignored: list[str] = field(default_factory=list)

	def as_text(self) -> str:
		text = (
			f"records upserted: {self.upserted}, deleted: {self.deleted}, child rows: {self.children}, "
			f"LINKS_TO: {self.links}, rule changes: {self.rule_changes}"
		)
		if self.ignored:
			text += f", ignored: {len(self.ignored)}"
		return text


def sync_keys(
	client,
	plan: DataPlan,
	keys: Iterable[str],
	fetch_by_names: FetchByNames,
	fetch_children_by_parents: FetchChildrenByParents,
	rules: tuple[Rule, ...] = RULES,
) -> IncrementalSummary:
	summary = IncrementalSummary()
	by_doctype, summary.ignored = group_keys(keys, plan)
	if not by_doctype:
		return summary
	plans = {p.name: p for p in plan.parents}

	# What still exists, and what is gone (deleted or renamed away).
	present: dict[str, list[dict]] = {}
	gone: dict[str, list[str]] = {}
	for doctype, names in by_doctype.items():
		records = fetch_by_names(plans[doctype], names)
		found = {str(r["name"]) for r in records}
		present[doctype] = records
		gone[doctype] = [n for n in names if n not in found]

	pivots = capture_pivots(client, by_doctype)

	for doctype, names in by_doctype.items():
		_run_names(client, iq.DELETE_CHILD_NODES, doctype, names)
		_run_names(client, iq.DELETE_OWNED_EDGES, doctype, names)
	for doctype, names in gone.items():
		if names:
			summary.deleted += _run_names(client, iq.DELETE_RECORDS, doctype, names)

	ensure_index(client)
	targets = plan.parent_names
	child_edges: dict[tuple[str, str], list[dict]] = defaultdict(list)
	link_edges: dict[tuple[str, str], list[dict]] = defaultdict(list)

	def collect_links(dt_plan: DocTypePlan, records: list[dict]) -> None:
		for record in records:
			for target, edge in build_link_edges(dt_plan, record, targets):
				link_edges[(dt_plan.name, target)].append(edge)

	for doctype, records in present.items():
		if not records:
			continue
		dt_plan = plans[doctype]
		ensure_index(client, doctype)
		rows = [build_record_row(dt_plan, r) for r in records]
		summary.upserted += write_batches(client, iq.MERGE_RECORDS.format(label=dq.quote_label(doctype)), rows)
		collect_links(dt_plan, records)

		parents = [str(r["name"]) for r in records]
		for fieldname, child_name in dt_plan.tables.items():
			child = plan.children.get(child_name)
			if not child:
				continue
			child_rows = fetch_children_by_parents(child, doctype, fieldname, parents)
			if not child_rows:
				continue
			ensure_index(client, child_name)
			built = [build_record_row(child, r) for r in child_rows]
			summary.children += write_batches(
				client, dq.CREATE_RECORDS.format(label=dq.quote_label(child_name)), built
			)
			collect_links(child, child_rows)
			child_edges[(doctype, child_name)].extend(
				{"src": str(r["parent"]), "dst": str(r["name"]), "field": fieldname} for r in child_rows
			)

	for (src, dst), rows in child_edges.items():
		cypher = dq.CREATE_DATA_CHILDREN.format(src=dq.quote_label(src), dst=dq.quote_label(dst))
		summary.child_edges += write_batches(client, cypher, rows)
	for (src, dst), rows in link_edges.items():
		cypher = dq.CREATE_DATA_LINKS.format(src=dq.quote_label(src), dst=dq.quote_label(dst))
		summary.links += write_batches(client, cypher, rows)

	# Masters the new versions point at, plus the records themselves.
	pivots |= capture_pivots(client, {dt: [str(r["name"]) for r in recs] for dt, recs in present.items() if recs})
	names = [n for ns in by_doctype.values() for n in ns]
	pivots |= set(names)

	summary.rule_changes = apply_scoped_rules(client, names, sorted(pivots), rules).total
	return summary
