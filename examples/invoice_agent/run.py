"""Run the invoice agent on one invoice, or all of them, and print the JSON results.

Usage:
  python -m examples.invoice_agent.run data/invoices/INV-0007.txt
  python -m examples.invoice_agent.run --all [--out data/results.jsonl]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from pathlib import Path

from mcp import StdioServerParameters

from agent_runtime.mcp_tools import connect
from agent_runtime.providers import from_config
from examples.invoice_agent.agent import process_invoice

REPO_ROOT = Path(__file__).resolve().parents[2]


async def run(args: argparse.Namespace) -> None:
    cfg = tomllib.loads(args.config.read_text())
    provider = from_config(cfg["provider"])
    paths = sorted((args.data / "invoices").glob("INV-*.txt")) if args.all else [args.invoice]
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "examples.invoice_agent.server", str((args.data / "records.sqlite").resolve())],
        cwd=REPO_ROOT,
    )
    out = open(args.out, "w", encoding="utf-8") if args.out else None
    try:
        async with connect(server) as (tools, call_tool):
            for path in paths:
                try:
                    result = await process_invoice(provider, tools, call_tool, path, cfg.get("max_steps", 8))
                except Exception as e:  # one broken run must not lose the rest of --all
                    result = {"invoice_id": path.stem, "decision": None, "error": f"{type(e).__name__}: {e}"}
                if args.all:
                    print(json.dumps(result), flush=True)
                    t = result.get("trace", {})
                    print(f"{path.stem} {result['decision']} steps={t.get('steps')} ${t.get('cost_usd', 0):.4f}",
                          file=sys.stderr)
                else:
                    print(json.dumps(result, indent=2))
                if out:
                    out.write(json.dumps(result) + "\n")
                    out.flush()
    finally:
        if out:
            out.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("invoice", nargs="?", type=Path, help="invoice text file")
    p.add_argument("--all", action="store_true", help="run every invoice under DATA/invoices")
    p.add_argument("--data", type=Path, default=Path("data"), help="dataset directory (invoices/, records.sqlite)")
    p.add_argument("--config", type=Path, default=Path(__file__).with_name("config.toml"))
    p.add_argument("--out", type=Path, help="also write results as JSON lines to this file")
    args = p.parse_args()
    if not args.all and not args.invoice:
        p.error("give an invoice file or --all")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
