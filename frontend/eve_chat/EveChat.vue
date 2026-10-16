<template>
	<div class="eve-chat">
		<div class="eve-halo" :class="{ 'is-open': open, 'is-minimized': minimized }" aria-hidden="true"></div>

		<button
			v-show="!open"
			type="button"
			class="eve-launcher"
			:title="launcherTitle"
			:aria-label="launcherTitle"
			@click="show"
		>
			<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
				<path d="M12 3.5c.5 4.3 2.2 6 6.5 6.5-4.3.5-6 2.2-6.5 6.5-.5-4.3-2.2-6-6.5-6.5 4.3-.5 6-2.2 6.5-6.5z" fill="currentColor" />
			</svg>
			<span class="eve-launcher-label">{{ __("Ask Eve") }}</span>
		</button>

		<section
			class="eve-panel"
			:class="{ 'is-open': open, 'is-minimized': minimized }"
			role="dialog"
			:aria-label="__('Eve chat')"
			:aria-hidden="!open"
		>
			<div class="eve-header" :title="minimized ? __('Expand') : ''" @click="onHeaderClick">
				<div class="eve-title">
					<span class="eve-mark" aria-hidden="true">✦</span>
					<div>
						<div class="eve-name">Eve</div>
						<div class="eve-sub">{{ __("Answers from your ERP graph") }}</div>
					</div>
				</div>
				<div class="eve-header-actions">
					<button type="button" class="eve-ghost eve-new-chat" :title="__('New chat')" @click.stop="newChat">
						{{ __("New chat") }}
					</button>
					<button
						v-if="!minimized"
						type="button"
						class="eve-ghost eve-icon"
						:title="__('Minimize')"
						:aria-label="__('Minimize')"
						@click.stop="minimize"
					>
						<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
							<path d="M5 12h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
						</svg>
					</button>
					<button
						v-else
						type="button"
						class="eve-ghost eve-icon"
						:title="__('Expand')"
						:aria-label="__('Expand')"
						@click.stop="restore"
					>
						<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
							<path d="M6 15l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
						</svg>
					</button>
					<button
						type="button"
						class="eve-ghost eve-icon"
						:title="__('Close')"
						:aria-label="__('Close')"
						@click.stop="close"
					>
						<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
							<path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
						</svg>
					</button>
				</div>
			</div>
			<ChatSession v-if="chatId" ref="session" :key="chatId" :chat-id="chatId" />
		</section>
	</div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import ChatSession from "./ChatSession.vue";

const CHAT_KEY = `eve_chat:id:${frappe.session.user}`;
const DEFAULT_SHORTCUT = "Cmd+E";

const props = defineProps({
	shortcut: { type: String, default: DEFAULT_SHORTCUT },
});

const __ = window.__;
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const open = ref(false);
const minimized = ref(false);
const session = ref(null);
const chatId = ref(read(CHAT_KEY) || newId());
const combo = parseShortcut(props.shortcut || DEFAULT_SHORTCUT);
const launcherTitle = computed(() => `${__("Ask Eve")} (${displayShortcut(combo)})`);

function read(key) {
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}

function write(key, value) {
	try {
		value == null ? localStorage.removeItem(key) : localStorage.setItem(key, value);
	} catch {
		// Storage blocked: the chat still works, it just won't survive a reload.
	}
}

function newId() {
	const id = frappe.utils.get_random(16);
	write(CHAT_KEY, id);
	return id;
}

// "Cmd+E" is Command on a Mac and Ctrl elsewhere. "Ctrl", "Meta" (Windows key), "Shift", "Alt" are literal.
function parseShortcut(spec) {
	const tokens = String(spec).toLowerCase().split("+").map((t) => t.trim()).filter(Boolean);
	const key = tokens.pop() || "e";
	const mods = new Set(tokens);
	const primary = mods.has("cmd") || mods.has("mod");
	return {
		key,
		meta: mods.has("meta") || (primary && IS_MAC),
		ctrl: mods.has("ctrl") || (primary && !IS_MAC),
		shift: mods.has("shift"),
		alt: mods.has("alt") || mods.has("option"),
	};
}

function matches(event, c) {
	return (
		event.key.toLowerCase() === c.key &&
		event.metaKey === c.meta &&
		event.ctrlKey === c.ctrl &&
		event.shiftKey === c.shift &&
		event.altKey === c.alt
	);
}

