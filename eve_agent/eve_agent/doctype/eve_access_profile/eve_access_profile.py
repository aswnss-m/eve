from frappe.model.document import Document


class EveAccessProfile(Document):
	def on_trash(self):
		# Also when a System Manager deletes the profile from the list: its graphs go with it.
		from eve_agent.access.profiles import drop_graphs

		drop_graphs(self.name, self.active_graph)
