"""The chat's safety net on the Frappe side: is a cited record readable, and who hears when it is not.

The chat package stays free of Frappe: api/chat.py injects these into ToolContext as can_read and
on_hidden_source. A record that fails the check came from the user's own graph, so it is a profile
graph bug, and it is logged for admins.
"""

import json
from collections.abc import Callable

import frappe

HIDDEN_SOURCE_TITLE = "Eve: result outside the user's permissions"


def make_can_read(user: str) -> Callable[[str, str], bool]:
	"""can_read(doctype, name) for one turn, cached per record.

	Frappe's has_permission decides: it also counts shares and owner access, so it is broader than the
	profile rules and never rejects a correct record. A User always passes (every profile keeps Users);
	a child row is checked through its parent, and a Comment through the document it is on (Frappe gives
	no role read on Comment; comments show with their document, as in profile graphs). Anything
	unexpected, a record deleted since the graph was updated included, counts as unreadable."""
	cache: dict[tuple[str, str], bool] = {}

	def can_read(doctype: str, name: str) -> bool:
		key = (doctype, str(name))
		if key not in cache:
			cache[key] = _readable(doctype, str(name), user)
		return cache[key]

	return can_read


def _readable(doctype: str, name: str, user: str) -> bool:
	if not doctype or not name:
		return False
	if doctype == "User":
		return True
	try:
		if doctype == "Comment":
			fields = ["reference_doctype", "reference_name"]
			ref = frappe.db.get_value("Comment", name, fields, as_dict=True)
			if not (ref and ref.reference_doctype and ref.reference_name):
				return False
			doctype, name = ref.reference_doctype, ref.reference_name
		# Existence first: has_permission loads the document, and a missing one would raise and msgprint.
		if frappe.is_table(doctype):
			row = frappe.db.get_value(doctype, name, ["parenttype", "parent"], as_dict=True)
			if not (row and row.parenttype and row.parent and frappe.db.exists(row.parenttype, row.parent)):
				return False
		elif not frappe.db.exists(doctype, name):
			return False
		# For a child row Frappe checks its parent document (and the table field's permlevel).
		return bool(frappe.has_permission(doctype, "read", doc=name, user=user))
	except Exception:
		return False


def log_hidden_source(user: str) -> Callable[[dict], None]:
	"""on_hidden_source(source): an Error Log for admins. Committed at once: a streamed answer's request
	committed before streaming began. Logging never fails a turn.

	A record that no longer exists (deleted, not drained out of the graph yet) is dropped without a log:
	that is drain lag, not a profile graph bug."""

	def log(source: dict) -> None:
		try:
			if not frappe.db.exists(source["doctype"], source["name"]):
				return
			details = json.dumps({**source, "user": user}, default=str)
			frappe.log_error(title=HIDDEN_SOURCE_TITLE, message=details)
			frappe.db.commit()
		except Exception:
			pass

	return log
