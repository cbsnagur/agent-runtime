"""ModelProvider adapter for the Anthropic Messages API."""

from __future__ import annotations

from typing import Any

import anthropic

from .base import ModelResponse, ToolCall, ToolSpec, Usage

FALLBACK_BETA = "server-side-fallback-2026-07-01"
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute cache write, relative to the input price
CACHE_READ_MULTIPLIER = 0.1


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str,
        input_usd_per_mtok: float,
        output_usd_per_mtok: float,
        max_tokens: int = 16000,
        effort: str | None = None,
        fallbacks: str | None = None,
        client: Any = None,
    ):
        self.model = model
        self.input_price = input_usd_per_mtok / 1e6
        self.output_price = output_usd_per_mtok / 1e6
        self.max_tokens = max_tokens
        self.effort = effort
        self.fallbacks = fallbacks  # "default" retries safety refusals on Anthropic's recommended model
        self.client = client or anthropic.Anthropic()

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        system: str | None = None,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": to_anthropic_messages(messages),
            "tools": [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools],
            "cache_control": {"type": "ephemeral"},  # caches system + tools + history across steps and invoices
        }
        if system:
            kwargs["system"] = system
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.fallbacks:
            resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks=self.fallbacks, **kwargs)
        else:
            resp = self.client.messages.create(**kwargs)

        u = resp.usage
        cache_write = u.cache_creation_input_tokens or 0
        cache_read = u.cache_read_input_tokens or 0
        cost = (
            u.input_tokens * self.input_price
            + cache_write * self.input_price * CACHE_WRITE_MULTIPLIER
            + cache_read * self.input_price * CACHE_READ_MULTIPLIER
            + u.output_tokens * self.output_price
        )
        return ModelResponse(
            text="".join(b.text for b in resp.content if b.type == "text"),
            tool_calls=[ToolCall(b.id, b.name, b.input) for b in resp.content if b.type == "tool_use"],
            usage=Usage(u.input_tokens + cache_write + cache_read, u.output_tokens, cost),
            stop_reason=resp.stop_reason or "",
            raw=[b.model_dump(mode="json", exclude_none=True) for b in resp.content],
        )


def to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate neutral messages (see base.py) to Anthropic's format.

    Consecutive tool results are merged into one user message, as the API expects.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            content = m.get("raw") or (
                ([{"type": "text", "text": m["content"]}] if m["content"] else [])
                + [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.get("tool_calls", [])]
            )
            out.append({"role": "assistant", "content": content})
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"],
                     "is_error": m.get("is_error", False)}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        else:
            raise ValueError(f"unknown role {m['role']!r}")
    return out

