"""Fill the incremental-sync and Eve Actions defaults on sites that predate the fields.

Single DocTypes only get field defaults when first created."""

import frappe

SETTINGS_DOCTYPE = "Eve Agent Settings"


def execute():
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	values = {}
	for fieldname in ("incremental_sync", "write_doctypes"):
		field = meta.get_field(fieldname)
		# get_single_value coerces a missing Check to 0, so look at the Singles row itself.
		stored = frappe.db.sql(
			"select value from tabSingles where doctype = %s and field = %s", (SETTINGS_DOCTYPE, fieldname)
		)
		if field and field.default and not stored:
			values[fieldname] = field.default
	if values:
		frappe.db.set_single_value(SETTINGS_DOCTYPE, values)
