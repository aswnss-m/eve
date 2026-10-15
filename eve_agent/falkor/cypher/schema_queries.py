"""Cypher used by the schema importer. All values go in as parameters."""

UPSERT_DOCTYPES = """
UNWIND $rows AS row
MERGE (d:DocType {name: row.name})
SET d.module = row.module,
    d.is_submittable = row.is_submittable,
    d.is_single = row.is_single,
    d.is_child = row.is_child,
    d.is_tree = row.is_tree,
    d.title_field = row.title_field,
    d.field_count = row.field_count,
    d.field_names = row.field_names,
    d.field_types = row.field_types,
    d.link_targets = row.link_targets,
    d.fields_json = row.fields_json,
    d.modified = row.modified
RETURN count(d) AS upserted
"""

DELETE_ALL_LINKS = "MATCH ()-[r:LINKS_TO]->() DELETE r"
DELETE_ALL_CHILDREN = "MATCH ()-[r:HAS_CHILD]->() DELETE r"

CREATE_LINKS = """
UNWIND $rows AS row
MATCH (a:DocType {name: row.src})
MATCH (b:DocType {name: row.dst})
MERGE (a)-[r:LINKS_TO {fieldname: row.fieldname}]->(b)
RETURN count(r) AS created
"""

CREATE_CHILDREN = """
UNWIND $rows AS row
MATCH (a:DocType {name: row.src})
MATCH (b:DocType {name: row.dst})
MERGE (a)-[r:HAS_CHILD {fieldname: row.fieldname}]->(b)
RETURN count(r) AS created
"""

PRUNE_MISSING = """
MATCH (d:DocType)
WHERE NOT d.name IN $names
DETACH DELETE d
RETURN count(d) AS removed
"""

COUNT_DOCTYPES = "MATCH (d:DocType) RETURN count(d) AS doctypes"
COUNT_LINKS = "MATCH ()-[r:LINKS_TO]->() RETURN count(r) AS links"
COUNT_CHILDREN = "MATCH ()-[r:HAS_CHILD]->() RETURN count(r) AS children"

EXPORT_DOCTYPES = """
MATCH (d:DocType)
RETURN d.name, d.module, d.is_submittable, d.is_single, d.is_child, d.is_tree,
       d.title_field, d.field_count, d.field_names, d.field_types, d.link_targets,
       d.fields_json, d.modified
ORDER BY d.name
"""

EXPORT_EDGES = """
MATCH (a:DocType)-[r]->(b:DocType)
RETURN a.name, type(r), r.fieldname, b.name
ORDER BY a.name, r.fieldname
"""
