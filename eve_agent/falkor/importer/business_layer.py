"""Apply the ERPNext / HRMS business rules to a freshly loaded data graph."""

from dataclasses import dataclass, field

from eve_agent.falkor.cypher.business_rules import RULES, Rule


@dataclass
class BusinessLayerSummary:
	counts: dict[str, int] = field(default_factory=dict)

	@property
	def total(self) -> int:
		return sum(self.counts.values())

	def as_text(self) -> str:
		applied = sum(1 for n in self.counts.values() if n)
		return f"business rules applied: {applied}/{len(self.counts)}, changes: {self.total}"


def apply_business_rules(client, rules: tuple[Rule, ...] = RULES) -> BusinessLayerSummary:
	summary = BusinessLayerSummary()
	for rule in rules:
		result = client.query(rule.cypher)
		summary.counts[rule.name] = int(result.rows[0][0] or 0) if result.rows else 0
	return summary


def apply_scoped_rules(
	client, names: list[str], pivots: list[str], rules: tuple[Rule, ...] = RULES, batch: int = 500
) -> BusinessLayerSummary:
	"""Run every rule's scoped statements for the records in `names` and the masters in `pivots`.

	Statements are a delete + create pair with the same scope, so this is idempotent.
	A statement reads either $names or $pivots; that list is sent in chunks.
	"""
	summary = BusinessLayerSummary()
	for rule in rules:
		count = 0
		for statement in rule.scoped:
			scope_list = pivots if "$pivots" in statement else names
			for start in range(0, len(scope_list), batch):
				chunk = scope_list[start : start + batch]
				result = client.query(statement, {"names": chunk, "pivots": chunk})
				if result.rows:
					count += int(result.rows[0][0] or 0)
		summary.counts[rule.name] = count
	return summary
