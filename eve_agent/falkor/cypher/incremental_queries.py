"""Cypher used by the incremental sync (eve_agent/falkor/importer/incremental.py).

Templates take a `{label}` placeholder filled with quote_label(); names and rows
go in as parameters. Every statement starts from `UNWIND $names` + a labelled
MATCH on `name`, so the per-label index is used.
"""

MERGE_RECORDS = """
UNWIND $rows AS row
MERGE (n:{label}:Record {{name: row.name}})
SET n = row
RETURN count(n) AS upserted
"""

# Child-row nodes are owned by their parent: drop them, the parent's rows are recreated.
DELETE_CHILD_NODES = """
UNWIND $names AS nm
MATCH (p:{label} {{name: nm}})-[:HAS_CHILD]->(c)
DETACH DELETE c
RETURN count(*) AS n
"""

# Edges with a `field` property (LINKS_TO, HAS_CHILD, forward renames) belong to
# their source record. Derived edges have none and are left to the scoped rules.
DELETE_OWNED_EDGES = """
UNWIND $names AS nm
MATCH (p:{label} {{name: nm}})-[r]->()
WHERE r.field IS NOT NULL
DELETE r
RETURN count(*) AS n
"""

DELETE_RECORDS = """
UNWIND $names AS nm
MATCH (n:{label} {{name: nm}})
DETACH DELETE n
RETURN count(*) AS n
"""

# Masters a record points at, directly and through its child rows. Walks only the
# record's own outgoing field-bearing edges, so cost follows the record, not the hub.
CAPTURE_PIVOTS_DIRECT = """
UNWIND $names AS nm
MATCH (x:{label} {{name: nm}})-[r]->(a:Record)
WHERE r.field IS NOT NULL AND type(r) <> 'HAS_CHILD'
RETURN DISTINCT a.name AS name
"""

CAPTURE_PIVOTS_VIA_CHILDREN = """
UNWIND $names AS nm
MATCH (x:{label} {{name: nm}})-[:HAS_CHILD]->()-[s]->(b:Record)
WHERE s.field IS NOT NULL
RETURN DISTINCT b.name AS name
"""
