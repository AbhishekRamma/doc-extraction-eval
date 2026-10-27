import json

import pytest

from src import extract
from src.extract import (CachedExtractor, RulesExtractor, extract_receipt, get_client,
                         parse_json, rules_extract)
from tests.conftest import FIXTURES

GOOD = json.dumps({"merchant": "Shop", "date": "2018-12-25", "total": "9.90", "line_items": []})


class FakeClient:
    """Returns canned replies in order and counts calls."""
    name = "fake"
    model = "fake-1"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, receipt_id, prompt, ocr_text):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_valid_first_try():
    c = FakeClient([GOOD])
    r = extract_receipt("a", "text", c)
    assert c.calls == 1 and r.valid and not r.needs_review and r.total == "9.90"


def test_retry_once_on_invalid_json_then_succeeds():
    c = FakeClient(["not json", GOOD])
    r = extract_receipt("a", "text", c)
    assert c.calls == 2 and r.valid and r.error is None


def test_retry_once_on_validation_error_then_succeeds():
    c = FakeClient([json.dumps({"line_items": "oops"}), GOOD])
    r = extract_receipt("a", "text", c)
    assert c.calls == 2 and r.valid


def test_gives_up_after_two_attempts():
    c = FakeClient(["nope", "[1, 2]", GOOD])
    r = extract_receipt("a", "text", c)
    assert c.calls == 2
    assert not r.valid and r.needs_review and r.error
    assert r.review_reasons == ["invalid_output"]


def test_provider_exception_is_retried_then_reported():
    c = FakeClient([TimeoutError("slow"), TimeoutError("slow again")])
    r = extract_receipt("a", "text", c)
    assert c.calls == 2 and not r.valid and "TimeoutError" in r.error


@pytest.mark.parametrize("payload,reasons", [
    ({"merchant": "S", "date": "2018-12-25", "total": None}, ["missing_total"]),
    ({"merchant": "S", "date": None, "total": "1.00"}, ["missing_date"]),
    ({"merchant": "S", "date": "2018-12-25", "total": "about ten"}, ["total_unparseable"]),
    ({"merchant": "S", "date": "someday", "total": "1.00"}, ["date_unparseable"]),
    ({"merchant": None, "date": None, "total": None}, ["missing_total", "missing_date"]),
])
def test_needs_review_rule(payload, reasons):
    r = extract_receipt("a", "t", FakeClient([json.dumps(payload)]))
    assert r.valid and r.needs_review and r.review_reasons == reasons


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert parse_json("Sure! {\"a\": 1} hope that helps") == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("[1]")


def test_prompt_is_versioned_and_contains_text():
    p = extract.build_prompt("HELLO OCR")
    assert "HELLO OCR" in p and "{ocr_text}" not in p
    assert extract.PROMPT_VERSION == "extract_v1"


def test_cached_extractor_reads_fixtures():
    c = CachedExtractor(FIXTURES / "cached_llm")
    assert extract_receipt("synthetic_001", "", c).total == "11.20"
    r2 = extract_receipt("synthetic_002", "", c)            # fenced reply, rupiah amounts
    assert r2.valid and r2.total == "60000.00" and r2.review_reasons == ["missing_date"]
    r3 = extract_receipt("synthetic_003", "", c)            # deliberately broken reply
    assert not r3.valid and r3.error
    r4 = extract_receipt("nope", "", c)                     # missing file: no retry
    assert not r4.valid and "FileNotFoundError" in r4.error


@pytest.mark.parametrize("text,total", [
    ("SHOP\nSUBTOTAL 60.000\nTOTAL 60.000\nCASH 100.000\nKEMBALI 40.000", "60000.00"),
    ("SHOP\nSub Total 9.43\nRounding 0.02\nTOTAL (RM) 9.45\nCash 10.00\nChange 0.55", "9.45"),
    ("SHOP\nTotal Bayar\nRp 1,591,600\nTunai 2,000,000", "1591600.00"),
    ("SHOP\nTotal Qty 3\nJumlah 45.500\nCASH 50.000", "45500.00"),
    ("SHOP\nTOTAL 10.00\nGRAND TOTAL 10.60\nCHANGE 9.40", "10.60"),
])
def test_rules_total(text, total):
    assert rules_extract(text)["total"] == total


def test_rules_merchant_and_date():
    out = rules_extract("TAX INVOICE\nABC TRADING SDN BHD\nTel: 03-1234\nDate: 25/12/2018\nTOTAL 1.00")
    assert out["merchant"] == "ABC TRADING SDN BHD"
    assert out["date"] == "2018-12-25"


def test_rules_guess_is_flagged():
    r = extract_receipt("a", "SHOP\nItem 4.00\nItem 6.50\n25/12/2018", RulesExtractor())
    assert r.total == "6.50" and r.review_reasons == ["total_guessed_no_keyword"]


def test_paid_providers_dormant_without_keys(monkeypatch):
    monkeypatch.setattr(extract, "_load_env", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for p in ("openai", "anthropic"):
        with pytest.raises(RuntimeError, match="not set"):
            get_client(p)


def test_ollama_unreachable_raises(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")  # discard port, nothing listens
    with pytest.raises(RuntimeError, match="not reachable"):
        get_client("ollama")
