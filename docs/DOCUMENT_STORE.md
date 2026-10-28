# Document store (MongoDB)

An optional second store sits next to the Postgres star schema. `src/load_mongo.py` takes the same `results/<run_id>.json` file as `src/load_db.py` and writes **one document per receipt** to the `receipts` collection, with its line items embedded:

```text
{receipt_id, run_id, model, prompt_version, ocr_engine, extractor_version, run_created_at,
 merchant, merchant_norm, date, total, valid, needs_review, review_reasons, error,
 line_items: [{line_no, description, quantity, unit_price, amount}]}
```

- **Types:** `date` is a BSON date (UTC midnight; null when no valid date was extracted). Money is `Decimal128` taken from the 2-dp strings, so sums are exact and match Postgres `NUMERIC`. `merchant_norm` uses `src/normalize.py`, with `"(unknown)"` for a missing merchant, as in `dim_merchant`.
- **Indexes:** unique `uq_receipt_run` on `(receipt_id, run_id)`, plus `ix_merchant_norm` and `ix_date`.
- **Idempotent:** each receipt is upserted on `(receipt_id, run_id)` with `$set` in one `bulk_write` of `UpdateOne` operations. Loading the CI fixture twice gave 20 inserted, then 0 inserted / 20 matched / 0 modified, still 20 documents.
- **Queries:** `src/mongo_queries.py` mirrors `sql/queries.sql` queries 1 (top merchants by spend), 2 (spend by month), 5 (review share per run) and 6 (review reasons). `scripts/mongo_parity.py` loads the same files into a scratch Postgres schema and a scratch Mongo database, runs each SQL query verbatim next to its pipeline, and fails on any difference. CI runs it on `tests/fixtures/ci_results_quick.json`. Locally I also ran it on 8 CORD and synthetic results files, 204 receipts in total, and all 4 queries returned identical rows.
- **SQL-only:** queries 3 (a percentile), 4 (a window function) and 7 (a running total) stay in Postgres.

**Why.** A receipt is naturally one nested document, so storing it whole gives one indexed lookup per receipt with no joins, which suits a review screen or an API serving one receipt at a time. Extractors and prompt versions also emit slightly different fields, and a document takes a new field without a migration.

**Trade-offs.** Run metadata and merchant names are duplicated in every document. Cross-entity questions are weaker without joins, and two stores have to be kept in sync, which is why the parity check exists. The star schema stays the place for analytics; Mongo is the per-receipt read model. "One indexed read per receipt" is a design reason, not a measured speed-up: I haven't measured latency.

## How to run it

```bash
pip install -e ".[mongo]"
docker compose up -d mongo            # mongo:7, local demo credentials, volume mongodata
export MONGO_URL='mongodb://receipts:receipts@localhost:27017/receipts?authSource=admin'
python -m src.load_mongo results/<run_id>.json      # re-running upserts, never duplicates

# Parity with Postgres (needs DATABASE_URL too); exits 1 on any difference
python scripts/mongo_parity.py tests/fixtures/ci_results_quick.json
```

On my build machine, compose started `mongo:7` (7.0.45) with port 27017 published to the host, and the loader, tests and parity script connected through that mapping. The Postgres side of the local parity runs was a native PostgreSQL 17, not the compose `db` service. CI uses `postgres:16` and `mongo:7` service containers. No hosted database was used.
