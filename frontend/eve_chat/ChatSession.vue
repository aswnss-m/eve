<template>
	<div class="eve-session">
		<div ref="scroller" class="eve-scroll" @scroll="onScroll">
			<div class="eve-column">
				<div v-if="!messages.length" class="eve-empty">
					<div class="eve-greeting">{{ __("What would you like to know?") }}</div>
					<p class="eve-empty-sub">
						{{ __("Ask about customers, items, suppliers, people and documents. Every answer shows the queries and records behind it.") }}
					</p>
					<div class="eve-starters">
						<button v-for="prompt in STARTERS" :key="prompt" class="eve-starter" @click="send(prompt)">
							{{ prompt }}
						</button>
					</div>
				</div>

				<Message
					v-for="(message, i) in messages"
					:key="message.id"
					:message="message"
					:streaming="busy && i === messages.length - 1 && message.role === 'assistant'"
				/>

				<div v-if="status === 'submitted'" class="eve-skeleton" aria-live="polite" :aria-label="__('Eve is thinking')">
					<span class="eve-skeleton-line" style="width: 92%"></span>
					<span class="eve-skeleton-line" style="width: 78%"></span>
					<span class="eve-skeleton-line" style="width: 40%"></span>
				</div>

				<div v-if="error" class="eve-error-inline" role="alert">
					<span>{{ error.message || __("Eve could not answer.") }}</span>
					<button type="button" class="eve-retry" @click="regenerate()">{{ __("Retry") }}</button>
				</div>
			</div>
		</div>

		<button
			v-show="!atBottom && messages.length"
			type="button"
			class="eve-jump"
			:aria-label="__('Scroll to latest message')"
			@click="scrollToBottom(true)"
		>
			<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
				<path d="M12 5v14M6 13l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
			</svg>
		</button>

		<div class="eve-dock">
			<div class="eve-column">
				<Composer ref="composer" :busy="busy" :placeholder="__('Ask Eve…')" @send="send" @stop="stop" />
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, nextTick, provide, ref, watch } from "vue";
import Composer from "./Composer.vue";
import Message from "./Message.vue";

// The Desk session cookie and CSRF token authenticate the stream.
const API = "/api/method/eve_agent.api.chat.stream";
const NEAR_BOTTOM_PX = 96;

const props = defineProps({
	chatId: { type: String, required: true },
});

const __ = window.__;
const STARTERS = [
	__("Top 10 best-selling items this year"),
	__("Who are our best customers?"),
	__("Which customers have gone quiet in the last 90 days?"),
	__("Which items depend on a single supplier?"),
];
const STORE_KEY = `eve_chat:messages:${frappe.session.user}:${props.chatId}`;

function loadMessages() {
	try {
		return JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
	} catch {
		return [];
	}
}

const messages = ref(loadMessages());
const status = ref("ready"); // ready | submitted | streaming | error
const error = ref(null);
const atBottom = ref(true);
const scroller = ref(null);
const composer = ref(null);
let controller = null;

const busy = computed(() => status.value === "submitted" || status.value === "streaming");

function uid() {
	return Math.random().toString(36).slice(2, 10);
}

function send(text) {
	const value = (text ?? "").trim();
	if (!value || busy.value) return;
	messages.value.push({ id: uid(), role: "user", parts: [{ type: "text", text: value }] });
	atBottom.value = true;
	stream();
}

function regenerate() {
	if (busy.value) return;
	const last = messages.value[messages.value.length - 1];
	if (last && last.role === "assistant") messages.value.pop();
	stream();
}

function stop() {
	controller?.abort();
}

// Posts the conversation and applies the server's events to a new assistant message.
async function stream() {
	const history = messages.value.filter((m) => m.parts.length).map(({ id, role, parts }) => ({ id, role, parts }));
	messages.value.push({ id: uid(), role: "assistant", parts: [] });
	const reply = messages.value[messages.value.length - 1];

	status.value = "submitted";
	error.value = null;
	controller = new AbortController();
	try {
		const res = await fetch(API, {
			method: "POST",
			headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token },
			body: JSON.stringify({ id: props.chatId, messages: history }),
			signal: controller.signal,
		});
		if (!res.ok) throw new Error(await failureText(res));
		await readEvents(res.body, reply);
		status.value = "ready";
	} catch (err) {
		if (err.name === "AbortError") {
			status.value = "ready";
		} else {
			error.value = err;
			status.value = "error";
		}
	} finally {
		controller = null;
		if (!reply.parts.length) messages.value.pop();
	}
}

async function failureText(res) {
	try {
		const body = await res.json();
		const first = JSON.parse(body._server_messages || "[]")[0];
		return (first && JSON.parse(first).message) || __("Eve could not answer.");
	} catch {
		return __("Eve could not answer.");
	}
}

async function readEvents(body, reply) {
	const reader = body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";
	for (;;) {
		const { value, done } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		let end = buffer.indexOf("\n\n");
		while (end !== -1) {
			const event = parseEvent(buffer.slice(0, end));
			buffer = buffer.slice(end + 2);
			if (event) apply(reply, event);
			end = buffer.indexOf("\n\n");
		}
	}
}

function parseEvent(block) {
	const data = block
		.split("\n")
		.filter((line) => line.startsWith("data:"))
		.map((line) => line.slice(5).trimStart())
		.join("\n");
	return data ? JSON.parse(data) : null;
}

