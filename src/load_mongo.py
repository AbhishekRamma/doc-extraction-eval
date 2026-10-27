"""Load a results/<run_id>.json file into MongoDB, one document per receipt.

  MONGO_URL=mongodb://user:pass@host:27017/receipts?authSource=admin \
      python -m src.load_mongo results/<run>.json

This is the document-store companion to src/load_db.py (the Postgres star
schema). It takes the same input file. Each receipt becomes one document in the
`receipts` collection, with its line items embedded as an array:

  {receipt_id, run_id, model, prompt_version, ocr_engine, extractor_version,
   run_created_at, merchant, merchant_norm, date, total, valid, needs_review,
   review_reasons, error, line_items: [{line_no, description, quantity,
   unit_price, amount}]}

Type choices:
- `date` is a BSON date (a UTC-midnight datetime), because BSON has no date-only
  type. It is null when the extractor found no valid date.
- money (`total`, `unit_price`, `amount`) is Decimal128, parsed from the 2-dp
  strings in the results file. Sums stay exact and match Postgres NUMERIC;
  doubles would not.
- `quantity` is a double, as in the results file.
- `merchant_norm` is normalize_merchant(merchant), or "(unknown)" when there
  is no usable merchant, the same rule as dim_merchant in the star schema.

Idempotent: every receipt is upserted on (receipt_id, run_id), backed by a
unique index, so loading the same run again replaces documents rather than
duplicating them.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.normalize import normalize_merchant
from src.schema import ReceiptResult, RunResult

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = "receipts"
COLLECTION = "receipts"
UNKNOWN_MERCHANT = "(unknown)"


def _dec(v):
    if v is None:
        return None
    from bson.decimal128 import Decimal128
    return Decimal128(Decimal(str(v)))


def _bson_date(iso: str | None):
    if not iso:
        return None
    d = date.fromisoformat(iso)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _run_created_at(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def receipt_document(run: RunResult, r: ReceiptResult) -> dict:
    """Build the Mongo document for one receipt of one run (pure, no I/O)."""
    return {
        "receipt_id": r.id,
        "run_id": run.run_id,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "ocr_engine": run.ocr_engine,
        "extractor_version": run.extractor_version,
        "run_created_at": _run_created_at(run.created_at),
        "merchant": r.merchant,
        "merchant_norm": normalize_merchant(r.merchant) or UNKNOWN_MERCHANT,
        "date": _bson_date(r.date),
        "total": _dec(r.total),
        "valid": r.valid,
        "needs_review": r.needs_review,
        "review_reasons": list(r.review_reasons),
        "error": r.error,
        "line_items": [
            {"line_no": n,
             "description": li.description,
             "quantity": li.quantity,
             "unit_price": _dec(li.unit_price),
             "amount": _dec(li.amount)}
            for n, li in enumerate(r.line_items, start=1)
        ],
    }


def ensure_indexes(coll) -> None:
    coll.create_index([("receipt_id", 1), ("run_id", 1)], unique=True,
                      name="uq_receipt_run")
    coll.create_index([("merchant_norm", 1)], name="ix_merchant_norm")
    coll.create_index([("date", 1)], name="ix_date")


def load_run(coll, run: RunResult) -> dict:
    """Upsert every receipt of `run`. Returns pymongo's bulk-write counts."""
    from pymongo import UpdateOne

    # $set of the whole document, not a replace: Mongo then reports an unchanged
    # reload as matched but not modified, which makes idempotency visible.
    ops = [UpdateOne({"receipt_id": r.id, "run_id": run.run_id},
                     {"$set": receipt_document(run, r)}, upsert=True)
           for r in run.receipts]
    counts = {"receipts": len(ops), "upserted": 0, "matched": 0, "modified": 0,
              "line_items": sum(len(r.line_items) for r in run.receipts),
              "needs_review": sum(1 for r in run.receipts if r.needs_review)}
    if ops:
        res = coll.bulk_write(ops, ordered=True)
        counts.update(upserted=res.upserted_count, matched=res.matched_count,
                      modified=res.modified_count)
    return counts


def connect(url: str, db_name: str | None = None, timeout_ms: int = 5000):
    """Return the `receipts` collection. The db comes from --db, then the URL path."""
    from pymongo import MongoClient

    client = MongoClient(url, serverSelectionTimeoutMS=timeout_ms, tz_aware=True)
    db = client[db_name] if db_name else client.get_default_database(DEFAULT_DB)
    return client, db[COLLECTION]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Load a results JSON into MongoDB")
    p.add_argument("results", type=Path, nargs="+")
    p.add_argument("--mongo-url", default=None)
    p.add_argument("--db", default=None,
                   help=f"database name (default: from the URL, else '{DEFAULT_DB}')")
    p.add_argument("--no-indexes", action="store_true", help="skip the create_index step")
    a = p.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    url = a.mongo_url or os.getenv("MONGO_URL")
    if not url:
        print("MONGO_URL is not set", file=sys.stderr)
        return 2
    try:
        import pymongo  # noqa: F401
    except ImportError:
        print("pymongo is not installed: pip install -e '.[mongo]'", file=sys.stderr)
        return 2
    client, coll = connect(url, a.db)
    try:
        if not a.no_indexes:
            ensure_indexes(coll)
        for path in a.results:
            run = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
            c = load_run(coll, run)
            print(f"loaded {path.name}: {c['receipts']} receipts "
                  f"({c['upserted']} inserted, {c['matched']} matched, "
                  f"{c['modified']} modified), {c['line_items']} line items, "
                  f"{c['needs_review']} need review")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