function displayShortcut(c) {
	if (IS_MAC) {
		return [c.ctrl && "⌃", c.alt && "⌥", c.shift && "⇧", c.meta && "⌘", c.key.toUpperCase()].filter(Boolean).join("");
	}
	return [c.ctrl && "Ctrl", c.alt && "Alt", c.shift && "Shift", c.meta && "Win", c.key.toUpperCase()]
		.filter(Boolean)
		.join("+");
}

// Opens Eve, or restores it from the minimized bar. The shortcut closes Eve only when it is open and expanded.
function toggle() {
	if (open.value && !minimized.value) close();
	else show();
}

function show() {
	open.value = true;
	minimized.value = false;
	nextTick(() => session.value?.focus());
}

function minimize() {
	minimized.value = true;
}

function restore() {
	minimized.value = false;
	nextTick(() => session.value?.focus());
}

function onHeaderClick() {
	if (minimized.value) restore();
}

function close() {
	open.value = false;
	minimized.value = false;
}

function newChat() {
	write(`eve_chat:messages:${frappe.session.user}:${chatId.value}`, null);
	chatId.value = newId();
	nextTick(() => session.value?.focus());
}

function onKeydown(event) {
	if (matches(event, combo)) {
		event.preventDefault();
		toggle();
		return;
	}
	// Esc closes Eve, unless a Desk dialog is in front of it.
	if (event.key === "Escape" && open.value && !document.querySelector(".modal.show")) close();
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown));
</script>

<style>
@property --eve-angle {
	syntax: "<angle>";
	inherits: false;
	initial-value: 0deg;
}
.eve-chat {
	font-size: var(--text-base, 14px);
}

/* Glow outside the chat panel, in the Siri palette. The halo has the panel's geometry and sits
   behind it, so only the rim beyond the panel edge shows. The panel is opaque, which hides the
   inside of the halo. */
.eve-halo {
	position: fixed;
	left: 50%;
	bottom: 24px;
	z-index: 1035;
	width: min(760px, calc(100vw - 48px));
	height: min(80vh, 860px);
	border-radius: 22px;
	pointer-events: none;
	opacity: 0;
	visibility: hidden;
	transform: translate(-50%, 18px) scale(0.98);
	transform-origin: bottom center;
	transition: opacity 0.6s ease, transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1), height 0.4s cubic-bezier(0.2, 0.8, 0.2, 1), visibility 0s linear 0.6s;
}
.eve-halo.is-open {
	opacity: 1;
	visibility: visible;
	transform: translate(-50%, 0) scale(1);
	transition: opacity 0.6s ease, transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1), height 0.4s cubic-bezier(0.2, 0.8, 0.2, 1), visibility 0s;
}
.eve-halo.is-minimized {
	height: 56px;
	width: min(360px, calc(100vw - 48px));
}
.eve-halo::before,
.eve-halo::after {
	content: "";
	position: absolute;
	border-style: solid;
	border-image: conic-gradient(
		from var(--eve-angle),
		#3e8eff,
		#a664ff,
		#ff5fa2,
		#ffb347,
		#41e0c1,
		#3e8eff
	) 1;
	animation: eve-spin 8s linear infinite;
}
/* A soft, wide band just outside the panel edge. */
.eve-halo::before {
	inset: -12px;
	border-width: 14px;
	border-radius: 34px;
	filter: blur(12px);
	opacity: 0.3;
}
/* A tighter, brighter rim closer to the edge. */
.eve-halo::after {
	inset: -5px;
	border-width: 6px;
	border-radius: 28px;
	filter: blur(7px);
	opacity: 0.16;
	animation-duration: 6s;
	animation-direction: reverse;
}
.eve-halo:not(.is-open)::before,
.eve-halo:not(.is-open)::after,
.eve-halo.is-minimized::before,
.eve-halo.is-minimized::after {
	animation-play-state: paused;
}
@keyframes eve-spin {
	to {
		--eve-angle: 360deg;
	}
}

