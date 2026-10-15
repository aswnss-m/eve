"""One chat turn: stream a model reply, run the tools it asks for, and repeat until it answers.

Events yielded (the endpoint writes each one as a server-sent event):
  text-delta    {delta}                       a piece of the answer
  tool-start    {toolCallId, toolName, input}
  tool-output   {toolCallId, output}          the tool's result, with its Cypher and sources
  tool-error    {toolCallId, errorText}       the tool refused; the model is told and can recover
"""

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

from eve_agent.chat.common import Tool

logger = logging.getLogger(__name__)

MAX_STEPS = 10
MAX_HISTORY = 30


def is_tool_part(part: dict) -> bool:
	return str(part.get("type", "")).startswith("tool-") or part.get("type") == "dynamic-tool"


def to_model_messages(system: str, ui_messages: list[dict]) -> list[dict]:
	"""Turn the widget's stored messages (text and tool parts) into chat-completion messages.

	Tool calls that never finished are left out, as the agent always did.
	"""
	messages: list[dict] = [{"role": "system", "content": system}]
	for message in ui_messages[-MAX_HISTORY:]:
		parts = message.get("parts") or []
		if message.get("role") == "user":
			text = " ".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
			if text:
				messages.append({"role": "user", "content": text})
			continue
		for part in parts:
			if part.get("type") == "text" and part.get("text"):
				messages.append({"role": "assistant", "content": part["text"]})
			elif is_tool_part(part) and part.get("state") in ("output-available", "output-error"):
				call_id = part.get("toolCallId") or ""
				name = part.get("toolName") or str(part.get("type")).removeprefix("tool-")
				call = {
					"id": call_id,
					"type": "function",
					"function": {"name": name, "arguments": _dumps(part.get("input") or {})},
				}
				messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
				if part["state"] == "output-available":
					content: Any = part.get("output") or {}
				else:
					content = {"error": part.get("errorText") or "The tool failed."}
				messages.append({"role": "tool", "tool_call_id": call_id, "content": _dumps(content)})
	return messages


def run_turn(
	*,
	system: str,
	ui_messages: list[dict],
	tools: list[Tool],
	completion: Callable[..., Any],
	model_kwargs: dict,
) -> Iterator[dict]:
	messages = to_model_messages(system, ui_messages)
	by_name = {tool.name: tool for tool in tools}
	specs = [tool.spec() for tool in tools]

	for _ in range(MAX_STEPS):
		calls: dict[int, dict] = {}
		text_parts: list[str] = []
		stream = completion(
			messages=messages,
			tools=specs,
			stream=True,
			drop_params=True,  # Some providers reject parameters others accept.
			**model_kwargs,
		)
		for chunk in stream:
			if not chunk.choices:
				continue
			delta = chunk.choices[0].delta
			if delta.content:
				text_parts.append(delta.content)
				yield {"type": "text-delta", "delta": delta.content}
			for piece in delta.tool_calls or []:
				call = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
				call["id"] = piece.id or call["id"]
				if piece.function is not None:
					call["name"] = call["name"] or piece.function.name or ""
					call["arguments"] += piece.function.arguments or ""

		if not calls:
			return  # The model answered without asking for a tool: the turn is done.

		ordered = [calls[i] for i in sorted(calls)]
		messages.append(
			{
				"role": "assistant",
				"content": "".join(text_parts) or None,
				"tool_calls": [
					{
						"id": c["id"],
						"type": "function",
						"function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
					}
					for c in ordered
				],
			}
		)
		for call in ordered:
			arguments = call["arguments"] or "{}"
			yield {
				"type": "tool-start",
				"toolCallId": call["id"],
				"toolName": call["name"],
				"input": _parse(arguments),
			}
			try:
				tool = by_name.get(call["name"])
				if tool is None:
					raise ValueError(f"Unknown tool '{call['name']}'.")
				output = tool.run(tool.params.model_validate_json(arguments))
			except Exception as exc:
				# The model sees the error and can recover, e.g. by calling find_entity first.
				error = str(exc)
				logger.info("tool %s failed: %s", call["name"], error)
				yield {"type": "tool-error", "toolCallId": call["id"], "errorText": error}
				messages.append(
					{"role": "tool", "tool_call_id": call["id"], "content": _dumps({"error": error})}
				)
				continue
			yield {"type": "tool-output", "toolCallId": call["id"], "output": output}
			messages.append({"role": "tool", "tool_call_id": call["id"], "content": _dumps(output)})


def _dumps(value: Any) -> str:
	return json.dumps(value, default=str)


def _parse(arguments: str) -> dict:
	try:
		value = json.loads(arguments)
	except ValueError:
		return {}
	return value if isinstance(value, dict) else {}
