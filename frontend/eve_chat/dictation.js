// Speech-to-text for the composer, through the browser's Web Speech API (Chrome, Edge, Safari).
// Firefox has no SpeechRecognition, so isSupported() hides the microphone there.

const Recognition = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

export function isSupported() {
	return Boolean(Recognition);
}

const ERRORS = {
	"not-allowed": "Microphone access was blocked. Allow it in the browser's site settings.",
	"service-not-allowed": "Speech recognition is not available in this browser.",
	"no-speech": "Nothing was heard. Try again, closer to the microphone.",
	"audio-capture": "No microphone was found.",
	network: "Speech recognition needs a network connection.",
	aborted: "",
};

// Dictates until stopped. Interim text streams through onInterim, finalized text through onFinal.
export function createDictation({ lang, onInterim, onFinal, onEnd, onError }) {
	const recognition = new Recognition();
	recognition.lang = lang || navigator.language || "en-US";
	recognition.interimResults = true;
	recognition.continuous = true; // Keep listening across pauses; the wave button stops it. Safari ignores this.
	recognition.maxAlternatives = 1;

	let finalText = "";
	recognition.onresult = (event) => {
		let interim = "";
		for (let i = event.resultIndex; i < event.results.length; i += 1) {
			const transcript = event.results[i][0].transcript;
			if (event.results[i].isFinal) finalText += transcript;
			else interim += transcript;
		}
		if (interim) onInterim?.(finalText + interim);
		else if (finalText) onFinal?.(finalText.trim());
	};
	recognition.onerror = (event) => {
		const message = ERRORS[event.error] ?? `Speech recognition failed (${event.error}).`;
		if (message) onError?.(message);
	};
	recognition.onend = () => onEnd?.(finalText.trim());

	return {
		start() {
			finalText = "";
			recognition.start();
		},
		stop() {
			recognition.stop(); // Delivers the final result, then onend.
		},
		abort() {
			recognition.abort();
		},
	};
}
