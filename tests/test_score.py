from examples.invoice_agent.score import FIELDS, field_checks, report, score

EXP = {
    "invoice_id": "INV-0001", "vendor_name": "Apex Office Supplies", "vendor_gstin": "29ABCDE1234A1Z5",
    "invoice_number": "001/1234", "invoice_date": "2026-08-15", "po_number": "PO-12345",
    "line_items": [{"description": "A4 paper ream", "quantity": 2, "unit_price": 280.0, "amount": 560.0}],
    "tax": 100.8, "total": 660.8,
}


def test_field_checks_normalise_names_dates_and_amounts():
    got = {**EXP, "vendor_name": "APEX OFFICE  SUPPLIES", "invoice_date": "15-08-2026", "total": "660.805"}
    assert all(field_checks(got, EXP).values())
    bad = {**EXP, "invoice_number": "001/1235", "tax": 101.0, "invoice_date": "garbage",
           "line_items": [{**EXP["line_items"][0], "amount": 561.0}]}
    assert [k for k, ok in field_checks(bad, EXP).items() if not ok] == ["invoice_date", "line_items", "tax"] or \
        {k for k, ok in field_checks(bad, EXP).items() if not ok} == {"invoice_number", "invoice_date", "line_items", "tax"}


def test_score_and_report_per_case_type():
    truth = [
        {"invoice_id": "INV-0001", "case": "clean", "expected_decision": "approve", "expected_fields": EXP},
        {"invoice_id": "INV-0002", "case": "duplicate", "expected_decision": "reject", "expected_fields": EXP},
    ]
    results = [{"invoice_id": "INV-0001", "fields": EXP, "decision": "approve", "trace": {"steps": 3, "cost_usd": 0.01}},
               {"invoice_id": "INV-0002", "fields": {}, "decision": "approve", "trace": {"steps": 4}}]
    rows = score(results, truth)
    assert rows[0]["fields_correct"] == len(FIELDS) and rows[1]["fields_correct"] == 0
    text = report(rows)
    assert "decision accuracy: 1/2 (50%)" in text
    assert "duplicate       0/1 (0%)" in text and "approve x1" in text
