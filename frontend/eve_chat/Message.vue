<template>
	<div :class="['eve-msg', 'eve-msg-' + message.role]" @click="onClick">
		<div v-if="message.role === 'user'" class="eve-bubble">{{ userText }}</div>

		<template v-else>
			<template v-for="(part, i) in message.parts" :key="i">
				<div v-if="part.type === 'text' && part.text" class="eve-md" v-html="html(part.text)"></div>
				<ActionCard v-else-if="isAction(part)" :part="part" @changed="persist" />
				<ToolStep v-else-if="isTool(part)" :part="part" />
			</template>

			<div v-if="sources.length" class="eve-sources">
				<a
					v-for="s in sources"
					:key="s.doctype + s.name"
					class="eve-source"
					:href="deskUrl(s)"
					:title="s.doctype + ': ' + s.name"
					:data-doctype="s.doctype"
					:data-name="s.name"
				>
					<span class="eve-source-type">{{ s.doctype }}</span>
					{{ s.title && s.title !== s.name ? s.title : s.name }}
				</a>
			</div>

			<div v-if="!streaming && answerText" class="eve-actions">
				<button type="button" class="eve-action" :aria-label="__('Copy answer')" @click="copyAnswer">
					{{ copied ? __("Copied") : __("Copy") }}
				</button>
				<span v-if="asOf" class="eve-asof">{{ __("Data as of {0}", [asOf]) }}</span>
			</div>
		</template>
	</div>
</template>

<script setup>
import { computed, inject, onBeforeUnmount, ref, watch } from "vue";
import ActionCard from "./ActionCard.vue";
import ToolStep from "./ToolStep.vue";
import { renderMarkdown } from "./markdown.js";
import { doctypeOf, lookupRecords } from "./locate.js";

const props = defineProps({
	message: { type: Object, required: true },
	streaming: { type: Boolean, default: false },
});

const __ = window.__;
// Saves the chat after a card changes state (ChatSession only saves when a reply ends).
const persist = inject("evePersist", () => {});
const copied = ref(false);
let copiedTimer = null;

const userText = computed(() =>
	props.message.parts
		.filter((p) => p.type === "text")
		.map((p) => p.text)
		.join("")
);
const answerText = computed(() =>
	props.message.parts
		.filter((p) => p.type === "text")
		.map((p) => p.text)
		.join("\n\n")
		.trim()
);

function isTool(part) {
	return part.type.startsWith("tool-") || part.type === "dynamic-tool";
}

// A record proposal: the tool output carries an action for the user to confirm.
function isAction(part) {
	return isTool(part) && part.state === "output-available" && part.output && part.output.action;
}

const outputs = computed(() =>
	props.message.parts.filter((p) => isTool(p) && p.state === "output-available" && p.output).map((p) => p.output)
);

// Every record the tools cited this answer, so names in the prose can link to them.
const allSources = computed(() => {
	const seen = new Set();
	const out = [];
	for (const output of outputs.value) {
		for (const s of output.sources || []) {
			const key = s.doctype + "|" + s.name;
			if (!seen.has(key)) {
				seen.add(key);
				out.push(s);
			}
		}
	}
	return out;
});

const sources = computed(() => allSources.value.slice(0, 12));

// Record IDs in the answer (e.g. SDR-0036, ACC-PINV-2026-00059) link too, even when no tool cited them.
const ID_PATTERN = /(?<![\w-])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+(?![\w-])/g;
const idNames = computed(() => {
	const found = new Set();
	for (const part of props.message.parts) {
		if (part.type === "text" && part.text) for (const m of part.text.matchAll(ID_PATTERN)) found.add(m[0]);
	}
	return [...found];
});
const lookedUp = ref(0);
watch(
	idNames,
	(names) => {
		if (names.length) lookupRecords(names).then(() => (lookedUp.value += 1));
	},
	{ immediate: true }
);

