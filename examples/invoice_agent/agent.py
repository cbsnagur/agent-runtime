"""The invoice agent: prompt, final-answer schema, and the flag_for_review invariant around the loop."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from agent_runtime.loop import CallTool, run_loop
from agent_runtime.providers.base import ModelProvider, ToolSpec

SYSTEM = """\
You process supplier invoices for accounts payable. Extract the invoice's fields, check them \
against the records with the tools, and decide: approve, hold or reject.

Run every check you can:
- lookup_vendor and get_purchase_order first (call both in the same turn).
- Then find_similar_invoices (needs the vendor_id, so only if the vendor was found) and \
check_totals (always; pass po_amount when the PO was found), again in the same turn.
Never do arithmetic yourself: check_totals does it exactly.

Then the first rule that applies, in this order, decides:
1. Vendor not found -> hold.
2. Purchase order not found -> hold.
3. find_similar_invoices returns a match with the same invoice_number -> reject (duplicate).
4. check_totals: totals_match is false -> reject (line items plus tax don't add up to the total).
5. check_totals: over_po_limit is true -> hold (total more than 2% over the PO amount).
6. Otherwise -> approve.

For hold or reject, call flag_for_review exactly once before your final answer. Never call it for approve.

Fields:
- vendor_name, vendor_gstin: if lookup_vendor found the vendor, the name and GSTIN it returned; \
otherwise as printed on the invoice (vendor_gstin null if the invoice shows none).
- invoice_number, po_number: exactly as printed.
- invoice_date: YYYY-MM-DD.
- line_items: description, quantity and unit_price as printed; amount from check_totals line_amounts.
- tax, total: as printed, as plain numbers.

Your final answer is only this JSON object, with no other text:
{"fields": {"vendor_name": str, "vendor_gstin": str | null, "invoice_number": str, \
"invoice_date": "YYYY-MM-DD", "po_number": str, "line_items": [{"description": str, "quantity": int, \
"unit_price": number, "amount": number}], "tax": number, "total": number}, \
"decision": "approve" | "hold" | "reject", "reason": "one or two sentences naming the rule that fired"}
"""


class LineItem(BaseModel):
    description: str
    quantity: int
    unit_price: float
    amount: float


class Fields(BaseModel):
    vendor_name: str
    vendor_gstin: str | None
    invoice_number: str
    invoice_date: date
    po_number: str
    line_items: list[LineItem]
    tax: float
    total: float


class FinalAnswer(BaseModel):
    fields: Fields
    decision: Literal["approve", "hold", "reject"]
    reason: str


def parse_final(text: str) -> FinalAnswer:
    """Parse the model's final reply; raises ValueError (pydantic's ValidationError is one) on a bad answer."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    return FinalAnswer.model_validate_json(text[start : end + 1])


async def process_invoice(
    provider: ModelProvider,
    tools: list[ToolSpec],
    call_tool: CallTool,
    path: Path,
    max_steps: int = 8,
) -> dict[str, Any]:
    """Run the agent on one invoice file and return the result record (see docs/agent-loop-spec.md)."""
    invoice_id = path.stem
    flags: list[dict[str, Any]] = []  # flag_for_review writes made in this run

    async def guarded_call(name: str, args: dict[str, Any]) -> Any:
        if name != "flag_for_review":
            return await call_tool(name, args)
        # ponytail: in-run dedupe only; a crash-and-resume can still double-write until Week 3's idempotency keys.
        if flags:
            return {**flags[0]["result"], "note": "already flagged in this run; not written again"}
        args = {**args, "invoice_id": invoice_id}
        result = await call_tool(name, args)
        flags.append({"decision": args.get("decision"), "result": result})
        return result

    prompt = f"Invoice id: {invoice_id}\n\n<invoice>\n{path.read_text()}</invoice>"
    run = await run_loop(provider, tools, guarded_call, prompt, parse_final, SYSTEM, max_steps)

    if run.output:
        fields: dict[str, Any] = run.output.fields.model_dump(mode="json")
        decision, reason = run.output.decision, run.output.reason
    else:
        fields, decision, reason = {}, "hold", run.error or "no answer"

    # Invariant: exactly one flag_for_review on every hold or reject, none on approve.
    tool_calls = list(run.tool_calls)
    flagged_by = "model" if flags else None
    if decision == "approve" and flags:
        decision = flags[0]["decision"]  # the flag is already written; the record must agree with it
        reason = f"Model answered approve after flagging this invoice; kept the flagged decision. {reason}"
    elif decision != "approve" and not flags:
        await guarded_call("flag_for_review", {"decision": decision, "reason": reason})
        tool_calls.append("flag_for_review")
        flagged_by = "runner"

    return {
        "invoice_id": invoice_id,
        "fields": fields,
        "decision": decision,
        "reason": reason,
        "trace": {
            "steps": run.steps,
            "tool_calls": tool_calls,
            "flagged_by": flagged_by,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "cost_usd": round(run.cost_usd, 6),
        },
    }
