"""Fill the access profile defaults on sites that predate the fields (as set_sync_defaults does).

Single DocTypes only get field defaults when first created."""

import frappe

SETTINGS_DOCTYPE = "Eve Agent Settings"


def execute():
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	values = {}
	for fieldname in ("max_access_profiles", "access_profile_retention_days"):
		field = meta.get_field(fieldname)
		stored = frappe.db.sql(
			"select value from tabSingles where doctype = %s and field = %s", (SETTINGS_DOCTYPE, fieldname)
		)
		if field and field.default and not stored:
			values[fieldname] = field.default
	if values:
		frappe.db.set_single_value(SETTINGS_DOCTYPE, values)
