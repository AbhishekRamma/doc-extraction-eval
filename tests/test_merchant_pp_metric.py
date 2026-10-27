"""pp-merchant-v1 is optional: a separate 'merchant_pp' metric (like merchant_fuzzy) that
never changes the raw merchant field, per-field/overall/receipt-exact scores or the CI gate.
All names are synthetic."""
import copy
import json
from pathlib import Path

import src.evaluate as ev
from src.evaluate import DEFAULT_LABELS, load_gold, load_results, score

ROOT = Path(__file__).resolve().parent.parent
GOLD = [
    {"id": "a", "merchant": "ACME WIDGETS SDN BHD", "date": "2020-01-02", "total": "10.00"},
    {"id": "b", "merchant": "ZETA MART", "date": None, "total": "5.00"},
    {"id": "c", "merchant": "OMEGA TRADING", "date": None, "total": None},
]
RESULTS = {"receipts": [
    {"id": "a", "merchant": "ACME WIDGETS SON BHD", "date": "2020-01-02", "total": "10.00"},
    {"id": "b", "merchant": "ZETA MART", "total": "5.00"},
    # c missing from results -> wrong everywhere, including merchant_pp
]}
STRICT_KEYS = ["per_field", "overall", "receipt_exact", "failures", "merchant_fuzzy",
               "valid_rate", "needs_review_rate", "n_receipts", "n_missing_from_results"]


def _strict(m):
    return {k: m[k] for k in STRICT_KEYS}


def test_merchant_pp_reported_separately():
    m = score(GOLD, RESULTS)
    assert m["per_field"]["merchant"]["correct"] == 1          # raw: SON BHD is wrong
    assert m["merchant_pp"] == {"correct": 2, "scored": 3, "postprocess": "pp-merchant-v1",
                                "accuracy": 2 / 3}
    assert m["overall"] == {"correct": 4, "scored": 6, "accuracy": 4 / 6}   # strict, raw only
    # the failure list reports the RAW prediction
    assert {"id": "a", "field": "merchant", "gold": "ACME WIDGETS SDN BHD",
            "predicted": "ACME WIDGETS SON BHD"} in m["failures"]


def test_raw_results_not_mutated():
    res = copy.deepcopy(RESULTS)
    score(GOLD, res)
    assert res == RESULTS


def test_strict_metrics_independent_of_postprocessing(monkeypatch):
    real = _strict(score(GOLD, RESULTS))
    # whatever the post-processor returns, nothing but merchant_pp may move
    monkeypatch.setattr(ev, "postprocess_merchant", lambda s: ("ACME WIDGETS SDN BHD", ["x"]))
    wild = score(GOLD, RESULTS)
    assert _strict(wild) == real
    assert wild["merchant_pp"]["correct"] == 1


def test_ci_gate_unaffected(monkeypatch):
    """The quick-split regression gate reads metrics['overall'] only; it is identical
    with post-processing replaced by anything, and the committed fixture is raw."""
    fixture = ROOT / "tests" / "fixtures" / "ci_results_quick.json"
    gold, res = load_gold(DEFAULT_LABELS, "quick"), load_results(fixture)
    base = score(gold, res)
    assert (base["overall"]["correct"], base["overall"]["scored"]) == (19, 19)  # rules-v2 fixture
    threshold = json.loads((ROOT / "eval_config.json").read_text())["min_overall_accuracy"]
    assert threshold == 0.94
    monkeypatch.setattr(ev, "postprocess_merchant", lambda s: ("garbage", []))
    assert _strict(score(gold, res)) == _strict(base)
    assert "merchant_pp" not in json.dumps(res)            # fixture carries no pp output
    src = (ROOT / "tests" / "test_regression.py").read_text()
    assert 'metrics["overall"]["accuracy"]' in src and "merchant_pp" not in src


def test_pipeline_never_applies_postprocessing():
    for f in ["src/pipeline.py", "src/extract.py", "src/schema.py"]:
        p = ROOT / f
        if p.exists():
            assert "postprocess" not in p.read_text(), f"{f} must not apply pp-merchant-v1"


def test_report_line_and_compare(tmp_path, capsys):
    m = score(GOLD, RESULTS)
    ev.print_report({k: "x" for k in ["run_id", "model", "prompt_version", "ocr_engine",
                                     "extractor_version"]}, "all", m)
    out = capsys.readouterr().out
    assert "merchant (post-proc)" in out and "optional pp-merchant-v1, not in overall" in out
    old = {"run": {"run_id": "old"}, "split": "all", "metrics": {k: v for k, v in m.items()
                                                                 if k != "merchant_pp"}}
    new = {"run": {"run_id": "new"}, "split": "all", "metrics": m}
    (tmp_path / "a.json").write_text(json.dumps(old)); (tmp_path / "b.json").write_text(json.dumps(new))
    ev.compare(tmp_path / "a.json", tmp_path / "b.json")
    out = capsys.readouterr().out
    assert "merchant_pp" in out and "n/a" in out                # older runs show n/a
