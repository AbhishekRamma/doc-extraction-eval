"""Compare a pipeline results file with the golden labels, field by field.

Usage (from the project root):
    python -m src.evaluate results/<run_id>.json [--split quick|holdout|test|full|all] [--final]
    python -m src.evaluate --compare eval_runs/A.json eval_runs/B.json
    # local-only SROIE set (never committed):
    python -m src.evaluate results/<run>.json --labels data/local/sroie/labels.json \
        --out-dir data/local/sroie/eval_runs

Splits (see src/splits.py):
         quick   = the 20 receipts labelled "quick"
         holdout = CORD's 80 non-quick receipts (the CORD held-out set)
         test    = a frozen held-out test set (SROIE sroie_100..299); refused without --final
         full    = every receipt in labels.json (same meaning as in src/pipeline.py)
         all     = alias for full
         Any selection that contains "test" rows (including full/all) needs --final.

Scoring rules (agreed with Software DEV):
- A field is only *scored* for receipts whose gold value is not null. CORD has
  no merchant/date labels, so on CORD those fields show "scored 0".
- accuracy(field) = correct / scored
- A gold id that is missing from the results file is wrong on every scored field.
- Both gold and prediction go through the same normalisers from src/normalize.py:
    merchant -> exact match after normalize_merchant (case/punctuation/spaces)
                (plus a secondary 'merchant_fuzzy' metric: similarity >= 0.90,
                 reported separately; it never changes overall/receipt exact)
                (plus an optional 'merchant_pp' metric: exact match after the
                 pp-merchant-v1 post-processing, also never in overall/receipt exact)
    date     -> ISO 'YYYY-MM-DD' equality after normalize_date
    total    -> Decimal equality after normalize_total ('60.000' == '60000')
- overall (micro) accuracy = all correct field checks / all scored field checks
- receipt exact = receipts where every scored field is right
  (only receipts with at least one scored field count)
- valid / needs_review rates are over the gold receipts in the split; a missing
  receipt counts as not valid and as needing review.
"""
from __future__ import annotations

import argparse
import json
from difflib import SequenceMatcher
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.normalize import normalize_date, normalize_merchant, normalize_total
from src.postprocess import VERSION as PP_VERSION, postprocess_merchant
from src.splits import SPLIT_CHOICES, TestSetLocked, check_test_access, select_rows

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = ROOT / "data" / "golden" / "labels.json"
EVAL_DIR = ROOT / "eval_runs"
FIELDS = ["merchant", "date", "total"]
MISSING = "<missing from results>"

# Secondary, lenient merchant metric ("merchant_fuzzy"). It is reported NEXT TO
# the strict exact match and never replaces it: overall (micro) and receipt
# exact stay strict. A prediction is fuzzy-correct when the two normalised
# strings are at least this similar (difflib ratio, 0.0 - 1.0).
MERCHANT_FUZZY_THRESHOLD = 0.90

# Optional, secondary merchant metric ("merchant_pp"), same pattern as merchant_fuzzy:
# the prediction is passed through the deterministic post-processing in
# src/postprocess.py (pp-merchant-v1) and then compared exactly. It is computed on a
# copy for this metric only. The raw merchant field, per_field["merchant"], overall,
# receipt exact, failures and the CI gate never see the post-processed value, and the
# pipeline never applies it. Held-out verdict: +0.5 to +1.5 pp, CIs cross 0, so it
# stays optional (docs/experiments/merchant_postprocess.md).


# ---------------------------------------------------------------- comparing
def normalise(field: str, value):
    """Turn a raw value into the form we compare on (or None)."""
    if field == "merchant":
        return normalize_merchant(value)
    if field == "date":
        return normalize_date(value)
    if field == "total":
        text = normalize_total(value)
        return None if text is None else Decimal(text)
    raise ValueError(f"unknown field {field}")


def merchant_similarity(gold_norm: str, pred_norm: str | None) -> float:
    """Character similarity of two normalised merchant names (0.0 - 1.0).

    SequenceMatcher.ratio() = 2 * matching characters / total characters,
    e.g. 'sunrise mini rnarket' vs 'sunrise mini market' = 0.923 (an OCR typo, just passes),
    'sunbean' vs 'northwind traders sdn bhd' = 0.12 (a different name, fails).
    """
    if not pred_norm:
        return 0.0
    return SequenceMatcher(None, gold_norm, pred_norm).ratio()


