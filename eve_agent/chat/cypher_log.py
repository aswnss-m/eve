"""Store every model-written Cypher (run_cypher) with the question behind it.

A future fast model can match a new question against these rows and reuse a
Cypher that worked. Logging never fails a turn: errors go to Error Log.
"""

import json

import frappe
from frappe.utils import now_datetime

LOG_DOCTYPE = "Eve Cypher Log"
MAX_QUESTION = 2000
MAX_ERROR = 1000


def record(entry: dict) -> None:
	try:
		doc = frappe.get_doc(
			{
				"doctype": LOG_DOCTYPE,
				"question": (entry.get("question") or "")[:MAX_QUESTION],
				"purpose": (entry.get("purpose") or "")[:140],
				"cypher": entry.get("cypher") or "",
				"params": json.dumps(entry.get("params") or {}, default=str),
				"row_count": int(entry.get("row_count") or 0),
				"ms": int(entry.get("ms") or 0),
				"status": entry.get("status") or "Success",
				"error": (entry.get("error") or "")[:MAX_ERROR],
				"user": entry.get("user"),
				"chat_id": entry.get("chat_id") or "",
				"model": entry.get("model") or "",
				"data_graph": entry.get("data_graph") or "",
				"asked_at": now_datetime(),
			}
		)
		doc.insert(ignore_permissions=True)
		# The request already committed before the answer started streaming; commit our own write.
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Eve Agent: could not write Cypher log")
