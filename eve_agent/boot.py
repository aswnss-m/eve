from eve_agent.api.agent import chat_allowed, get_settings


def boot_session(bootinfo) -> None:
	"""Tell the Desk whether to render the Ask Eve button for this user, and which shortcut opens it."""
	settings = get_settings()
	bootinfo.eve = {
		"enabled": bool(settings.enable_chat),
		"can_chat": chat_allowed(settings),
		# .get, not attribute access: a Settings doc cached before the field existed has no attribute for it.
		"shortcut": settings.get("shortcut") or "Cmd+E",
	}
