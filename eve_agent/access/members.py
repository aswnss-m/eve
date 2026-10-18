"""Whom each access profile serves: enabled System Users with a Chat Role, counted per profile key.

Counting resolves every such user's scope, so the counts are cached for a few minutes. Sweeps
recompute them; a chat request at the profile limit only reads the cache, or computes it once.
"""

from collections import Counter

import frappe

from eve_agent.access.resolve import resolve_scope
from eve_agent.api.agent import DEFAULT_CHAT_ROLES
from eve_agent.falkor.importer.data_rows import parse_lines
from eve_agent.sync.data_sync import load_plan

CACHE_KEY = "eve_agent:profile_members"
UNAVAILABLE_KEY = "eve_agent:profile_members_unavailable"
CACHE_SECONDS = 300


def chat_users(settings) -> list[str]:
	"""Enabled System Users with a Chat Role. Unlike chat_allowed, it ignores Enable Chat."""
	roles = set(parse_lines(settings.chat_roles) or DEFAULT_CHAT_ROLES)
	users = frappe.get_all("User", {"enabled": 1, "user_type": "System User"}, pluck="name")
	return [user for user in users if roles & set(frappe.get_roles(user))]


def member_counts(settings) -> Counter:
	"""Profile key -> how many chat users map to it, computed now and cached for later readers."""
	plan = load_plan(settings)
	counts = Counter(resolve_scope(user, plan).key for user in chat_users(settings))
	frappe.cache.set_value(CACHE_KEY, dict(counts), expires_in_sec=CACHE_SECONDS)
	frappe.cache.delete_value(UNAVAILABLE_KEY)
	return counts


def cached_member_counts(settings) -> Counter | None:
	"""The cached counts, else member_counts. None when they can't be computed: the failure is
	logged and not retried for a few minutes, so a broken user does not fail every chat request."""
	cached = frappe.cache.get_value(CACHE_KEY, expires=True)
	if cached is not None:
		return Counter(cached)
	if frappe.cache.get_value(UNAVAILABLE_KEY, expires=True):
		return None
	try:
		return member_counts(settings)
	except Exception:
		frappe.cache.set_value(UNAVAILABLE_KEY, 1, expires_in_sec=CACHE_SECONDS)
		frappe.log_error(title="Eve Agent: could not count access profile members")
		return None
