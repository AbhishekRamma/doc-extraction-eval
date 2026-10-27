import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import paired_eval  # noqa: E402

GOLD = [{"id": f"r{i}", "merchant": None, "date": None, "total": f"{i + 1}.00"} for i in range(10)]


def res(correct_ids):
    return {"receipts": [{"id": g["id"], "total": g["total"] if g["id"] in correct_ids else "0.01"}
                         for g in GOLD]}


def test_counts_and_ci():
    a = paired_eval.correctness(GOLD, res({"r0", "r1", "r2", "r3"}), ["total"])
    b = paired_eval.correctness(GOLD, res({"r0", "r1", "r2", "r4", "r5", "r6"}), ["total"])
    rep = paired_eval.paired_stats(a, b, n_boot=500, seed=1)
    t = rep["total"]
    assert (t["a"]["correct"], t["b"]["correct"], t["scored"]) == (4, 6, 10)
    assert (t["fixed"], t["broken"]) == (3, 1)
    assert t["a"]["ci95"][0] <= 0.4 <= t["a"]["ci95"][1]
    assert t["diff_b_minus_a"]["accuracy"] == 0.2
    # same seed -> same CI
    assert paired_eval.paired_stats(a, b, n_boot=500, seed=1)["total"] == t


def test_mcnemar_exact():
    assert paired_eval.mcnemar_exact_p(0, 0) == 1.0
    assert abs(paired_eval.mcnemar_exact_p(5, 0) - 0.0625) < 1e-12
    assert paired_eval.mcnemar_exact_p(3, 3) == 1.0


def test_null_gold_not_scored_and_missing_prediction_wrong():
    gold = GOLD[:2] + [{"id": "rx", "merchant": None, "date": None, "total": None}]
    c = paired_eval.correctness(gold, {"receipts": [{"id": "r0", "total": "1.00"}]}, ["total"])
    assert c == {"total": {"r0": True, "r1": False}}


def test_cli(tmp_path, capsys):
    (tmp_path / "g.json").write_text(json.dumps(GOLD))
    (tmp_path / "a.json").write_text(json.dumps(res({"r0"})))
    (tmp_path / "b.json").write_text(json.dumps(res({"r0", "r1"})))
    out = tmp_path / "rep.json"
    assert paired_eval.main([str(tmp_path / "a.json"), str(tmp_path / "b.json"), "--labels",
                             str(tmp_path / "g.json"), "--fields", "total", "--n-boot", "200",
                             "--json", str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["total"]["fixed"] == 1 and rep["total"]["broken"] == 0
