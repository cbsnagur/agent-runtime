from types import SimpleNamespace as NS

from agent_runtime.providers import from_config
from agent_runtime.providers.anthropic import to_anthropic_messages
from agent_runtime.providers.base import ToolCall, ToolSpec


def test_translates_neutral_messages_and_merges_tool_results():
    calls = [ToolCall("t1", "a", {"x": 1}), ToolCall("t2", "b", {})]
    out = to_anthropic_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": calls, "raw": None},
        {"role": "tool", "tool_call_id": "t1", "content": "{}", "is_error": False},
        {"role": "tool", "tool_call_id": "t2", "content": '{"error": "x"}', "is_error": True},
        {"role": "assistant", "content": "done", "tool_calls": [], "raw": [{"type": "thinking", "signature": "s"}]},
    ])
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant"]
    assert [b["type"] for b in out[1]["content"]] == ["tool_use", "tool_use"]
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["t1", "t2"] and out[2]["content"][1]["is_error"]
    assert out[3]["content"] == [{"type": "thinking", "signature": "s"}]  # raw blocks replay unchanged


class FakeClient:
    def __init__(self):
        self.sent = None
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.sent = kwargs
        return NS(
            content=[NS(type="text", text="ok", model_dump=lambda **_: {"type": "text", "text": "ok"}),
                     NS(type="tool_use", id="t1", name="a", input={"x": 1},
                        model_dump=lambda **_: {"type": "tool_use", "id": "t1", "name": "a", "input": {"x": 1}})],
            usage=NS(input_tokens=1_000_000, output_tokens=100_000, cache_creation_input_tokens=0,
                     cache_read_input_tokens=1_000_000),
            stop_reason="tool_use",
        )


def test_provider_from_config_prices_usage_and_parses_tool_calls():
    client = FakeClient()
    p = from_config({"provider": "anthropic", "model": "m", "input_usd_per_mtok": 4.0, "output_usd_per_mtok": 20.0,
                     "effort": "medium", "fallbacks": "default", "client": client})
    r = p.complete([{"role": "user", "content": "hi"}], [ToolSpec("a", "d", {"type": "object"})], "sys")
    assert r.text == "ok" and r.tool_calls == [ToolCall("t1", "a", {"x": 1})]
    assert r.usage.input_tokens == 2_000_000 and round(r.usage.cost_usd, 6) == 4.0 + 0.4 + 2.0
    assert client.sent["fallbacks"] == "default" and client.sent["output_config"] == {"effort": "medium"}
    assert client.sent["system"] == "sys" and client.sent["tools"][0]["input_schema"] == {"type": "object"}
