"""Paired before/after comparison of two results files on the same golden set.

For each scored field (and the micro 'overall'), reports:
- accuracy of A and of B with a receipt-level bootstrap 95% CI (percentile, fixed seed)
- a bootstrap 95% CI of the difference B - A (same resamples, so it is paired)
- McNemar-style paired counts: fixed (A wrong -> B right), broken (A right -> B wrong),
  and the exact two-sided McNemar p-value (binomial on fixed vs broken)

Scoring is src/evaluate.py's exact match after normalisation, so the numbers match
`python -m src.evaluate`. Only aggregate numbers are printed (no per-receipt values),
unless --list-ids is given, which prints receipt ids (never gold or predicted values).

  python scripts/paired_eval.py --labels data/local/sroie/labels.json --split full \
      results/A.json results/B.json --fields total
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.evaluate import FIELDS, load_gold, normalise  # noqa: E402


def correctness(gold_rows: list[dict], results: dict, fields: list[str]) -> dict[str, dict[str, bool]]:
    """{field: {receipt_id: correct}} for every scored (non-null, normalisable) gold field."""
    preds: dict[str, dict] = {}
    for rec in results.get("receipts", []):
        preds.setdefault(str(rec.get("id")), rec)  # first one wins, like evaluate.score
    out: dict[str, dict[str, bool]] = {f: {} for f in fields}
    for g in gold_rows:
        rid = str(g["id"])
        for f in fields:
            if g.get(f) is None:
                continue
            gn = normalise(f, g[f])
            if gn is None:
                continue
            p = preds.get(rid)
            pn = None if p is None else normalise(f, p.get(f))
            out[f][rid] = pn is not None and pn == gn
    return out


def mcnemar_exact_p(fixed: int, broken: int) -> float:
    n = fixed + broken
    if n == 0:
        return 1.0
    k = min(fixed, broken)
    p = 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, p)


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    i = q * (len(xs) - 1)
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def paired_stats(a: dict[str, dict[str, bool]], b: dict[str, dict[str, bool]],
                 n_boot: int = 10000, seed: int = 0) -> dict:
    fields = list(a)
    ids = sorted({rid for f in fields for rid in a[f]})
    rng = random.Random(seed)
    # per receipt: (correct_a, correct_b, scored) for each field
    def per_receipt(field_list):
        rows = []
        for rid in ids:
            ca = sum(a[f][rid] for f in field_list if rid in a[f])
            cb = sum(b[f].get(rid, False) for f in field_list if rid in a[f])
            s = sum(rid in a[f] for f in field_list)
            rows.append((ca, cb, s))
        return rows

    groups = {f: [f] for f in fields}
    if len(fields) > 1:
        groups["overall"] = fields
    samples = [[rng.randrange(len(ids)) for _ in ids] for _ in range(n_boot)]
    report = {"n_receipts": len(ids), "bootstrap": {"n": n_boot, "seed": seed,
                                                   "unit": "receipt", "ci": "percentile 95%"}}
    for name, fl in groups.items():
        rows = per_receipt(fl)
        ca, cb, s = (sum(r[k] for r in rows) for k in range(3))
        accs_a, accs_b, diffs = [], [], []
        for idx in samples:
            sa = sum(rows[i][0] for i in idx); sb = sum(rows[i][1] for i in idx)
            ss = sum(rows[i][2] for i in idx) or 1
            accs_a.append(sa / ss); accs_b.append(sb / ss); diffs.append((sb - sa) / ss)
        fixed = sum(1 for f in fl for rid in a[f] if not a[f][rid] and b[f].get(rid, False))
        broken = sum(1 for f in fl for rid in a[f] if a[f][rid] and not b[f].get(rid, False))
        report[name] = {
            "scored": s,
            "a": {"correct": ca, "accuracy": ca / s if s else None,
                  "ci95": [_pct(accs_a, .025), _pct(accs_a, .975)]},
            "b": {"correct": cb, "accuracy": cb / s if s else None,
                  "ci95": [_pct(accs_b, .025), _pct(accs_b, .975)]},
            "diff_b_minus_a": {"accuracy": (cb - ca) / s if s else None,
                               "ci95": [_pct(diffs, .025), _pct(diffs, .975)]},
            "fixed": fixed, "broken": broken,
            "mcnemar_exact_p": mcnemar_exact_p(fixed, broken),
        }
    return report


def ids_changed(a, b) -> dict[str, dict[str, list[str]]]:
    return {f: {"fixed": sorted(r for r in a[f] if not a[f][r] and b[f].get(r)),
                "broken": sorted(r for r in a[f] if a[f][r] and not b[f].get(r))} for f in a}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("a", help="results file A (before)")
    p.add_argument("b", help="results file B (after)")
    p.add_argument("--labels", default=str(ROOT / "data" / "golden" / "labels.json"))
    p.add_argument("--split", default="all", help="quick | full | all | any split tag in labels.json")
    p.add_argument("--fields", nargs="+", default=FIELDS, choices=FIELDS)
    p.add_argument("--ids", nargs="*", default=None, help="restrict to these receipt ids")
    p.add_argument("--n-boot", type=int, default=10000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--list-ids", action="store_true", help="also print fixed/broken receipt ids")
    p.add_argument("--json", default=None, help="write the report here (keep SROIE reports local)")
    a = p.parse_args(argv)

    gold = load_gold(Path(a.labels), a.split)
    if a.ids is not None:
        keep = set(a.ids)
        gold = [g for g in gold if str(g["id"]) in keep]
    ra, rb = (json.loads(Path(x).read_text(encoding="utf-8")) for x in (a.a, a.b))
    ca, cb = correctness(gold, ra, a.fields), correctness(gold, rb, a.fields)
    rep = paired_stats(ca, cb, a.n_boot, a.seed)
    rep.update(a_file=a.a, b_file=a.b, labels=a.labels, split=a.split,
               a_extractor=ra.get("extractor_version"), b_extractor=rb.get("extractor_version"))
    print(f"A={rep['a_extractor']}  B={rep['b_extractor']}  receipts={rep['n_receipts']}  "
          f"bootstrap n={a.n_boot} seed={a.seed}")
    for name in [*a.fields, "overall"]:
        r = rep.get(name)
        if not r or not r["scored"]:
            continue
        fa, fb, d = r["a"], r["b"], r["diff_b_minus_a"]
        print(f"{name:<9} A {fa['correct']}/{r['scored']} {fa['accuracy']:.1%} "
              f"[{fa['ci95'][0]:.1%}, {fa['ci95'][1]:.1%}]   "
              f"B {fb['correct']}/{r['scored']} {fb['accuracy']:.1%} "
              f"[{fb['ci95'][0]:.1%}, {fb['ci95'][1]:.1%}]   "
              f"diff {d['accuracy']:+.1%} [{d['ci95'][0]:+.1%}, {d['ci95'][1]:+.1%}]   "
              f"fixed {r['fixed']} broken {r['broken']} McNemar p={r['mcnemar_exact_p']:.2g}")
    if a.list_ids:
        rep["changed_ids"] = ids_changed(ca, cb)
        print(json.dumps(rep["changed_ids"], indent=1))
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
