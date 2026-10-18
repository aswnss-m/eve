const SYNC_DONE_EVENT = "eve_agent_sync_done";

function queue_sync(frm, method, label) {
	frappe.call({
		method,
		callback() {
			frappe.show_alert({ message: __("{0} queued", [label]), indicator: "blue" });
		},
	});
}

frappe.ui.form.on("Eve Agent Settings", {
	onload(frm) {
		frappe.realtime.off(SYNC_DONE_EVENT);
		frappe.realtime.on(SYNC_DONE_EVENT, (data) => {
			const ok = data.status === "Success";
			frappe.show_alert({
				message: __("{0} sync {1}", [data.kind === "data" ? __("Data graph") : __("Schema"), data.status]),
				indicator: ok ? "green" : "red",
			});
			if (frm.doc && !frm.is_dirty()) frm.reload_doc();
		});
	},

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

		const sync = __("Sync");
		frm.add_custom_button(
			__("Sync Schema Now"),
			() => queue_sync(frm, "eve_agent.api.sync.sync_schema", __("Schema sync")),
			sync
		);
		frm.add_custom_button(
			__("Sync Data Graph Now"),
			() => queue_sync(frm, "eve_agent.api.sync.sync_data_graph", __("Data graph sync")),
			sync
		);
		frm.add_custom_button(
			__("Full Resync"),
			() => queue_sync(frm, "eve_agent.api.sync.sync_all", __("Full resync")),
			sync
		);
		frm.add_custom_button(
			__("Sync Pending Changes Now"),
			() => queue_sync(frm, "eve_agent.api.sync.drain_now", __("Incremental sync")),
			sync
		);
		frm.add_custom_button(
			__("Rebuild Access Profiles"),
			() => queue_sync(frm, "eve_agent.api.sync.rebuild_access_profiles", __("Access profile rebuild")),
			sync
		);

		if (frm.doc.browser_url) {
			frm.add_custom_button(__("Open FalkorDB Browser"), () => window.open(frm.doc.browser_url, "_blank"));
		}
	},
});
