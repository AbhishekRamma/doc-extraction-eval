"""Fetch a LOCAL-ONLY SROIE golden set into data/local/sroie/.

Two sets, both local only:
  dev  (default)  receipts 0-99   -> labels.json        (sroie_000..099; first 20 = quick)
  test (--test)   receipts 100-299 -> labels_test.json  (sroie_100..299, split "test")
                  + test_manifest.json (ids, sha256 of every image and label, source
                  commit, date). The test set is FROZEN: --test refuses to run if a
                  manifest already exists; --verify-test re-hashes the files against it.
                  Scoring split "test" needs `python -m src.evaluate ... --final`.

!! Nothing this script writes may be committed. !!
SROIE's licence is not confirmed from the official ICDAR RRC site (a third-party
mirror says CC BY 4.0), so we use it on this machine only. data/local/ is in
.gitignore; the script refuses to run if that is not the case.

Source (primary): the corrected SROIE task-3 *train* data hosted in the
zzzDavid/ICDAR-2019-SROIE GitHub repo, folder data/ (img/NNN.jpg + key/NNN.json
with company/date/address/total), pinned to commit ZZZ_COMMIT below. Files are
fetched one by one from raw.githubusercontent.com (no full clone needed).
Note: that repo's MIT licence covers its code, not this data.

Fallback (only if the primary fails): the Hugging Face mirror
jsdnrs/ICDAR2019-SROIE, train parquet, pinned to HF_REVISION.

Selection: the source keys sorted by name (keys without an image skipped), then
positions 0-99 (dev) or 100-299 (test). Deterministic, no randomness. First 20 = "quick".

Output (all under data/local/sroie/):
  raw/key/*.json, raw/img/*.jpg   untouched downloads (primary source)
  images/sroie_000.jpg ...        copies used by the pipeline
  labels.json                     same shape as data/golden/labels.json
  id_map.json                     sroie_XXX -> original source file name
  SOURCE.txt                      exactly which source + commit was used

Labels are normalised with src/normalize.py (gold side only), like CORD.

Run from the project root:   .venv/bin/python scripts/fetch_sroie.py [--test | --verify-test]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.normalize import normalize_date, normalize_merchant, normalize_total  # noqa: E402

ZZZ_REPO = "zzzDavid/ICDAR-2019-SROIE"
ZZZ_COMMIT = "27be4271b251c256f695acbade9a801bffe85994"   # master on 2020-07-20
HF_REPO = "jsdnrs/ICDAR2019-SROIE"
HF_REVISION = "bffe40c26759f3376ec2b3ae9031dbba54cd587c"
HF_TRAIN = "data/train-00000-of-00001.parquet"

OUT = ROOT / "data" / "local" / "sroie"
RAW = OUT / "raw"
IMAGES = OUT / "images"
N_RECEIPTS = 100
QUICK_SIZE = 20
TEST_START, TEST_STOP = 100, 300          # source positions 100..299 -> sroie_100..299
TEST_LABELS = OUT / "labels_test.json"
TEST_MANIFEST = OUT / "test_manifest.json"


def check_ignored() -> None:
    """Stop if git would NOT ignore data/local/ (safety net against committing)."""
    # check the folder itself: data/local/sroie may be a symlink (e.g. in a git worktree),
    # and git refuses to check paths "beyond a symbolic link"
    r = subprocess.run(["git", "check-ignore", "-q", str(OUT)], cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit("data/local/ is not gitignored - refusing to download SROIE")


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


# ----------------------------------------------------------- primary source
def fetch_zzz(start: int = 0, stop: int = N_RECEIPTS) -> list[tuple[str, dict, Path]]:
    """Download key/img pairs at sorted positions [start, stop). Returns (source_name, key, image_path)."""
    tree = json.loads(get(f"https://api.github.com/repos/{ZZZ_REPO}/git/trees/{ZZZ_COMMIT}?recursive=1"))
    paths = {t["path"] for t in tree["tree"]}
    keys = sorted(p for p in paths if re.fullmatch(r"data/key/[^/]+\.json", p))
    picked = []
    for key_path in keys:
        stem = Path(key_path).stem
        img_path = f"data/img/{stem}.jpg"
        if img_path not in paths:
            print(f"  skip {stem}: no image in repo")
            continue
        picked.append((stem, key_path, img_path))
        if len(picked) == stop:
            break
    if len(picked) < stop:
        raise RuntimeError(f"source has only {len(picked)} key/img pairs, need {stop}")
    picked = picked[start:stop]

    out = []
    base = f"https://raw.githubusercontent.com/{ZZZ_REPO}/{ZZZ_COMMIT}/"
    for stem, key_path, img_path in picked:
        key_file, img_file = RAW / "key" / f"{stem}.json", RAW / "img" / f"{stem}.jpg"
        for remote, local in [(key_path, key_file), (img_path, img_file)]:
            if not local.exists():          # re-runs reuse earlier downloads
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(get(base + remote))
        out.append((stem, json.loads(key_file.read_text(encoding="utf-8")), img_file))
    return out


# ----------------------------------------------------------- fallback source
def fetch_hf() -> list[tuple[str, dict, Path]]:
    import pyarrow.parquet as pq  # pip install pyarrow
    from PIL import Image

    local = RAW / "hf_train.parquet"
    if not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(get(f"https://huggingface.co/datasets/{HF_REPO}/resolve/{HF_REVISION}/{HF_TRAIN}"))
    rows = sorted(pq.read_table(local, columns=["image", "key", "entities"]).to_pylist(),
                  key=lambda r: r["key"])[:N_RECEIPTS]
    out = []
    for r in rows:
        img_file = RAW / "img" / f"{r['key']}.jpg"
        img_file.parent.mkdir(parents=True, exist_ok=True)
        Image.open(io.BytesIO(r["image"]["bytes"])).convert("RGB").save(img_file, "JPEG", quality=95)
        out.append((r["key"], r["entities"], img_file))
    return out


# ----------------------------------------------------------- labels
def ambiguous_date(raw: str) -> bool:
    """True if a numeric date could be read both day-first and month-first."""
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})\b", raw)
    return bool(m) and int(m[1]) <= 12 and int(m[2]) <= 12 and m[1] != m[2]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_row(row: dict) -> str:
    """Hash of one label row (canonical JSON: sorted keys, no spaces)."""
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def label_row(rid: str, key: dict, split: str) -> tuple[dict, list[str]]:
    raw = {f: (key.get(f) or None) for f in ["company", "date", "total"]}
    row = {"id": rid, "image": f"data/local/sroie/images/{rid}.jpg", "source": "SROIE",
           "split": split,
           # gold merchant is stored normalised (casefolded, no punctuation),
           # which is what evaluate.py compares on anyway
           "merchant": normalize_merchant(raw["company"]), "date": normalize_date(raw["date"]),
           "total": normalize_total(raw["total"])}
    # ids and field names only: values are never printed for the test set
    problems = []
    for field, src in [("merchant", "company"), ("date", "date"), ("total", "total")]:
        if raw[src] is None:
            problems.append(f"{rid} {field}: missing in source -> null")
        elif row[field] is None:
            problems.append(f"{rid} {field}: unparseable -> null")
    return row, problems


def fetch_test() -> None:
    """Fetch and FREEZE the held-out test set (positions 100..299). Local only."""
    check_ignored()
    if TEST_MANIFEST.exists():
        raise SystemExit(f"{TEST_MANIFEST.relative_to(ROOT)} exists: the test set is frozen. "
                         "Use --verify-test to check it; delete the manifest only if you mean "
                         "to re-freeze (and say so in the journal).")
    receipts = fetch_zzz(TEST_START, TEST_STOP)
    IMAGES.mkdir(parents=True, exist_ok=True)
    labels, entries, problems = [], [], []
    for i, (name, key, img_file) in enumerate(receipts, start=TEST_START):
        rid = f"sroie_{i:03d}"
        dest = ROOT / "data" / "local" / "sroie" / "images" / f"{rid}.jpg"
        shutil.copyfile(img_file, dest)
        row, probs = label_row(rid, key, "test")
        labels.append(row)
        problems += probs
        entries.append({"id": rid, "source_name": name,
                        "image_sha256": sha256_file(dest),
                        "source_key_sha256": sha256_file(RAW / "key" / f"{name}.json"),
                        "label_sha256": sha256_row(row)})
    TEST_LABELS.write_text(json.dumps(labels, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "description": "FROZEN SROIE held-out test set. Local only, never commit. "
                       "Score only with `python -m src.evaluate ... --final`.",
        "source": f"GitHub {ZZZ_REPO}, folder data/ (img + key), commit {ZZZ_COMMIT}",
        "source_commit": ZZZ_COMMIT,
        "selection": f"source keys sorted by name (keys without an image skipped), "
                     f"positions {TEST_START}..{TEST_STOP - 1}",
        "labels_file": "data/local/sroie/labels_test.json",
        "labels_file_sha256": sha256_file(TEST_LABELS),
        "label_hash": "sha256 of the label row as canonical JSON (sort_keys, separators=(',', ':'))",
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_receipts": len(entries),
        "receipts": entries,
    }
    TEST_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Froze {len(entries)} test receipts: {TEST_LABELS.relative_to(ROOT)}, "
          f"{TEST_MANIFEST.relative_to(ROOT)}")
    print(f"Label values set to null or missing: {len(problems)} (ids only, values not printed)")
    for p in problems:
        print("  " + p)


def verify_test() -> int:
    """Re-hash every test image and label against the frozen manifest."""
    manifest = json.loads(TEST_MANIFEST.read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in json.loads(TEST_LABELS.read_text(encoding="utf-8"))}
    bad = []
    for e in manifest["receipts"]:
        img = ROOT / "data" / "local" / "sroie" / "images" / f"{e['id']}.jpg"
        if not img.exists() or sha256_file(img) != e["image_sha256"]:
            bad.append(f"{e['id']}: image changed or missing")
        if e["id"] not in rows or sha256_row(rows[e["id"]]) != e["label_sha256"]:
            bad.append(f"{e['id']}: label changed or missing")
    if set(rows) != {e["id"] for e in manifest["receipts"]}:
        bad.append("labels_test.json ids differ from the manifest")
    print(f"{len(manifest['receipts'])} test receipts checked, {len(bad)} problem(s)")
    for b in bad:
        print("  " + b)
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch the local-only SROIE dev or test set")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--test", action="store_true", help="fetch + freeze the test set (100..299)")
    g.add_argument("--verify-test", action="store_true", help="check the frozen test set hashes")
    args = ap.parse_args()
    if args.test:
        fetch_test()
        return
    if args.verify_test:
        raise SystemExit(verify_test())
    check_ignored()
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        receipts = fetch_zzz()
        source = (f"GitHub {ZZZ_REPO}, folder data/ (img + key), commit {ZZZ_COMMIT}, "
                  "via raw.githubusercontent.com")
    except Exception as e:
        print(f"Primary source failed ({e}); using Hugging Face fallback")
        receipts = fetch_hf()
        source = f"Hugging Face {HF_REPO}, {HF_TRAIN}, revision {HF_REVISION}"
    print(f"Source: {source}\nReceipts: {len(receipts)}")

    IMAGES.mkdir(parents=True, exist_ok=True)
    labels, id_map, problems, notes = [], {}, [], []
    for i, (name, key, img_file) in enumerate(receipts):
        rid = f"sroie_{i:03d}"
        image_rel = f"data/local/sroie/images/{rid}.jpg"
        shutil.copyfile(img_file, ROOT / image_rel)
        id_map[rid] = name

        raw = {f: (key.get(f) or None) for f in ["company", "date", "total"]}
        merchant = normalize_merchant(raw["company"])
        date = normalize_date(raw["date"])
        total = normalize_total(raw["total"])
        # log anything missing or unparseable (the label becomes null)
        for field, raw_v, norm in [("merchant", raw["company"], merchant),
                                   ("date", raw["date"], date), ("total", raw["total"], total)]:
            if norm is None:
                problems.append(f"{rid} ({name}) {field}: raw={raw_v!r} -> null")
        if raw["date"] and date and ambiguous_date(raw["date"]):
            notes.append(f"{rid} ({name}) date {raw['date']!r} could be DD/MM or MM/DD; read as {date}")

        labels.append({
            "id": rid, "image": image_rel, "source": "SROIE",
            "split": "quick" if i < QUICK_SIZE else "full",
            # gold merchant is stored normalised (casefolded, no punctuation),
            # which is what evaluate.py compares on anyway
            "merchant": merchant, "date": date, "total": total,
        })

    (OUT / "labels.json").write_text(json.dumps(labels, indent=2) + "\n", encoding="utf-8")
    (OUT / "id_map.json").write_text(json.dumps(id_map, indent=2) + "\n", encoding="utf-8")
    (OUT / "SOURCE.txt").write_text(
        f"SROIE local-only golden set. DO NOT COMMIT.\n"
        f"Source: {source}\n"
        f"Fetched: {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}\n"
        f"Selection: first {N_RECEIPTS} source keys sorted by name; first {QUICK_SIZE} = quick\n"
        f"Licence: unverified from ICDAR RRC (third-party mirror says CC BY 4.0); not redistributed.\n"
        f"Citation: Huang et al. 2019, ICDAR2019 Competition on Scanned Receipt OCR and "
        f"Information Extraction, arXiv:2103.10213\n", encoding="utf-8")

    print(f"Wrote {len(labels)} labels to {OUT.relative_to(ROOT)}/labels.json")
    print(f"\nValues set to null ({len(problems)}):")
    for p in problems:
        print("  " + p)
    print(f"\nAmbiguous numeric dates ({len(notes)}):")
    for n in notes:
        print("  " + n)


if __name__ == "__main__":
    main()
