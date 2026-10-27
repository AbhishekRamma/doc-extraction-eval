"""Integration test: needs a reachable PostgreSQL in DATABASE_URL (skipped otherwise).

The test creates and drops its own schema (search_path) so it never touches
real tables.
"""
import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from src import load_db  # noqa: E402
from src.schema import LineItem, ReceiptResult, RunResult  # noqa: E402

URL = os.getenv("DATABASE_URL")


def _connect():
    if not URL:
        pytest.skip("DATABASE_URL not set")
    try:
        return psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable: {e}")


@pytest.fixture
def conn():
    c = _connect()
    schema = f"test_{uuid.uuid4().hex[:8]}"
    c.execute(f"CREATE SCHEMA {schema}")
    c.execute(f"SET search_path TO {schema}")
    load_db.apply_schema(c)
    yield c
    c.execute(f"DROP SCHEMA {schema} CASCADE")
    c.close()


def _run():
    return RunResult(run_id="run1", model="cached:x", prompt_version="extract_v1",
                     ocr_engine="tesseract-5", extractor_version="1", receipts=[
        ReceiptResult(id="a", merchant="Shop A", date="2018-12-25", total="11.20",
                      line_items=[LineItem(description="x", quantity=2, unit_price="3.50", amount="7.00"),
                                  LineItem(description="y", quantity=1, amount="4.20")]),
        ReceiptResult(id="b", merchant="shop a.", date=None, total="60000.00",
                      needs_review=True, review_reasons=["missing_date"]),
        ReceiptResult(id="c", valid=False, needs_review=True, review_reasons=["image_not_found"],
                      error="image not found"),
    ])


def _counts(c):
    return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("dim_receipt", "fact_receipt_item", "review_queue", "dim_merchant"))


def test_load_and_idempotent(conn):
    load_db.load_run(conn, _run())
    first = _counts(conn)
    # 3 receipts; facts: 2 items + 1 + 1 receipt-level rows; 2 reviews;
    # merchants: (unknown) + 'shop a' (A and 'a.' normalise to the same)
    assert first == (3, 4, 2, 2)
    load_db.load_run(conn, _run())
    assert _counts(conn) == first


def test_queries_sql_runs(conn):
    load_db.load_run(conn, _run())
    sql = (load_db.ROOT / "sql" / "queries.sql").read_text()
    sql = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    stmts = [s for s in sql.split(";") if s.strip()]
    assert len(stmts) >= 5
    for s in stmts:
        conn.execute(s).fetchall()
    top = conn.execute("SELECT merchant_name, SUM(total_amount) FROM dim_receipt JOIN dim_merchant "
                       "USING (merchant_key) WHERE valid GROUP BY 1 ORDER BY 2 DESC").fetchall()
    assert top[0][1] == 60011.20 or float(top[0][1]) == 60011.20
