"""MCP server (stdio) exposing the invoice records in records.sqlite as tools.

Usage: python -m examples.invoice_agent.server [path/to/records.sqlite]
The database path can also come from the RECORDS_DB environment variable.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

PO_TOLERANCE = 0.02  # same rule as generate_data.PO_TOLERANCE
AMOUNT_EPSILON = 0.01

mcp = MCPServer("invoice-records")


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(os.environ.get("RECORDS_DB", "data/records.sqlite"))
    db.row_factory = sqlite3.Row
    return db


@mcp.tool()
def lookup_vendor(name: str, gstin: str | None = None) -> dict:
    """Find a vendor in the approved vendor master.

    Pass the vendor name exactly as printed on the invoice, and the GSTIN if the invoice shows one
    (null otherwise). Matches on GSTIN first, then on the name (case-insensitive, otherwise exact).
    Returns {found, vendor_id, name, gstin, active}. found=false means the vendor is not approved:
    that is a normal answer, not an error. When found, `name` and `gstin` are the canonical values
    on record; use them in the extracted fields and pass `vendor_id` to find_similar_invoices.
    """
    with _db() as db:
        row = None
        if gstin:
            row = db.execute("SELECT * FROM vendors WHERE gstin = ?", (gstin.strip(),)).fetchone()
        if row is None:
            row = db.execute("SELECT * FROM vendors WHERE name = ? COLLATE NOCASE", (name.strip(),)).fetchone()
    if row is None:
        return {"found": False, "vendor_id": None, "name": None, "gstin": None, "active": None}
    return {"found": True, "vendor_id": row["vendor_id"], "name": row["name"], "gstin": row["gstin"],
            "active": bool(row["active"])}


@mcp.tool()
def get_purchase_order(po_number: str) -> dict:
    """Fetch the purchase order (PO) an invoice references, e.g. "PO-12345".

    Returns {found, po_number, vendor_id, amount, status}. `amount` is the approved PO value in INR,
    including tax. found=false means no such PO exists: a normal answer, not an error.
    """
    with _db() as db:
        row = db.execute("SELECT * FROM purchase_orders WHERE po_number = ?", (po_number.strip(),)).fetchone()
    if row is None:
        return {"found": False, "po_number": po_number, "vendor_id": None, "amount": None, "status": None}
    return {"found": True, **dict(row)}


@mcp.tool()
def find_similar_invoices(vendor_id: str, invoice_number: str, total: float | None = None) -> dict:
    """Search invoices already processed for this vendor, to catch duplicates.

    Needs the vendor_id from lookup_vendor and the invoice number as printed. Returns
    {matches: [{invoice_number, total, processed_on}]}: earlier invoices from the same vendor with
    the same invoice number, or (if `total` is given) the same total. A match with the same
    invoice_number IS a duplicate. A match on total alone is only similar, not a duplicate.
    An empty list means no earlier invoice was found.
    """
    with _db() as db:
        rows = db.execute(
            "SELECT invoice_number, total, processed_on FROM processed_invoices "
            "WHERE vendor_id = ? AND (invoice_number = ? OR (? IS NOT NULL AND ABS(total - ?) < ?))",
            (vendor_id, invoice_number.strip(), total, total, AMOUNT_EPSILON),
        ).fetchall()
    return {"matches": [dict(r) for r in rows]}


class LineIn(BaseModel):
    quantity: float
    unit_price: float


@mcp.tool()
def check_totals(line_items: list[LineIn], tax: float, total: float, po_amount: float | None = None) -> dict:
    """Do the invoice arithmetic exactly. Always use this instead of adding numbers yourself.

    Pass every line item's quantity and unit_price, the stated tax and the stated total, plus the PO
    amount from get_purchase_order when there is one. Returns:
      line_amounts       quantity x unit_price per line, in order (use these as line item amounts)
      subtotal           sum of line_amounts
      computed_total     subtotal + tax
      totals_match       true if computed_total equals the stated total (to 0.01)
      po_limit           the most the invoice may be: po_amount + 2%
      over_po_limit      true if the stated total exceeds po_limit (null without po_amount)
    """
    line_amounts = [round(li.quantity * li.unit_price, 2) for li in line_items]
    subtotal = round(sum(line_amounts), 2)
    computed = round(subtotal + tax, 2)
    po_limit = round(po_amount * (1 + PO_TOLERANCE), 2) if po_amount is not None else None
    return {
        "line_amounts": line_amounts,
        "subtotal": subtotal,
        "computed_total": computed,
        "totals_match": abs(computed - total) < AMOUNT_EPSILON,
        "difference": round(total - computed, 2),
        "po_limit": po_limit,
        "over_po_limit": None if po_limit is None else total > po_limit,
    }


@mcp.tool()
def flag_for_review(invoice_id: str, decision: Literal["hold", "reject"], reason: str) -> dict:
    """Record that an invoice needs a human: call exactly once for every hold or reject, never for approve.

    `invoice_id` is the id given in the task (e.g. "INV-0007"), `decision` is "hold" or "reject",
    and `reason` is one or two sentences naming the rule that fired. Writes a row to the review
    queue and returns {flag_id, created}. This has a side effect: do not call it twice.
    """
    # ponytail: not idempotent across runs yet; Week 3 adds an idempotency key (invoice_id + run_id).
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS review_flags (flag_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " invoice_id TEXT, decision TEXT, reason TEXT, created TEXT)"
        )
        cur = db.execute(
            "INSERT INTO review_flags (invoice_id, decision, reason, created) VALUES (?, ?, ?, ?)",
            (invoice_id, decision, reason, created),
        )
    return {"flag_id": cur.lastrowid, "created": created}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        os.environ["RECORDS_DB"] = sys.argv[1]
    mcp.run("stdio")
