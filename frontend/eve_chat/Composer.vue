<template>
	<div class="eve-composer-wrap">
		<div v-if="hint" class="eve-composer-hint" role="status">{{ hint }}</div>
		<div class="eve-composer" :class="{ 'is-busy': busy, 'is-listening': listening }">
			<textarea
				ref="input"
				v-model="draft"
				rows="1"
				class="eve-textarea"
				:placeholder="listening ? __('Listening…') : placeholder"
				:aria-label="__('Message Eve')"
				@input="resize"
				@keydown="onKeydown"
			></textarea>

			<!-- Secondary mic: only while there is text, since the empty box already offers the wave button. -->
			<button
				v-if="micSupported && !busy && !listening && hasText"
				type="button"
				class="eve-round eve-mic"
				:title="__('Dictate')"
				:aria-label="__('Dictate')"
				@click="toggleMic"
			>
				<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
					<rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" />
					<path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
				</svg>
			</button>

			<!-- Primary button: stop while Eve answers, animated wave while listening, arrow with text, wave when empty. -->
			<button
				v-if="busy"
				type="button"
				class="eve-round eve-stop"
				:title="__('Stop')"
				:aria-label="__('Stop')"
				@click="$emit('stop')"
			>
				<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="3" fill="currentColor" /></svg>
			</button>
			<button
				v-else-if="listening"
				type="button"
				class="eve-round eve-send eve-wave is-live"
				:title="__('Stop listening')"
				:aria-label="__('Stop listening')"
				aria-pressed="true"
				@click="toggleMic"
			>
				<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
					<rect class="eve-bar" x="3" y="9" width="2.6" height="6" rx="1.3" fill="currentColor" />
					<rect class="eve-bar" x="7.3" y="6" width="2.6" height="12" rx="1.3" fill="currentColor" />
					<rect class="eve-bar" x="11.6" y="3" width="2.6" height="18" rx="1.3" fill="currentColor" />
					<rect class="eve-bar" x="15.9" y="6" width="2.6" height="12" rx="1.3" fill="currentColor" />
					<rect class="eve-bar" x="20.2" y="9" width="2.6" height="6" rx="1.3" fill="currentColor" />
				</svg>
			</button>
			<button
				v-else-if="hasText || !micSupported"
				type="button"
				class="eve-round eve-send"
				:disabled="!hasText"
				:title="__('Send') + ' (Enter)'"
				:aria-label="__('Send')"
				@click="submit"
			>
				<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
					<path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
				</svg>
			</button>
			<button
				v-else
				type="button"
				class="eve-round eve-send eve-wave"
				:title="__('Dictate')"
				:aria-label="__('Dictate')"
				@click="toggleMic"
			>
				<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
					<rect x="3" y="9" width="2.6" height="6" rx="1.3" fill="currentColor" />
					<rect x="7.3" y="6" width="2.6" height="12" rx="1.3" fill="currentColor" />
					<rect x="11.6" y="3" width="2.6" height="18" rx="1.3" fill="currentColor" />
					<rect x="15.9" y="6" width="2.6" height="12" rx="1.3" fill="currentColor" />
					<rect x="20.2" y="9" width="2.6" height="6" rx="1.3" fill="currentColor" />
				</svg>
			</button>
		</div>
	</div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import { createDictation, isSupported } from "./dictation.js";

const MAX_HEIGHT = 200;
const HINT_MS = 4000;

const props = defineProps({
	busy: { type: Boolean, default: false },
	placeholder: { type: String, default: "" },
});
const emit = defineEmits(["send", "stop"]);

const __ = window.__;
const draft = ref("");
const hasText = computed(() => draft.value.trim().length > 0);
const input = ref(null);
const micSupported = isSupported();
const listening = ref(false);
const hint = ref("");
let hintTimer = null;
let dictation = null;
let draftBase = ""; // What was typed before dictation started; the transcript is appended to it.

function submit() {
	const text = draft.value.trim();
	if (!text || props.busy) return;
	stopDictation();
	draft.value = "";
	nextTick(resize);
	emit("send", text);
}

function onKeydown(event) {
	// Enter sends; Shift+Enter keeps the browser's newline. Enter that confirms IME text does not send.
	if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
	event.preventDefault();
	submit();
}

// Grow with the text, up to a cap, then scroll inside the box.
function resize() {
	const el = input.value;
	if (!el) return;
	el.style.height = "auto";
	el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
	el.style.overflowY = el.scrollHeight > MAX_HEIGHT ? "auto" : "hidden";
}

function showHint(text) {
	hint.value = text;
	clearTimeout(hintTimer);
	hintTimer = setTimeout(() => (hint.value = ""), HINT_MS);
}

// The transcript goes into the box for review; the user sends it with Enter or the button.
function setTranscript(text) {
	draft.value = draftBase + text;
	nextTick(resize);
}

