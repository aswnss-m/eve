"""Send rows to FalkorDB in batches using UNWIND $rows."""

from collections.abc import Iterable, Iterator

from eve_agent.falkor.cypher.constants import SCHEMA_BATCH_SIZE


def chunked(items: list, size: int = SCHEMA_BATCH_SIZE) -> Iterator[list]:
	for start in range(0, len(items), size):
		yield items[start : start + size]


def write_batches(client, cypher: str, rows: Iterable[dict], size: int = SCHEMA_BATCH_SIZE) -> int:
	"""Run `cypher` once per batch with $rows set. Returns the sum of the single returned count column."""
	total = 0
	for batch in chunked(list(rows), size):
		result = client.query(cypher, params={"rows": batch})
		if result.rows:
			total += int(result.rows[0][0] or 0)
	return total
