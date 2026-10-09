# agent-runtime

A small, production-shaped runtime for tool-using agents. It is built around the four things that break when agents leave the demo stage:

- **Evals:** a fixed dataset, scorers and a CI gate, so every prompt or model change is measured.
- **Durable execution:** runs checkpoint after each step and resume after a crash without repeating side effects.
- **Tool security:** tools run behind a permission policy, isolated, with an audit log and prompt-injection tests.
- **Observability and cost:** one trace per run, with tokens and cost on every model and tool call.

The model provider is configurable (Anthropic or OpenAI), and the agent loop never calls a vendor SDK directly.

The example workload is an invoice-processing agent (`examples/invoice_agent`). It extracts fields, checks them against vendor and purchase-order records through MCP tools, and decides whether to approve, hold or reject.

Design, trade-offs and what changes at scale: [docs/design.md](docs/design.md).

## Status

| Part | State |
| --- | --- |
| Synthetic invoice dataset with ground truth | Done |
| Provider interface | Defined; adapters in progress |
| Agent loop, MCP tools, eval harness | In progress |
| Durable execution, tool sandbox, tracing | Planned |

## Quick start

```bash
pip install -e ".[dev]"
python examples/invoice_agent/generate_data.py --out data --count 50
pytest -q
```

The generator writes rendered invoices, `ground_truth.jsonl` (expected fields and decision per invoice) and `records.sqlite` (vendors, purchase orders and previously processed invoices). Case types: clean, total mismatch, duplicate, missing PO, PO over limit and unknown vendor.
