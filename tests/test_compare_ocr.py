import importlib.util

import pytest

from src import ocr
from tests.conftest import FIXTURES, ROOT

spec = importlib.util.spec_from_file_location("compare_ocr", ROOT / "scripts" / "compare_ocr.py")
co = importlib.util.module_from_spec(spec)
spec.loader.exec_module(co)


def test_scoring_proxy():
    rows = [{"id": "a", "total": "60000.00", "date": None},
            {"id": "b", "total": "9.45", "date": "2018-12-25"}]
    texts = {"a": "TOTAL 60.000\nCASH 100.000", "b": "Date 25/12/2018\nTOTAL 9.46"}
    s = co.score(rows, texts, {"a": {"total": {"total_price": "60.000", "cashprice": "100.000"},
                                    "menu": {"cnt": "2", "price": "30.000"}}})
    assert s["total_found"] == 0.5 and s["date_found"] == 1.0
    assert s["amount_recall"] == round(2 / 3, 3)     # 60000, 100000 found; 30000 not


@pytest.mark.skipif(not ocr.tesseract_available(), reason="tesseract not installed")
def test_compare_runs_on_fixtures(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ocr, "CACHE_DIR", tmp_path / "cache")
    out = tmp_path / "s.json"
    rc = co.main(["--labels", str(FIXTURES / "labels.json"), "--engines", "tesseract",
                  "--no-cache", "--gold-raw", str(tmp_path / "none.json"), "--out", str(out),
                  "--summary-json", str(tmp_path / "ocr_comparison.json")])
    assert rc == 0
    import json
    s = json.loads(out.read_text())[0]
    assert s["engine"] == "tesseract" and s["images_timed"] == 3 and s["total_found"] == 1.0
    summ = json.loads((tmp_path / "ocr_comparison.json").read_text())
    e = summ["engines"][0]
    assert summ["dataset"] == "CORD-v2 quick split (3)"
    assert e["n_images"] == 3 and e["mean_sec_per_image"] == s["sec_per_image"]
    assert e["rules_total_n"] == s["total_n"] and 0 <= e["rules_total_correct"] <= e["rules_total_n"]


def test_rules_total_in_score():
    rows = [{"id": "a", "total": "9.45"}, {"id": "b", "total": "5.00"}]
    texts = {"a": "SHOP\nSub Total 9.43\nTOTAL 9.45\nCash 10.00", "b": "nothing here"}
    s = co.score(rows, texts, None)
    assert s["rules_total_correct"] == 1 and s["rules_total_n"] == 2


def test_summary_keeps_failed_engine_with_nulls():
    ok = {"engine": "tesseract", "version": "tesseract-5", "n_receipts": 2, "images_timed": 2,
          "sec_per_image": 0.3, "model_load_sec": None, "total_found": 0.5, "amount_recall": None,
          "rules_total_correct": 1, "rules_total_n": 2, "n_errors": 0}
    bad = {"engine": "paddleocr", "error": "ModuleNotFoundError: No module named 'paddleocr'"}
    summ = co.build_summary([ok, bad], "CORD-v2 quick split (2)", run_date="2026-01-01")
    assert summ["run_date"] == "2026-01-01" and summ["hardware"]["cpu_count"] >= 1
    good, failed = summ["engines"]
    assert good["mean_sec_per_image"] == 0.3 and good["note"] is None
    assert all(failed[k] is None for k in ("n_images", "mean_sec_per_image",
                                           "gold_total_found_rate", "rules_total_correct"))
    assert "not runnable" in failed["note"]


def test_summary_file_is_not_gitignored():
    import subprocess
    r = subprocess.run(["git", "check-ignore", "-q", "results/ocr_comparison.json"], cwd=ROOT)
    if r.returncode == 128:
        pytest.skip("not a git checkout")
    assert r.returncode == 1  # 1 = not ignored
