// Eve agent HTTP app: the Desk widget streams chats here.
//
// POST /chat  (Bearer token from eve_agent.api.agent.session)
//   body: AI SDK UI chat request { id, messages }
//   reply: AI SDK UI message stream (text, tool calls with their Cypher and sources)

import { convertToModelMessages, stepCountIs, streamText, type LanguageModel, type UIMessage } from "ai";
import { Hono } from "hono";
import { cors } from "hono/cors";
import { AuthError, getSiteConfig, verifyToken } from "./auth.js";
import { memoryContext, saveTurn } from "./memory.js";
import { languageModel } from "./models.js";
import { systemPrompt } from "./prompt.js";
import { makeContext, type ToolContext } from "./tools/common.js";
import { buildTools } from "./tools/index.js";

const MAX_STEPS = 10;
const MAX_HISTORY = 30;

export interface Deps {
  /** Verify the caller and load their site's config. Throws AuthError for a bad token. */
  authorize(header: string | undefined): Promise<ToolContext>;
  model(ctx: ToolContext): LanguageModel;
}

export const defaultDeps: Deps = {
  async authorize(header) {
    const user = await verifyToken(header);
    return makeContext(await getSiteConfig(user.site), user);
  },
  model: (ctx) => languageModel(ctx.config),
};

function lastUserText(messages: UIMessage[]): string {
  const last = [...messages].reverse().find((m) => m.role === "user");
  return (last?.parts ?? [])
    .map((p) => (p.type === "text" ? p.text : ""))
    .join(" ")
    .trim();
}

export function createApp(deps: Deps = defaultDeps) {
  const app = new Hono();

  app.use("*", cors({ origin: (origin) => origin || "*", allowHeaders: ["Authorization", "Content-Type"], maxAge: 600 }));

  app.get("/health", (c) => c.json({ ok: true }));

  app.post("/chat", async (c) => {
    let ctx: ToolContext;
    try {
      ctx = await deps.authorize(c.req.header("Authorization"));
    } catch (err) {
      const status = err instanceof AuthError ? 401 : 502;
      return c.json({ error: (err as Error).message }, status);
    }

    const { id, messages } = (await c.req.json()) as { id: string; messages: UIMessage[] };
    const history = messages.slice(-MAX_HISTORY);
    const tools = buildTools(ctx);
    const question = lastUserText(history);

    const result = streamText({
      model: deps.model(ctx),
      system: systemPrompt(ctx, await memoryContext(ctx)),
      messages: await convertToModelMessages(history, { tools, ignoreIncompleteToolCalls: true }),
      tools,
      stopWhen: stepCountIs(MAX_STEPS),
      onEnd: async ({ text }) => {
        try {
          await saveTurn(ctx, id, question, text);
        } catch (err) {
          console.error("memory: could not save turn", err);
        }
      },
      onError: ({ error }) => console.error("chat error", error),
    });

    return result.toUIMessageStreamResponse({
      originalMessages: history,
      onError: (error) => (error instanceof Error ? error.message : String(error)),
    });
  });

  return app;
}
