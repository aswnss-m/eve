"""Pure access scope: what a user may read, a stable profile key, and plan filtering.

No Frappe imports. Task 3 fills `AccessScope` from Frappe; the importer and chat read it.
"""

import hashlib
import json
from dataclasses import dataclass, replace

from eve_agent.falkor.importer.data_rows import DataPlan, DocTypePlan

SPECIAL_KEEP = ("User", "Comment")  # kept in every profile plan; Comment rows are filtered by the reader
SPECIAL_DROP = ("ToDo",)  # dropped from every non-full profile plan


@dataclass
class AccessScope:
	key: str
	full_access: bool
	readable: dict[str, tuple[int, ...]]  # parent AND child DocType -> readable permlevels (sorted)
	user_permissions: dict[str, tuple[tuple[str, str], ...]]  # allow DocType -> sorted ((doc, for), ...)
	own_record_doctypes: tuple[str, ...] = ()  # sorted, e.g. ("Employee",)
	strict: bool = False
	parents: tuple[str, ...] = ()  # sorted readable parent DocTypes (derived, not part of the key)

	def can_see(self, doctype: str) -> bool:
		return self.full_access or doctype in self.readable or doctype in SPECIAL_KEEP

	def to_json(self) -> str:
		return json.dumps(
			{
				"key": self.key,
				"full_access": self.full_access,
				"readable": {k: list(v) for k, v in self.readable.items()},
				"user_permissions": {k: [list(p) for p in v] for k, v in self.user_permissions.items()},
				"own_record_doctypes": list(self.own_record_doctypes),
				"strict": self.strict,
				"parents": list(self.parents),
			}
		)

	@classmethod
	def from_json(cls, text: str) -> "AccessScope":
		d = json.loads(text)
		return cls(
			key=d["key"],
			full_access=d["full_access"],
			readable={k: tuple(v) for k, v in d["readable"].items()},
			user_permissions={k: tuple((a, b) for a, b in v) for k, v in d["user_permissions"].items()},
			own_record_doctypes=tuple(d.get("own_record_doctypes", ())),
			strict=d.get("strict", False),
			parents=tuple(d.get("parents", ())),
		)


FULL_SCOPE = AccessScope(key="full", full_access=True, readable={}, user_permissions={})


def make_key(readable, user_permissions, own_record_doctypes, strict) -> str:
	canonical = {
		"readable": {k: sorted(v) for k, v in sorted(readable.items())},
		"user_permissions": {k: sorted([a, b] for a, b in v) for k, v in sorted(user_permissions.items())},
		"own_record_doctypes": sorted(own_record_doctypes),
		"strict": bool(strict),
	}
	text = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
	return hashlib.sha1(text.encode()).hexdigest()[:12]


def _filtered(plan: DocTypePlan, levels: tuple[int, ...]) -> DocTypePlan:
	allowed = set(levels)

	def ok(fieldname: str) -> bool:
		return plan.field_levels.get(fieldname, 0) in allowed

	return replace(
		plan,
		value_fields=[f for f in plan.value_fields if ok(f)],
		text_fields=[f for f in plan.text_fields if ok(f)],
		links={k: v for k, v in plan.links.items() if ok(k)},
		dynamic_links={k: v for k, v in plan.dynamic_links.items() if ok(k)},
		tables={k: v for k, v in plan.tables.items() if ok(k)},
		field_levels={k: v for k, v in plan.field_levels.items() if v in allowed},
	)


def filter_plan(plan: DataPlan, scope: AccessScope) -> DataPlan:
	if scope.full_access:
		return plan
	own = set(scope.own_record_doctypes)
	parents = []
	for p in plan.parents:
		if p.name in SPECIAL_DROP:
			continue
		if p.name in SPECIAL_KEEP:
			parents.append(_filtered(p, (0,)))
		elif p.name in scope.readable and p.name not in own:
			parents.append(_filtered(p, scope.readable[p.name]))
	children = {}
	for p in parents:
		for child_name in p.tables.values():
			child = plan.children.get(child_name)
			if child and child_name in scope.readable and child_name not in children:
				children[child_name] = _filtered(child, scope.readable[child_name])
	return DataPlan(parents=parents, children=children)
