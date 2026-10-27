"""rules-v2 total picker. All receipts here are synthetic (made-up shops and amounts)."""
import json

import pytest

from src.extract import (RulesExtractor, RulesExtractorV2, _find_total_v2, _money_v2,
                         extract_receipt, get_client, rules_extract, rules_extract_v2)

# rules-v1's own total cases must still hold under rules-v2
V1_CASES = [
    ("SHOP\nSUBTOTAL 60.000\nTOTAL 60.000\nCASH 100.000\nKEMBALI 40.000", "60000.00"),
    ("SHOP\nSub Total 9.43\nRounding 0.02\nTOTAL (RM) 9.45\nCash 10.00\nChange 0.55", "9.45"),
    ("SHOP\nTotal Bayar\nRp 1,591,600\nTunai 2,000,000", "1591600.00"),
    ("SHOP\nTotal Qty 3\nJumlah 45.500\nCASH 50.000", "45500.00"),
    ("SHOP\nTOTAL 10.00\nGRAND TOTAL 10.60\nCHANGE 9.40", "10.60"),
]


@pytest.mark.parametrize("text,total", V1_CASES)
def test_v2_keeps_v1_cases(text, total):
    assert rules_extract_v2(text)["total"] == total


def L(text):
    return [l.strip() for l in text.splitlines() if l.strip()]


@pytest.mark.parametrize("text,total", [
    # after-tax total beats the pre-tax subtotal; the GST summary table is ignored
    ("SHOP\nTotal Amount (Excluding GST) :\n74.00\nGST Amount :\n4.44\nRounding Adj :\n0.01\n"
     "Total Amount (Inclusive of GST) :\n78.45\nCASH :\n80.00\nChange :\n1.55\n"
     "GST SUMMARY\nTotal :\n74.00\n4.44", "78.45"),
    # value printed a few lines below its label, with a lone currency line in between
    ("SHOP\nSub Total\n79.00\nGST 6%\n4.74\nTOTAL :\nRM\n83.74\nCASH\n90.00\nCHANGE:\n6.26", "83.74"),
    # the amount after the rounding line is the rounded total
    ("SHOP\nTotal Incl. GST\n12.34\nRounding Adj\n0.01\nTotal\n12.35\nCash\n20.00\nChange\n7.65", "12.35"),
    # unlabelled rounded total right after the rounding adjustment's value
    ("SHOP\nTOTAL SUM\nRM\n83.47\nROUNDING ADJ.\n-0.02\nRM\n83.45\nCASH\nRM\n90.00\nCHANGE\nRM\n6.55",
     "83.45"),
    # 'Total Incl. GST@6%' must not read the 6 of 6% as the amount
    ("SHOP\nTOTAL INCL.GST@6%\nRM 94.20\nCASH\nRM 100.00\nCHANGE\nRM 5.80", "94.20"),
    # quantity / item-count lines are not totals (OCR 'ITENS' too); value on the next line
    ("SHOP\nSUB TOTAL\n35,000\nTOTAL SALES\n35,000\nTOTAL ITENS\n1\nCASH\n50,000\nCHANGE\n15,000",
     "35000.00"),
    # 'TOTAL (2 item)' is still the total line
    ("SHOP\nA\n70,000\nB\n50,000\nTOTAL (2 item)\n120,000\nCC.Visa\n120,000", "120000.00"),
    # common OCR misread of TOTAL
    ("SHOP\nSub Total\n37.00\nTatal Due :\n39.22\nCASH :\n41.00", "39.22"),
    # a short OCR fragment between label and value is skipped, but a QTY label is not
    ("SHOP\nTOTAL\nWnx\n26.40\nCASH\n26.40", "26.40"),
])
def test_v2_totals(text, total):
    assert rules_extract_v2(text)["total"] == total
    assert not rules_extract_v2(text).get("_total_guessed")


def test_v2_no_keyword_uses_cash_minus_change():
    text = "SHOP\nItem\n80000\nSub Total\n80000\nTunai\n100000\nKembalian\nRp.20000"
    assert _find_total_v2(L(text)) == ("80000.00", True)


def test_v2_no_keyword_change_zero():
    text = "SHOP\nItem\n156000\n156000\nSub Total\n156000\nTunai\n156000\nKembalian\nRp.0"
    out = rules_extract_v2(text)
    assert out["total"] == "156000.00" and out["_total_guessed"] is True


def test_v2_fallback_rejects_merged_digits_ids_and_zero():
    # '8120,000' (a stray digit merged into 120,000) and a long reg number are not money
    text = "SHOP\nGST No: 000123456789\n8120,000\n120,000\n0.00\nThank you"
    assert _find_total_v2(L(text)) == ("120000.00", True)


def test_v2_fallback_prefers_separated_amounts_over_bare_ids():
    text = "901016\nITEM\n60.000\nDISC\n-60.000\nTAX\n5.455\nTOTAL\n(Qty\n2.00\n60.000"
    assert rules_extract_v2(text)["total"] == "60000.00"


def test_money_v2():
    assert _money_v2("RM 47.18") == ["47.18"]
    assert _money_v2("GST 6% 0.83") == ["0.83"]
    assert _money_v2("8120,000") == []           # malformed thousands group
    assert _money_v2("0.00") == []               # zero is never the total
    assert _money_v2("-0.02") == []              # adjustments / discounts
    assert _money_v2("000584089600") == []       # 12-digit ID
    assert _money_v2("156000") == ["156000.00"]  # CORD prints bare integers
    assert _money_v2("156000", bare=False) == []


def test_rules_v1_is_unchanged():
    # the v1 behaviour that v2 fixes must still be there, so old runs stay reproducible
    text = "SHOP\nSUB TOTAL\n35,000\nTOTAL SALES\n35,000\nTOTAL ITENS\n1\nCASH\n50,000"
    assert rules_extract(text)["total"] == "1.00"
    assert RulesExtractor.version == "rules-v1"


def test_rules_v2_provider_and_version():
    c = get_client("rules-v2")
    assert isinstance(c, RulesExtractorV2) and c.version == "rules-v2"
    out = json.loads(c.complete("x", "", "SHOP\nTOTAL 5.00\n14/03/2019"))
    assert out["total"] == "5.00" and out["date"] == "2019-03-14"
    r = extract_receipt("x", "SHOP\nItem 4.00\nItem 6.50\n14/03/2019", c)
    assert r.total == "6.50" and r.review_reasons == ["total_guessed_no_keyword"]


def test_rules_v2_is_the_default_provider(monkeypatch):
    import argparse
    from src import pipeline
    from src.extract import DEFAULT_PROVIDER
    assert DEFAULT_PROVIDER == "rules-v2"
    seen = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, *a, **k):
        seen["provider"] = real(self, *a, **k).provider
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", spy)
    with pytest.raises(SystemExit):
        pipeline.main([])
    assert seen["provider"] == "rules-v2"
    # rules-v1 stays selectable and frozen
    v1 = get_client("rules")
    assert isinstance(v1, RulesExtractor) and v1.version == "rules-v1"
