"""Turn plain DocType descriptions into graph rows. No Frappe imports here.

Input is one dict per DocType, built by eve_agent.sync.meta_reader:

    {
        "name": "Sales Order", "module": "Selling", "is_submittable": True,
        "is_single": False, "is_child": False, "is_tree": False,
        "title_field": "customer_name", "modified": "2026-10-01 10:00:00",
        "fields": [{"fieldname": ..., "fieldtype": ..., "options": ..., "label": ..., "reqd": 0}],
    }
"""

import json

LINK_FIELDTYPES = {"Link"}
TABLE_FIELDTYPES = {"Table", "Table MultiSelect"}
DYNAMIC_FIELDTYPES = {"Dynamic Link"}


def build_doctype_row(meta: dict) -> dict:
	fields = meta.get("fields", [])
	return {
		"name": meta["name"],
		"module": meta.get("module") or "",
		"is_submittable": bool(meta.get("is_submittable")),
		"is_single": bool(meta.get("is_single")),
		"is_child": bool(meta.get("is_child")),
		"is_tree": bool(meta.get("is_tree")),
		"title_field": meta.get("title_field") or "",
		"field_count": len(fields),
		"field_names": [f["fieldname"] for f in fields],
		"field_types": [f["fieldtype"] for f in fields],
		"link_targets": [link_target(f) for f in fields if link_target(f)],
		"fields_json": json.dumps(fields, sort_keys=True),
		"modified": str(meta.get("modified") or ""),
	}


def link_target(field: dict) -> str:
	"""Describe where a field points. Dynamic Link has no fixed target, so it is marked."""
	fieldtype = field.get("fieldtype")
	fieldname = field["fieldname"]
	if fieldtype in LINK_FIELDTYPES or fieldtype in TABLE_FIELDTYPES:
		return f"{fieldname}:{field.get('options') or ''}"
	if fieldtype in DYNAMIC_FIELDTYPES:
		return f"dynamic:{fieldname}"
	return ""


def build_edges(meta: dict, known_doctypes: set[str]) -> tuple[list[dict], list[dict]]:
	"""Return (link_edges, child_edges). Targets that are not known DocTypes are skipped."""
	links, children = [], []
	for field in meta.get("fields", []):
		target = field.get("options")
		if not target or target not in known_doctypes:
			continue
		edge = {"src": meta["name"], "dst": target, "fieldname": field["fieldname"]}
		if field["fieldtype"] in LINK_FIELDTYPES:
			links.append(edge)
		elif field["fieldtype"] in TABLE_FIELDTYPES:
			children.append(edge)
	return links, children
