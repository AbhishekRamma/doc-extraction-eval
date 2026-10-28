"""Compare free local OCR engines on the golden 'quick' split.

Measures, per engine (real numbers only, nothing estimated):
- seconds per image (wall clock, model load time reported separately)
- total_found: share of receipts whose gold total appears among the amounts
  in the OCR text after normalize_total (a text-quality proxy; CORD has no
  plain-text OCR ground truth in our labels, so CER is not computed)
- date_found: same for gold dates (only receipts that have a gold date)
- amount_recall: if data/golden/cord_gt_raw.json exists, the share of all
  gold money strings (item prices, subtotal, total...) found in the OCR text

Engines can live in different virtualenvs (torch / paddle are large and can
conflict), so each engine runs in a subprocess with its own interpreter:

  python scripts/compare_ocr.py --engines tesseract easyocr \
      --python easyocr=/path/to/easyocr-venv/bin/python

OCR text is cached in data/ocr_cache/<engine>/<id>.txt (use --no-cache to re-time).

It also writes a machine-readable summary (default results/ocr_comparison.json,
committed for the CORD quick split) with, per engine: version, n_images,
mean_sec_per_image, model_load_sec, gold_total_found_rate, gold_amounts_found_rate
and rules_total_correct / rules_total_n (OCR text -> rules extractor -> total
matches gold after normalize_total), plus dataset, hardware and run date.
Engines that could not run are kept with null numbers and a note.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.normalize import normalize_date, normalize_total  # noqa: E402
from src.splits import SPLIT_CHOICES, check_test_access, select_rows  # noqa: E402

AMOUNT = re.compile(r"\d[\d.,]*\d|\d")
DATEISH = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}\s*[A-Za-z]{3,9}\.?\s*\d{2,4}")


def amounts_in(text: str) -> set[str]:
    return {v for v in (normalize_total(x) for x in AMOUNT.findall(text)) if v}


def dates_in(text: str) -> set[str]:
    return {v for v in (normalize_date(x) for x in DATEISH.findall(text)) if v}


def gold_amounts(raw: dict) -> set[str]:
    """Every *price* / *total* value in a CORD gt_parse dict, normalised."""
    out = set()

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str) and re.search(r"price|total", key) and "cnt" not in key:
            v = normalize_total(o.lstrip("-"))
            if v and v != "0.00":
                out.add(v)
    walk(raw)
    return out


def score(rows: list[dict], texts: dict[str, str], gold_raw: dict | None) -> dict:
    from src.extract import rules_extract
    t_hit = t_n = d_hit = d_n = a_hit = a_n = 0
    r_hit = r_n = 0
    for r in rows:
        text = texts.get(r["id"])
        if text is None:
            continue
        if r.get("total"):
            t_n += 1
            gold_total = normalize_total(r["total"])
            t_hit += gold_total in amounts_in(text)
            r_n += 1
            r_hit += normalize_total(rules_extract(text).get("total")) == gold_total
        if r.get("date"):
            d_n += 1
            d_hit += normalize_date(r["date"]) in dates_in(text)
        if gold_raw and r["id"] in gold_raw:
            g = gold_amounts(gold_raw[r["id"]])
            a_n += len(g)
            a_hit += len(g & amounts_in(text))
    frac = lambda h, n: (round(h / n, 3) if n else None)
    return {"total_found": frac(t_hit, t_n), "total_n": t_n,
            "date_found": frac(d_hit, d_n), "date_n": d_n,
            "amount_recall": frac(a_hit, a_n), "amount_n": a_n,
            "rules_total_correct": r_hit, "rules_total_n": r_n}


def _hardware() -> dict:
    try:
        import torch  # only to report a GPU honestly if one exists
        gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:
        gpu = None
    return {"cpu_count": os.cpu_count(), "gpu": gpu, "note": "CPU only" if gpu is None else "GPU present"}


def build_summary(results: list[dict], dataset: str, run_date: str | None = None) -> dict:
    """Machine-readable summary: one entry per engine, nulls (never guesses) on failure."""
    engines = []
    for r in results:
        ok = "error" not in r
        engines.append({
            "engine": r["engine"],
            "version": r.get("version"),
            "n_images": r.get("n_receipts") if ok else None,
            "images_timed": r.get("images_timed") if ok else None,
            "mean_sec_per_image": r.get("sec_per_image") if ok else None,
            "model_load_sec": r.get("model_load_sec") if ok else None,
            "gold_total_found_rate": r.get("total_found") if ok else None,
            "gold_amounts_found_rate": r.get("amount_recall") if ok else None,
            "rules_total_correct": r.get("rules_total_correct") if ok else None,
            "rules_total_n": r.get("rules_total_n") if ok else None,
            "note": (None if ok and not r.get("n_errors") else
                     f"{r.get('n_errors')} image(s) failed" if ok else
                     "engine not runnable here: " + r["error"][-200:]),
        })
    return {"dataset": dataset, "hardware": _hardware(),
            "run_date": run_date or _dt.date.today().isoformat(),
            "timing_note": "sec per image excludes model load; cached OCR text is not timed (use --no-cache)",
            "engines": engines}


# ---- worker: runs inside the engine's own interpreter ----------------------

def worker(engine: str, rows: list[dict], use_cache: bool) -> dict:
    from src import ocr
    t0 = time.perf_counter()
    load = 0.0
    texts, secs, errors = {}, [], {}
    for i, r in enumerate(rows):
        img = ROOT / r["image"]
        cache = ocr.CACHE_DIR / engine / f"{r['id']}.txt"
        if use_cache and cache.exists():
            texts[r["id"]] = cache.read_text(encoding="utf-8")
            continue
        if not img.exists():
            errors[r["id"]] = "image not found"
            continue
        s = time.perf_counter()
        try:
            text = ocr.ocr_image(img, engine=engine)
        except Exception as e:  # keep going, report it
            errors[r["id"]] = f"{type(e).__name__}: {e}"[:200]
            continue
        dt = time.perf_counter() - s
        if not secs and engine != "tesseract":
            load = dt  # first call includes model load; time it again below
            s = time.perf_counter()
            text = ocr.ocr_image(img, engine=engine)
            dt = time.perf_counter() - s
            load -= dt
        secs.append(dt)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
        texts[r["id"]] = text
        print(f"  {engine} {i + 1}/{len(rows)} {r['id']} {dt:.2f}s", file=sys.stderr)
    return {"engine": engine, "version": ocr.engine_version(engine),
            "images_timed": len(secs),
            "sec_per_image": round(sum(secs) / len(secs), 3) if secs else None,
            "model_load_sec": round(load, 2) if load else None,
            "wall_sec": round(time.perf_counter() - t0, 1),
            "texts": texts, "errors": errors}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--labels", type=Path, default=ROOT / "data" / "golden" / "labels.json")
    p.add_argument("--gold-raw", type=Path, default=ROOT / "data" / "golden" / "cord_gt_raw.json")
    p.add_argument("--split", default="quick", choices=SPLIT_CHOICES)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--engines", nargs="+", default=["tesseract"])
    p.add_argument("--python", action="append", default=[],
                   help="engine=/path/to/python for engines in another venv")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--out", type=Path, default=None, help="write the summary JSON here")
    p.add_argument("--summary-json", type=Path, default=ROOT / "results" / "ocr_comparison.json",
                   help="machine-readable summary (default results/ocr_comparison.json)")
    p.add_argument("--dataset", default=None,
                   help="dataset label in the summary (default: 'CORD-v2 <split> split (<n>)')")
    p.add_argument("--worker", default=None, help=argparse.SUPPRESS)
    a = p.parse_args(argv)

    if not a.labels.exists():
        print(f"labels not found: {a.labels} (built by scripts/build_golden.py)", file=sys.stderr)
        return 2
    rows = select_rows(json.loads(a.labels.read_text(encoding="utf-8")), a.split)
    check_test_access(rows)   # OCR comparison is a tuning tool: never on the test split
    rows = rows[: a.limit] if a.limit else rows

    if a.worker:
        print(json.dumps(worker(a.worker, rows, not a.no_cache)))
        return 0

    pythons = dict(x.split("=", 1) for x in a.python)
    gold_raw = json.loads(a.gold_raw.read_text()) if a.gold_raw.exists() else None
    summary = []
    for eng in a.engines:
        py = pythons.get(eng, sys.executable)
        cmd = [py, __file__, "--worker", eng, "--labels", str(a.labels), "--split", a.split]
        if a.limit:
            cmd += ["--limit", str(a.limit)]
        if a.no_cache:
            cmd.append("--no-cache")
        print(f"== {eng} ({py})", file=sys.stderr)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            summary.append({"engine": eng, "error": proc.stderr.strip()[-400:] or f"exit {proc.returncode}"})
            print(proc.stderr[-2000:], file=sys.stderr)
            continue
        res = json.loads(proc.stdout.strip().splitlines()[-1])
        res.update(score(rows, res.pop("texts"), gold_raw))
        res["n_receipts"] = len(rows)
        res["n_errors"] = len(res["errors"])
        summary.append(res)

    cols = ["engine", "version", "n_receipts", "images_timed", "sec_per_image", "model_load_sec",
            "total_found", "date_found", "amount_recall", "n_errors"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for s in summary:
        print("| " + " | ".join(str(s.get(c, "")) for c in cols) + " |")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(summary, indent=2))
    if a.summary_json:
        dataset = a.dataset or f"CORD-v2 {a.split} split ({len(rows)})"
        a.summary_json.parent.mkdir(parents=True, exist_ok=True)
        a.summary_json.write_text(json.dumps(build_summary(summary, dataset), indent=2) + "\n",
                                  encoding="utf-8")
        print(f"wrote {a.summary_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
