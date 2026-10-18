"""The agent loop: call the model, run its tool calls, feed results back, repeat until it answers.

Talks to the model only through agent_runtime.providers.base, and to tools only through the
`call_tool` coroutine it is given, so it runs the same against Claude, another vendor, or a fake.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, TypeVar

from agent_runtime.providers.base import ModelProvider, ToolSpec

T = TypeVar("T")
CallTool = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass
class LoopResult(Generic[T]):
    output: T | None  # the parsed final answer; None when the loop gave up
    error: str | None  # "step_limit" or "parse_error: ..." when output is None
    steps: int = 0  # model calls made
    tool_calls: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    messages: list[dict[str, Any]] = field(default_factory=list)


async def run_loop(
    provider: ModelProvider,
    tools: list[ToolSpec],
    call_tool: CallTool,
    prompt: str,
    parse: Callable[[str], T],
    system: str | None = None,
    max_steps: int = 8,
) -> LoopResult[T]:
    """Run one task to a parsed final answer.

    `parse` turns the model's final text into the output, raising ValueError if it can't. A parse
    failure is sent back to the model once; a second failure ends the run. Tool errors (unknown
    name, bad arguments, an exception) go back to the model as error results; they never raise.
    """
    known = {t.name for t in tools}
    result: LoopResult[T] = LoopResult(output=None, error=None, messages=[{"role": "user", "content": prompt}])
    messages = result.messages
    retried = False

    while result.steps < max_steps:
        resp = await asyncio.to_thread(provider.complete, messages, tools, system)
        result.steps += 1
        if resp.usage:
            result.input_tokens += resp.usage.input_tokens
            result.output_tokens += resp.usage.output_tokens
            result.cost_usd += resp.usage.cost_usd
        messages.append({"role": "assistant", "content": resp.text, "tool_calls": resp.tool_calls, "raw": resp.raw})

        if resp.tool_calls:
            for call in resp.tool_calls:
                result.tool_calls.append(call.name)
                content, is_error = await _run_tool(call.name, call.arguments, known, call_tool)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content, "is_error": is_error})
            continue

        try:
            result.output = parse(resp.text)
            return result
        except ValueError as e:
            if retried:
                result.error = f"parse_error: {e}"
                return result
            retried = True
            messages.append({
                "role": "user",
                "content": f"Your final answer could not be parsed: {e}\nReply with only the corrected JSON object.",
            })

    result.error = "step_limit"
    return result


async def _run_tool(name: str, args: Any, known: set[str], call_tool: CallTool) -> tuple[str, bool]:
    if name not in known:
        return json.dumps({"error": f"unknown tool {name!r}; available: {sorted(known)}"}), True
    if not isinstance(args, dict):
        return json.dumps({"error": f"arguments must be a JSON object, got {args!r}"}), True
    try:
        return json.dumps(await call_tool(name, args)), False
    except Exception as e:  # tool failures are data for the model, not crashes
        return json.dumps({"error": f"{type(e).__name__}: {e}"}), True
