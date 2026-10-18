from typing import Any

from .base import ModelProvider, ModelResponse, ToolCall, ToolSpec, Usage


def from_config(cfg: dict[str, Any]) -> ModelProvider:
    """Build the provider named by cfg["provider"]; the remaining keys go to its constructor.

    Adapters are imported here, lazily, so the vendor SDK is only needed for the one in use.
    """
    kwargs = {k: v for k, v in cfg.items() if k != "provider"}
    if cfg["provider"] == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(**kwargs)
    raise ValueError(f"unknown provider {cfg['provider']!r}")


__all__ = ["ModelProvider", "ModelResponse", "ToolCall", "ToolSpec", "Usage", "from_config"]
