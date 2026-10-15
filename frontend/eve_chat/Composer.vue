<template>
	<div class="eve-composer" :class="{ 'is-busy': busy }">
		<textarea
			ref="input"
			v-model="draft"
			rows="1"
			class="eve-textarea"
			:placeholder="placeholder"
			:aria-label="__('Message Eve')"
			@input="resize"
			@keydown="onKeydown"
		></textarea>
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
			v-else
			type="button"
			class="eve-round eve-send"
			:disabled="!draft.trim()"
			:title="__('Send') + ' (Enter)'"
			:aria-label="__('Send')"
			@click="submit"
		>
			<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
				<path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
			</svg>
		</button>
	</div>
</template>

<script setup>
import { nextTick, ref } from "vue";

const MAX_HEIGHT = 200;

const props = defineProps({
	busy: { type: Boolean, default: false },
	placeholder: { type: String, default: "" },
});
const emit = defineEmits(["send", "stop"]);

const __ = window.__;
const draft = ref("");
const input = ref(null);

function submit() {
	const text = draft.value.trim();
	if (!text || props.busy) return;
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

function focus() {
	input.value?.focus();
}

defineExpose({ focus });
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
.eve-textarea {
	flex: 1;
	min-width: 0;
	min-height: 24px;
	max-height: 200px;
	resize: none;
	border: none;
	outline: none;
	background: transparent;
	color: var(--text-color, #1f272e);
	font: inherit;
	font-size: var(--text-md, 14px);
	line-height: 1.5;
	padding: 9px 0;
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
.eve-round:focus-visible {
	outline: 2px solid var(--primary, #2490ef);
	outline-offset: 2px;
}
</style>
