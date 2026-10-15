// Markdown for Eve's answers: parsed by marked, sanitised by DOMPurify, then
// decorated (code blocks get a language label and copy button, tables scroll,
// and record names the tools returned become links to their Desk records).
// Model output and ERP values never reach the page as raw HTML.
import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: true });

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
	if (node.tagName === "A" && node.getAttribute("href")) {
		node.setAttribute("target", "_blank");
		node.setAttribute("rel", "noopener noreferrer");
	}
});

const escape = (value) =>
	String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// refs: [{doctype, name}] for every record the answer's tools cited.
export function renderMarkdown(text, { copyLabel = "Copy", refs = [] } = {}) {
	const html = DOMPurify.sanitize(marked.parse(text || "", { async: false }), { USE_PROFILES: { html: true } });
	return decorate(html, copyLabel, refs);
}

function decorate(html, copyLabel, refs) {
	const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
	const root = doc.body.firstElementChild;

	for (const pre of root.querySelectorAll("pre")) {
		const code = pre.querySelector("code");
		const match = code && code.className.match(/language-(\S+)/);
		const lang = match ? match[1] : "";
		const wrap = doc.createElement("div");
		wrap.className = "eve-code";
		wrap.innerHTML =
			`<div class="eve-code-head"><span>${escape(lang || "text")}</span>` +
			`<button type="button" class="eve-copy" data-copy>${escape(copyLabel)}</button></div>`;
		pre.replaceWith(wrap);
		wrap.appendChild(pre);
	}

	for (const table of root.querySelectorAll("table")) {
		const wrap = doc.createElement("div");
		wrap.className = "eve-table";
		table.replaceWith(wrap);
		wrap.appendChild(table);
	}

	linkify(root, refs);
	return root.innerHTML;
}

// Turns each cited record name in the prose into a link. Whole names only, so
// "SDR-0049" does not match inside "SDR-00491"; code and existing links are left alone.
function linkify(root, refs) {
	const byName = new Map();
	for (const ref of refs) {
		if (ref.name && !byName.has(ref.name)) byName.set(ref.name, ref.doctype);
	}
	if (!byName.size) return;

	const names = [...byName.keys()].sort((a, b) => b.length - a.length).map(escapeRegex);
	const pattern = new RegExp(`(?<![\\w-])(${names.join("|")})(?![\\w-])`, "g");
	const doc = root.ownerDocument;

	const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
	const nodes = [];
	while (walker.nextNode()) nodes.push(walker.currentNode);

	for (const node of nodes) {
		if (node.parentElement.closest("code, pre, a")) continue;
		const text = node.nodeValue;
		const matches = [...text.matchAll(pattern)];
		if (!matches.length) continue;

		const frag = doc.createDocumentFragment();
		let last = 0;
		for (const m of matches) {
			const [name] = m;
			const doctype = byName.get(name);
			frag.append(text.slice(last, m.index));
			const link = doc.createElement("a");
			link.className = "eve-ref";
			link.textContent = name;
			link.href = `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`;
			link.dataset.doctype = doctype;
			link.dataset.name = name;
			frag.append(link);
			last = m.index + name.length;
		}
		frag.append(text.slice(last));
		node.replaceWith(frag);
	}
}
