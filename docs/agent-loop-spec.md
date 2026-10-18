# Agent loop: requirements (Week 1)

What to build this week, derived from `docs/design.md` and the data the generator already produces (`examples/invoice_agent/generate_data.py`). You write the code; this is the contract it should meet.

## 1. What the loop does

Input: one invoice text file (`data/invoices/INV-XXXX.txt`).
Output: one JSON result:

```json
{
  "invoice_id": "INV-0007",
  "fields": {
    "vendor_name": "...", "vendor_gstin": "...", "invoice_number": "...",
    "invoice_date": "YYYY-MM-DD", "po_number": "PO-12345",
    "line_items": [{"description": "...", "quantity": 2, "unit_price": 280.0, "amount": 560.0}],
    "tax": 100.8, "total": 660.8
  },
  "decision": "approve | hold | reject",
  "reason": "one or two sentences naming the rule that fired",
  "trace": {"steps": 4, "tool_calls": ["lookup_vendor", "..."], "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
}
```

`fields` uses the same keys as `expected_fields` in `ground_truth.jsonl`, so the scorer can diff them directly.

## 2. The four MCP tools

All four read or write `data/records.sqlite`. Run them as one MCP server (stdio) using the MCP Python SDK.

| Tool | Input | Returns | Notes |
| --- | --- | --- | --- |
| `lookup_vendor` | `name: str`, `gstin: str \| null` | `{found, vendor_id, name, gstin, active}` | Match on GSTIN first, then exact name. Unknown vendor returns `found: false`, not an error. |
| `get_purchase_order` | `po_number: str` | `{found, po_number, vendor_id, amount, status}` | Missing PO returns `found: false`. |
| `find_similar_invoices` | `vendor_id: str`, `invoice_number: str`, `total: float \| null` | `{matches: [{invoice_number, total, processed_on}]}` | Searches `processed_invoices`. Exact vendor + invoice number is a duplicate. |
| `flag_for_review` | `invoice_id: str`, `decision: "hold" \| "reject"`, `reason: str` | `{flag_id, created}` | The only tool with a side effect. Writes a row to a new `review_flags` table (add it in the server, not the generator). |

Tool descriptions matter: the model only knows what the description says. Write each one as if a new teammate had to use it without reading the code.

## 3. Decision rules (what ground truth expects)

These come straight from the generator, so they are the labels you'll be scored on.

| Check, in this order | Decision | Case type |
| --- | --- | --- |
| Vendor not found | hold | `unknown_vendor` |
| PO not found | hold | `missing_po` |
| Same vendor + invoice number already processed | reject | `duplicate` |
| Line items + tax don't add up to the stated total | reject | `total_mismatch` |
| Total is more than 2% over the PO amount | hold | `po_over_limit` |
| Everything passes | approve | `clean` |

Decide one thing yourself: does the model do the arithmetic, or does a tool or plain code? Either is defensible, but say which in `design.md` and why. (An interviewer will ask.)

## 4. Loop requirements

1. **Provider-agnostic.** The loop imports only `agent_runtime.providers.base`. The Anthropic adapter implements `ModelProvider`; picking it comes from config, not code.
2. **Standard tool loop.** Call the model, run any tool calls, append results, repeat until the model returns a final answer with no tool calls.
3. **Step limit.** Stop after N model calls (default 8) and return `hold` with reason `step_limit`. Never loop forever.
4. **Structured final answer.** The final decision must parse into the schema in section 1. If it doesn't, retry once with the parse error, then fall back to `hold`.
5. **Tool errors are data.** A tool that raises returns an error result to the model; it does not crash the run.
6. **`flag_for_review` exactly once** on every `hold` or `reject`, never on `approve`. This becomes a trajectory check later.
7. **Usage tracked.** Sum tokens and cost across model calls into `trace`.
8. **Runnable from the CLI:** `python -m examples.invoice_agent.run data/invoices/INV-0007.txt` prints the JSON; `--all` runs every invoice.

## 5. Scorers (last build item this week)

- Field accuracy: exact match for IDs and invoice number, ±0.01 for amounts, normalised ISO dates.
- Decision accuracy against `expected_decision`.
- A per-case table: invoice, case type, expected, got, fields correct, steps, cost. Then overall accuracy, and accuracy per case type.

## 6. Done for Week 1 when

- `--all` runs the 50 invoices end to end against Claude.
- The scorer prints the table, and you know your decision accuracy per case type.
- Tests cover the four tools against a small generated dataset, and the loop with a fake provider (no API calls in CI).

## What a staff reviewer will look at

- Whether the loop can be tested without a network (fake provider).
- How you handle the unhappy paths: unknown tool name, bad JSON args, step limit, malformed final answer.
- Whether `flag_for_review` is safe to call twice (it won't be yet; that's Week 3, but note it).
- Whether the per-case-type numbers tell you *why* the agent fails, not just how often.
