"""Guard for agent-generated Cypher.

Two layers protect the graph: this local check rejects obvious writes with a
clear error, and GRAPH.RO_QUERY is enforced by FalkorDB itself.
"""

import re

_STRING_RE = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")
_WRITE_RE = re.compile(
	r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH)\b|db\.idx\.[A-Za-z.]*(create|drop)",
	re.IGNORECASE,
)


class WriteQueryError(ValueError):
	pass


def assert_read_only(cypher: str) -> None:
	# Remove string literals first so a value like 'SET' does not trigger the check.
	stripped = _STRING_RE.sub("''", cypher)
	match = _WRITE_RE.search(stripped)
	if match:
		raise WriteQueryError(f"Write clause '{match.group(0)}' is not allowed in a read-only query")
