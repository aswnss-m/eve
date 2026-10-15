// Builds the Desk chat widget into eve_agent/public/dist (committed, so installs need no Node build).
// Frappe's own esbuild targets ES2017, so the widget is bundled here with Vite.
import vue from "@vitejs/plugin-vue";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
	plugins: [vue()],
	define: { "process.env.NODE_ENV": JSON.stringify("production") },
	build: {
		target: "es2020",
		outDir: fileURLToPath(new URL("../eve_agent/public/dist", import.meta.url)),
		emptyOutDir: true,
		cssCodeSplit: false,
		lib: {
			entry: fileURLToPath(new URL("./eve_chat/main.js", import.meta.url)),
			name: "EveChat",
			formats: ["iife"],
			fileName: () => "eve_chat.js",
			cssFileName: "eve_chat",
		},
	},
});