const linkRefs = computed(() => {
	void lookedUp.value; // Re-run once the server has answered the lookups.
	const refs = [...allSources.value];
	for (const name of idNames.value) {
		const doctype = doctypeOf(name);
		if (doctype) refs.push({ doctype, name });
	}
	return refs;
});

function html(text) {
	return renderMarkdown(text, { copyLabel: __("Copy"), refs: linkRefs.value });
}

const asOf = computed(() => {
	const output = outputs.value.find((o) => o.as_of);
	return output ? frappe.datetime.str_to_user(output.as_of) : "";
});

function deskUrl(s) {
	return `/app/${frappe.router.slug(s.doctype)}/${encodeURIComponent(s.name)}`;
}

// Opens a record inside the Desk. The page does not reload, so the chat and its panel stay as they are.
function openRecord(event, doctype, name) {
	if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; // Let the browser open a new tab.
	event.preventDefault();
	frappe.set_route(frappe.router.slug(doctype), name);
}

// Record links (in the answer or the source chips) open in place. Code blocks carry a copy button.
function onClick(event) {
	const link = event.target.closest(".eve-ref, .eve-source");
	if (link && link.dataset.doctype) {
		openRecord(event, link.dataset.doctype, link.dataset.name);
		return;
	}
	const button = event.target.closest("[data-copy]");
	if (!button) return;
	const code = button.closest(".eve-code")?.querySelector("code");
	copyText(code ? code.textContent : "");
	flash(button, __("Copied"));
}

function copyAnswer() {
	copied.value = true;
	clearTimeout(copiedTimer);
	copiedTimer = setTimeout(() => (copied.value = false), 1600);
	copyText(answerText.value);
}

function flash(button, done) {
	const label = button.textContent;
	button.textContent = done;
	setTimeout(() => (button.textContent = label), 1600);
}

// navigator.clipboard needs a secure context; the Desk may be served over plain http.
function copyText(text) {
	if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
	const area = document.createElement("textarea");
	area.value = text;
	area.style.position = "fixed";
	area.style.opacity = "0";
	document.body.appendChild(area);
	area.select();
	document.execCommand("copy");
	area.remove();
	return Promise.resolve();
}

onBeforeUnmount(() => clearTimeout(copiedTimer));
</script>

<style>
.eve-msg {
	animation: eve-rise 0.35s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}
