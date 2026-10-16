import frappe
from frappe import _
from frappe.model.document import Document

from eve_agent.falkor.importer.data_rows import parse_lines


class EveAgentSettings(Document):
	def validate(self):
		# Typos in DocType lists are reported, never blocking: the sync ignores what it cannot find.
		self._warn_unknown("extra_doctypes", _("Additional DocTypes to Graph"), skip_child_and_single=True)
		self._warn_unknown("write_doctypes", _("DocTypes Eve may write"))

	def _warn_unknown(self, fieldname: str, label: str, skip_child_and_single: bool = False) -> None:
		problems = []
		for name in parse_lines(self.get(fieldname)):
			if not frappe.db.exists("DocType", name):
				problems.append(_("{0} is not a DocType").format(name))
				continue
			if skip_child_and_single:
				meta = frappe.get_meta(name)
				if meta.istable:
					problems.append(_("{0} is a child table; its rows sync with their parent").format(name))
				elif meta.issingle:
					problems.append(_("{0} is a single DocType and is not synced").format(name))
		if problems:
			frappe.msgprint(
				"<br>".join(problems), title=_("{0}: some lines will be ignored").format(label), indicator="orange"
			)
