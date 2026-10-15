// Record lookups for IDs that appear in answers (e.g. SDR-0036): one request per name,
// cached for the page. The server answers with the DocType only.
const known = new Map(); // name -> DocType, or null when no record has that name
const inflight = new Map(); // name -> Promise that settles when the name is known

export function doctypeOf(name) {
	return known.get(name) || null;
}

export function lookupRecords(names) {
	const missing = [...new Set(names)].filter((n) => !known.has(n) && !inflight.has(n));
	if (missing.length) {
		const request = new Promise((resolve) => {
			frappe.call({
				method: "eve_agent.api.chat.locate",
				args: { names: missing },
				callback: (r) => {
					for (const n of missing) known.set(n, (r.message && r.message[n]) || null);
					resolve();
				},
				error: () => {
					for (const n of missing) known.set(n, null);
					resolve();
				},
			});
		});
		for (const n of missing) inflight.set(n, request);
	}
	return Promise.all(names.map((n) => inflight.get(n)).filter(Boolean));
}
