"""Who may chat with Eve, and the Settings they read. The chat itself is in eve_agent.api.chat."""

import frappe

from eve_agent.falkor.importer.data_rows import parse_lines
from eve_agent.sync.schema_sync import SETTINGS_DOCTYPE

DEFAULT_CHAT_ROLES = ("System Manager", "Sales Manager", "Accounts Manager")


def get_settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def chat_allowed(settings=None, user: str | None = None) -> bool:
	settings = settings or get_settings()
	if not settings.enable_chat:
		return False
	user = user or frappe.session.user
	if user == "Guest":
		return False
	roles = set(parse_lines(settings.chat_roles) or DEFAULT_CHAT_ROLES)
	return bool(roles & set(frappe.get_roles(user)))
