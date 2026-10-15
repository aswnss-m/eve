// Who is asking, and how this site is configured.
//
// The Desk widget gets a short-lived HS256 token from eve_agent.api.agent.session.
// We verify it with the shared secret, then fetch the site's runtime config
// (LLM, graph names) from Frappe with an HMAC-signed request.

import { createHmac } from "node:crypto";
import { jwtVerify } from "jose";

export interface ChatUser {
  user: string;
  name: string;
  site: string;
  roles: string[];
}

export interface SiteConfig {
  site: string;
  app_url: string;
  model: string;
  api_key: string;
  base_url: string;
  redis_url: string;
  data_graph: string;
  schema_graph: string;
  memory_graph: string;
  data_as_of: string;
  currency: string;
  company: string;
}

const CONFIG_TTL_MS = 60_000;

function env(name: string, fallback?: string): string {
  const value = process.env[name] ?? fallback;
  if (value === undefined || value === "") throw new Error(`${name} is not set. See agent/.env.example.`);
  return value;
}

const secretKey = () => new TextEncoder().encode(env("EVE_SHARED_SECRET"));

export async function verifyToken(header: string | undefined): Promise<ChatUser> {
  const token = header?.startsWith("Bearer ") ? header.slice(7) : "";
  if (!token) throw new AuthError("Missing bearer token");
  try {
    const { payload } = await jwtVerify(token, secretKey(), { algorithms: ["HS256"] });
    return {
      user: String(payload.sub),
      name: String(payload.name ?? payload.sub),
      site: String(payload.site),
      roles: Array.isArray(payload.roles) ? payload.roles.map(String) : [],
    };
  } catch {
    throw new AuthError("Invalid or expired token");
  }
}

export class AuthError extends Error {}

export function sign(secret: string, timestamp: string, site: string): string {
  return createHmac("sha256", secret).update(`${timestamp}.${site}`).digest("hex");
}

const configCache = new Map<string, { at: number; config: SiteConfig }>();

export async function getSiteConfig(site: string): Promise<SiteConfig> {
  const cached = configCache.get(site);
  if (cached && Date.now() - cached.at < CONFIG_TTL_MS) return cached.config;

  const timestamp = String(Math.floor(Date.now() / 1000));
  const url = `${env("FRAPPE_URL", "http://127.0.0.1:8000")}/api/method/eve_agent.api.agent.runtime_config`;
  const res = await fetch(url, {
    headers: {
      // Frappe picks the site from this header, so one agent serves a multi-site bench.
      "X-Frappe-Site-Name": site,
      "X-Eve-Timestamp": timestamp,
      "X-Eve-Signature": sign(env("EVE_SHARED_SECRET"), timestamp, site),
    },
  });
  if (!res.ok) throw new Error(`Frappe runtime_config for ${site} failed: HTTP ${res.status}`);
  const config = (await res.json()).message as SiteConfig;
  // The agent may reach FalkorDB at a different address than Frappe does (e.g. inside Docker).
  if (process.env.FALKORDB_URL) config.redis_url = process.env.FALKORDB_URL;
  configCache.set(site, { at: Date.now(), config });
  return config;
}
