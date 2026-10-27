"""Build the golden dataset from the CORD-v2 test split.

What this script does (step by step):
  1. Downloads ONE file: the CORD-v2 *test* split (100 receipts) as a parquet
     file from the official Hugging Face dataset `naver-clova-ix/cord-v2`
     (licence CC BY 4.0). The download is pinned to a fixed dataset commit so
     every run gets exactly the same data. It is cached in data/raw/ (gitignored).
  2. Saves each receipt image as data/golden/images/cord_test_000.jpg ... 099.
     CORD ships the images as PNG; we re-encode them to JPEG (quality 95)
     because the agreed file names end in .jpg and JPEG is much smaller in git.
  3. Writes data/golden/labels.json: one row per receipt with the agreed shape
        {"id", "image", "source", "split", "merchant", "date", "total"}
     - merchant and date are always null: NAVER removed the store_info fields
       from the public CORD release, so there is no gold label for them.
     - total comes from gt_parse.total.total_price and is normalised with
       src.normalize.normalize_total -- the SAME function used on predictions,
       so gold and predicted totals are compared on equal terms.
  4. Writes data/golden/cord_gt_raw.json: the untouched gt_parse per receipt
     (useful later for line-item scoring).

Quick split: the first 20 receipts by id (cord_test_000 ... cord_test_019).
This is deterministic and easy to explain; CORD's test split is not sorted by
anything meaningful, so the first 20 are as good a sample as any.

Run from the project root:
    .venv/bin/pip install pyarrow pillow
    .venv/bin/python scripts/build_golden.py
"""
from __future__ import annotations

import io
import json
import re
import sys
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq
from PIL import Image

# Make `import src...` work when running `python scripts/build_golden.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.normalize import normalize_total  # noqa: E402  (owned by Software DEV)

# Pinned dataset commit on Hugging Face (so the data never silently changes).
HF_REVISION = "7f0115a4b758a71d6473b8d085751692da2fef98"
PARQUET_URL = (
    "https://huggingface.co/datasets/naver-clova-ix/cord-v2/resolve/"
    f"{HF_REVISION}/data/test-00000-of-00001-9c204eb3f4e11791.parquet"
)
RAW_PARQUET = ROOT / "data" / "raw" / "cord_v2_test.parquet"
GOLDEN_DIR = ROOT / "data" / "golden"
IMAGES_DIR = GOLDEN_DIR / "images"
QUICK_SIZE = 20

# A "clean" CORD price: digits, optionally grouped in 3s by '.' or ','.
# e.g. '60.000', '1,565,938', '91000'. Anything else (currency prefix,
# trailing decimals, mixed separators) still goes through normalize_total,
# but we log it so a human can double-check the result.
CLEAN_PRICE = re.compile(r"\d{1,3}([.,]\d{3})*|\d+")


def download_parquet() -> Path:
    """Download the test parquet once; reuse the cached copy afterwards."""
    if RAW_PARQUET.exists():
        print(f"Using cached {RAW_PARQUET.relative_to(ROOT)}")
        return RAW_PARQUET
    RAW_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {PARQUET_URL}")
    urllib.request.urlretrieve(PARQUET_URL, RAW_PARQUET)
    return RAW_PARQUET


def main() -> None:
    table = pq.read_table(download_parquet())
    rows = table.to_pylist()  # list of {"image": {"bytes", "path"}, "ground_truth": str}
    print(f"Loaded {len(rows)} receipts from the CORD-v2 test split")

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    labels, raw_gt = [], {}
    null_totals, flagged_totals = [], []

    for i, row in enumerate(rows):
        gt = json.loads(row["ground_truth"])
        # Sanity check: CORD's own image_id should match the row position.
        if gt["meta"]["image_id"] != i:
            raise SystemExit(f"row {i} has image_id {gt['meta']['image_id']}")

        rid = f"cord_test_{i:03d}"
        image_rel = f"data/golden/images/{rid}.jpg"

        # Save the image as JPEG (RGB, because JPEG has no alpha channel).
        img = Image.open(io.BytesIO(row["image"]["bytes"])).convert("RGB")
        img.save(ROOT / image_rel, "JPEG", quality=95)

        # Gold total: gt_parse.total.total_price -> normalize_total.
        gt_parse = gt["gt_parse"]
        raw_gt[rid] = gt_parse
        total_block = gt_parse.get("total")
        raw_total = total_block.get("total_price") if isinstance(total_block, dict) else None

        total = None
        if raw_total is None:
            null_totals.append((rid, "total_price missing"))
        else:
            total = normalize_total(raw_total)
            if total is None:
                null_totals.append((rid, f"unparseable {raw_total!r}"))
            elif not CLEAN_PRICE.fullmatch(raw_total.strip()):
                flagged_totals.append((rid, raw_total, total))

        labels.append({
            "id": rid,
            "image": image_rel,
            "source": "CORD-v2",
            "split": "quick" if i < QUICK_SIZE else "holdout",
            "merchant": None,  # not in public CORD (store_info removed)
            "date": None,      # not in public CORD (store_info removed)
            "total": total,
        })

    (GOLDEN_DIR / "labels.json").write_text(json.dumps(labels, indent=2) + "\n", encoding="utf-8")
    (GOLDEN_DIR / "cord_gt_raw.json").write_text(
        json.dumps(raw_gt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # ---- Log anything a human should look at -------------------------------
    print(f"Wrote {len(labels)} labels and {len(labels)} images")
    print(f"\nTotals set to null ({len(null_totals)}):")
    for rid, why in null_totals:
        print(f"  {rid}: {why}")
    print(f"\nTotals that did not look like a plain CORD price ({len(flagged_totals)}),"
          " please eyeball:")
    for rid, raw, norm in flagged_totals:
        print(f"  {rid}: raw={raw!r} -> {norm}")


if __name__ == "__main__":
    main()
