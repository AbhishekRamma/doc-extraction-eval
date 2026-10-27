"""Unit tests for src/evaluate.py using tiny hand-made, clearly fake fixtures.

Expected outcome per fake receipt (split "all"):
  fake_001  all three right after normalisation  ('TOKO MAJU.' / '25 Mar 2019' / '60000')
  fake_002  merchant wrong, date right ('14/03/2019' == '2019-03-14'), total right
  fake_003  merchant/date gold are null -> not scored; total wrong (14000 vs 15000)
  fake_004  missing from results -> wrong on all 3 scored fields
  fake_999  extra prediction with no gold -> ignored
"""
from pathlib import Path

from src.evaluate import load_gold, load_results, run_metadata, score

FIX = Path(__file__).parent / "fixtures"
GOLD = FIX / "eval_gold_fake.json"
RESULTS = FIX / "eval_results_fake.json"


def _score(split="all"):
    return score(load_gold(GOLD, split), load_results(RESULTS))


def test_per_field_counts_skip_null_gold():
    m = _score()
    # merchant scored on 001, 002, 004 (003 gold is null)
    assert m["per_field"]["merchant"] == {"correct": 1, "scored": 3, "accuracy": 1 / 3}
    assert m["per_field"]["date"] == {"correct": 2, "scored": 3, "accuracy": 2 / 3}
    # total scored on all 4: 001 and 002 right
    assert m["per_field"]["total"] == {"correct": 2, "scored": 4, "accuracy": 0.5}


def test_overall_and_receipt_exact():
    m = _score()
    assert m["overall"]["correct"] == 5 and m["overall"]["scored"] == 10
    # only fake_001 is fully right, out of 4 receipts with a scored field
    assert m["receipt_exact"]["correct"] == 1 and m["receipt_exact"]["scored"] == 4


def test_missing_id_counts_wrong_on_every_scored_field():
    m = _score()
    missing = [f for f in m["failures"] if f["id"] == "fake_004"]
    assert {f["field"] for f in missing} == {"merchant", "date", "total"}
    assert m["n_missing_from_results"] == 1


def test_extra_prediction_is_ignored():
    m = _score()
    assert m["n_extra_in_results"] == 1
    assert all(f["id"] != "fake_999" for f in m["failures"])


def test_normalisation_date_and_total():
    m = _score()
    failed = {(f["id"], f["field"]) for f in m["failures"]}
    assert ("fake_001", "date") not in failed      # '25 Mar 2019' == '2019-03-25'
    assert ("fake_001", "total") not in failed     # '60000' == '60.000' (IDR)
    assert ("fake_002", "date") not in failed      # '2019-03-14' == '14/03/2019'
    assert ("fake_002", "total") not in failed     # '1591600.00' == '1,591,600'
    assert ("fake_003", "total") in failed         # 14000 != 15000


def test_valid_and_review_rates_count_missing():
    m = _score()
    assert m["valid_rate"] == {"count": 2, "of": 4, "rate": 0.5}
    # 002, 003 flagged + 004 missing
    assert m["needs_review_rate"] == {"count": 3, "of": 4, "rate": 0.75}


def test_split_filter():
    m = _score("quick")
    assert m["n_receipts"] == 3 and m["n_missing_from_results"] == 0
    # the non-quick rows are labelled "holdout"
    h = _score("holdout")
    assert h["n_receipts"] == 1 and h["n_missing_from_results"] == 1
    # "full" is the whole golden set (same meaning as src/pipeline.py)
    assert _score("full")["n_receipts"] == 4
    assert _score("all")["n_receipts"] == 4


def test_nothing_scored_gives_none_not_zero():
    gold = [{"id": "fake_x", "split": "quick", "merchant": None, "date": None, "total": None}]
    m = score(gold, {"receipts": []})
    assert m["overall"]["accuracy"] is None
    assert m["per_field"]["merchant"]["scored"] == 0


def test_metadata_defaults_to_unknown():
    meta = run_metadata(load_results(RESULTS))
    assert meta["model"] == "fake-model"
    assert meta["ocr_engine"] == "unknown" and meta["extractor_version"] == "unknown"


# ---- secondary merchant_fuzzy metric -------------------------------------
from src.evaluate import MERCHANT_FUZZY_THRESHOLD, merchant_similarity  # noqa: E402


def _one(gold_merchant, pred_merchant):
    gold = [{"id": "fake_m1", "split": "quick", "merchant": gold_merchant, "date": None, "total": None}]
    results = {"receipts": [{"id": "fake_m1", "merchant": pred_merchant}]}
    return score(gold, results)


def test_fuzzy_counts_ocr_typo_but_strict_does_not():
    assert merchant_similarity("sunrise mini market", "sunrise mini rnarket") >= MERCHANT_FUZZY_THRESHOLD
    m = _one("SUNRISE MINI MARKET", "SUNRISE MINI RNARKET")
    assert m["merchant_fuzzy"]["correct"] == 1 and m["merchant_fuzzy"]["scored"] == 1
    assert m["per_field"]["merchant"]["correct"] == 0          # strict stays strict
    assert m["overall"]["correct"] == 0                        # fuzzy not in overall
    assert m["receipt_exact"]["correct"] == 0                  # nor in receipt exact


def test_fuzzy_rejects_brand_vs_legal_name():
    assert merchant_similarity("northwind traders sdn bhd", "sunbean") < MERCHANT_FUZZY_THRESHOLD
    m = _one("NORTHWIND TRADERS SDN BHD", "SUNBEAN")
    assert m["merchant_fuzzy"]["correct"] == 0 and m["merchant_fuzzy"]["scored"] == 1


def test_fuzzy_missing_prediction_and_existing_fixture_numbers():
    assert merchant_similarity("anything", None) == 0.0
    m = _score()
    # fixture: 001 exact, 002 'cafe teh' vs 'cafe kopi' (not >= 0.90), 004 missing
    assert m["merchant_fuzzy"] == {"correct": 1, "scored": 3, "threshold": 0.90, "accuracy": 1 / 3}
    assert m["overall"]["correct"] == 5 and m["overall"]["scored"] == 10   # unchanged
