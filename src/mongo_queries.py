"""MongoDB aggregation pipelines that mirror queries in sql/queries.sql.

Each entry in PIPELINES has the number of the SQL query it mirrors (the
`-- N.` heading in sql/queries.sql) and a pipeline over the `receipts`
collection written by src/load_mongo.py. The semantics match the SQL:
- spend uses the receipt total, only on valid receipts with a known total
- receipts with no merchant group under "(unknown)" and those with no date
  under month "(unknown)", as dim_merchant / dim_date do

Differences, by design:
- query 1 groups by merchant_norm. The SQL groups by dim_merchant.merchant_name,
  which is the first raw name seen for that merchant_norm (one name per norm),
  so the parity check compares on normalize_merchant(merchant_name).
- the rounding in query 5 is done half-up on Decimal128 to match Postgres
  ROUND(numeric). Mongo's $round rounds half to even.

compare() turns SQL rows and pipeline output into the same plain rows, so
scripts/mongo_parity.py and tests/test_mongo_parity.py can assert equality.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from src.normalize import normalize_merchant

ROOT = Path(__file__).resolve().parent.parent
QUERIES_SQL = ROOT / "sql" / "queries.sql"

_SPEND_MATCH = {"$match": {"valid": True, "total": {"$ne": None}}}

PIPELINES: dict[str, dict] = {
    "top_merchants_by_spend": {
        "sql_query": 1,
        "pipeline": [
            _SPEND_MATCH,
            {"$group": {"_id": "$merchant_norm",
                        "receipts": {"$sum": 1},
                        "total_spend": {"$sum": "$total"}}},
            {"$sort": {"total_spend": -1, "_id": 1}},
            {"$limit": 10},
            {"$project": {"_id": 0, "merchant_norm": "$_id", "receipts": 1, "total_spend": 1}},
        ],
    },
    "spend_by_month": {
        "sql_query": 2,
        "pipeline": [
            _SPEND_MATCH,
            {"$group": {"_id": {"$ifNull": [
                            {"$dateToString": {"format": "%Y-%m", "date": "$date"}},
                            "(unknown)"]},
                        "receipts": {"$sum": 1},
                        "total_spend": {"$sum": "$total"}}},
            {"$sort": {"_id": 1}},
            {"$project": {"_id": 0, "month": "$_id", "receipts": 1, "total_spend": 1}},
        ],
    },
    "review_share_by_run": {
        "sql_query": 5,
        "pipeline": [
            {"$group": {"_id": {"run_id": "$run_id", "model": "$model",
                                "ocr_engine": "$ocr_engine"},
                        "receipts": {"$sum": 1},
                        "needs_review": {"$sum": {"$cond": ["$needs_review", 1, 0]}}}},
            {"$project": {
                "_id": 0, "run_id": "$_id.run_id", "model": "$_id.model",
                "ocr_engine": "$_id.ocr_engine", "receipts": 1, "needs_review": 1,
                # half-up to 1 dp: trunc(x * 10 + 0.5) / 10, in Decimal128
                "pct_needs_review": {"$divide": [{"$trunc": {"$add": [
                    {"$multiply": [{"$divide": [
                        {"$multiply": [{"$toDecimal": 100}, "$needs_review"]},
                        "$receipts"]}, 10]},
                    {"$toDecimal": "0.5"}]}}, 10]},
            }},
            {"$sort": {"run_id": 1}},
        ],
    },
    "review_reasons": {
        "sql_query": 6,
        "pipeline": [
            {"$match": {"needs_review": True}},
            # load_db.py queues a receipt with no reasons as ['unspecified']
            {"$project": {"reason": {"$cond": [
                {"$gt": [{"$size": {"$ifNull": ["$review_reasons", []]}}, 0]},
                "$review_reasons", ["unspecified"]]}}},
            {"$unwind": "$reason"},
            {"$group": {"_id": "$reason", "n": {"$sum": 1}}},
            {"$sort": {"n": -1, "_id": 1}},
            {"$project": {"_id": 0, "reason": "$_id", "n": 1}},
        ],
    },
}


def sql_query(n: int, path: Path = QUERIES_SQL) -> str:
    """Return the text of query `-- n.` from sql/queries.sql, verbatim."""
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^-- (\d+)\. .*$", text)
    # blocks = [preamble, '1', body1, '2', body2, ...]
    for num, body in zip(blocks[1::2], blocks[2::2]):
        if int(num) == n:
            return body.strip()
    raise KeyError(f"query {n} not found in {path}")


def run_pipeline(coll, name: str) -> list[dict]:
    return list(coll.aggregate(PIPELINES[name]["pipeline"]))


def _d(v):
    """Decimal128 / Decimal / int -> Decimal (numeric equality, scale ignored)."""
    if v is None:
        return None
    if hasattr(v, "to_decimal"):
        v = v.to_decimal()
    return Decimal(v).normalize() if not isinstance(v, bool) else v


def _norm_sql_merchant(name: str) -> str:
    return "(unknown)" if name == "(unknown)" else (normalize_merchant(name) or "(unknown)")


def rows_from_sql(name: str, cur_rows: list[tuple]) -> list[tuple]:
    if name == "top_merchants_by_spend":
        return [(_norm_sql_merchant(m), int(n), _d(s)) for m, n, s in cur_rows]
    if name == "spend_by_month":
        return [(m, int(n), _d(s)) for m, n, s in cur_rows]
    if name == "review_share_by_run":
        return [(r, mo, o, int(n), int(nr), _d(p)) for r, mo, o, n, nr, p in cur_rows]
    if name == "review_reasons":
        return [(r, int(n)) for r, n in cur_rows]
    raise KeyError(name)


def rows_from_mongo(name: str, docs: list[dict]) -> list[tuple]:
    if name == "top_merchants_by_spend":
        return [(d["merchant_norm"], d["receipts"], _d(d["total_spend"])) for d in docs]
    if name == "spend_by_month":
        return [(d["month"], d["receipts"], _d(d["total_spend"])) for d in docs]
    if name == "review_share_by_run":
        return [(d["run_id"], d["model"], d["ocr_engine"], d["receipts"],
                 d["needs_review"], _d(d["pct_needs_review"])) for d in docs]
    if name == "review_reasons":
        return [(d["reason"], d["n"]) for d in docs]
    raise KeyError(name)


def compare(name: str, sql_rows: list[tuple], mongo_rows: list[tuple]) -> dict:
    """Same rows (as a multiset), plus whether the order matches too.

    Row order is reported separately because several SQL queries order on a
    column with ties (e.g. ORDER BY n DESC), and Postgres text ordering
    depends on the database collation.
    """
    key = lambda r: tuple("" if x is None else str(x) for x in r)  # noqa: E731
    same_rows = sorted(sql_rows, key=key) == sorted(mongo_rows, key=key)
    return {"query": name, "sql_query": PIPELINES[name]["sql_query"],
            "rows": len(sql_rows), "mongo_rows": len(mongo_rows),
            "identical_rows": same_rows, "identical_order": sql_rows == mongo_rows}


def parity(pg_conn, coll) -> list[dict]:
    """Run every SQL query in PIPELINES and its pipeline; compare the results.

    `pg_conn` must already hold the star schema loaded by src/load_db.py (its
    search_path may point at a scratch schema) and `coll` the same runs loaded
    by src/load_mongo.py.
    """
    out = []
    for name, spec in PIPELINES.items():
        sql_rows = rows_from_sql(name, pg_conn.execute(sql_query(spec["sql_query"])).fetchall())
        mongo_rows = rows_from_mongo(name, run_pipeline(coll, name))
        res = compare(name, sql_rows, mongo_rows)
        if not res["identical_rows"]:
            res["sql"] = [list(map(str, r)) for r in sql_rows]
            res["mongo"] = [list(map(str, r)) for r in mongo_rows]
        out.append(res)
    return out
