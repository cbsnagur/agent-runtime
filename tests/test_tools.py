import asyncio
import json
import sqlite3

import pytest

from agent_runtime.mcp_tools import ToolError, connect
from examples.invoice_agent import server
from examples.invoice_agent.generate_data import generate


@pytest.fixture
def truth(tmp_path, monkeypatch):
    generate(tmp_path, 120, seed=1)
    monkeypatch.setenv("RECORDS_DB", str(tmp_path / "records.sqlite"))
    rows = [json.loads(line) for line in (tmp_path / "ground_truth.jsonl").read_text().splitlines()]
    return {case: [r["expected_fields"] for r in rows if r["case"] == case] for case in {r["case"] for r in rows}}


def _lines(f):
    return [server.LineIn(quantity=li["quantity"], unit_price=li["unit_price"]) for li in f["line_items"]]


def test_lookup_vendor_matches_gstin_then_name(truth):
    f = truth["clean"][0]
    by_gstin = server.lookup_vendor("some other spelling", f["vendor_gstin"])
    assert by_gstin["found"] and by_gstin["name"] == f["vendor_name"] and by_gstin["active"] is True
    by_name = server.lookup_vendor(f["vendor_name"].upper(), None)
    assert by_name["vendor_id"] == by_gstin["vendor_id"] and by_name["gstin"] == f["vendor_gstin"]


def test_lookup_vendor_unknown_is_not_found(truth):
    f = truth["unknown_vendor"][0]
    assert server.lookup_vendor(f["vendor_name"], f["vendor_gstin"])["found"] is False


def test_get_purchase_order(truth):
    po = server.get_purchase_order(truth["clean"][0]["po_number"])
    assert po["found"] and po["amount"] > 0 and po["status"] == "open"
    assert server.get_purchase_order(truth["missing_po"][0]["po_number"])["found"] is False


def test_find_similar_invoices_finds_duplicates_only(truth):
    dup = truth["duplicate"][0]
    vendor_id = server.lookup_vendor(dup["vendor_name"], dup["vendor_gstin"])["vendor_id"]
    matches = server.find_similar_invoices(vendor_id, dup["invoice_number"], None)["matches"]
    assert [m["invoice_number"] for m in matches] == [dup["invoice_number"]]

    clean = truth["clean"][0]
    vendor_id = server.lookup_vendor(clean["vendor_name"], clean["vendor_gstin"])["vendor_id"]
    assert server.find_similar_invoices(vendor_id, clean["invoice_number"], clean["total"])["matches"] == []


@pytest.mark.parametrize("case, totals_match, over", [
    ("clean", True, False), ("total_mismatch", False, None), ("po_over_limit", True, True),
])
def test_check_totals(truth, case, totals_match, over):
    f = truth[case][0]
    po = server.get_purchase_order(f["po_number"])
    out = server.check_totals(_lines(f), f["tax"], f["total"], po["amount"])
    assert out["line_amounts"] == [li["amount"] for li in f["line_items"]]
    assert out["totals_match"] is totals_match
    if over is not None:
        assert out["over_po_limit"] is over
    assert server.check_totals(_lines(f), f["tax"], f["total"])["over_po_limit"] is None


def test_flag_for_review_writes_a_row(truth, tmp_path):
    a = server.flag_for_review("INV-0001", "hold", "PO not found.")
    b = server.flag_for_review("INV-0002", "reject", "Duplicate.")
    assert b["flag_id"] == a["flag_id"] + 1 and a["created"]
    rows = sqlite3.connect(tmp_path / "records.sqlite").execute("SELECT invoice_id, decision FROM review_flags").fetchall()
    assert rows == [("INV-0001", "hold"), ("INV-0002", "reject")]


def test_over_mcp_tools_are_listed_and_errors_raise(truth):
    async def go():
        async with connect(server.mcp) as (tools, call_tool):
            names = {t.name for t in tools}
            assert {"lookup_vendor", "get_purchase_order", "find_similar_invoices", "flag_for_review"} <= names
            assert all(t.description and t.input_schema["type"] == "object" for t in tools)
            found = await call_tool("lookup_vendor", {"name": truth["clean"][0]["vendor_name"]})
            assert found["found"] is True
            with pytest.raises(ToolError):
                await call_tool("flag_for_review", {"invoice_id": "INV-1", "decision": "approve", "reason": "x"})
            with pytest.raises(ToolError):
                await call_tool("no_such_tool", {})

    asyncio.run(go())
