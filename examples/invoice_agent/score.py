"""Score agent results against ground truth: a per-case table, then accuracy overall and per case type.

Usage: python -m examples.invoice_agent.score data/results.jsonl [--truth data/ground_truth.jsonl]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

AMOUNT_TOL = 0.01
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d/%m/%Y")
FIELDS = ("vendor_name", "vendor_gstin", "invoice_number", "invoice_date", "po_number", "line_items", "tax", "total")


def _text(v: Any) -> str:
    return " ".join(str(v or "").split()).casefold()


def _amount_eq(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) <= AMOUNT_TOL
    except (TypeError, ValueError):
        return False


def _date(v: Any) -> date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            pass
    return None


def _lines_eq(got: Any, exp: list[dict]) -> bool:
    if not isinstance(got, list) or len(got) != len(exp):
        return False
    return all(
        isinstance(g, dict)
        and _text(g.get("description")) == _text(e["description"])
        and all(_amount_eq(g.get(k), e[k]) for k in ("quantity", "unit_price", "amount"))
        for g, e in zip(got, exp)
    )


def field_checks(got: dict[str, Any], exp: dict[str, Any]) -> dict[str, bool]:
    """Exact match for IDs, case/whitespace-insensitive names, ±0.01 amounts, normalised dates."""
    return {
        "vendor_name": _text(got.get("vendor_name")) == _text(exp["vendor_name"]),
        "vendor_gstin": (got.get("vendor_gstin") or "").strip() == exp["vendor_gstin"],
        "invoice_number": (got.get("invoice_number") or "").strip() == exp["invoice_number"],
        "invoice_date": _date(got.get("invoice_date")) is not None
        and _date(got.get("invoice_date")) == _date(exp["invoice_date"]),
        "po_number": (got.get("po_number") or "").strip() == exp["po_number"],
        "line_items": _lines_eq(got.get("line_items"), exp["line_items"]),
        "tax": _amount_eq(got.get("tax"), exp["tax"]),
        "total": _amount_eq(got.get("total"), exp["total"]),
    }


def score(results: list[dict], truth: list[dict]) -> list[dict]:
    by_id = {r["invoice_id"]: r for r in results}
    rows = []
    for t in truth:
        r = by_id.get(t["invoice_id"], {})
        checks = field_checks(r.get("fields") or {}, t["expected_fields"])
        trace = r.get("trace", {})
        rows.append({
            "invoice_id": t["invoice_id"],
            "case": t["case"],
            "expected": t["expected_decision"],
            "got": r.get("decision") or ("error" if r.get("error") else "missing"),
            "fields_correct": sum(checks.values()),
            "wrong_fields": [k for k, ok in checks.items() if not ok],
            "steps": trace.get("steps", 0),
            "cost_usd": trace.get("cost_usd", 0.0),
            "reason": r.get("reason") or r.get("error") or "",
        })
    return rows


def report(rows: list[dict]) -> str:
    lines = [f"{'invoice':<10}{'case':<16}{'expected':<10}{'got':<9}{'fields':<8}{'steps':>5}{'cost':>9}  wrong fields"]
    for r in rows:
        mark = " " if r["got"] == r["expected"] else "x"
        lines.append(
            f"{r['invoice_id']:<10}{r['case']:<16}{r['expected']:<10}{r['got']:<8}{mark}"
            f"{r['fields_correct']}/{len(FIELDS):<6}{r['steps']:>5}{r['cost_usd']:>9.4f}  {', '.join(r['wrong_fields'])}"
        )

    def acc(rs: list[dict]) -> str:
        ok = sum(r["got"] == r["expected"] for r in rs)
        return f"{ok}/{len(rs)} ({100 * ok / len(rs):.0f}%)"

    lines += ["", f"decision accuracy: {acc(rows)}",
              f"all fields correct: {sum(r['fields_correct'] == len(FIELDS) for r in rows)}/{len(rows)}",
              f"total cost: ${sum(r['cost_usd'] for r in rows):.4f}", "",
              f"{'case':<16}{'decision':<14}{'fields ok':<11}got (what the misses were decided as)"]
    by_case: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_case[r["case"]].append(r)
    for case, rs in sorted(by_case.items()):
        misses = Counter(r["got"] for r in rs if r["got"] != r["expected"])
        fields_ok = sum(r["fields_correct"] == len(FIELDS) for r in rs)
        lines.append(f"{case:<16}{acc(rs):<14}{f'{fields_ok}/{len(rs)}':<11}"
                     + (", ".join(f"{g} x{n}" for g, n in misses.items()) or "-"))
    wrong = Counter(f for r in rows for f in r["wrong_fields"])
    if wrong:
        lines += ["", "most-missed fields: " + ", ".join(f"{f} x{n}" for f, n in wrong.most_common())]
    return "\n".join(lines)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results", type=Path)
    p.add_argument("--truth", type=Path, default=Path("data/ground_truth.jsonl"))
    args = p.parse_args()
    print(report(score(_jsonl(args.results), _jsonl(args.truth))))


if __name__ == "__main__":
    main()
