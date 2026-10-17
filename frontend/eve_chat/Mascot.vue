<template>
	<span class="eve-mascot" :class="{ 'eve-mascot-plain': clip === null }" aria-hidden="true">
		<img v-show="clip === null" :src="`${MASCOT_URL}/idle_no_background.webp`" alt="" />
		<!-- Every clip stays mounted and preloaded; switching only toggles visibility, so there is no blank frame. -->
		<video
			v-for="name in clips"
			v-show="clip === name"
			:key="name"
			:ref="(el) => (videos[name] = el)"
			muted
			:loop="name === 'idle_animation' || name === 'eve_searching_looped'"
			playsinline
			disablepictureinpicture
			preload="auto"
			@ended="onEnded(name)"
		>
			<source :src="`${MASCOT_URL}/animations/${name}.mp4`" type="video/mp4" />
			<img :src="`${MASCOT_URL}/animations/${name}.gif`" alt="" />
		</video>
	</span>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";

// Eve's face. At rest: the idle clip cropped to a circle, or with `plain` the still head cut-out
// with no background. While `busy`: she raises a laptop and types; when busy ends, the laptop goes
// back down and she returns to rest. Clips are MP4 with a GIF for browsers without video.
// Files and conventions: eve_agent/public/mascot/animations/INSTRUCTION.md
const props = defineProps({
	plain: { type: Boolean, default: false },
	busy: { type: Boolean, default: false },
});
const MASCOT_URL = "/assets/eve_agent/mascot";
const stillOnly = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const clips = props.plain
	? ["eve_searching", "eve_searching_looped", "eve_searching_reversed"]
	: ["idle_animation", "eve_searching", "eve_searching_looped", "eve_searching_reversed"];
const videos = {};
const looping = ref(false); // busy: the laptop-raising intro has finished, the typing loop is on
const outro = ref(false);
// Reduced motion never fires `ended`, so the outro would stick on its first frame: skip it.
watch(
	() => props.busy,
	(now, before) => {
		if (now) looping.value = false;
		if (before && !now && !stillOnly) outro.value = true;
	}
);

// null = the still webp; otherwise a clip name under animations/
const clip = computed(() => {
	if (props.busy) return looping.value ? "eve_searching_looped" : "eve_searching";
	if (outro.value) return "eve_searching_reversed";
	return props.plain ? null : "idle_animation";
});

// The clip that stops is rewound while hidden, so it shows its first frame the moment it is next shown.
function play(name, previous) {
	if (previous) {
		videos[previous].pause();
		videos[previous].currentTime = 0;
	}
	if (name && !stillOnly) videos[name].play();
}
onMounted(() => play(clip.value));
watch(clip, play, { flush: "post" });

function onEnded(name) {
	if (name === "eve_searching_reversed") outro.value = false;
	else if (name === "eve_searching") looping.value = true;
}
</script>

<style>
.eve-mascot {
	display: block;
	flex: none;
	width: 36px;
	height: 36px;
	border-radius: 50%;
	overflow: hidden;
	background: #fff; /* the clip's own background, so the disc stays clean in dark mode */
	transition: border-radius 0.25s ease, background 0.25s ease;
}
.eve-mascot-plain {
	border-radius: 0;
	background: none;
}
/* The head spans ~65% of the square; 1.15x fills the disc without clipping the hair. */
.eve-mascot video,
.eve-mascot > img {
	display: block;
	width: 100%;
	height: 100%;
	object-fit: cover;
	transform: scale(1.15);
	pointer-events: none;
}
.eve-mascot-plain > img {
	object-fit: contain;
	transform: none;
}
</style>
