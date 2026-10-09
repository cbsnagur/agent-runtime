# agent-runtime: design

A small, production-shaped runtime for document-processing agents, built to show four things agent platforms need: evals, durable execution, tool security and observability.

Status: draft, week 1. Sections marked *(later)* get filled as each phase lands.

## Problem

Enterprise teams want agents that read business documents (invoices, purchase orders) and act on them through tools: extract fields, check them against records, and flag problems. Demos work; production breaks. Specifically:

- Nobody can say whether a prompt or model change made the agent better or worse.
- A run that dies halfway (timeout, crash, rate limit) either restarts from scratch or repeats side effects.
- Tools run with whatever access the process has, and document text can carry prompt injections.
- Nobody can say what one run cost or where its time went.

## The agent task

Input: one invoice (PDF or text). The agent must:

1. Extract vendor, invoice number, date, line items, tax and total.
2. Look up the vendor and purchase order through tools.
3. Check the invoice: totals add up, PO matches, not a duplicate.
4. Output a decision (`approve`, `hold`, `reject`) with a short reason.

Tools are exposed over MCP: `lookup_vendor`, `get_purchase_order`, `find_similar_invoices`, `flag_for_review`. Backing data is a local SQLite database seeded with synthetic vendors and POs.

## Architecture

```
invoice ──▶ Runner ──▶ Agent loop ──▶ MCP tool client ──▶ Sandboxed tools
              │             │                                    │
              │             ▼                                    ▼
              │       Model provider                       Permission policy
              │       (Claude | OpenAI, from config)       + audit log
              ▼
        Checkpoint store (SQLite/Postgres)    Tracer: OpenTelemetry spans, tokens, cost per span
```

- **Runner:** drives one run as a sequence of steps; persists state after each step.
- **Agent loop:** model call, tool calls, repeat until a decision or a step limit. It talks only to the provider interface, never to a vendor SDK directly.
- **Model provider:** one small interface (`complete(messages, tools) -> response with text, tool calls, token usage`) with an adapter per vendor: Anthropic and OpenAI to start. The provider, model and price per token come from config (`provider: anthropic`, `model: ...`), so switching needs no code change. Adapters translate tool schemas and tool-call formats, which differ between the two APIs.
- **Tool layer:** every tool call goes through a policy check and is logged.
- **Eval harness:** runs the agent over a fixed dataset and scores it; CI fails on regression.

## Evals (weeks 1–2)

- **Dataset:** 50–100 synthetic invoices with ground-truth JSON, including hard cases: wrong totals, missing PO, duplicates, unusual layouts, and later, injected instructions.
- **Scorers:**
  - Field accuracy: exact match for IDs, tolerance for amounts, normalised dates.
  - Decision accuracy against the labelled decision.
  - Trajectory checks: required tools called, forbidden tools not called.
  - LLM-as-judge for the reason text, with the judge's agreement with your own labels measured on 20 cases.
- **Regression gate:** a GitHub Action runs the eval on every PR and fails if decision accuracy drops more than 2 points below the stored baseline.
- **Report:** per-case results as a table, so failures are easy to read.
- **Provider comparison:** the same eval runs against each configured provider and model, reporting accuracy, cost and latency side by side.

## Durable execution *(later, weeks 3–4)*

Checkpoint after each step; resume a killed run from the last checkpoint; idempotency keys so side-effecting tools (`flag_for_review`) never run twice.

## Tool security *(later, weeks 5–6)*

Per-tool permission policy, tools isolated in a subprocess or container with limits, audit log, and an injection test set in the evals.

## Observability and cost *(later, weeks 3–4)*

One trace per run, one span per model call and tool call, with tokens and cost on each span.

## Alternatives considered *(later)*

For example: Temporal or LangGraph instead of a hand-built runner; function calling instead of MCP; LiteLLM or another gateway library instead of your own provider adapters; a hosted eval tool instead of a custom harness. Say what each would cost and why you chose what you did.

## What changes at 100× *(later)*

Many concurrent runs, multi-tenant isolation, queueing and back-pressure, model rate limits, and cost controls per tenant.

## Non-goals

A UI, OCR accuracy (inputs are clean text or text PDFs), and fine-tuning.

## Stack

Python 3.12, Anthropic and OpenAI SDKs behind a provider interface, MCP Python SDK, SQLite (Postgres later), pytest, GitHub Actions, OpenTelemetry.
