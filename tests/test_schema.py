import json

import pytest
from pydantic import ValidationError

from src.schema import LineItem, Receipt, ReceiptResult, RunResult


def test_receipt_normalises_fields():
    r = Receipt.model_validate({"merchant": "  Shop  ", "date": "25/12/2018", "total": "RM 1,234.50"})
    assert r.merchant == "Shop"
    assert r.date == "2018-12-25"
    assert r.total == "1234.50"
    assert r.line_items == []


def test_unparseable_values_become_none():
    r = Receipt.model_validate({"merchant": "", "date": "not a date", "total": "n/a"})
    assert (r.merchant, r.date, r.total) == (None, None, None)


def test_line_item_validation():
    li = LineItem.model_validate({"description": "Tea", "quantity": 2, "unit_price": "22.000", "amount": "44.000"})
    assert li.amount == "44000.00" and li.quantity == 2
    with pytest.raises(ValidationError):
        LineItem.model_validate({"quantity": 1})          # description required
    with pytest.raises(ValidationError):
        LineItem.model_validate({"description": "x", "quantity": "lots"})


def test_line_items_must_be_list():
    with pytest.raises(ValidationError):
        Receipt.model_validate({"line_items": "nope"})


def test_run_result_matches_agreed_format():
    run = RunResult(run_id="r1", model="rules:rules", prompt_version="none",
                    ocr_engine="tesseract-5.5.0", extractor_version="rules-v1",
                    receipts=[ReceiptResult(id="a", total="1.00", needs_review=True,
                                            review_reasons=["missing_date"])])
    d = json.loads(run.model_dump_json())
    assert set(d) == {"run_id", "model", "prompt_version", "ocr_engine",
                      "extractor_version", "created_at", "receipts"}
    assert d["created_at"].endswith("Z")
    rec = d["receipts"][0]
    assert set(rec) == {"id", "merchant", "date", "total", "line_items", "valid",
                        "needs_review", "review_reasons", "error"}
    assert RunResult.model_validate(d).receipts[0].total == "1.00"


def test_usage_key_only_written_when_present():
    r = ReceiptResult(id="a", usage={"total_tokens": 7})
    assert json.loads(r.model_dump_json())["usage"] == {"total_tokens": 7}
    assert "usage" not in json.loads(ReceiptResult(id="b").model_dump_json())
