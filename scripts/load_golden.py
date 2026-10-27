"""Load the golden dataset and check it is complete.

Checks that every row in data/golden/labels.json has an image that exists and
that Pillow can open, then prints simple counts.

Run from the project root:
    .venv/bin/python scripts/load_golden.py                      # CORD (committed)
    .venv/bin/python scripts/load_golden.py --labels data/local/sroie/labels.json   # SROIE (local only)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "data" / "golden" / "labels.json"
FIELDS = ["merchant", "date", "total"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Check a golden labels file and its images")
    p.add_argument("--labels", type=Path, default=LABELS, help="labels.json to check")
    args = p.parse_args(argv)
    print(f"Labels file: {args.labels}")
    rows = json.loads(args.labels.read_text(encoding="utf-8"))
    problems = []

    for row in rows:
        path = ROOT / row["image"]
        if not path.exists():
            problems.append(f"{row['id']}: missing image {row['image']}")
            continue
        try:
            with Image.open(path) as img:
                img.verify()  # checks the file is a readable image without decoding it all
        except Exception as e:  # any Pillow failure counts as a broken image
            problems.append(f"{row['id']}: cannot open {row['image']} ({e})")

    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        problems.append("duplicate ids in labels.json")

    print(f"Receipts: {len(rows)}")
    print(f"Images OK: {len(rows) - len(problems)} / {len(rows)}")
    print("Per split: ", dict(Counter(r["split"] for r in rows)))
    print("Per source:", dict(Counter(r["source"] for r in rows)))
    print("Non-null gold labels per field:")
    for f in FIELDS:
        n = sum(r.get(f) is not None for r in rows)
        print(f"  {f:<9} {n} / {len(rows)}")

    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
