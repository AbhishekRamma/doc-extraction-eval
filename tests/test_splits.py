"""The frozen 'test' split can only be scored with --final; everything else is unchanged."""
import json

import pytest

from src import evaluate, pipeline
from src.splits import TestSetLocked, check_test_access, select_rows

ROWS = [
    {"id": "a", "split": "quick"},
    {"id": "b", "split": "holdout"},
    {"id": "c", "split": "test"},
]


def test_select_rows():
    assert [r["id"] for r in select_rows(ROWS, "quick")] == ["a"]
    assert [r["id"] for r in select_rows(ROWS, "holdout")] == ["b"]
    assert [r["id"] for r in select_rows(ROWS, "test")] == ["c"]
    assert len(select_rows(ROWS, "full")) == len(select_rows(ROWS, "all")) == 3
    assert len(select_rows(ROWS, None)) == 3
    with pytest.raises(ValueError):
        select_rows(ROWS, "nope")


def test_check_test_access():
    check_test_access(ROWS[:2])                 # no test rows: fine
    with pytest.raises(TestSetLocked):
        check_test_access(ROWS)
    check_test_access(ROWS, final=True)


def _fake_test_set(tmp_path):
    labels = tmp_path / "labels_test.json"
    labels.write_text(json.dumps([
        {"id": "t1", "image": "none.jpg", "split": "test",
         "merchant": "Shop", "date": "2020-01-02", "total": "5.00"}]))
    results = tmp_path / "res.json"
    results.write_text(json.dumps({"run_id": "r", "receipts": [
        {"id": "t1", "merchant": "SHOP", "date": "2020-01-02", "total": "5.00", "valid": True}]}))
    return labels, results


def test_load_gold_refuses_test_without_final(tmp_path):
    labels, _ = _fake_test_set(tmp_path)
    with pytest.raises(TestSetLocked):
        evaluate.load_gold(labels, "test")
    # 'full'/'all' cannot be used to sneak test rows past the guard
    with pytest.raises(TestSetLocked):
        evaluate.load_gold(labels, "full")
    assert len(evaluate.load_gold(labels, "test", final=True)) == 1


def test_evaluate_cli_guard(tmp_path, capsys):
    labels, results = _fake_test_set(tmp_path)
    out = tmp_path / "runs"
    base = [str(results), "--labels", str(labels), "--split", "test", "--out-dir", str(out)]
    assert evaluate.main(base) == 2
    err = capsys.readouterr().err
    assert "--final" in err
    assert not out.exists()                     # nothing written when refused
    assert evaluate.main(base + ["--final"]) == 0
    saved = json.loads(next(out.glob("*.json")).read_text())
    assert saved["final"] is True


def test_quick_and_holdout_need_no_final(tmp_path):
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([
        {"id": "q", "split": "quick", "merchant": None, "date": None, "total": "1.00"},
        {"id": "h", "split": "holdout", "merchant": None, "date": None, "total": "2.00"}]))
    assert len(evaluate.load_gold(labels, "holdout")) == 1
    assert len(evaluate.load_gold(labels, "full")) == 2


def test_pipeline_may_predict_on_test(tmp_path):
    """Producing predictions is allowed; only scoring is guarded."""
    labels, _ = _fake_test_set(tmp_path)
    rows = pipeline.load_labels(labels, "test")
    assert [r["id"] for r in rows] == ["t1"]
