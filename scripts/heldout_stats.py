"""Uncertainty for extraction accuracy: bootstrap CIs and a paired McNemar test.

Given one labels file and one or more pipeline results files scored on the SAME
receipts, this prints and saves:
  - per-field and overall (micro) accuracy for each run, with a 95% percentile
    bootstrap CI from resampling RECEIPTS with replacement (fixed seed). Overall is
    re-computed as correct/scored inside each resample, so receipts with a null gold
    field are handled exactly like src/evaluate.py does.
  - for each requested pair A,B: the paired difference B-A in overall accuracy with a
    bootstrap CI (same resampled receipts for both runs), and McNemar's test on the
    field-level discordant pairs (b = A right & B wrong, c = A wrong & B right).
    The exact two-sided binomial p-value is always reported; the continuity-corrected
    chi-square p-value is added for reference when b + c >= 25.

Scoring uses src.evaluate.score per receipt, so the definitions (normalisation, null
gold not scored, missing prediction = wrong) cannot drift from the evaluator; the
script also checks its totals against a whole-file score.

Scoring the frozen 'test' split needs --final (same guard as src/evaluate.py).
Output is aggregate numbers only (no per-receipt values), so a SROIE summary can be
saved under data/local/ and quoted in docs.

Example:
  python scripts/heldout_stats.py --labels data/local/sroie/labels_test.json --split test --final \
      --run rules=data/local/sroie/results/heldout/sroie_test_rules_doctr.json \
      --run v1=... --run v2=... --pair v1 v2 --out data/local/sroie/eval_runs/heldout/stats.json
Only the standard library is used (random, math), so it runs anywhere pytest does.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.evaluate import FIELDS, load_gold, load_results, run_metadata, score  # noqa: E402
from src.splits import SPLIT_CHOICES, TestSetLocked  # noqa: E402

DEFAULT_SEED = 20261024
DEFAULT_RESAMPLES = 10_000


# ------------------------------------------------------------------ per receipt
def receipt_outcomes(gold_rows: list[dict], results: dict) -> list[dict]:
    """For each gold receipt: {field: True/False} for every SCORED field (others absent)."""
    by_id = {}
    for rec in results.get("receipts", []):
        by_id.setdefault(str(rec.get("id")), rec)   # first wins, like evaluate.score
    out = []
    for g in gold_rows:
        rid = str(g["id"])
        one = {"receipts": [by_id[rid]] if rid in by_id else []}
        m = score([g], one)
        wrong = {f["field"] for f in m["failures"]}
        out.append({f: f not in wrong for f in FIELDS if m["per_field"][f]["scored"]})
    return out


def counts(outcomes: list[dict], idx=None) -> dict:
    """{field: [correct, scored]} plus 'overall', over receipts idx (default all)."""
    c = {f: [0, 0] for f in [*FIELDS, "overall"]}
    for i in (range(len(outcomes)) if idx is None else idx):
        for f, ok in outcomes[i].items():
            c[f][0] += ok
            c[f][1] += 1
            c["overall"][0] += ok
            c["overall"][1] += 1
    return c


def _acc(cs):
    return cs[0] / cs[1] if cs[1] else None


def percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0, 1]) of an already sorted list."""
    if not sorted_vals:
        return float("nan")
    pos = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def bootstrap(runs: dict[str, list[dict]], n_resamples: int, seed: int,
              pairs: list[tuple[str, str]] = ()) -> tuple[dict, dict]:
    """Percentile CIs per run/metric and per pair (B - A overall), paired resamples."""
    n = len(next(iter(runs.values())))
    rng = random.Random(seed)
    samples = {name: {m: [] for m in [*FIELDS, "overall"]} for name in runs}
    diffs = {f"{a}->{b}": [] for a, b in pairs}
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        accs = {}
        for name, oc in runs.items():
            cs = counts(oc, idx)
            accs[name] = {m: _acc(v) for m, v in cs.items()}
            for m, v in accs[name].items():
                if v is not None:
                    samples[name][m].append(v)
        for a, b in pairs:
            if accs[a]["overall"] is not None and accs[b]["overall"] is not None:
                diffs[f"{a}->{b}"].append(accs[b]["overall"] - accs[a]["overall"])

    def ci(vals):
        vals = sorted(vals)
        return [percentile(vals, 0.025), percentile(vals, 0.975)] if vals else None

    run_ci = {name: {m: ci(v) for m, v in per.items()} for name, per in samples.items()}
    pair_ci = {k: ci(v) for k, v in diffs.items()}
    return run_ci, pair_ci


# ------------------------------------------------------------------ McNemar
def mcnemar(b: int, c: int) -> dict:
    """Paired test on discordant counts. b = A right/B wrong, c = A wrong/B right."""
    n = b + c
    if n == 0:
        p_exact = 1.0
    else:
        k = min(b, c)
        tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
        p_exact = min(1.0, 2 * tail)
    out = {"b_A_right_B_wrong": b, "c_A_wrong_B_right": c, "discordant": n,
           "p_exact_two_sided": p_exact, "method": "exact binomial (two-sided)"}
    if n >= 25:
        chi2 = (abs(b - c) - 1) ** 2 / n
        # chi-square with 1 df: P(X > chi2) = erfc(sqrt(chi2 / 2))
        out["chi2_cc"] = chi2
        out["p_chi2_cc"] = math.erfc(math.sqrt(chi2 / 2))
    return out


