import json
import sqlite3
from collections import Counter

from examples.invoice_agent.generate_data import CASES, PO_TOLERANCE, generate


def _load(tmp_path, count=200, seed=1):
    generate(tmp_path, count, seed)
    rows = [json.loads(line) for line in (tmp_path / "ground_truth.jsonl").read_text().splitlines()]
    db = sqlite3.connect(tmp_path / "records.sqlite")
    return rows, db


def test_writes_one_invoice_per_row(tmp_path):
    rows, _ = _load(tmp_path, count=30)
    assert len(rows) == 30
    assert len(list((tmp_path / "invoices").glob("*.txt"))) == 30


def test_is_deterministic(tmp_path):
    a, _ = _load(tmp_path / "a", seed=3)
    b, _ = _load(tmp_path / "b", seed=3)
    assert a == b


def test_every_case_type_appears(tmp_path):
    rows, _ = _load(tmp_path)
    assert set(Counter(r["case"] for r in rows)) == set(CASES)


def test_labels_match_the_records(tmp_path):
    rows, db = _load(tmp_path)
    vendors = {name for (name,) in db.execute("SELECT name FROM vendors")}
    for r in rows:
        f = r["expected_fields"]
        subtotal = round(sum(li["amount"] for li in f["line_items"]), 2)
        po = db.execute("SELECT amount FROM purchase_orders WHERE po_number = ?", (f["po_number"],)).fetchone()
        dup = db.execute(
            "SELECT 1 FROM processed_invoices WHERE invoice_number = ?", (f["invoice_number"],)
        ).fetchone()
        arithmetic_ok = abs(subtotal + f["tax"] - f["total"]) < 0.01

        case = r["case"]
        assert arithmetic_ok == (case != "total_mismatch"), r["invoice_id"]
        assert (dup is not None) == (case == "duplicate"), r["invoice_id"]
        assert (po is None) == (case == "missing_po"), r["invoice_id"]
        assert (f["vendor_name"] in vendors) == (case != "unknown_vendor"), r["invoice_id"]
        if po is not None:
            over = f["total"] > po[0] * (1 + PO_TOLERANCE)
            assert over == (case == "po_over_limit"), r["invoice_id"]
