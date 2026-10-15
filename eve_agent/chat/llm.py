"""Map Eve Agent Settings' "provider:model" to a LiteLLM model and its options.

Settings keep the same format as before the switch, so existing sites need no
migration:
  anthropic:claude-sonnet-5-5   -> anthropic/claude-sonnet-5-5
  openai:gpt-4o                 -> openai/gpt-4o
  google:gemini-2.5-pro         -> gemini/gemini-2.5-pro
  compatible:llama3.1           -> openai/llama3.1 at the Base URL (Ollama, vLLM, LiteLLM proxy...)
Any other prefix is passed to LiteLLM unchanged, e.g. "ollama/llama3.1" or "azure/<deployment>".
"""

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
DEFAULT_COMPATIBLE_BASE_URL = "http://127.0.0.1:11434/v1"

_PROVIDERS = {"anthropic": "anthropic", "openai": "openai", "google": "gemini"}


def model_arguments(model: str, api_key: str = "", base_url: str = "") -> dict:
	"""The keyword arguments for litellm.completion that select and reach the model."""
	if ":" not in model:
		# A bare name means Anthropic, as the agent always did. "provider/model" is LiteLLM's own form.
		name = model if "/" in model else f"anthropic/{model}"
		return {"model": name, "api_key": api_key or None, **({"api_base": base_url} if base_url else {})}

	provider, _, name = model.partition(":")
	if provider == "compatible":
		return {
			"model": f"openai/{name}",
			# OpenAI-compatible servers often need no key, but the client wants a non-empty one.
			"api_key": api_key or "not-needed",
			"api_base": base_url or DEFAULT_COMPATIBLE_BASE_URL,
		}
	args = {"model": f"{_PROVIDERS.get(provider, provider)}/{name}", "api_key": api_key or None}
	if base_url:
		args["api_base"] = base_url
	return args
