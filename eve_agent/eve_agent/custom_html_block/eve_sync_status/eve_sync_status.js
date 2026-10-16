const grid = root_element.querySelector(".eve-sync-grid");
const esc = frappe.utils.escape_html;

function when(dt) {
	if (!dt) return __("never");
	return `<span title="${esc(frappe.datetime.str_to_user(dt))}">${esc(frappe.datetime.prettyDate(dt))}</span>`;
}

function pill(status) {
	if (!status) return "";
	const color = status === "Success" ? "green" : "red";
	return `<span class="indicator-pill ${color}">${esc(__(status))}</span>`;
}

function card(title, dt, status, summary) {
	return `<div class="eve-sync-card">
		<div class="head"><b>${esc(title)}</b>${pill(status)}</div>
		<div>${when(dt)}</div>
		<div class="summary">${esc(summary || "")}</div>
	</div>`;
}

function render(s) {
	if (!s) return;
	const data_title = s.active_data_graph ? `${__("Data graph")} · ${s.active_data_graph}` : __("Data graph");
	grid.innerHTML =
		card(__("Schema graph"), s.last_synced_at, s.last_sync_status, s.last_sync_summary) +
		card(data_title, s.last_data_synced_at, s.last_data_sync_status, s.last_data_sync_summary) +
		card(__("Incremental"), s.last_incremental_sync_at, "", s.last_incremental_summary);
}

const load = () => frappe.xcall("eve_agent.api.settings.sync_status").then(render);
load();
frappe.realtime.on("eve_agent_sync_done", load);