def field_mcnemar(a: list[dict], b: list[dict], fields=FIELDS) -> dict:
    """McNemar over all (receipt, field) checks scored in both runs."""
    bb = cc = 0
    for ra, rb in zip(a, b):
        for f in fields:
            if f in ra and f in rb:
                if ra[f] and not rb[f]:
                    bb += 1
                elif rb[f] and not ra[f]:
                    cc += 1
    return mcnemar(bb, cc)


# ------------------------------------------------------------------ CLI
def analyse(labels: Path, split: str, runs: dict[str, Path], pairs: list[tuple[str, str]],
            n_resamples: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED,
            final: bool = False) -> dict:
    gold = load_gold(labels, split, final=final)
    outcomes, report_runs = {}, {}
    for name, path in runs.items():
        results = load_results(path)
        oc = receipt_outcomes(gold, results)
        whole = score(gold, results)
        cs = counts(oc)
        # consistency check against the evaluator on the whole file
        assert cs["overall"] == [whole["overall"]["correct"], whole["overall"]["scored"]], name
        outcomes[name] = oc
        report_runs[name] = {
            "results_file": str(path), **run_metadata(results),
            "metrics": {m: {"correct": v[0], "scored": v[1], "accuracy": _acc(v)}
                        for m, v in cs.items()},
            "receipt_exact": whole["receipt_exact"], "valid_rate": whole["valid_rate"],
            "merchant_fuzzy": whole["merchant_fuzzy"],
        }
    run_ci, pair_ci = bootstrap(outcomes, n_resamples, seed, pairs)
    for name in report_runs:
        for m, v in report_runs[name]["metrics"].items():
            v["ci95"] = run_ci[name][m]
    pair_out = {}
    for a, b in pairs:
        ma, mb = report_runs[a]["metrics"]["overall"], report_runs[b]["metrics"]["overall"]
        pair_out[f"{a}->{b}"] = {
            "overall_correct_delta": mb["correct"] - ma["correct"],
            "overall_accuracy_delta": mb["accuracy"] - ma["accuracy"],
            "overall_delta_ci95": pair_ci[f"{a}->{b}"],
            "mcnemar_all_fields": field_mcnemar(outcomes[a], outcomes[b]),
            "mcnemar_per_field": {f: field_mcnemar(outcomes[a], outcomes[b], [f]) for f in FIELDS},
        }
    return {"labels_file": str(labels), "split": split, "final": final,
            "n_receipts": len(gold), "bootstrap": {"resamples": n_resamples, "seed": seed,
                                                   "unit": "receipt", "interval": "percentile 95%"},
            "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "runs": report_runs, "pairs": pair_out}


def print_report(rep: dict) -> None:
    print(f"{rep['labels_file']} split={rep['split']} n={rep['n_receipts']} "
          f"bootstrap={rep['bootstrap']['resamples']} seed={rep['bootstrap']['seed']}")
    for name, r in rep["runs"].items():
        print(f"\n{name}  ({r['extractor_version']}, {r['ocr_engine']})")
        for m, v in r["metrics"].items():
            if not v["scored"]:
                continue
            lo, hi = v["ci95"]
            print(f"  {m:<9} {v['correct']:>4}/{v['scored']:<4} {v['accuracy'] * 100:6.1f}%  "
                  f"95% CI [{lo * 100:.1f}, {hi * 100:.1f}]")
    for k, p in rep["pairs"].items():
        lo, hi = p["overall_delta_ci95"]
        mc = p["mcnemar_all_fields"]
        print(f"\n{k}: overall {p['overall_correct_delta']:+d} fields "
              f"({p['overall_accuracy_delta'] * 100:+.1f} pts, 95% CI [{lo * 100:+.1f}, {hi * 100:+.1f}])")
        print(f"  McNemar all fields: b={mc['b_A_right_B_wrong']} c={mc['c_A_wrong_B_right']} "
              f"exact p={mc['p_exact_two_sided']:.4g}")
        for f, m in p["mcnemar_per_field"].items():
            print(f"    {f:<9} b={m['b_A_right_B_wrong']} c={m['c_A_wrong_B_right']} "
                  f"exact p={m['p_exact_two_sided']:.4g}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--split", default="all", choices=SPLIT_CHOICES)
    p.add_argument("--run", action="append", required=True, metavar="NAME=results.json")
    p.add_argument("--pair", nargs=2, action="append", default=[], metavar=("A", "B"))
    p.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--final", action="store_true", help="allow scoring the frozen 'test' split")
    p.add_argument("--out", type=Path, default=None, help="save the summary JSON here")
    a = p.parse_args(argv)
    runs = dict(x.split("=", 1) for x in a.run)
    runs = {k: Path(v) for k, v in runs.items()}
    for x, y in a.pair:
        if x not in runs or y not in runs:
            p.error(f"--pair {x} {y}: both must be --run names")
    try:
        rep = analyse(a.labels, a.split, runs, [tuple(x) for x in a.pair],
                      a.resamples, a.seed, a.final)
    except TestSetLocked as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print_report(rep)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
        print(f"\nSaved {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
