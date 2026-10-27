"""Tests for scripts/heldout_stats.py (bootstrap CI + McNemar), on fake data."""
import importlib.util
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("heldout_stats", ROOT / "scripts" / "heldout_stats.py")
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)

FIX = ROOT / "tests" / "fixtures"


def test_mcnemar_exact_known_values():
    # b=0, c=6: two-sided exact p = 2 * 0.5**6
    assert hs.mcnemar(0, 6)["p_exact_two_sided"] == pytest.approx(2 / 64)
    # symmetric discordance is p = 1
    assert hs.mcnemar(3, 3)["p_exact_two_sided"] == 1.0
    assert hs.mcnemar(0, 0)["p_exact_two_sided"] == 1.0
    # b=1, c=9: 2 * (C(10,0)+C(10,1)) / 2**10
    assert hs.mcnemar(1, 9)["p_exact_two_sided"] == pytest.approx(2 * 11 / 1024)
    assert "chi2_cc" not in hs.mcnemar(1, 9)


def test_mcnemar_chi2_reference_for_large_counts():
    m = hs.mcnemar(10, 30)
    assert m["chi2_cc"] == pytest.approx((20 - 1) ** 2 / 40)
    # chi-square(1) survival at 3.841 is 0.05
    assert math.erfc(math.sqrt(3.841 / 2)) == pytest.approx(0.05, abs=1e-3)


def test_field_mcnemar_counts_only_fields_scored_in_both():
    a = [{"merchant": True, "total": False}, {"date": True}]
    b = [{"merchant": False, "total": True}, {"date": True}]
    m = hs.field_mcnemar(a, b)
    assert (m["b_A_right_B_wrong"], m["c_A_wrong_B_right"]) == (1, 1)


def test_percentile_interpolates():
    assert hs.percentile([0.0, 1.0], 0.5) == 0.5
    assert hs.percentile([1.0, 2.0, 3.0], 0.0) == 1.0
    assert hs.percentile([1.0, 2.0, 3.0], 1.0) == 3.0


def test_bootstrap_is_deterministic_and_brackets_point():
    oc = [{"total": True}] * 8 + [{"total": False}] * 2
    ci1, _ = hs.bootstrap({"r": oc}, 2000, 7)
    ci2, _ = hs.bootstrap({"r": oc}, 2000, 7)
    assert ci1 == ci2
    lo, hi = ci1["r"]["overall"]
    assert lo <= 0.8 <= hi and 0.0 <= lo < hi <= 1.0


def test_bootstrap_degenerate_all_right():
    oc = [{"total": True}] * 5
    ci, pair = hs.bootstrap({"a": oc, "b": oc}, 500, 1, [("a", "b")])
    assert ci["a"]["overall"] == [1.0, 1.0]
    assert pair["a->b"] == [0.0, 0.0]
    assert ci["a"]["merchant"] is None      # never scored


def test_analyse_matches_evaluator_on_fixture(tmp_path):
    # fixture: 10 scored fields; run A = fixture results, run B = perfect
    gold = json.loads((FIX / "eval_gold_fake.json").read_text())
    perfect = tmp_path / "perfect.json"
    perfect.write_text(json.dumps({"run_id": "perfect", "receipts": [
        {"id": g["id"], "merchant": g["merchant"], "date": g["date"], "total": g["total"]}
        for g in gold]}))
    rep = hs.analyse(FIX / "eval_gold_fake.json", "all",
                     {"A": FIX / "eval_results_fake.json", "B": perfect}, [("A", "B")],
                     n_resamples=300, seed=3)
    a = rep["runs"]["A"]["metrics"]["overall"]
    b = rep["runs"]["B"]["metrics"]["overall"]
    assert (b["correct"], b["scored"]) == (10, 10)
    p = rep["pairs"]["A->B"]
    assert p["overall_correct_delta"] == 10 - a["correct"]
    mc = p["mcnemar_all_fields"]
    assert mc["b_A_right_B_wrong"] == 0 and mc["c_A_wrong_B_right"] == 10 - a["correct"]


def test_cli_refuses_test_without_final(tmp_path, capsys):
    labels = tmp_path / "l.json"
    labels.write_text(json.dumps([{"id": "t", "split": "test", "merchant": None,
                                   "date": None, "total": "1.00"}]))
    res = tmp_path / "r.json"
    res.write_text(json.dumps({"receipts": [{"id": "t", "total": "1.00"}]}))
    args = ["--labels", str(labels), "--split", "test", "--run", f"x={res}", "--resamples", "50"]
    assert hs.main(args) == 2
    assert hs.main(args + ["--final"]) == 0
