"""Conversation memory in its own graph (<site>_memory), one subgraph per user:

  (:User)-[:HAD]->(:Conversation)-[:HAS_MESSAGE]->(:Message)
  (:Message)-[:MENTIONS]->(:EntityRef {doctype, name})
  (:User)-[:ASKED_ABOUT {count, last_at}]->(:EntityRef)
  (:User)-[:SAVED]->(:Insight)-[:DERIVED_FROM]->(:EntityRef)

EntityRef points back at a record in the data graph by (doctype, name), so
memory survives the data graph being rebuilt.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from eve_agent.chat.common import Tool, ToolContext, result

MAX_TEXT = 2000
MAX_ENTITY_REFS = 50

_indexed: set[str] = set()


def ensure_indexes(memory) -> None:
	if memory.name in _indexed:
		return
	for lbl, prop in (("User", "name"), ("Conversation", "id"), ("EntityRef", "key")):
		try:
			memory.write(f"CREATE INDEX FOR (n:{lbl}) ON (n.{prop})")
		except Exception:
			pass  # Already indexed.
	_indexed.add(memory.name)


def entity_refs(sources: list[dict]) -> list[dict]:
	seen: set[str] = set()
	out = []
	for source in sources:
		key = f"{source['doctype']}|{source['name']}"
		if key in seen:
			continue
		seen.add(key)
		out.append(
			{
				"key": key,
				"doctype": source["doctype"],
				"name": source["name"],
				"title": source.get("title") or "",
			}
		)
	return out[:MAX_ENTITY_REFS]


SAVE_TURN = """
MERGE (u:User {name: $user}) SET u.full_name = $full_name
MERGE (c:Conversation {id: $chat_id}) ON CREATE SET c.started_at = $now, c.title = $title
SET c.updated_at = $now
MERGE (u)-[:HAD]->(c)
CREATE (c)-[:HAS_MESSAGE]->(:Message {role: 'user', text: $question, at: $now})
CREATE (c)-[:HAS_MESSAGE]->(m:Message {role: 'assistant', text: $answer, at: $now})
WITH u, m
UNWIND $refs AS ref
MERGE (e:EntityRef {key: ref.key}) ON CREATE SET e.doctype = ref.doctype, e.name = ref.name, e.title = ref.title
CREATE (m)-[:MENTIONS]->(e)
MERGE (u)-[a:ASKED_ABOUT]->(e) ON CREATE SET a.count = 0
SET a.count = a.count + 1, a.last_at = $now"""


def save_turn(ctx: ToolContext, chat_id: str, question: str, answer: str) -> None:
	ensure_indexes(ctx.memory)
	ctx.memory.write(
		SAVE_TURN,
		{
			"user": ctx.user.user,
			"full_name": ctx.user.name,
			"chat_id": f"{ctx.user.user}:{chat_id}",
			"title": question[:120],
			"question": question[:MAX_TEXT],
			"answer": answer[:MAX_TEXT],
			"now": datetime.now(timezone.utc).isoformat(),
			"refs": entity_refs(ctx.cited),
		},
	)


MEMORY_CONTEXT = """
MATCH (u:User {name: $user})
OPTIONAL MATCH (u)-[a:ASKED_ABOUT]->(e:EntityRef)
WITH u, e, a ORDER BY a.count DESC, a.last_at DESC
WITH u, collect(e.doctype + ' ' + e.name + ' (asked ' + toString(a.count) + 'x)')[0..8] AS interests
OPTIONAL MATCH (u)-[:HAD]->(c:Conversation)
WITH u, interests, c ORDER BY c.updated_at DESC
WITH u, interests, collect(c.title + ' [' + substring(c.updated_at, 0, 10) + ']')[0..5] AS recent
OPTIONAL MATCH (u)-[:SAVED]->(i:Insight)
WITH interests, recent, i ORDER BY i.at DESC
RETURN interests, recent, collect(i.text + ' [' + substring(i.at, 0, 10) + ']')[0..5] AS insights"""


def memory_context(ctx: ToolContext) -> str:
	"""A short summary of what this user cares about, for the system prompt."""
	try:
		run = ctx.memory.ro("Memory context", MEMORY_CONTEXT, {"user": ctx.user.user})
	except Exception:
		return ""  # Memory graph does not exist yet.
	if not run.rows:
		return ""
	row = run.rows[0]

	def items(value) -> list[str]:
		return [str(v) for v in (value or []) if v]

	parts = []
	if items(row.get("interests")):
		parts.append("Records they ask about most: " + "; ".join(items(row.get("interests"))))
	if items(row.get("recent")):
		parts.append("Recent conversations: " + "; ".join(items(row.get("recent"))))
	if items(row.get("insights")):
		parts.append("Insights they saved: " + "; ".join(items(row.get("insights"))))
	return "\n".join(parts)


RECALL = """
MATCH (u:User {name: $user})-[:HAD]->(c:Conversation)-[:HAS_MESSAGE]->(m:Message)
WHERE $topic = '' OR toLower(m.text) CONTAINS $topic
OPTIONAL MATCH (m)-[:MENTIONS]->(e:EntityRef)
RETURN c.title AS conversation, m.role AS role, m.at AS at, substring(m.text, 0, 400) AS text,
       collect(e.doctype + ' ' + e.name)[0..5] AS mentions
