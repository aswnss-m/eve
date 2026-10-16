"""Who may chat with Eve, and the Settings they read. The chat itself is in eve_agent.api.chat."""

import frappe

from eve_agent.falkor.importer.data_rows import parse_lines
from eve_agent.sync.schema_sync import SETTINGS_DOCTYPE

DEFAULT_CHAT_ROLES = ("System Manager", "Sales Manager", "Accounts Manager")


def get_settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def write_doctypes(settings=None) -> list[str]:
	"""DocTypes Eve may propose records for. .get: cached Settings may predate the field."""
	settings = settings or get_settings()
	return parse_lines(settings.get("write_doctypes"))


def write_allowed(settings, mode: str, doctype: str) -> bool:
	"""Settings-level gate for record writes. Frappe's own permissions are checked separately."""
	flag = settings.get("allow_create") if mode == "create" else settings.get("allow_update")
	return bool(flag) and doctype in write_doctypes(settings)


def chat_allowed(settings=None, user: str | None = None) -> bool:
	settings = settings or get_settings()
	if not settings.enable_chat:
		return False
	user = user or frappe.session.user
	if user == "Guest":
		return False
	roles = set(parse_lines(settings.chat_roles) or DEFAULT_CHAT_ROLES)
	return bool(roles & set(frappe.get_roles(user)))
