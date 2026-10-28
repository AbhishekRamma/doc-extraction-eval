"""End-to-end run: labels.json -> OCR -> extract -> results/<run_id>.json

Examples:
  python -m src.pipeline --split quick --provider rules-v2 --limit 20   # rules-v2 is the default
  python -m src.pipeline --split quick --provider rules --limit 20      # frozen rules-v1
  python -m src.pipeline --labels tests/fixtures/labels.json --provider cached \
      --cached-dir tests/fixtures/cached_llm
  # paid (needs DEEPSEEK_API_KEY), then replay the saved responses for free:
  python -m src.pipeline --labels data/local/sroie/labels.json --split quick --provider deepseek \
      --ocr-engine doctr --ocr-cache-dir data/local/sroie/ocr_cache \
      --llm-cache-dir data/local/sroie/llm_cache --out-dir data/local/sroie/results
  python -m src.pipeline ... --provider cached --llm-cache-dir data/local/sroie/llm_cache
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src import ocr
from src.extract import (EXTRACTOR_VERSION, PROMPT_VERSION, PROMPT_VERSIONS, DEFAULT_PROVIDER, PROVIDERS,
                         extract_receipt, get_client, resolve_prompt_version)
from src.schema import ReceiptResult, RunResult
from src.splits import SPLIT_CHOICES, select_rows

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = ROOT / "data" / "golden" / "labels.json"


def load_labels(path: Path, split: str | None = None, limit: int | None = None) -> list[dict]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    # 'quick'/'holdout'/'test' = rows with that label; 'full' (alias 'all') = every row.
    # Running predictions on 'test' is allowed; *scoring* it needs evaluate --final.
    rows = select_rows(rows, split)
    if limit:
        rows = rows[:limit]
    return rows


def run(labels_path: Path, split: str, provider: str, limit: int | None,
        engine: str = ocr.DEFAULT_ENGINE, cached_dir: Path | None = None,
        out_dir: Path | None = None, run_id: str | None = None,
        ocr_cache_dir: Path | None = None, llm_cache_dir: Path | None = None,
        client=None, prompt_version: str = PROMPT_VERSION) -> Path:
    prompt_version = resolve_prompt_version(prompt_version)
    rows = load_labels(labels_path, split, limit)
    engine = ocr.resolve_engine(engine)
    if client is None:
        kw = {}
        if provider == "cached" and (cached_dir or llm_cache_dir):
            kw = {"cache_dir": cached_dir or llm_cache_dir}
        elif provider == "deepseek":
            kw = {"prompt_version": prompt_version}
            if llm_cache_dir:
                kw["cache_dir"] = llm_cache_dir
        client = get_client(provider, **kw)
    cached_pv = getattr(client, "prompt_version", None)
    if cached_pv and cached_pv != prompt_version:
        raise RuntimeError(f"cache/client was made with prompt {cached_pv} but --prompt-version "
                           f"is {prompt_version}; pass --prompt-version {cached_pv}")
    model = getattr(client, "model", provider)
    now = datetime.now(timezone.utc)
    run_id = run_id or f"{now:%Y%m%dT%H%M%SZ}_{provider}_{prompt_version}"

    results: list[ReceiptResult] = []
    for row in rows:
        rid = str(row["id"])
        img = (ROOT / row["image"]) if not Path(row["image"]).is_absolute() else Path(row["image"])
        if not img.exists():
            print(f"[skip] {rid}: image not found ({row['image']})", file=sys.stderr)
            results.append(ReceiptResult(id=rid, valid=False, needs_review=True,
                                         review_reasons=["image_not_found"],
                                         error="image not found"))
            continue
        try:
            text, secs = ocr.ocr_cached(rid, img, engine=engine, cache_dir=ocr_cache_dir)
        except Exception as e:
            print(f"[ocr-error] {rid}: {e}", file=sys.stderr)
            results.append(ReceiptResult(id=rid, valid=False, needs_review=True,
                                         review_reasons=["ocr_failed"],
                                         error=f"ocr failed: {type(e).__name__}: {e}"))
            continue
        res = extract_receipt(rid, text, client, prompt_version=prompt_version)
        if hasattr(client, "usage_for"):
            res.usage = client.usage_for(rid)
        results.append(res)
        flag = "REVIEW " + ",".join(res.review_reasons) if res.needs_review else "ok"
        print(f"[{rid}] ocr={secs:.2f}s merchant={res.merchant!r} date={res.date} "
              f"total={res.total} -> {flag}", file=sys.stderr)

    extractor_version = getattr(client, "version", f"{provider}-llm-v{EXTRACTOR_VERSION}")
    run = RunResult(run_id=run_id, model=f"{provider}:{model}",
                    prompt_version=prompt_version if not provider.startswith("rules") else "none",
                    ocr_engine=ocr.engine_version(engine),
                    extractor_version=extractor_version,
                    created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), receipts=results)
    out_dir = Path(out_dir or ROOT / "results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{run_id}.json"
    out.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    n = len(results)
    print(f"wrote {out} ({n} receipts, {sum(r.valid for r in results)} valid, "
          f"{sum(r.needs_review for r in results)} need review)", file=sys.stderr)
    if hasattr(client, "usage_for"):
        tot: dict[str, int] = {}
        for r in results:
            for k, v in (r.usage or {}).items():
                tot[k] = tot.get(k, 0) + v
        print(f"tokens: prompt={tot.get('prompt_tokens', 0)} "
              f"completion={tot.get('completion_tokens', 0)} total={tot.get('total_tokens', 0)}",
              file=sys.stderr)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    p.add_argument("--split", default="quick", choices=SPLIT_CHOICES,
                   help="quick = 20-receipt subset; holdout = CORD's other 80; test = frozen "
                        "test rows; full (alias: all) = whole labels file")
    p.add_argument("--provider", default=DEFAULT_PROVIDER, choices=PROVIDERS,
                   help=f"extractor (default {DEFAULT_PROVIDER}; 'rules' = frozen rules-v1)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--ocr-engine", default=ocr.DEFAULT_ENGINE, choices=["auto", *ocr.ENGINES],
                   help="auto = docTR if installed, else tesseract")
    p.add_argument("--cached-dir", type=Path, default=None,
                   help="folder of saved LLM JSON for --provider cached")
    p.add_argument("--llm-cache-dir", type=Path, default=None,
                   help="where live LLM providers save raw responses (<dir>/<id>.json); "
                        "also read by --provider cached (default data/llm_cache/<provider>)")
    p.add_argument("--ocr-cache-dir", type=Path, default=None,
                   help="OCR text cache root, laid out as <dir>/<engine>/<id>.txt "
                        "(default data/ocr_cache)")
    p.add_argument("--prompt-version", default="v1",
                   choices=[v.removeprefix("extract_") for v in PROMPT_VERSIONS] + PROMPT_VERSIONS,
                   help="LLM prompt template in src/prompts/ (default v1; ignored by rules)")
    p.add_argument("--out-dir", type=Path, default=None)
    p.add_argument("--run-id", default=None)
    a = p.parse_args(argv)
    if not a.labels.exists():
        print(f"labels file not found: {a.labels}\n(data/golden/labels.json is built by scripts/build_golden.py; "
              f"for a demo use --labels tests/fixtures/labels.json)", file=sys.stderr)
        return 2
    try:
        out = run(a.labels, a.split, a.provider, a.limit, a.ocr_engine, a.cached_dir, a.out_dir,
                  a.run_id, ocr_cache_dir=a.ocr_cache_dir, llm_cache_dir=a.llm_cache_dir,
                  prompt_version=a.prompt_version)
    except (RuntimeError, ValueError) as e:  # e.g. missing API key: fail before any receipt is processed
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
