"""The one interface the agent loop uses to talk to a model.

Each vendor (Anthropic, OpenAI) gets an adapter that implements ModelProvider and
translates tool schemas and tool-call formats to and from these types. Which adapter
and model to use comes from config, so the agent loop never imports a vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    cost_usd: float  # computed by the adapter from configured per-token prices


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    stop_reason: str = ""


class ModelProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        system: str | None = None,
    ) -> ModelResponse: ...
