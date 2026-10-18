"""The agent loop and the invoice agent, driven by a scripted fake provider: no network."""

import asyncio
import json

from agent_runtime.loop import run_loop
from agent_runtime.providers.base import ModelResponse, ToolCall, ToolSpec, Usage
from examples.invoice_agent.agent import process_invoice

TOOLS = [ToolSpec(n, n, {"type": "object"}) for n in
         ("lookup_vendor", "get_purchase_order", "find_similar_invoices", "check_totals", "flag_for_review")]

FIELDS = {
    "vendor_name": "Apex Office Supplies", "vendor_gstin": "29ABCDE1234A1Z5", "invoice_number": "001/1234",
    "invoice_date": "2026-08-15", "po_number": "PO-12345",
    "line_items": [{"description": "A4 paper ream", "quantity": 2, "unit_price": 280.0, "amount": 560.0}],
    "tax": 100.8, "total": 660.8,
}


class FakeProvider:
    """Returns scripted responses in order, repeating the last one forever."""

    name, model = "fake", "fake-1"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, tools, system=None):
        resp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return resp


def call(tool, n=0, **args):
    return ModelResponse("", [ToolCall(f"c{n}", tool, args)], Usage(10, 5, 0.001))


def final(decision="approve", fields=FIELDS):
    text = json.dumps({"fields": fields, "decision": decision, "reason": f"Rule says {decision}."})
    return ModelResponse(text, [], Usage(20, 50, 0.002))


class FakeTools:
    def __init__(self):
        self.calls = []

    async def __call__(self, name, args):
        self.calls.append((name, args))
        if name == "get_purchase_order":
            raise RuntimeError("database is locked")
        if name == "flag_for_review":
            return {"flag_id": len(self.calls), "created": "now"}
        return {"found": True}


def loop(provider, tools=None, parse=json.loads, max_steps=8):
    return asyncio.run(run_loop(provider, TOOLS, tools or FakeTools(), "go", parse, "sys", max_steps))


def tool_results(r):
    return [json.loads(m["content"]) | {"is_error": m["is_error"]} for m in r.messages if m["role"] == "tool"]


def test_tool_loop_runs_to_final_answer_and_sums_usage():
    r = loop(FakeProvider(call("lookup_vendor", name="Apex"), ModelResponse('{"ok": 1}', [], Usage(1, 2, 0.5))))
    assert r.output == {"ok": 1} and r.error is None
    assert r.steps == 2 and r.tool_calls == ["lookup_vendor"]
    assert (r.input_tokens, r.output_tokens, r.cost_usd) == (11, 7, 0.501)
    assert tool_results(r) == [{"found": True, "is_error": False}]


def test_tool_errors_are_returned_to_the_model():
    bad_args = ModelResponse("", [ToolCall("c9", "lookup_vendor", "not a dict")])
    r = loop(FakeProvider(call("delete_everything"), call("get_purchase_order", po_number="PO-1"), bad_args,
                          ModelResponse("{}", [])))
    unknown, raised, malformed = tool_results(r)
    assert all(x["is_error"] for x in (unknown, raised, malformed))
    assert "unknown tool" in unknown["error"]
    assert "database is locked" in raised["error"]
    assert "JSON object" in malformed["error"]
    assert r.output == {}


def test_step_limit_stops_the_loop():
    provider = FakeProvider(call("lookup_vendor", name="x"))
    r = loop(provider, max_steps=3)
    assert r.output is None and r.error == "step_limit" and r.steps == provider.calls == 3


def test_malformed_final_answer_is_retried_once():
    r = loop(FakeProvider(ModelResponse("not json", []), ModelResponse('{"a": 1}', [])))
    assert r.output == {"a": 1} and r.steps == 2
    assert "could not be parsed" in r.messages[-2]["content"]

    r = loop(FakeProvider(ModelResponse("not json", [])))
    assert r.output is None and r.error.startswith("parse_error") and r.steps == 2


# --- invoice agent: the flag_for_review invariant -------------------------------------------------


def run_agent(tmp_path, *responses, max_steps=8):
    path = tmp_path / "INV-0007.txt"
    path.write_text("TAX INVOICE ...")
    tools = FakeTools()
    result = asyncio.run(process_invoice(FakeProvider(*responses), TOOLS, tools, path, max_steps))
    return result, [args for name, args in tools.calls if name == "flag_for_review"]


def test_approve_is_never_flagged(tmp_path):
    result, flags = run_agent(tmp_path, call("lookup_vendor", name="Apex"), final("approve"))
    assert result["decision"] == "approve" and flags == []
    assert result["fields"] == FIELDS and result["invoice_id"] == "INV-0007"
    assert result["trace"]["steps"] == 2 and result["trace"]["cost_usd"] == 0.003


def test_model_flag_is_written_once_with_the_real_invoice_id(tmp_path):
    flag = dict(invoice_id="WRONG", decision="reject", reason="dup")
    result, flags = run_agent(tmp_path, call("flag_for_review", 1, **flag), call("flag_for_review", 2, **flag),
                              final("reject"))
    assert result["decision"] == "reject" and len(flags) == 1 and flags[0]["invoice_id"] == "INV-0007"
    assert result["trace"]["flagged_by"] == "model"


def test_runner_flags_when_the_model_forgets(tmp_path):
    result, flags = run_agent(tmp_path, final("hold"))
    assert result["decision"] == "hold" and len(flags) == 1 and result["trace"]["flagged_by"] == "runner"
    assert result["trace"]["tool_calls"] == ["flag_for_review"]


def test_step_limit_holds_and_flags_once(tmp_path):
    result, flags = run_agent(tmp_path, call("lookup_vendor", name="x"), max_steps=4)
    assert result["decision"] == "hold" and result["reason"] == "step_limit" and result["trace"]["steps"] == 4
    assert [f["reason"] for f in flags] == ["step_limit"]


def test_unparseable_answer_holds_and_flags_once(tmp_path):
    result, flags = run_agent(tmp_path, final("approve", fields={**FIELDS, "invoice_date": "15th August"}))
    assert result["decision"] == "hold" and result["reason"].startswith("parse_error") and len(flags) == 1


def test_flag_then_approve_keeps_the_flagged_decision(tmp_path):
    result, flags = run_agent(tmp_path, call("flag_for_review", decision="hold", reason="x"), final("approve"))
    assert result["decision"] == "hold" and len(flags) == 1
