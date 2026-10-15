// Ask Eve: a chat panel on every Desk page, for users allowed by Eve Agent Settings.
import { createApp } from "vue";
import EveChat from "./EveChat.vue";

function mount() {
	const eve = frappe.boot && frappe.boot.eve;
	if (!eve || !eve.can_chat || document.getElementById("eve-chat-root")) return;
	const root = document.createElement("div");
	root.id = "eve-chat-root";
	document.body.appendChild(root);
	createApp(EveChat, { shortcut: eve.shortcut }).mount(root);
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", mount);
} else {
	mount();
}
