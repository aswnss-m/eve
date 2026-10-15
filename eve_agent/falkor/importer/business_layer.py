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
