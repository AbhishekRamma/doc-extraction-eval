"""MongoDB pipelines vs sql/queries.sql.

- the pure parts (SQL query extraction, row comparison) always run
- test_pipelines_* need a reachable MongoDB in MONGO_URL
- test_parity_* also need PostgreSQL in DATABASE_URL: they load the SAME results
  file through src/load_db.py and src/load_mongo.py and assert that each SQL
  query and its pipeline return the same rows

Each test uses its own scratch Postgres schema and Mongo database and drops it.
"""
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from src import mongo_queries
from src.schema import LineItem, ReceiptResult, RunResult

ROOT = Path(__file__).resolve().parent.parent
CI_FIXTURE = ROOT / "tests" / "fixtures" / "ci_results_quick.json"
MONGO_URL = os.getenv("MONGO_URL")
PG_URL = os.getenv("DATABASE_URL")


def _synthetic_run():
    # covers what the CORD fixture lacks: dates in two months, line items,
    # a review case with no reasons, an invalid receipt and a repeated merchant
    return RunResult(run_id="syn", model="cached:x", prompt_version="extract_v1",
                     ocr_engine="tesseract-5", extractor_version="1", receipts=[
        ReceiptResult(id="a", merchant="Shop A", date="2018-12-25", total="11.20",
                      line_items=[LineItem(description="x", quantity=2, unit_price="3.50", amount="7.00"),
                                  LineItem(description="y", quantity=1, amount="4.20")]),
        ReceiptResult(id="b", merchant="shop a.", date="2018-12-02", total="5.05"),
        ReceiptResult(id="c", merchant="Kedai B", date="2019-01-03", total="100.00",
                      needs_review=True),
        ReceiptResult(id="d", merchant=None, date=None, total="1.00", needs_review=True,
                      review_reasons=["missing_merchant", "missing_date"]),
        ReceiptResult(id="e", valid=False, needs_review=True, review_reasons=["image_not_found"],
                      error="image not found"),
    ])


def test_sql_query_extraction():
    q1 = mongo_queries.sql_query(1)
    assert q1.startswith("SELECT m.merchant_name") and q1.rstrip().endswith("LIMIT 10;")
    for spec in mongo_queries.PIPELINES.values():
        assert mongo_queries.sql_query(spec["sql_query"]).startswith("SELECT")
    with pytest.raises(KeyError):
        mongo_queries.sql_query(99)


def test_compare_reports_rows_and_order_separately():
    rows = [("x", 2), ("y", 2)]
    res = mongo_queries.compare("review_reasons", rows, list(reversed(rows)))
    assert res["identical_rows"] and not res["identical_order"]
    assert not mongo_queries.compare("review_reasons", rows, rows[:1])["identical_rows"]


def test_sql_merchant_name_maps_to_norm():
    rows = mongo_queries.rows_from_sql("top_merchants_by_spend",
                                       [("Shop A", 2, Decimal("16.25")), ("(unknown)", 1, Decimal("1.00"))])
    assert rows == [("shop a", 2, Decimal("16.25")), ("(unknown)", 1, Decimal("1"))]


@pytest.fixture
def mongo_coll():
    if not MONGO_URL:
        pytest.skip("MONGO_URL not set")
    pytest.importorskip("pymongo")
    from pymongo.errors import PyMongoError

    from src import load_mongo
    client, coll = load_mongo.connect(MONGO_URL, db_name=f"test_{uuid.uuid4().hex[:8]}",
                                      timeout_ms=3000)
    try:
        client.admin.command("ping")
    except PyMongoError as e:
        client.close()
        pytest.skip(f"MongoDB not reachable: {e}")
    load_mongo.ensure_indexes(coll)
    yield coll
    client.drop_database(coll.database.name)
    client.close()


@pytest.fixture
def pg_conn():
    if not PG_URL:
        pytest.skip("DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    from src import load_db
    try:
        c = psycopg.connect(PG_URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable: {e}")
    schema = f"test_{uuid.uuid4().hex[:8]}"
    c.execute(f"CREATE SCHEMA {schema}")
    c.execute(f"SET search_path TO {schema}")
    load_db.apply_schema(c)
    yield c
    c.execute(f"DROP SCHEMA {schema} CASCADE")
    c.close()


def test_pipelines_on_synthetic_run(mongo_coll):
    from src import load_mongo
    load_mongo.load_run(mongo_coll, _synthetic_run())
    top = mongo_queries.rows_from_mongo(
        "top_merchants_by_spend", mongo_queries.run_pipeline(mongo_coll, "top_merchants_by_spend"))
    assert top == [("kedai b", 1, Decimal("100")), ("shop a", 2, Decimal("16.25")),
                   ("(unknown)", 1, Decimal("1"))]
    months = mongo_queries.rows_from_mongo(
        "spend_by_month", mongo_queries.run_pipeline(mongo_coll, "spend_by_month"))
    assert months == [("(unknown)", 1, Decimal("1")), ("2018-12", 2, Decimal("16.25")),
                      ("2019-01", 1, Decimal("100"))]
    share = mongo_queries.rows_from_mongo(
        "review_share_by_run", mongo_queries.run_pipeline(mongo_coll, "review_share_by_run"))
    assert share == [("syn", "cached:x", "tesseract-5", 5, 3, Decimal("60"))]
    reasons = dict(mongo_queries.rows_from_mongo(
        "review_reasons", mongo_queries.run_pipeline(mongo_coll, "review_reasons")))
    assert reasons == {"unspecified": 1, "missing_merchant": 1, "missing_date": 1,
                       "image_not_found": 1}


def test_pct_rounds_half_up(mongo_coll):
    # 1 of 16 = 6.25 %: Postgres ROUND(numeric, 1) gives 6.3, Mongo $round would give 6.2
    from src import load_mongo
    run = RunResult(run_id="r16", model="m", prompt_version="p", receipts=[
        ReceiptResult(id=str(i), needs_review=(i == 0)) for i in range(16)])
    load_mongo.load_run(mongo_coll, run)
    (row,) = mongo_queries.run_pipeline(mongo_coll, "review_share_by_run")
    assert mongo_queries._d(row["pct_needs_review"]) == Decimal("6.3")


def _assert_parity(pg_conn, mongo_coll, runs):
    from src import load_db, load_mongo
    for _ in range(2):  # the second load must change nothing
        for run in runs:
            load_db.load_run(pg_conn, run)
            load_mongo.load_run(mongo_coll, run)
    n = sum(len(r.receipts) for r in runs)
    assert pg_conn.execute("SELECT COUNT(*) FROM dim_receipt").fetchone()[0] == n
    assert mongo_coll.count_documents({}) == n
    results = mongo_queries.parity(pg_conn, mongo_coll)
    assert [r["query"] for r in results] == list(mongo_queries.PIPELINES)
    for r in results:
        assert r["identical_rows"], r


def test_parity_ci_fixture(pg_conn, mongo_coll):
    _assert_parity(pg_conn, mongo_coll,
                   [RunResult.model_validate_json(CI_FIXTURE.read_text(encoding="utf-8"))])


def test_parity_synthetic_and_fixture(pg_conn, mongo_coll):
    _assert_parity(pg_conn, mongo_coll,
                   [_synthetic_run(),
                    RunResult.model_validate_json(CI_FIXTURE.read_text(encoding="utf-8"))])