.eve-launcher {
	position: fixed;
	right: 24px;
	bottom: 24px;
	z-index: 1036;
	height: 52px;
	padding: 0 20px 0 16px;
	display: inline-flex;
	align-items: center;
	gap: 10px;
	border-radius: 999px;
	font-size: 14px;
	font-weight: 600;
	border: 1px solid var(--border-color, #d1d8dd);
	background: var(--fg-color, #fff);
	color: var(--primary, var(--blue-500, #2490ef));
	box-shadow: var(--shadow-lg, 0 6px 24px rgba(0, 0, 0, 0.18));
	cursor: pointer;
	transition: transform 0.2s cubic-bezier(0.2, 0.8, 0.2, 1), box-shadow 0.2s ease;
}
.eve-launcher svg {
	display: block;
	flex: none;
}
.eve-launcher-label {
	display: block;
	line-height: 1;
	white-space: nowrap;
}
.eve-launcher:hover {
	transform: translateY(-2px) scale(1.04);
	box-shadow:
		0 0 0 1px rgba(166, 100, 255, 0.25),
		0 0 14px 2px rgba(166, 100, 255, 0.35),
		0 0 26px 5px rgba(62, 142, 255, 0.25),
		0 0 36px 8px rgba(255, 95, 162, 0.16);
}
.eve-launcher:focus-visible {
	outline: 2px solid var(--primary, #2490ef);
	outline-offset: 3px;
}

.eve-panel {
	position: fixed;
	left: 50%;
	bottom: 24px;
	z-index: 1036;
	display: flex;
	flex-direction: column;
	width: min(760px, calc(100vw - 48px));
	height: min(80vh, 860px);
	background: var(--fg-color, #fff);
	color: var(--text-color, #1f272e);
	border: 1px solid var(--border-color, #e2e6e9);
	border-radius: 22px;
	box-shadow: var(--shadow-2xl, 0 18px 48px rgba(0, 0, 0, 0.22));
	overflow: hidden;
	opacity: 0;
	visibility: hidden;
	pointer-events: none;
	transform: translate(-50%, 18px) scale(0.98);
	transform-origin: bottom center;
	transition: opacity 0.25s ease, transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1), height 0.4s cubic-bezier(0.2, 0.8, 0.2, 1), visibility 0s linear 0.4s;
}
.eve-panel.is-open {
	opacity: 1;
	visibility: visible;
	pointer-events: auto;
	transform: translate(-50%, 0) scale(1);
	transition: opacity 0.25s ease, transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1), height 0.4s cubic-bezier(0.2, 0.8, 0.2, 1), visibility 0s;
}
/* Minimized: just the header bar remains, and the chat keeps running behind it. */
.eve-panel.is-minimized {
	height: 56px;
	width: min(360px, calc(100vw - 48px));
	cursor: pointer;
}
.eve-panel.is-minimized .eve-session {
	display: none;
}
.eve-panel.is-minimized .eve-header {
	padding: 0 12px 0 18px;
	height: 100%;
}
.eve-panel.is-minimized .eve-sub,
.eve-panel.is-minimized .eve-new-chat {
	display: none;
}
.eve-title {
	white-space: nowrap;
}
.eve-header {
	display: flex;
	align-items: center;
	justify-content: space-between;
	padding: 14px 16px 10px 20px;
}
.eve-title {
	display: flex;
	align-items: center;
	gap: 10px;
}
.eve-mark {
	color: var(--primary, var(--blue-500, #2490ef));
	font-size: 18px;
	line-height: 1;
}
.eve-name {
	font-weight: 600;
	font-size: var(--text-md, 14px);
}
.eve-sub {
	font-size: var(--text-xs, 11px);
	color: var(--text-muted, #8d99a6);
}
.eve-header-actions {
	display: flex;
	align-items: center;
	gap: 4px;
}
.eve-ghost {
	border: none;
	background: transparent;
	color: var(--text-muted, #6c7680);
	font-size: 12px;
	padding: 5px 10px;
	border-radius: 999px;
	cursor: pointer;
	transition: background 0.15s ease, color 0.15s ease;
}
.eve-ghost:hover {
	background: var(--control-bg, #f4f5f6);
	color: var(--text-color, #1f272e);
}
.eve-icon {
	display: inline-flex;
	align-items: center;
	padding: 6px;
}

@media (max-width: 640px) {
	.eve-launcher {
		right: 16px;
		bottom: 16px;
	}
	.eve-panel,
	.eve-panel.is-open,
	.eve-halo,
	.eve-halo.is-open {
		left: 0;
		right: 0;
		bottom: 0;
		width: 100%;
		height: 92vh;
		border-radius: 22px 22px 0 0;
		transform: translateY(18px);
	}
	.eve-panel.is-open,
	.eve-halo.is-open {
		transform: none;
	}
	.eve-panel.is-minimized,
	.eve-halo.is-minimized {
		height: 56px;
		width: 100%;
		border-radius: 22px 22px 0 0;
	}
	.eve-halo::before,
	.eve-halo::after {
		inset: -10px -10px 0;
	}
}
@media (prefers-reduced-motion: reduce) {
	.eve-halo::before,
	.eve-halo::after {
		animation: none;
	}
	.eve-panel,
	.eve-panel.is-open,
	.eve-halo,
	.eve-halo.is-open,
	.eve-launcher {
		transition: none;
	}
}
</style>