.eve-msg-user {
	display: flex;
	justify-content: flex-end;
}
.eve-bubble {
	max-width: 82%;
	padding: 9px 14px;
	border-radius: 18px 18px 4px 18px;
	background: var(--control-bg, #f4f5f6);
	color: var(--text-color, #1f272e);
	font-size: var(--text-md, 14px);
	line-height: 1.5;
	white-space: pre-wrap;
	overflow-wrap: anywhere;
}
.eve-md {
	font-size: var(--text-md, 14px);
	line-height: 1.7;
	color: var(--text-color, #1f272e);
	overflow-wrap: anywhere;
}
.eve-md > *:first-child {
	margin-top: 0;
}
.eve-md > *:last-child {
	margin-bottom: 0;
}
.eve-md p {
	margin: 0 0 0.85em;
}
.eve-md h1,
.eve-md h2,
.eve-md h3,
.eve-md h4 {
	font-weight: 600;
	line-height: 1.35;
	margin: 1.25em 0 0.45em;
}
.eve-md h1 {
	font-size: 1.25em;
}
.eve-md h2 {
	font-size: 1.15em;
}
.eve-md h3 {
	font-size: 1.05em;
}
.eve-md h4 {
	font-size: 1em;
}
.eve-md ul,
.eve-md ol {
	margin: 0 0 0.85em;
	padding-left: 1.5em;
}
.eve-md li {
	margin: 0.2em 0;
}
.eve-md li > p {
	margin: 0 0 0.35em;
}
.eve-md strong {
	font-weight: 600;
}
.eve-md a {
	color: var(--primary, var(--blue-500, #2490ef));
	text-decoration: underline;
	text-decoration-thickness: 1px;
	text-underline-offset: 2px;
}
.eve-md a:hover {
	opacity: 0.8;
}
.eve-md code:not(pre code) {
	font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
	font-size: 0.88em;
	padding: 1px 5px;
	border-radius: 5px;
	background: var(--control-bg, #f4f5f6);
}
.eve-md blockquote {
	margin: 0.85em 0;
	padding-left: 12px;
	border-left: 3px solid var(--border-color, #e2e6e9);
	color: var(--text-muted, #6c7680);
}
.eve-md hr {
	border: none;
	border-top: 1px solid var(--border-color, #e2e6e9);
	margin: 1.2em 0;
}
.eve-code {
	margin: 0.85em 0;
	border: 1px solid var(--border-color, #e2e6e9);
	border-radius: 12px;
	background: var(--control-bg, #f4f5f6);
	overflow: hidden;
}
.eve-code-head {
	display: flex;
	align-items: center;
	justify-content: space-between;
	padding: 5px 8px 5px 12px;
	font-size: 11px;
	color: var(--text-muted, #6c7680);
	border-bottom: 1px solid var(--border-color, #e2e6e9);
}
.eve-copy {
	border: none;
	background: transparent;
	color: var(--text-muted, #6c7680);
	font-size: 11px;
	padding: 2px 8px;
	border-radius: 6px;
	cursor: pointer;
}
.eve-copy:hover {
	background: var(--fg-color, #fff);
	color: var(--text-color, #1f272e);
}
.eve-code pre {
	margin: 0;
	padding: 12px 14px;
	overflow-x: auto;
	background: transparent;
	font-size: 12.5px;
	line-height: 1.55;
}
.eve-code pre code {
	padding: 0;
	background: none;
	font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
	white-space: pre;
}
.eve-table {
	margin: 0.85em 0;
	overflow-x: auto;
}
.eve-table table {
	width: 100%;
	border-collapse: collapse;
	font-size: 13px;
}
.eve-table th,
.eve-table td {
	padding: 6px 10px;
	border-bottom: 1px solid var(--border-color, #e2e6e9);
	text-align: left;
	vertical-align: top;
}
.eve-table th {
	font-weight: 600;
	background: var(--control-bg, #f4f5f6);
	white-space: nowrap;
}
.eve-sources {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	margin-top: 10px;
}
.eve-source {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	max-width: 100%;
	padding: 3px 10px;
	border: 1px solid var(--border-color, #e2e6e9);
	border-radius: 999px;
	font-size: 12px;
	color: var(--text-color, #1f272e);
	text-decoration: none;
	transition: background 0.15s ease;
}
.eve-ref {
	color: var(--primary, var(--blue-500, #2490ef));
	text-decoration: none;
	border-bottom: 1px dotted currentColor;
	cursor: pointer;
	font-weight: 500;
	white-space: nowrap;
}
.eve-ref:hover {
	opacity: 0.75;
}
.eve-source:hover {
	background: var(--control-bg, #f4f5f6);
	color: var(--text-color, #1f272e);
}
.eve-source-type {
	color: var(--text-muted, #6c7680);
	font-size: 11px;
}
.eve-actions {
	display: flex;
	align-items: center;
	gap: 10px;
	margin-top: 8px;
	opacity: 0.85;
}
.eve-action {
	border: none;
	background: transparent;
	color: var(--text-muted, #6c7680);
	font-size: 12px;
	padding: 2px 8px;
	margin-left: -8px;
	border-radius: 6px;
	cursor: pointer;
}
.eve-action:hover {
	background: var(--control-bg, #f4f5f6);
	color: var(--text-color, #1f272e);
}
.eve-asof {
	font-size: 11px;
	color: var(--text-muted, #6c7680);
}
@keyframes eve-rise {
	from {
		opacity: 0;
		transform: translateY(6px);
	}
	to {
		opacity: 1;
		transform: none;
	}
}
@media (prefers-reduced-motion: reduce) {
	.eve-msg {
		animation: none;
	}
}
</style>
