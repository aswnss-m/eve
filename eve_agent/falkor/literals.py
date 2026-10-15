"""Render Python values as Cypher literals.

FalkorDB's CYPHER prefix takes `name=value` pairs written as Cypher literals,
so we inline parameters instead of relying on the client library.
"""

import math
import re

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ESCAPES = {"\\": "\\\\", "'": "\\'", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def to_literal(value) -> str:
	if value is None:
		return "null"
	if isinstance(value, bool):
		return "true" if value else "false"
	if isinstance(value, int):
		return str(value)
	if isinstance(value, float):
		if math.isnan(value) or math.isinf(value):
			raise ValueError("NaN and Infinity cannot be sent to FalkorDB")
		return repr(value)
	if isinstance(value, str):
		return "'" + "".join(_ESCAPES.get(ch, ch) for ch in value) + "'"
	if isinstance(value, (list, tuple)):
		return "[" + ", ".join(to_literal(v) for v in value) + "]"
	if isinstance(value, dict):
		parts = []
		for key, item in value.items():
			if not _KEY_RE.match(str(key)):
				raise ValueError(f"Invalid map key for Cypher literal: {key!r}")
			parts.append(f"{key}: {to_literal(item)}")
		return "{" + ", ".join(parts) + "}"
	raise TypeError(f"Unsupported parameter type: {type(value).__name__}")


def with_params(cypher: str, params: dict | None) -> str:
	"""Prefix a query with CYPHER name=value pairs."""
	if not params:
		return cypher
	prefix = " ".join(f"{name}={to_literal(value)}" for name, value in params.items())
	return f"CYPHER {prefix} {cypher}"
