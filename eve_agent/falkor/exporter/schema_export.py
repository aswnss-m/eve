"""Export the schema graph to a JSON file for backup or debugging."""

import json
from pathlib import Path

from eve_agent.falkor.cypher import schema_queries as q


def export_schema(client, path: str | Path) -> dict:
	nodes = [
		{
			"name": row[0],
			"module": row[1],
			"is_submittable": row[2],
			"is_single": row[3],
			"is_child": row[4],
			"is_tree": row[5],
			"title_field": row[6],
			"field_count": row[7],
			"field_names": row[8],
			"field_types": row[9],
			"link_targets": row[10],
			"fields_json": row[11],
			"modified": row[12],
		}
		for row in client.query(q.EXPORT_DOCTYPES).rows
	]
	edges = [
		{"src": row[0], "type": row[1], "fieldname": row[2], "dst": row[3]}
		for row in client.query(q.EXPORT_EDGES).rows
	]
	payload = {"graph": client.graph_name, "doctypes": nodes, "edges": edges}
	Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
	return {"doctypes": len(nodes), "edges": len(edges), "path": str(path)}
