-- Star schema for extracted receipts (PostgreSQL 16+).
-- Grain of the fact table: one row per receipt line item. Receipts without
-- line items get one synthetic row (line_no = 0) carrying the receipt total,
-- so spend queries still see them.
-- Safe to run more than once.

CREATE TABLE IF NOT EXISTS dim_merchant (
    merchant_key   SERIAL PRIMARY KEY,
    merchant_name  TEXT NOT NULL,           -- as extracted
    merchant_norm  TEXT NOT NULL UNIQUE     -- normalize_merchant(), '(unknown)' if missing
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key     INTEGER PRIMARY KEY,       -- YYYYMMDD, 0 = unknown date
    full_date    DATE,
    year         SMALLINT,
    quarter      SMALLINT,
    month        SMALLINT,
    month_name   TEXT,
    day          SMALLINT,
    day_of_week  SMALLINT,                  -- ISO: 1 = Monday
    is_weekend   BOOLEAN
);

CREATE TABLE IF NOT EXISTS dim_receipt (
    receipt_key        SERIAL PRIMARY KEY,
    receipt_id         TEXT NOT NULL,
    run_id             TEXT NOT NULL,
    model              TEXT,
    prompt_version     TEXT,
    ocr_engine         TEXT,
    extractor_version  TEXT,
    run_created_at     TIMESTAMPTZ,
    merchant_key       INTEGER REFERENCES dim_merchant (merchant_key),
    date_key           INTEGER REFERENCES dim_date (date_key),
    total_amount       NUMERIC(14, 2),
    valid              BOOLEAN NOT NULL,
    needs_review       BOOLEAN NOT NULL,
    error              TEXT,
    UNIQUE (run_id, receipt_id)
);

CREATE TABLE IF NOT EXISTS fact_receipt_item (
    receipt_key   INTEGER NOT NULL REFERENCES dim_receipt (receipt_key) ON DELETE CASCADE,
    line_no       INTEGER NOT NULL,          -- 0 = receipt-level row (no line items)
    merchant_key  INTEGER NOT NULL REFERENCES dim_merchant (merchant_key),
    date_key      INTEGER NOT NULL REFERENCES dim_date (date_key),
    description   TEXT,
    quantity      NUMERIC(12, 3),
    unit_price    NUMERIC(14, 2),
    amount        NUMERIC(14, 2),
    receipt_total NUMERIC(14, 2),            -- degenerate copy for easy aggregation
    PRIMARY KEY (receipt_key, line_no)
);

CREATE TABLE IF NOT EXISTS review_queue (
    review_id    SERIAL PRIMARY KEY,
    receipt_key  INTEGER NOT NULL UNIQUE REFERENCES dim_receipt (receipt_key) ON DELETE CASCADE,
    reasons      TEXT[] NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'rejected')),
    queued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ,
    notes        TEXT
);

CREATE INDEX IF NOT EXISTS ix_fact_date ON fact_receipt_item (date_key);
CREATE INDEX IF NOT EXISTS ix_fact_merchant ON fact_receipt_item (merchant_key);
CREATE INDEX IF NOT EXISTS ix_review_status ON review_queue (status);

INSERT INTO dim_date (date_key) VALUES (0) ON CONFLICT DO NOTHING;
INSERT INTO dim_merchant (merchant_name, merchant_norm) VALUES ('(unknown)', '(unknown)')
ON CONFLICT DO NOTHING;
