"""Generate a synthetic invoice dataset with ground truth.

Writes, under the output directory:
  invoices/INV-XXXX.txt   one rendered invoice per case
  ground_truth.jsonl      expected fields, decision and case type per invoice
  records.sqlite          vendors, purchase orders and previously processed invoices

Usage: python examples/invoice_agent/generate_data.py --out data --count 50 --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

TAX_RATE = 0.18
PO_TOLERANCE = 0.02  # invoice may exceed its PO by up to 2%

# Case type -> expected decision. Weights set the mix of a generated dataset.
CASES = {
    "clean": ("approve", 0.55),
    "total_mismatch": ("reject", 0.10),
    "duplicate": ("reject", 0.10),
    "missing_po": ("hold", 0.10),
    "po_over_limit": ("hold", 0.08),
    "unknown_vendor": ("hold", 0.07),
}

VENDOR_NAMES = [
    "Apex Office Supplies", "Brightline Logistics", "Cobalt IT Services", "Deccan Packaging",
    "Everest Facility Mgmt", "Fernwood Catering", "Granite Security", "Harbor Freight Lines",
    "Indus Cloud Hosting", "Jasper Print Works", "Kaveri Electricals", "Lotus Travel Desk",
    "Meridian Legal LLP", "Northstar Staffing", "Orchid Pest Control", "Pinnacle Furniture",
]
ITEMS = [
    ("A4 paper ream", 280), ("Laptop stand", 1450), ("Courier shipment", 650),
    ("Cloud VM (monthly)", 8200), ("Security guard shift", 1900), ("Team lunch (per head)", 420),
    ("Printer toner", 3600), ("Ergonomic chair", 11500), ("Network switch", 18400),
    ("Legal consultation (hour)", 6500), ("Pest control visit", 2700), ("Flight booking fee", 350),
]


@dataclass
class LineItem:
    description: str
    quantity: int
    unit_price: float

    @property
    def amount(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class Invoice:
    invoice_id: str
    vendor_name: str
    vendor_gstin: str
    invoice_number: str
    invoice_date: str
    po_number: str
    line_items: list[LineItem] = field(default_factory=list)
    tax: float = 0.0
    total: float = 0.0


def _gstin(rng: random.Random) -> str:
    letters = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5))
    return f"29{letters}{rng.randint(1000, 9999)}{rng.choice('ABCDEFGH')}1Z{rng.randint(1, 9)}"


def _pick_case(rng: random.Random) -> str:
    names = list(CASES)
    return rng.choices(names, weights=[CASES[n][1] for n in names])[0]


def _render(inv: Invoice, layout: int) -> str:
    items = inv.line_items
    if layout == 0:
        rows = "\n".join(
            f"  {li.description:<28}{li.quantity:>4} x {li.unit_price:>10.2f} = {li.amount:>12.2f}" for li in items
        )
        return (
            f"TAX INVOICE\n{inv.vendor_name}\nGSTIN: {inv.vendor_gstin}\n\n"
            f"Invoice No: {inv.invoice_number}\nDate: {inv.invoice_date}\nPO Ref: {inv.po_number}\n\n"
            f"Items:\n{rows}\n\nGST @18%: {inv.tax:.2f}\nTOTAL DUE: INR {inv.total:.2f}\n"
        )
    if layout == 1:
        rows = "\n".join(f"| {li.description} | {li.quantity} | {li.unit_price:.2f} | {li.amount:.2f} |" for li in items)
        d = date.fromisoformat(inv.invoice_date)
        return (
            f"From: {inv.vendor_name} (GSTIN {inv.vendor_gstin})\n"
            f"Bill #{inv.invoice_number} dated {d.strftime('%d %b %Y')}, against purchase order {inv.po_number}\n\n"
            f"| Description | Qty | Rate | Amount |\n|---|---|---|---|\n{rows}\n\n"
            f"Tax: Rs. {inv.tax:,.2f}\nGrand total: Rs. {inv.total:,.2f}\n"
        )
    rows = "; ".join(f"{li.quantity} x {li.description} at {li.unit_price:.2f}" for li in items)
    d = date.fromisoformat(inv.invoice_date)
    return (
        f"{inv.vendor_name.upper()}\nInvoice {inv.invoice_number} / {d.strftime('%d-%m-%Y')}\n"
        f"Your PO: {inv.po_number}\nSupplied: {rows}.\n"
        f"Taxes (18% GST) {inv.tax:.2f}. Amount payable {inv.total:.2f} INR.\n"
    )


def generate(out: Path, count: int, seed: int) -> None:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    (out / "invoices").mkdir(exist_ok=True)

    vendors = [
        {"vendor_id": f"V{i + 1:03d}", "name": n, "gstin": _gstin(rng), "active": 1}
        for i, n in enumerate(VENDOR_NAMES)
    ]
    db_path = out / "records.sqlite"
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    db.executescript(
        """
        CREATE TABLE vendors (vendor_id TEXT PRIMARY KEY, name TEXT, gstin TEXT, active INTEGER);
        CREATE TABLE purchase_orders (po_number TEXT PRIMARY KEY, vendor_id TEXT, amount REAL, status TEXT);
        CREATE TABLE processed_invoices (vendor_id TEXT, invoice_number TEXT, total REAL, processed_on TEXT);
        """
    )
    db.executemany("INSERT INTO vendors VALUES (:vendor_id, :name, :gstin, :active)", vendors)

    start = date(2026, 7, 1)
    truth = []
    used: set[str] = set()

    def unique(make) -> str:
        while (value := make()) in used:
            pass
        used.add(value)
        return value

    for i in range(count):
        case = _pick_case(rng)
        vendor = rng.choice(vendors)
        items = [
            LineItem(desc, rng.randint(1, 12), float(price))
            for desc, price in rng.sample(ITEMS, rng.randint(1, 4))
        ]
        subtotal = round(sum(li.amount for li in items), 2)
        tax = round(subtotal * TAX_RATE, 2)
        total = round(subtotal + tax, 2)
        inv = Invoice(
            invoice_id=f"INV-{i + 1:04d}",
            vendor_name=vendor["name"],
            vendor_gstin=vendor["gstin"],
            invoice_number=unique(lambda: f"{vendor['vendor_id'][1:]}/{rng.randint(1000, 9999)}"),
            invoice_date=(start + timedelta(days=rng.randint(0, 90))).isoformat(),
            po_number=unique(lambda: f"PO-{rng.randint(10000, 99999)}"),
            line_items=items,
            tax=tax,
            total=total,
        )
        if case == "total_mismatch":
            # Off by 3-20% either way: big enough to matter, never negative.
            inv.total = round(total * (1 + rng.choice([-1, 1]) * rng.uniform(0.03, 0.2)), 2)
        elif case == "unknown_vendor":
            inv.vendor_name = rng.choice(["Zenith Traders", "Quickfix Solutions", "Ruby Enterprises"])
            inv.vendor_gstin = _gstin(rng)
        elif case == "duplicate":
            db.execute(
                "INSERT INTO processed_invoices VALUES (?, ?, ?, ?)",
                (vendor["vendor_id"], inv.invoice_number, inv.total, inv.invoice_date),
            )

        # The PO covers what the vendor billed, except when the case is that it doesn't.
        if case == "po_over_limit":
            po_amount = round(inv.total / rng.uniform(1.08, 1.4), 2)
        else:
            po_amount = round(inv.total * rng.uniform(1.0, 1.15), 2)

        if case != "missing_po":
            db.execute(
                "INSERT INTO purchase_orders VALUES (?, ?, ?, 'open')",
                (inv.po_number, vendor["vendor_id"], po_amount),
            )

        (out / "invoices" / f"{inv.invoice_id}.txt").write_text(_render(inv, rng.randint(0, 2)))
        expected = asdict(inv)
        expected["line_items"] = [asdict(li) | {"amount": li.amount} for li in inv.line_items]
        truth.append(
            {
                "invoice_id": inv.invoice_id,
                "case": case,
                "expected_decision": CASES[case][0],
                "expected_fields": expected,
            }
        )

    db.commit()
    db.close()
    with open(out / "ground_truth.jsonl", "w") as f:
        for row in truth:
            f.write(json.dumps(row) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=Path("data"))
    p.add_argument("--count", type=int, default=50)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()
    generate(args.out, args.count, args.seed)
    print(f"Wrote {args.count} invoices to {args.out}")


if __name__ == "__main__":
    main()
