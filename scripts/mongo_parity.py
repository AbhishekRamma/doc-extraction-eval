"""Parity check: the same results file in Postgres and MongoDB gives the same answers.

  DATABASE_URL=postgresql://... MONGO_URL=mongodb://... \
      python scripts/mongo_parity.py [results.json ...]

The default input is tests/fixtures/ci_results_quick.json. The script loads
the files into a scratch Postgres schema (via src/load_db.py) and a scratch
Mongo database (via src/load_mongo.py). It runs each SQL query from
sql/queries.sql listed in src/mongo_queries.PIPELINES, plus the matching
aggregation pipeline, and compares the rows. It loads every file twice to show
that both loaders are idempotent, then drops both scratch stores (unless you
pass --keep). Exit code 1 if any query differs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import load_db, load_mongo, mongo_queries  # noqa: E402
from src.schema import RunResult  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("results", type=Path, nargs="*",
                   default=[ROOT / "tests" / "fixtures" / "ci_results_quick.json"])
    p.add_argument("--database-url", default=None)
    p.add_argument("--mongo-url", default=None)
    p.add_argument("--keep", action="store_true", help="keep the scratch schema / database")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    p.add_argument("--quiet-names", action="store_true",
                   help="print counts only, never row contents (use for local-only data)")
    a = p.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    pg_url = a.database_url or os.getenv("DATABASE_URL")
    mongo_url = a.mongo_url or os.getenv("MONGO_URL")
    if not pg_url or not mongo_url:
        print("DATABASE_URL and MONGO_URL must both be set", file=sys.stderr)
        return 2

    import psycopg

    tag = f"parity_{uuid.uuid4().hex[:8]}"
    runs = [RunResult.model_validate_json(f.read_text(encoding="utf-8")) for f in a.results]
    report = {"scratch": tag, "files": [f.name for f in a.results], "loads": []}
    client, coll = load_mongo.connect(mongo_url, db_name=tag)
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {tag}")
        try:
            conn.execute(f"SET search_path TO {tag}")
            load_db.apply_schema(conn)
            load_mongo.ensure_indexes(coll)
            for attempt in (1, 2):
                for f, run in zip(a.results, runs):
                    pg = load_db.load_run(conn, run)
                    mg = load_mongo.load_run(coll, run)
                    report["loads"].append({"pass": attempt, "file": f.name, "postgres": pg,
                                            "mongo": mg})
            report["postgres_dim_receipt_rows"] = conn.execute(
                "SELECT COUNT(*) FROM dim_receipt").fetchone()[0]
            report["mongo_documents"] = coll.count_documents({})
            report["queries"] = mongo_queries.parity(conn, coll)
        finally:
            if not a.keep:
                conn.execute(f"DROP SCHEMA {tag} CASCADE")
                client.drop_database(tag)
            client.close()

    ok = all(q["identical_rows"] for q in report["queries"])
    if a.quiet_names:
        for q in report["queries"]:
            q.pop("sql", None)
            q.pop("mongo", None)
    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        for l in report["loads"]:
            m = l["mongo"]
            print(f"pass {l['pass']} {l['file']}: postgres {l['postgres']['receipts']} receipts / "
                  f"{l['postgres']['items']} fact rows; mongo {m['upserted']} inserted, "
                  f"{m['matched']} matched, {m['modified']} modified")
        print(f"postgres dim_receipt rows: {report['postgres_dim_receipt_rows']}, "
              f"mongo documents: {report['mongo_documents']}")
        for q in report["queries"]:
            print(f"query {q['sql_query']} {q['query']}: {q['rows']} SQL rows, "
                  f"{q['mongo_rows']} Mongo rows, identical rows={q['identical_rows']}, "
                  f"identical order={q['identical_order']}")
    print("PARITY OK" if ok else "PARITY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
