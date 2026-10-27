-- Analytical queries over the receipt star schema.
-- Spend is measured on the receipt total (one value per receipt) and only on
-- receipts that are valid (rows with an unknown total are excluded).
-- Note: CORD totals are in IDR, SROIE in MYR, so do not sum across sources in
-- a real report without a currency column.

-- 1. Top merchants by spend
SELECT m.merchant_name,
       COUNT(*)                AS receipts,
       SUM(r.total_amount)     AS total_spend
FROM dim_receipt r
JOIN dim_merchant m USING (merchant_key)
WHERE r.valid AND r.total_amount IS NOT NULL
GROUP BY m.merchant_name
ORDER BY total_spend DESC
LIMIT 10;

-- 2. Spend by month (unknown dates grouped separately)
SELECT COALESCE(TO_CHAR(d.full_date, 'YYYY-MM'), '(unknown)') AS month,
       COUNT(*)                                             AS receipts,
       SUM(r.total_amount)                                  AS total_spend
FROM dim_receipt r
JOIN dim_date d USING (date_key)
WHERE r.valid AND r.total_amount IS NOT NULL
GROUP BY 1
ORDER BY 1;

-- 3. Average basket: value per receipt and number of line items per receipt
SELECT ROUND(AVG(r.total_amount), 2)                    AS avg_basket_value,
       ROUND(AVG(items.n_items), 2)                     AS avg_items_per_receipt,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r.total_amount) AS median_basket_value
FROM dim_receipt r
JOIN (SELECT receipt_key, COUNT(*) FILTER (WHERE line_no > 0) AS n_items
      FROM fact_receipt_item GROUP BY receipt_key) items USING (receipt_key)
WHERE r.valid AND r.total_amount IS NOT NULL;

-- 4. Window function: rank merchants by spend within each month
SELECT month, merchant_name, spend,
       RANK() OVER (PARTITION BY month ORDER BY spend DESC) AS rank_in_month
FROM (
    SELECT COALESCE(TO_CHAR(d.full_date, 'YYYY-MM'), '(unknown)') AS month,
           m.merchant_name,
           SUM(r.total_amount) AS spend
    FROM dim_receipt r
    JOIN dim_merchant m USING (merchant_key)
    JOIN dim_date d USING (date_key)
    WHERE r.valid AND r.total_amount IS NOT NULL
    GROUP BY 1, 2
) s
ORDER BY month, rank_in_month;

-- 5. Share of receipts needing review, per pipeline run
SELECT r.run_id, r.model, r.ocr_engine,
       COUNT(*)                                                  AS receipts,
       COUNT(*) FILTER (WHERE r.needs_review)                   AS needs_review,
       ROUND(100.0 * COUNT(*) FILTER (WHERE r.needs_review) / COUNT(*), 1) AS pct_needs_review
FROM dim_receipt r
GROUP BY r.run_id, r.model, r.ocr_engine
ORDER BY r.run_id;

-- 6. Most common review reasons in the open queue
SELECT reason, COUNT(*) AS n
FROM review_queue q, UNNEST(q.reasons) AS reason
WHERE q.status = 'open'
GROUP BY reason
ORDER BY n DESC;

-- 7. Running total of spend by date (window SUM)
SELECT d.full_date,
       SUM(r.total_amount)                                   AS day_spend,
       SUM(SUM(r.total_amount)) OVER (ORDER BY d.full_date)  AS running_spend
FROM dim_receipt r
JOIN dim_date d USING (date_key)
WHERE r.valid AND r.total_amount IS NOT NULL AND d.full_date IS NOT NULL
GROUP BY d.full_date
ORDER BY d.full_date;
