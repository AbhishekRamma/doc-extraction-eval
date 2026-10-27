"""pp-merchant-v1 tests. All names are synthetic."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from src.postprocess import VERSION, fix_legal_suffix, postprocess_merchant, resplit_joined  # noqa: E402
import postprocess_results  # noqa: E402


@pytest.mark.parametrize("raw,want", [
    ("ACME WIDGETS SON BHD", "ACME WIDGETS SDN BHD"),
    ("ACME WIDGETS SIN BHD", "ACME WIDGETS SDN BHD"),
    ("ACME WIDGETS S0N. BHD.", "ACME WIDGETS SDN BHD"),
    ("ACME WIDGETS 5DN BHD", "ACME WIDGETS SDN BHD"),
    ("ACME WIDGETS SDN 8HD", "ACME WIDGETS SDN BHD"),
    ("ACME WIDGETS SON BHD (123456-X)", "ACME WIDGETS SDN BHD (123456-X)"),
    ("ACME WIDGETSSDN BHD", "ACME WIDGETS SDN BHD"),
    ("ACME WIDGETS SDNBHD", "ACME WIDGETS SDN BHD"),
    ("Acme Widgets Son Bhd", "Acme Widgets SDN BHD"),
    ("ZETA WORKS S/D", "ZETA WORKS S/B"),
    ("ZETA WORKS 5/B", "ZETA WORKS S/B"),
    ("ZETA WORKS S/8 (99999-A)", "ZETA WORKS S/B (99999-A)"),
])
def test_suffix_fixes(raw, want):
    assert fix_legal_suffix(raw)[0] == want


@pytest.mark.parametrize("raw", [
    "ACME WIDGETS SDN BHD",          # already right
    "RESTORAN SIN KEE",              # SIN not followed by BHD
    "SON BHD TRADING",               # not at the end -> not the legal suffix
    "KEDAI SON",                     # no BHD
    "ZETA WORKS S/B",
    "ACME CO. (M) BHD",              # Berhad without SDN is a valid suffix
    "ZETA S/D ENTERPRISE",           # S/D not at the end
    "WIDGET BHP STATION",            # BHP without SDN
])
def test_suffix_leaves_alone(raw):
    assert fix_legal_suffix(raw) == (raw, [])


@pytest.mark.parametrize("raw,want", [
    ("ZETA MINIMARKET", "ZETA MINI MARKET"),
    ("ZETA FRE5HMARKET", "ZETA FRESH MARKET"),    # one 5/s confusion
    ("ZETA BAKERYHOUSE", "ZETA BAKERY HOUSE"),
    ("ZETA BOOKSHOP", "ZETA BOOK SHOP"),
    ("ZETA MINLSTORE", "ZETA MINI STORE"),        # one l/i confusion
    ("zeta bookstationery", "zeta book stationery"),
    ("ZETA HARDWARESTORE", "ZETA HARDWARE STORE"),
])
def test_resplit(raw, want):
    assert resplit_joined(raw)[0] == want


@pytest.mark.parametrize("raw", [
    "ZETA HARDWARE",       # dictionary word itself
    "ZETAQUIXOTE MART",    # unknown parts
    "ZETA TAILORING",      # 'tail'+'oring' is not two dictionary words
    "SUPERMARKET",         # compound already in the dictionary
    "ABC",                 # short
    "BOOKMART2",           # mixed digits not resplit
])
def test_resplit_leaves_alone(raw):
    assert resplit_joined(raw) == (raw, [])


def test_postprocess_none_and_rules_listed():
    assert postprocess_merchant(None) == (None, [])
    s, fired = postprocess_merchant("ZETA FRE5HMARKET SON BHD")
    assert s == "ZETA FRESH MARKET SDN BHD"
    assert fired == ["suffix:sdn-bhd-ocr", "resplit"]


def test_results_file_keeps_raw(tmp_path):
    raw = {"run_id": "r", "extractor_version": "x-v1",
           "receipts": [{"id": "a", "merchant": "ACME SON BHD", "total": "1.00"},
                        {"id": "b", "merchant": None}]}
    out, counts = postprocess_results.postprocess_results(raw)
    assert raw["receipts"][0]["merchant"] == "ACME SON BHD"      # input untouched
    r0 = out["receipts"][0]
    assert (r0["merchant"], r0["merchant_raw"], r0["total"]) == ("ACME SDN BHD", "ACME SON BHD", "1.00")
    assert out["extractor_version"] == f"x-v1+{VERSION}" and out["run_id"] == "r__pp"
    assert counts["receipts_changed"] == 1
    p = tmp_path / "raw.json"; p.write_text(json.dumps(raw))
    with pytest.raises(SystemExit):
        postprocess_results.main([str(p), str(p)])
