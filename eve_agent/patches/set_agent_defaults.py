"""Fill the Eve Chat settings on sites installed before they existed.

Single DocTypes only get field defaults when first created, so existing sites
would otherwise start with chat disabled and no LLM model.
"""

import frappe

SETTINGS_DOCTYPE = "Eve Agent Settings"


def execute():
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	values = {}
	for fieldname in ("enable_chat", "chat_roles", "llm_model"):
		field = meta.get_field(fieldname)
		if field and field.default and not frappe.db.get_single_value(SETTINGS_DOCTYPE, fieldname):
			values[fieldname] = field.default
	if values:
		frappe.db.set_single_value(SETTINGS_DOCTYPE, values)
