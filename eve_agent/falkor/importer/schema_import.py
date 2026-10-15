"""Write DocType schema into the graph.

Full sync: upsert every DocType, rebuild all LINKS_TO and HAS_CHILD edges,
then remove DocType nodes that no longer exist in Frappe.
"""

from dataclasses import dataclass

from eve_agent.falkor.cypher import schema_queries as q
from eve_agent.falkor.importer.batch_writer import write_batches
from eve_agent.falkor.importer.schema_rows import build_doctype_row, build_edges


@dataclass
class SchemaImportSummary:
	doctypes: int
	links: int
	children: int
	pruned: int

	def as_text(self) -> str:
		return (
			f"DocTypes: {self.doctypes}, LINKS_TO: {self.links}, "
			f"HAS_CHILD: {self.children}, pruned: {self.pruned}"
		)


def import_schema(client, metas: list[dict]) -> SchemaImportSummary:
	known = {m["name"] for m in metas}
	doctype_rows = [build_doctype_row(m) for m in metas]

	link_edges, child_edges = [], []
	for meta in metas:
		links, children = build_edges(meta, known)
		link_edges.extend(links)
		child_edges.extend(children)

	write_batches(client, q.UPSERT_DOCTYPES, doctype_rows)

	client.query(q.DELETE_ALL_LINKS)
	client.query(q.DELETE_ALL_CHILDREN)
	links = write_batches(client, q.CREATE_LINKS, link_edges)
	children = write_batches(client, q.CREATE_CHILDREN, child_edges)

	pruned = int(client.query(q.PRUNE_MISSING, params={"names": sorted(known)}).rows[0][0] or 0)

	return SchemaImportSummary(
		doctypes=len(doctype_rows),
		links=links,
		children=children,
		pruned=pruned,
	)
