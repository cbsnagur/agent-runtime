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
| Provider interface, Anthropic adapter | Done |
| Agent loop, MCP tools, scorer | Done |
| OpenAI adapter, trajectory checks, CI eval gate | Planned |
| Durable execution, tool sandbox, tracing | Planned |

## Quick start

```bash
pip install -e ".[dev]"
python examples/invoice_agent/generate_data.py --out data --count 50
pytest -q                                   # no network: the loop is tested with a fake provider

# needs ANTHROPIC_API_KEY (or `ant auth login`); provider and model come from examples/invoice_agent/config.toml
python -m examples.invoice_agent.run data/invoices/INV-0007.txt
python -m examples.invoice_agent.run --all --out data/results.jsonl
python -m examples.invoice_agent.score data/results.jsonl
```

The generator writes rendered invoices, `ground_truth.jsonl` (expected fields and decision per invoice) and `records.sqlite` (vendors, purchase orders and previously processed invoices). Case types: clean, total mismatch, duplicate, missing PO, PO over limit and unknown vendor.
