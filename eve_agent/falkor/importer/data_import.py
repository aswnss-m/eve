"""Write ERP records into the data graph.

Full rebuild: drop the data graph, create one node per record (and per
child-table row), then add HAS_CHILD and LINKS_TO edges. Records are fetched
through callables so this module stays free of Frappe imports.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from eve_agent.falkor.cypher import data_queries as dq
from eve_agent.falkor.cypher import schema_queries as sq
from eve_agent.falkor.importer.batch_writer import write_batches
from eve_agent.falkor.importer.data_rows import (
	DataPlan,
	DocTypePlan,
	build_link_edges,
	build_record_row,
	schema_node_from_export_row,
)

# fetch_records(plan) -> list of dicts, or None when the DocType has no table.
FetchRecords = Callable[[DocTypePlan], list[dict] | None]
# fetch_children(child_plan, parent_doctype, parentfield) -> list of dicts.
FetchChildren = Callable[[DocTypePlan, str, str], list[dict]]


@dataclass
class DataImportSummary:
	doctypes: int = 0
	records: int = 0
	children: int = 0
	child_edges: int = 0
	links: int = 0
	skipped: list[str] = field(default_factory=list)

	def as_text(self) -> str:
		text = (
			f"DocTypes with data: {self.doctypes}, records: {self.records}, child rows: {self.children}, "
			f"HAS_CHILD: {self.child_edges}, LINKS_TO: {self.links}"
		)
		if self.skipped:
			text += f", skipped: {', '.join(self.skipped)}"
		return text


def read_schema_nodes(schema_client) -> list[dict]:
	return [schema_node_from_export_row(row) for row in schema_client.query(sq.EXPORT_DOCTYPES).rows]


class _Loader:
	def __init__(self, client):
		self.client = client
		self.indexed: set[str] = set()
		self.loaded: dict[str, set[str]] = defaultdict(set)  # DocType -> names

	def create_nodes(self, plan: DocTypePlan, records: list[dict]) -> int:
		label = dq.quote_label(plan.name)
		if plan.name not in self.indexed:
			self.client.query(dq.CREATE_INDEX.format(label=label))
			self.indexed.add(plan.name)
		rows = [build_record_row(plan, r) for r in records]
		self.loaded[plan.name].update(r["name"] for r in records)
		return write_batches(self.client, dq.CREATE_RECORDS.format(label=label), rows)

	def create_edges(self, template: str, grouped: dict[tuple[str, str], list[dict]]) -> int:
		total = 0
		for (src, dst), rows in grouped.items():
			rows = [r for r in rows if r["dst"] in self.loaded.get(dst, ())]
			if rows:
				cypher = template.format(src=dq.quote_label(src), dst=dq.quote_label(dst))
				total += write_batches(self.client, cypher, rows)
		return total


def import_data(client, plan: DataPlan, fetch_records: FetchRecords, fetch_children: FetchChildren) -> DataImportSummary:
	summary = DataImportSummary()
	loader = _Loader(client)
	targets = plan.parent_names
	child_edges: dict[tuple[str, str], list[dict]] = defaultdict(list)
	link_edges: dict[tuple[str, str], list[dict]] = defaultdict(list)

	def collect_links(dt_plan: DocTypePlan, records: list[dict]) -> None:
		for record in records:
			for target, edge in build_link_edges(dt_plan, record, targets):
				link_edges[(dt_plan.name, target)].append(edge)

	client.delete_graph()

	for parent in plan.parents:
		records = fetch_records(parent)
		if records is None:
			summary.skipped.append(parent.name)
			continue
		if not records:
			continue
		summary.doctypes += 1
		summary.records += loader.create_nodes(parent, records)
		collect_links(parent, records)

		names = loader.loaded[parent.name]
		for fieldname, child_name in parent.tables.items():
			child = plan.children.get(child_name)
			if not child:
				continue
			rows = [r for r in fetch_children(child, parent.name, fieldname) if r.get("parent") in names]
			if not rows:
				continue
			summary.children += loader.create_nodes(child, rows)
			collect_links(child, rows)
			child_edges[(parent.name, child_name)].extend(
				{"src": r["parent"], "dst": r["name"], "field": fieldname} for r in rows
			)

	summary.child_edges = loader.create_edges(dq.CREATE_DATA_CHILDREN, child_edges)
	summary.links = loader.create_edges(dq.CREATE_DATA_LINKS, link_edges)
	return summary
