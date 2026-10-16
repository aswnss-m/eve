<template>
	<details class="eve-step" :class="'eve-step-' + state">
		<summary>
			<span class="eve-step-dot" aria-hidden="true"></span>
			<span class="eve-step-name">{{ label }}</span>
			<span class="eve-step-meta">{{ meta }}</span>
			<svg class="eve-step-chevron" viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
				<path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
			</svg>
		</summary>
		<div class="eve-step-body">
			<div v-if="part.input && Object.keys(part.input).length" class="eve-step-input">
				<code>{{ JSON.stringify(part.input) }}</code>
			</div>
			<div v-if="part.state === 'output-error'" class="eve-step-error">{{ part.errorText }}</div>
			<div v-if="output && output.note" class="eve-step-note">{{ output.note }}</div>
			<div v-for="(q, i) in queries" :key="i" class="eve-query">
				<div class="eve-query-title">{{ q.title }} · {{ __("{0} rows", [q.rows.length]) }} · {{ q.ms }} ms</div>
				<pre class="eve-cypher">{{ q.cypher }}</pre>
				<div v-if="q.rows.length" class="eve-rows">
					<table>
						<thead>
							<tr><th v-for="c in columns(q)" :key="c">{{ c }}</th></tr>
						</thead>
						<tbody>
							<tr v-for="(row, r) in q.rows.slice(0, 8)" :key="r">
								<td v-for="c in columns(q)" :key="c">{{ cell(row[c]) }}</td>
							</tr>
						</tbody>
					</table>
					<div v-if="q.rows.length > 8" class="eve-step-note">{{ __("+{0} more rows", [q.rows.length - 8]) }}</div>
				</div>
			</div>
		</div>
	</details>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({ part: { type: Object, required: true } });
const __ = window.__;

const LABELS = {
	find_entity: "Looked up a name",
	top_selling_items: "Ranked items by sales",
	top_customers: "Ranked customers",
	top_suppliers: "Ranked suppliers",
	customer_profile: "Profiled a customer",
	item_profile: "Profiled an item",
	also_bought: "Followed customer → item → customer → item",
	dormant_customers: "Found quiet customers",
	supplier_risk: "Checked single-source items",
	who_knows: "Found the people involved",
	decision_trail: "Traced the document flow",
	explain_connection: "Found the shortest path",
	graph_schema: "Read the graph schema",
	describe_doctype: "Read a DocType's fields",
	run_cypher: "Ran a custom query",
	record_form: "Read the fields a record needs",
	propose_record: "Proposed a record",
	recall: "Searched past conversations",
	remember_insight: "Saved to memory",
};

const name = computed(() => props.part.toolName || props.part.type.replace(/^tool-/, ""));
const label = computed(() => __(LABELS[name.value] || name.value));
const state = computed(() =>
	props.part.state === "output-available" ? "done" : props.part.state === "output-error" ? "error" : "running"
);
const output = computed(() => (props.part.state === "output-available" ? props.part.output : null));
const queries = computed(() => (output.value && output.value.queries) || []);
const meta = computed(() => {
	if (state.value === "running") return __("running");
	if (state.value === "error") return __("failed");
	const rows = queries.value.reduce((n, q) => n + q.rows.length, 0);
	const n = queries.value.length;
	return n ? __("{0} {1}, {2} rows", [n, n === 1 ? __("query") : __("queries"), rows]) : "";
});

function columns(q) {
	return Object.keys(q.rows[0] || {});
}

function cell(value) {
	if (value == null) return "";
	if (typeof value === "number") return Number.isInteger(value) ? value : value.toFixed(2);
	if (typeof value === "object") return JSON.stringify(value);
	return String(value);
}
</script>

<style>
.eve-step {
	margin: 6px 0;
	font-size: var(--text-sm, 12px);
	color: var(--text-muted, #6c7680);
}
.eve-step summary {
	display: flex;
	align-items: center;
	gap: 8px;
	padding: 4px 0;
	cursor: pointer;
	list-style: none;
	user-select: none;
}
.eve-step summary::-webkit-details-marker {
	display: none;
}
.eve-step-dot {
	width: 6px;
	height: 6px;
	border-radius: 50%;
	background: var(--primary, #2490ef);
	flex: none;
}
.eve-step-running .eve-step-dot {
	animation: eve-pulse 1.1s ease-in-out infinite;
}
.eve-step-error .eve-step-dot {
	background: var(--red-500, #e24c4c);
}
.eve-step-name {
	color: var(--text-color, #1f272e);
	font-weight: 500;
}
.eve-step-meta {
	opacity: 0.8;
}
.eve-step-chevron {
	margin-left: 2px;
	transition: transform 0.2s ease;
	opacity: 0.6;
}
.eve-step[open] .eve-step-chevron {
	transform: rotate(180deg);
}
.eve-step-body {
	margin: 4px 0 8px 14px;
	padding-left: 10px;
	border-left: 1px solid var(--border-color, #e2e6e9);
	display: flex;
	flex-direction: column;
	gap: 8px;
}
.eve-step-input code {
	font-size: 11px;
	background: transparent;
	color: var(--text-muted, #6c7680);
	word-break: break-all;
}
.eve-step-error {
	color: var(--red-500, #c0392b);
}
.eve-query-title {
	font-weight: 500;
	margin-bottom: 4px;
}
.eve-cypher {
	margin: 0;
	padding: 8px 10px;
	max-height: 160px;
	overflow: auto;
	white-space: pre-wrap;
	font-size: 11px;
	background: var(--control-bg, #f4f5f6);
	color: var(--text-color, #1f272e);
	border-radius: var(--border-radius, 8px);
}
.eve-rows {
	margin-top: 6px;
	overflow-x: auto;
}
.eve-rows table {
	width: 100%;
	border-collapse: collapse;
	font-size: 11px;
}
.eve-rows th,
.eve-rows td {
	padding: 3px 6px;
	border-bottom: 1px solid var(--border-color, #e2e6e9);
	text-align: left;
	white-space: nowrap;
	color: var(--text-color, #1f272e);
}
.eve-rows th {
	color: var(--text-muted, #6c7680);
	font-weight: 500;
}
.eve-step-note {
	font-size: 11px;
	color: var(--text-muted, #6c7680);
}
@keyframes eve-pulse {
	0%,
	100% {
		opacity: 0.3;
		transform: scale(0.8);
	}
	50% {
		opacity: 1;
		transform: scale(1.15);
	}
}
</style>