function toggleMic() {
	if (listening.value) {
		dictation?.stop();
		return;
	}
	const typed = draft.value.trim();
	draftBase = typed ? `${typed} ` : "";
	dictation = createDictation({
		lang: frappe.boot?.lang,
		onInterim: setTranscript,
		onFinal: setTranscript,
		onError: showHint,
		onEnd: () => {
			listening.value = false;
			dictation = null;
			focus();
		},
	});
	try {
		dictation.start();
		listening.value = true;
		hint.value = "";
	} catch {
		showHint(__("Could not start the microphone."));
		dictation = null;
	}
}

function stopDictation() {
	if (!listening.value) return;
	dictation?.abort();
	listening.value = false;
	dictation = null;
}

function focus() {
	input.value?.focus();
}

onBeforeUnmount(() => {
	stopDictation();
	clearTimeout(hintTimer);
});

defineExpose({ focus, stopDictation });
</script>

<style>
.eve-composer {
	display: flex;
	align-items: flex-end;
	gap: 8px;
	padding: 6px 6px 6px 16px;
	border: 1px solid var(--border-color, #d1d8dd);
	border-radius: 26px;
	background: var(--fg-color, #fff);
	box-shadow: var(--shadow-sm, 0 1px 2px rgba(0, 0, 0, 0.08));
	transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.eve-composer:focus-within {
	border-color: var(--primary, var(--blue-500, #2490ef));
	box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary, #2490ef) 14%, transparent);
}
.eve-composer.is-listening {
	border-color: var(--primary, var(--blue-500, #2490ef));
	box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary, #2490ef) 18%, transparent);
}
.eve-composer-wrap {
	position: relative;
}
/* Floats above the box, so showing it never moves the input. */
.eve-composer-hint {
	position: absolute;
	left: 16px;
	right: 16px;
	bottom: calc(100% + 6px);
	font-size: 11px;
	color: var(--text-muted, #6c7680);
	pointer-events: none;
}
.eve-textarea {
	flex: 1;
	min-width: 0;
	/* One line (21px) plus padding is 34px: the same height as the round buttons, so they sit centred. */
	min-height: 34px;
	max-height: 200px;
	resize: none;
	border: none;
	outline: none;
	background: transparent;
	color: var(--text-color, #1f272e);
	font: inherit;
	font-size: 14px;
	line-height: 21px;
	padding: 6.5px 0;
	box-sizing: border-box;
}
.eve-textarea::placeholder {
	color: var(--text-muted, #8d99a6);
}
.eve-round {
	flex: none;
	display: inline-flex;
	align-items: center;
	justify-content: center;
	width: 34px;
	height: 34px;
	margin-bottom: 0;
	border: none;
	border-radius: 50%;
	cursor: pointer;
	transition: transform 0.15s ease, opacity 0.15s ease, background 0.15s ease;
}
.eve-round:active {
	transform: scale(0.94);
}
.eve-send {
	background: var(--primary, var(--blue-500, #2490ef));
	color: #fff;
}
.eve-send:disabled {
	background: var(--control-bg, #f0f2f4);
	color: var(--text-muted, #8d99a6);
	cursor: default;
}
.eve-stop {
	background: var(--control-bg, #f0f2f4);
	color: var(--text-color, #1f272e);
}
.eve-mic {
	background: transparent;
	color: var(--text-muted, #6c7680);
}
.eve-mic:hover {
	background: var(--control-bg, #f0f2f4);
	color: var(--text-color, #1f272e);
}
/* The wave: five bars. While listening they breathe, each on its own beat. */
.eve-wave svg {
	overflow: visible;
}
.eve-wave.is-live {
	animation: eve-ring 1.4s ease-in-out infinite;
}
.eve-wave.is-live .eve-bar {
	transform-box: fill-box;
	transform-origin: center;
	animation: eve-bar 0.9s ease-in-out infinite;
}
.eve-wave.is-live .eve-bar:nth-child(1) {
	animation-delay: 0s;
}
.eve-wave.is-live .eve-bar:nth-child(2) {
	animation-delay: 0.15s;
}
.eve-wave.is-live .eve-bar:nth-child(3) {
	animation-delay: 0.3s;
}
.eve-wave.is-live .eve-bar:nth-child(4) {
	animation-delay: 0.45s;
}
.eve-wave.is-live .eve-bar:nth-child(5) {
	animation-delay: 0.6s;
}
@keyframes eve-bar {
	0%,
	100% {
		transform: scaleY(0.35);
	}
	50% {
		transform: scaleY(1.35);
	}
}
@keyframes eve-ring {
	0%,
	100% {
		box-shadow: 0 0 0 0 color-mix(in srgb, var(--primary, #2490ef) 45%, transparent);
	}
	50% {
		box-shadow: 0 0 0 7px transparent;
	}
}
.eve-round:focus-visible {
	outline: 2px solid var(--primary, #2490ef);
	outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
	.eve-wave.is-live,
	.eve-wave.is-live .eve-bar {
		animation: none;
	}
}
</style>