# ---------------------------------------------------------------- loading
def load_gold(path: Path, split: str = "all", final: bool = False) -> list[dict]:
    """Gold rows for a split. Raises TestSetLocked if they include the frozen
    'test' split and final is False (see src/splits.py)."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    # "full" means the whole golden set, matching src/pipeline.py; "all" is an alias.
    rows = select_rows(rows, split)
    check_test_access(rows, final)
    return rows


def load_results(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- scoring
def score(gold_rows: list[dict], results: dict) -> dict:
    """Score one results dict against gold rows. Returns metrics + failures."""
    # Index predictions by id. If an id appears twice we keep the first one.
    predictions: dict[str, dict] = {}
    duplicates = []
    for rec in results.get("receipts", []):
        rid = str(rec.get("id"))
        if rid in predictions:
            duplicates.append(rid)
        else:
            predictions[rid] = rec

    gold_ids = {str(g["id"]) for g in gold_rows}
    per_field = {f: {"correct": 0, "scored": 0} for f in FIELDS}
    merchant_fuzzy = {"correct": 0, "scored": 0}   # secondary metric, see constant above
    merchant_pp = {"correct": 0, "scored": 0}      # optional secondary metric, never in overall
    failures = []
    warnings = []
    exact_correct = exact_scored = 0
    valid_count = review_count = missing_count = 0

    for gold in gold_rows:
        rid = str(gold["id"])
        pred = predictions.get(rid)
        if pred is None:
            missing_count += 1
            review_count += 1           # a missing receipt needs a human
        else:
            valid_count += bool(pred.get("valid", False))
            review_count += bool(pred.get("needs_review", False))

        receipt_all_right = True
        receipt_has_scored_field = False
        for field in FIELDS:
            gold_raw = gold.get(field)
            if gold_raw is None:
                continue                # null gold = not scored
            gold_norm = normalise(field, gold_raw)
            if gold_norm is None:
                # gold has a value we cannot normalise: skip it but say so
                warnings.append(f"{rid}: gold {field} {gold_raw!r} could not be normalised; not scored")
                continue

            receipt_has_scored_field = True
            per_field[field]["scored"] += 1
            pred_raw = MISSING if pred is None else pred.get(field)
            pred_norm = None if pred is None else normalise(field, pred_raw)

            if field == "merchant":
                # secondary lenient check; it does NOT feed overall/receipt exact
                merchant_fuzzy["scored"] += 1
                if merchant_similarity(gold_norm, pred_norm) >= MERCHANT_FUZZY_THRESHOLD:
                    merchant_fuzzy["correct"] += 1
                # optional post-processed check on a copy; it does NOT feed overall either
                merchant_pp["scored"] += 1
                pp_norm = None if pred is None else normalise(field, postprocess_merchant(pred_raw)[0])
                if pp_norm is not None and pp_norm == gold_norm:
                    merchant_pp["correct"] += 1

            if pred_norm is not None and pred_norm == gold_norm:
                per_field[field]["correct"] += 1
            else:
                receipt_all_right = False
                failures.append({"id": rid, "field": field,
                                 "gold": gold_raw, "predicted": pred_raw})

        if receipt_has_scored_field:
            exact_scored += 1
            exact_correct += receipt_all_right

    n = len(gold_rows)
    total_correct = sum(v["correct"] for v in per_field.values())
    total_scored = sum(v["scored"] for v in per_field.values())

    def ratio(a, b):
        return a / b if b else None     # None = nothing to score (not 0%)

    return {
        "n_receipts": n,
        "n_missing_from_results": missing_count,
        "n_extra_in_results": len(set(predictions) - gold_ids),
        "duplicate_ids_in_results": duplicates,
        "per_field": {f: {**v, "accuracy": ratio(v["correct"], v["scored"])}
                      for f, v in per_field.items()},
        "merchant_fuzzy": {**merchant_fuzzy, "threshold": MERCHANT_FUZZY_THRESHOLD,
                           "accuracy": ratio(merchant_fuzzy["correct"], merchant_fuzzy["scored"])},
        "merchant_pp": {**merchant_pp, "postprocess": PP_VERSION,
                        "accuracy": ratio(merchant_pp["correct"], merchant_pp["scored"])},
        "overall": {"correct": total_correct, "scored": total_scored,
                    "accuracy": ratio(total_correct, total_scored)},
        "receipt_exact": {"correct": exact_correct, "scored": exact_scored,
                          "accuracy": ratio(exact_correct, exact_scored)},
        "valid_rate": {"count": valid_count, "of": n, "rate": ratio(valid_count, n)},
        "needs_review_rate": {"count": review_count, "of": n, "rate": ratio(review_count, n)},
        "failures": failures,
        "warnings": warnings,
    }


def run_metadata(results: dict) -> dict:
    """Pull the run description out of a results file ('unknown' if absent)."""
    return {key: results.get(key) or "unknown"
            for key in ["run_id", "model", "prompt_version", "ocr_engine",
                        "extractor_version", "created_at"]}


# ---------------------------------------------------------------- printing
def fmt(correct, scored, acc) -> str:
    if not scored:
        return f"{correct}/{scored}  (not scored)"
    return f"{correct}/{scored}  ({acc * 100:.1f}%)"


def print_report(meta: dict, split: str, m: dict) -> None:
    print(f"Run {meta['run_id']}  model={meta['model']}  prompt={meta['prompt_version']}  "
          f"ocr={meta['ocr_engine']}  extractor={meta['extractor_version']}")
    print(f"Split: {split}  receipts: {m['n_receipts']}  "
          f"missing from results: {m['n_missing_from_results']}")
    print()
    print(f"{'metric':<24} {'correct/scored (accuracy)'}")
    print("-" * 52)
    for f, v in m["per_field"].items():
        print(f"{f:<24} {fmt(v['correct'], v['scored'], v['accuracy'])}")
        if f == "merchant" and "merchant_fuzzy" in m:
            z = m["merchant_fuzzy"]
            label = f"merchant (fuzzy>={z['threshold']:.2f})"
            print(f"{label:<24} {fmt(z['correct'], z['scored'], z['accuracy'])}  [secondary, not in overall]")
        if f == "merchant" and "merchant_pp" in m:
            z = m["merchant_pp"]
            print(f"{'merchant (post-proc)':<24} {fmt(z['correct'], z['scored'], z['accuracy'])}  "
                  f"[optional {z['postprocess']}, not in overall]")
    o, e = m["overall"], m["receipt_exact"]
    print(f"{'overall (micro)':<24} {fmt(o['correct'], o['scored'], o['accuracy'])}")
    print(f"{'receipt exact':<24} {fmt(e['correct'], e['scored'], e['accuracy'])}")
    for name in ["valid_rate", "needs_review_rate"]:
        r = m[name]
        print(f"{name:<24} {fmt(r['count'], r['of'], r['rate'])}")

    print(f"\nFailures ({len(m['failures'])}):")
    for fl in m["failures"]:
        print(f"  {fl['id']:<16} {fl['field']:<9} gold={fl['gold']!r:<14} predicted={fl['predicted']!r}")
    for w in m["warnings"]:
        print("WARNING:", w)
    if m["duplicate_ids_in_results"]:
        print("WARNING: duplicate ids in results (first kept):", m["duplicate_ids_in_results"])


def compare(path_a: Path, path_b: Path) -> None:
    """Print two saved eval_runs/*.json files side by side."""
    a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (path_a, path_b))
    print(f"A: {a['run']['run_id']} ({a['split']})   B: {b['run']['run_id']} ({b['split']})")

    def show(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    rows = [(f, a["metrics"]["per_field"][f]["accuracy"], b["metrics"]["per_field"][f]["accuracy"])
            for f in FIELDS]
    # older eval_runs files have no merchant_fuzzy block -> shown as n/a
    fuzzy = [m["metrics"].get("merchant_fuzzy", {}).get("accuracy") for m in (a, b)]
    rows.insert(1, ("merchant_fuzzy", *fuzzy))
    pp = [m["metrics"].get("merchant_pp", {}).get("accuracy") for m in (a, b)]
    rows.insert(2, ("merchant_pp", *pp))
    rows.append(("overall", a["metrics"]["overall"]["accuracy"], b["metrics"]["overall"]["accuracy"]))
    rows.append(("receipt exact", a["metrics"]["receipt_exact"]["accuracy"],
                 b["metrics"]["receipt_exact"]["accuracy"]))
    for name, x, y in rows:
        print(f"{name:<14} A={show(x):>7}  B={show(y):>7}")


# ---------------------------------------------------------------- CLI
def evaluate_file(results_path: Path, split: str = "all", labels_path: Path = DEFAULT_LABELS,
                  out_dir: Path | None = EVAL_DIR, final: bool = False) -> dict:
    """Score a results file, print the report and (optionally) save it."""
    gold = load_gold(labels_path, split, final=final)   # guard first: nothing printed
    results = load_results(results_path)
    metrics = score(gold, results)
    meta = run_metadata(results)
    print_report(meta, split, metrics)

    report = {
        "run": meta,
        "split": split,
        "results_file": str(results_path),
        "labels_file": str(labels_path),
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "final": bool(final),
        "metrics": metrics,
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{meta['run_id']}__{split}.json"
        out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"\nSaved {out}")
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Per-field accuracy of a results file vs golden labels")
    p.add_argument("results", nargs="?", help="results/<run_id>.json")
    p.add_argument("--split", choices=SPLIT_CHOICES, default="all")
    p.add_argument("--final", action="store_true",
                   help="allow scoring the frozen 'test' split (one-off final measurement)")
    p.add_argument("--labels", default=str(DEFAULT_LABELS), help="gold labels.json")
    p.add_argument("--out-dir", default=str(EVAL_DIR),
                   help="where to save the report (use data/local/... for local-only sets)")
    p.add_argument("--compare", nargs=2, metavar=("A", "B"),
                   help="compare two saved eval_runs/*.json files instead")
    args = p.parse_args(argv)

    if args.compare:
        compare(*args.compare)
        return 0
    if not args.results:
        p.error("give a results file or --compare A B")
    try:
        evaluate_file(Path(args.results), args.split, Path(args.labels), Path(args.out_dir),
                      final=args.final)
    except TestSetLocked as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
