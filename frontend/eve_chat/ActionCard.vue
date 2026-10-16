<template>
	<div class="eve-card" :class="'eve-card-' + action.status">
		<div class="eve-card-head">
			<span class="eve-card-kind">{{ heading }}</span>
			<span v-if="action.is_submittable && action.status === 'pending'" class="eve-card-badge">{{ __("draft only") }}</span>
		</div>
		<div v-if="action.summary" class="eve-card-summary">{{ action.summary }}</div>

		<dl v-if="scalars.length" class="eve-card-fields">
			<template v-for="[key, value] in scalars" :key="key">
				<dt>{{ key }}</dt>
				<dd>{{ display(value) }}</dd>
			</template>
		</dl>

		<div v-for="[key, rows] in tables" :key="key" class="eve-card-table">
			<div class="eve-card-table-name">{{ key }} · {{ __("{0} rows", [rows.length]) }}</div>
			<table>
				<thead>
					<tr><th v-for="c in columns(rows)" :key="c">{{ c }}</th></tr>
				</thead>
				<tbody>
					<tr v-for="(row, r) in rows" :key="r">
						<td v-for="c in columns(rows)" :key="c">{{ display(row[c]) }}</td>
					</tr>
				</tbody>
			</table>
		</div>

		<div v-if="action.status === 'error'" class="eve-card-error">{{ action.error }}</div>

		<div class="eve-card-actions">
			<template v-if="action.status === 'pending'">
				<button type="button" class="eve-card-btn eve-card-primary" :disabled="busy" @click="confirm">
					{{ busy ? __("Saving…") : confirmLabel }}
				</button>
				<button type="button" class="eve-card-btn" :disabled="busy" @click="cancel">{{ __("Cancel") }}</button>
			</template>
			<template v-else-if="done">
				<span class="eve-card-status">{{ doneText }}</span>
				<button type="button" class="eve-card-btn" @click="openRecord">{{ __("Open") }}</button>
			</template>
			<span v-else-if="action.status === 'cancelled'" class="eve-card-status">{{ __("Cancelled") }}</span>
			<template v-else-if="action.status === 'error'">
				<button type="button" class="eve-card-btn eve-card-primary" :disabled="busy" @click="confirm">{{ __("Try again") }}</button>
				<button type="button" class="eve-card-btn" :disabled="busy" @click="cancel">{{ __("Cancel") }}</button>
			</template>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";

const props = defineProps({ part: { type: Object, required: true } });
const emit = defineEmits(["changed"]);
const __ = window.__;

const action = computed(() => props.part.output.action);
const busy = ref(false);
const done = computed(() => action.value.status === "created" || action.value.status === "updated");

const heading = computed(() => {
	const a = action.value;
	if (a.kind === "update") return __("Update {0} {1}", [a.doctype, a.name]);
	return a.is_submittable ? __("Create {0} (draft)", [a.doctype]) : __("Create {0}", [a.doctype]);
});
const confirmLabel = computed(() => {
	const a = action.value;
	if (a.kind === "update") return __("Save changes");
	return a.is_submittable ? __("Create draft") : __("Create");
});
const doneText = computed(() => {
	const a = action.value;
	const what = a.title && a.title !== a.name ? `${a.name} (${a.title})` : a.name;
	return a.kind === "update" ? __("Saved {0}", [what]) : __("Created {0}", [what]);
});

const entries = computed(() => Object.entries(action.value.values || {}));
const scalars = computed(() => entries.value.filter(([, v]) => !Array.isArray(v)));
const tables = computed(() => entries.value.filter(([, v]) => Array.isArray(v) && v.length));

function columns(rows) {
	const keys = new Set();
	for (const row of rows) for (const k of Object.keys(row || {})) keys.add(k);
	return [...keys];
}

function display(value) {
	if (value == null || value === "") return "—";
	if (typeof value === "object") return JSON.stringify(value);
	return String(value);
}

async function confirm() {
	const a = action.value;
	busy.value = true;
	try {
		const created = await frappe.xcall("eve_agent.api.actions.apply", {
			mode: a.kind,
			doctype: a.doctype,
			values: a.values,
			name: a.name,
		});
		a.status = a.kind === "update" ? "updated" : "created";
		a.name = created.name;
		a.title = created.title;
		a.error = null;
		const sources = props.part.output.sources || (props.part.output.sources = []);
		if (!sources.some((s) => s.doctype === created.doctype && s.name === created.name)) {
			sources.push({ doctype: created.doctype, name: created.name, title: created.title });
		}
		emit("changed");
		offerToOpen(created);
	} catch (err) {
		a.status = "error";
		a.error = errorText(err);
		emit("changed");
	} finally {
		busy.value = false;
	}
}