// Builds the message parts the widget renders: text, and tool-* parts with state and output.
function apply(reply, event) {
	if (event.type === "error") throw new Error(event.errorText);
	if (event.type === "text-delta") {
		status.value = "streaming";
		const last = reply.parts[reply.parts.length - 1];
		if (last && last.type === "text") last.text += event.delta;
		else reply.parts.push({ type: "text", text: event.delta });
	} else if (event.type === "tool-start") {
		status.value = "streaming";
		reply.parts.push({
			type: `tool-${event.toolName}`,
			toolName: event.toolName,
			toolCallId: event.toolCallId,
			state: "input-available",
			input: event.input,
		});
	} else if (event.type === "tool-output" || event.type === "tool-error") {
		const part = reply.parts.find((p) => p.toolCallId === event.toolCallId);
		if (!part) return;
		if (event.type === "tool-output") {
			part.state = "output-available";
			part.output = event.output;
		} else {
			part.state = "output-error";
			part.errorText = event.errorText;
		}
	}
}

function onScroll() {
	const el = scroller.value;
	if (!el) return;
	atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
}

// Follow the answer while it streams, but only if the reader has not scrolled up to read.
function scrollToBottom(smooth = false) {
	const el = scroller.value;
	if (!el) return;
	el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
	atBottom.value = true;
}

watch(
	messages,
	async () => {
		await nextTick();
		if (atBottom.value) scrollToBottom(false);
	},
	{ deep: true }
);

// Save the chat once a reply is complete (and when a card changes), so it survives page reloads.
function persist() {
	try {
		localStorage.setItem(STORE_KEY, JSON.stringify(messages.value.slice(-40)));
	} catch {
		// Storage full or blocked: keep the chat in memory only.
	}
}
provide("evePersist", persist);

watch(status, (value) => {
	if (value !== "ready") return;
	persist();
	composer.value?.focus();
});

function focus() {
	composer.value?.focus();
}

defineExpose({ focus });
</script>

<style>
.eve-session {
	position: relative;
	display: flex;
	flex-direction: column;
	flex: 1;
	min-height: 0;
}
.eve-scroll {
	flex: 1;
	min-height: 0;
	overflow-y: auto;
	scroll-behavior: auto;
	padding: 8px 0 24px;
	overscroll-behavior: contain;
}
.eve-column {
	width: 100%;
	max-width: 720px;
	margin: 0 auto;
	padding: 0 20px;
	box-sizing: border-box;
}
.eve-scroll > .eve-column {
	display: flex;
	flex-direction: column;
	gap: 26px;
}
.eve-empty {
	margin-top: 9vh;
	display: flex;
	flex-direction: column;
	gap: 10px;
	animation: eve-rise 0.45s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}
.eve-greeting {
	font-size: 22px;
	font-weight: 600;
	letter-spacing: -0.01em;
	color: var(--text-color, #1f272e);
}
.eve-empty-sub {
	margin: 0 0 14px;
	font-size: var(--text-md, 14px);
	line-height: 1.6;
	color: var(--text-muted, #6c7680);
	max-width: 520px;
}
.eve-starters {
	display: flex;
	flex-direction: column;
	gap: 6px;
}
.eve-starter {
	text-align: left;
	padding: 10px 14px;
	border: 1px solid var(--border-color, #e2e6e9);
	border-radius: 14px;
	background: var(--fg-color, #fff);
	color: var(--text-color, #1f272e);
	font-size: var(--text-md, 14px);
	cursor: pointer;
	transition: background 0.15s ease, transform 0.15s ease;
}
.eve-starter:hover {
	background: var(--control-bg, #f4f5f6);
}
.eve-starter:active {
	transform: scale(0.99);
}
.eve-skeleton {
	display: flex;
	flex-direction: column;
	gap: 10px;
	padding: 6px 0;
}
.eve-skeleton-line {
	display: block;
	height: 12px;
	border-radius: 6px;
	background: linear-gradient(
		90deg,
		var(--skeleton-bg, #f3f3f3) 0%,
		var(--fg-color, #fff) 50%,
		var(--skeleton-bg, #f3f3f3) 100%
	);
	background-size: 200% 100%;
	animation: eve-shimmer 1.4s ease-in-out infinite;
}
.eve-error-inline {
	display: flex;
	align-items: center;
	gap: 10px;
	padding: 10px 14px;
	border-radius: 14px;
	background: var(--control-bg, #f4f5f6);
	color: var(--red-500, #c0392b);
	font-size: var(--text-sm, 12px);
}
.eve-retry {
	margin-left: auto;
	border: 1px solid var(--border-color, #e2e6e9);
	background: var(--fg-color, #fff);
	color: var(--text-color, #1f272e);
	border-radius: 999px;
	padding: 2px 12px;
	font-size: 12px;
	cursor: pointer;
}
.eve-dock {
	padding: 6px 0 16px;
	background: linear-gradient(to bottom, transparent, var(--fg-color, #fff) 40%);
}
.eve-dock .eve-column {
	padding: 0 16px;
}
.eve-jump {
	position: absolute;
	left: 50%;
	bottom: 96px;
	transform: translateX(-50%);
	width: 32px;
	height: 32px;
	border-radius: 50%;
	border: 1px solid var(--border-color, #e2e6e9);
	background: var(--fg-color, #fff);
	color: var(--text-color, #1f272e);
	box-shadow: var(--shadow-md, 0 2px 8px rgba(0, 0, 0, 0.12));
	display: inline-flex;
	align-items: center;
	justify-content: center;
	cursor: pointer;
	z-index: 2;
	animation: eve-rise 0.25s ease both;
}
@keyframes eve-shimmer {
	0% {
		background-position: 100% 0;
	}
	100% {
		background-position: -100% 0;
	}
}
@media (prefers-reduced-motion: reduce) {
	.eve-skeleton-line,
	.eve-empty {
		animation: none;
	}
}
</style>
