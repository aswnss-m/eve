"""Read DocType metadata from Frappe and return plain dicts.

This is the only module in the sync path that talks to Frappe's DB.
"""

import frappe

FIELD_KEYS = ("fieldname", "fieldtype", "options", "label", "reqd")


def read_doctype_metas() -> list[dict]:
	metas = []
	for name in frappe.get_all("DocType", pluck="name"):
		meta = frappe.get_meta(name)
		metas.append(
			{
				"name": meta.name,
				"module": meta.module,
				"is_submittable": meta.is_submittable,
				"is_single": meta.issingle,
				"is_child": meta.istable,
				"is_tree": meta.is_tree,
				"title_field": meta.title_field,
				"modified": meta.modified,
				"fields": [{k: getattr(f, k, None) for k in FIELD_KEYS} for f in meta.fields],
			}
		)
	return metas