function cancel() {
	action.value.status = "cancelled";
	emit("changed");
}

function openRecord() {
	const a = action.value;
	frappe.set_route(frappe.router.slug(a.doctype), a.name);
}

// After a write: a Desk dialog with the record name and a button that opens it in place.
function offerToOpen(created) {
	const draft = created.is_submittable && created.docstatus === 0;
	const message = draft
		? __("{0} {1} was created as a draft. Review and submit it in the Desk.", [created.doctype, created.name])
		: __("{0} {1} was saved.", [created.doctype, created.name]);
	const dialog = new frappe.ui.Dialog({
		title: action.value.kind === "update" ? __("Saved") : __("Created"),
		fields: [{ fieldtype: "HTML", fieldname: "body", options: `<p>${frappe.utils.escape_html(message)}</p>` }],
		primary_action_label: __("Open now"),
		primary_action() {
			dialog.hide();
			openRecord();
		},
		secondary_action_label: __("Later"),
	});
	dialog.show();
}

// frappe.xcall rejects with the response; the validation message is in _server_messages.
function errorText(err) {
	try {
		const first = JSON.parse(err._server_messages || "[]")[0];
		const message = first && JSON.parse(first).message;
		if (message) return frappe.utils.strip_html ? frappe.utils.strip_html(message) : message;
	} catch {
		// Fall through to the generic text.
	}
	return err?.message || (err?.exc_type ? `${err.exc_type}` : __("The record could not be saved."));
}
</script>

<style>
.eve-card {
	margin: 8px 0;
	padding: 12px 14px;
	border: 1px solid var(--border-color, #e2e6e9);
	border-radius: 14px;
	background: var(--fg-color, #fff);
	font-size: var(--text-sm, 12px);
	color: var(--text-color, #1f272e);
}
.eve-card-pending {
	border-color: var(--primary, #2490ef);
}
.eve-card-error {
	color: var(--red-500, #c0392b);
	margin-top: 8px;
}
.eve-card-head {
	display: flex;
	align-items: center;
	gap: 8px;
}
.eve-card-kind {
	font-weight: 600;
	font-size: var(--text-md, 14px);
}
.eve-card-badge {
	padding: 1px 8px;
	border-radius: 999px;
	background: var(--control-bg, #f4f5f6);
	color: var(--text-muted, #6c7680);
	font-size: 11px;
}
.eve-card-summary {
	margin-top: 4px;
	color: var(--text-muted, #6c7680);
}
.eve-card-fields {
	display: grid;
	grid-template-columns: max-content 1fr;
	gap: 4px 14px;
	margin: 10px 0 0;
}
.eve-card-fields dt {
	color: var(--text-muted, #6c7680);
	font-family: var(--font-mono, ui-monospace, Menlo, monospace);
	font-size: 11px;
	padding-top: 1px;
}
.eve-card-fields dd {
	margin: 0;
	overflow-wrap: anywhere;
}
.eve-card-table {
	margin-top: 10px;
	overflow-x: auto;
}
.eve-card-table-name {
	font-family: var(--font-mono, ui-monospace, Menlo, monospace);
	font-size: 11px;
	color: var(--text-muted, #6c7680);
	margin-bottom: 4px;
}
.eve-card-table table {
	width: 100%;
	border-collapse: collapse;
	font-size: 11px;
}
.eve-card-table th,
.eve-card-table td {
	padding: 3px 6px;
	border-bottom: 1px solid var(--border-color, #e2e6e9);
	text-align: left;
	white-space: nowrap;
}
.eve-card-table th {
	color: var(--text-muted, #6c7680);
	font-weight: 500;
}
.eve-card-actions {
	display: flex;
	align-items: center;
	gap: 8px;
	margin-top: 12px;
}
.eve-card-btn {
	border: 1px solid var(--border-color, #d1d8dd);
	background: var(--fg-color, #fff);
	color: var(--text-color, #1f272e);
	border-radius: 999px;
	padding: 4px 14px;
	font-size: 12px;
	cursor: pointer;
}
.eve-card-btn:disabled {
	opacity: 0.6;
	cursor: default;
}
.eve-card-primary {
	background: var(--primary, var(--blue-500, #2490ef));
	border-color: transparent;
	color: #fff;
	font-weight: 600;
}
.eve-card-status {
	color: var(--text-muted, #6c7680);
}
</style>
