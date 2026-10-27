"""Load a results/<run_id>.json file into the PostgreSQL star schema.

  DATABASE_URL=postgresql://user:pass@host:5432/db python -m src.load_db results/<run>.json

Idempotent: loading the same run twice replaces that run's rows (keyed on
run_id + receipt id) instead of duplicating them.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg

from src.normalize import normalize_merchant
from src.schema import RunResult

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / "sql" / "schema.sql"


def apply_schema(conn) -> None:
    conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def _date_key(conn, iso: str | None) -> int:
    if not iso:
        return 0
    d = date.fromisoformat(iso)
    key = d.year * 10000 + d.month * 100 + d.day
    conn.execute(
        """INSERT INTO dim_date (date_key, full_date, year, quarter, month, month_name,
                                 day, day_of_week, is_weekend)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (date_key) DO NOTHING""",
        (key, d, d.year, (d.month - 1) // 3 + 1, d.month, d.strftime("%B"), d.day,
         d.isoweekday(), d.isoweekday() >= 6))
    return key


def _merchant_key(conn, name: str | None) -> int:
    norm = normalize_merchant(name) or "(unknown)"
    row = conn.execute(
        """INSERT INTO dim_merchant (merchant_name, merchant_norm) VALUES (%s, %s)
           ON CONFLICT (merchant_norm) DO UPDATE SET merchant_norm = EXCLUDED.merchant_norm
           RETURNING merchant_key""",
        (name if norm != "(unknown)" else "(unknown)", norm)).fetchone()
    return row[0]


def _num(v):
    return None if v is None else Decimal(str(v))


def load_run(conn, run: RunResult) -> dict:
    counts = {"receipts": 0, "items": 0, "review": 0}
    with conn.transaction():
        for r in run.receipts:
            mk = _merchant_key(conn, r.merchant)
            dk = _date_key(conn, r.date)
            rk = conn.execute(
                """INSERT INTO dim_receipt (receipt_id, run_id, model, prompt_version, ocr_engine,
                        extractor_version, run_created_at, merchant_key, date_key, total_amount,
                        valid, needs_review, error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (run_id, receipt_id) DO UPDATE SET
                        model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version,
                        ocr_engine = EXCLUDED.ocr_engine,
                        extractor_version = EXCLUDED.extractor_version,
                        run_created_at = EXCLUDED.run_created_at,
                        merchant_key = EXCLUDED.merchant_key, date_key = EXCLUDED.date_key,
                        total_amount = EXCLUDED.total_amount, valid = EXCLUDED.valid,
                        needs_review = EXCLUDED.needs_review, error = EXCLUDED.error
                   RETURNING receipt_key""",
                (r.id, run.run_id, run.model, run.prompt_version, run.ocr_engine,
                 run.extractor_version, run.created_at, mk, dk, _num(r.total),
                 r.valid, r.needs_review, r.error)).fetchone()[0]
            counts["receipts"] += 1

            # replace this receipt's facts (idempotent reload)
            conn.execute("DELETE FROM fact_receipt_item WHERE receipt_key = %s", (rk,))
            items = r.line_items or [None]
            for n, li in enumerate(items, start=1 if r.line_items else 0):
                conn.execute(
                    """INSERT INTO fact_receipt_item (receipt_key, line_no, merchant_key, date_key,
                            description, quantity, unit_price, amount, receipt_total)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (rk, n, mk, dk,
                     li.description if li else None,
                     _num(li.quantity) if li else None,
                     _num(li.unit_price) if li else None,
                     _num(li.amount) if li else _num(r.total),
                     _num(r.total)))
                counts["items"] += 1

            if r.needs_review:
                conn.execute(
                    """INSERT INTO review_queue (receipt_key, reasons) VALUES (%s, %s)
                       ON CONFLICT (receipt_key) DO UPDATE SET reasons = EXCLUDED.reasons""",
                    (rk, r.review_reasons or ["unspecified"]))
                counts["review"] += 1
            else:
                conn.execute("DELETE FROM review_queue WHERE receipt_key = %s", (rk,))
    return counts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Load a results JSON into PostgreSQL")
    p.add_argument("results", type=Path, nargs="+")
    p.add_argument("--database-url", default=None)
    p.add_argument("--no-schema", action="store_true", help="skip CREATE IF NOT EXISTS step")
    a = p.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    url = a.database_url or os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    with psycopg.connect(url, autocommit=True) as conn:
        if not a.no_schema:
            apply_schema(conn)
        for path in a.results:
            run = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
            c = load_run(conn, run)
            print(f"loaded {path.name}: {c['receipts']} receipts, {c['items']} fact rows, "
                  f"{c['review']} in review queue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
