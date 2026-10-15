// Pluggable LLMs through the AI SDK provider registry.
//
// The model string comes from Eve Agent Settings as "provider:model", e.g.
//   anthropic:claude-sonnet-5-5   openai:<model>   google:<model>
//   compatible:llama3.1  (any OpenAI-compatible server: Ollama, LiteLLM, vLLM...)
// The API key comes from Settings, or from the provider's usual env var.

import { createAnthropic } from "@ai-sdk/anthropic";
import { createGoogle } from "@ai-sdk/google";
import { createOpenAI } from "@ai-sdk/openai";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { createProviderRegistry, type LanguageModel } from "ai";
import type { SiteConfig } from "./auth.js";

function buildRegistry(config: SiteConfig) {
  const apiKey = config.api_key || undefined;
  return createProviderRegistry({
    anthropic: createAnthropic({ apiKey }),
    openai: createOpenAI({ apiKey }),
    google: createGoogle({ apiKey }),
    compatible: createOpenAICompatible({
      name: "compatible",
      baseURL: config.base_url || "http://127.0.0.1:11434/v1",
      apiKey,
    }),
  });
}

const registries = new Map<string, ReturnType<typeof buildRegistry>>();

export function languageModel(config: SiteConfig): LanguageModel {
  const key = `${config.api_key}|${config.base_url}`;
  let registry = registries.get(key);
  if (!registry) {
    registry = buildRegistry(config);
    registries.set(key, registry);
  }
  const id = config.model.includes(":") ? config.model : `anthropic:${config.model}`;
  return registry.languageModel(id as Parameters<typeof registry.languageModel>[0]);
}
