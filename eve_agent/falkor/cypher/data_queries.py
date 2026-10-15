"""Cypher used by the data importer.

Labels cannot be passed as parameters, so templates take a `{label}` (or
`{src}` / `{dst}`) placeholder filled with quote_label(). Values still go in
as parameters.
"""

RECORD_LABEL = "Record"


def quote_label(name: str) -> str:
	"""Backtick-quote a DocType name for use as a label. Backticks are doubled."""
	return "`" + name.replace("`", "``") + "`"


CREATE_INDEX = "CREATE INDEX FOR (n:{label}) ON (n.name)"

# Full-text indexes back the agent's fuzzy entity lookup (find_entity).
FULLTEXT_LABELS = ("Customer", "Supplier", "Item", "Employee", "Project", "Sales Person", "User", "Lead")
CREATE_FULLTEXT_INDEX = "CREATE FULLTEXT INDEX FOR (n:{label}) ON (n.name, n.title)"

CREATE_RECORDS = """
UNWIND $rows AS row
CREATE (n:{label}:Record)
SET n = row
RETURN count(n) AS created
"""

CREATE_DATA_LINKS = """
UNWIND $rows AS row
MATCH (a:{src} {{name: row.src}})
MATCH (b:{dst} {{name: row.dst}})
CREATE (a)-[r:LINKS_TO {{field: row.field}}]->(b)
RETURN count(r) AS created
"""

CREATE_DATA_CHILDREN = """
UNWIND $rows AS row
MATCH (a:{src} {{name: row.src}})
MATCH (b:{dst} {{name: row.dst}})
CREATE (a)-[r:HAS_CHILD {{field: row.field}}]->(b)
RETURN count(r) AS created
"""

COUNT_SCHEMA_DOCTYPES = "MATCH (d:DocType) RETURN count(d) AS doctypes"