ORDER BY at DESC LIMIT 12"""

RECALL_INSIGHTS = """
MATCH (u:User {name: $user})-[:SAVED]->(i:Insight)
WHERE $topic = '' OR toLower(i.text) CONTAINS $topic
OPTIONAL MATCH (i)-[:DERIVED_FROM]->(e:EntityRef)
RETURN i.text AS insight, i.at AS at, collect(e.doctype + ' ' + e.name) AS sources
ORDER BY at DESC LIMIT 10"""

SAVE_INSIGHT = """
MERGE (u:User {name: $user})
CREATE (u)-[:SAVED]->(i:Insight {text: $text, at: $now})
WITH i
UNWIND $refs AS ref
MERGE (e:EntityRef {key: ref.key}) ON CREATE SET e.doctype = ref.doctype, e.name = ref.name, e.title = ref.title
CREATE (i)-[:DERIVED_FROM]->(e)"""


class RecallArgs(BaseModel):
	topic: str = Field("", description="Word or name to search for; empty for most recent")


class SourceRef(BaseModel):
	doctype: str
	name: str


class RememberInsightArgs(BaseModel):
	text: str = Field(min_length=5, max_length=500)
	sources: list[SourceRef] = Field(default_factory=list, max_length=20)


def memory_tools(ctx: ToolContext) -> list[Tool]:
	def recall(args: RecallArgs) -> dict:
		ensure_indexes(ctx.memory)
		params = {"user": ctx.user.user, "topic": args.topic.lower()}
		runs = [
			ctx.memory.ro("Past messages", RECALL, params),
			ctx.memory.ro("Saved insights", RECALL_INSIGHTS, params),
		]
		return result(ctx, runs, [])

	def remember_insight(args: RememberInsightArgs) -> dict:
		ensure_indexes(ctx.memory)
		ctx.memory.write(
			SAVE_INSIGHT,
			{
				"user": ctx.user.user,
				"text": args.text,
				"now": datetime.now(timezone.utc).isoformat(),
				"refs": entity_refs([s.model_dump() for s in args.sources]),
			},
		)
		return {"saved": True, "text": args.text}

	return [
		Tool(
			"recall",
			"Search this user's past conversations with Eve and the insights they saved. Use when they refer to earlier chats ('last time', 'what did we find about...').",
			RecallArgs,
			recall,
		),
		Tool(
			"remember_insight",
			"Save a finding for this user so it can be recalled in later sessions, linked to the records it came from. Use when the user asks you to remember something, or after an important conclusion they confirm.",
			RememberInsightArgs,
			remember_insight,
		),
	]
