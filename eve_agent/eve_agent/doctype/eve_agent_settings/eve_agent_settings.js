frappe.ui.form.on("Eve Agent Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: "eve_agent.api.settings.test_connection",
				freeze: true,
				callback(r) {
					const res = r.message || {};
					if (res.ok) {
						frappe.msgprint(
							__("Connected. Redis {0}, graph module: {1}, graph: {2}", [
								res.redis_version,
								res.graph_module ? "yes" : "no",
								res.graph,
							])
						);
					} else {
						frappe.msgprint({ title: __("Connection failed"), message: res.error, indicator: "red" });
					}
				},
			});
		});

		frm.add_custom_button(__("Sync Schema Now"), () => {
			frappe.call({
				method: "eve_agent.api.sync.sync_schema",
				callback() {
					frappe.show_alert({ message: __("Schema sync queued"), indicator: "green" });
				},
			});
		});

		if (frm.doc.browser_url) {
			frm.add_custom_button(__("Open FalkorDB Browser"), () => window.open(frm.doc.browser_url, "_blank"));
		}
	},
});
