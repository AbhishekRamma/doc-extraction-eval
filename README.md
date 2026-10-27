# doc-extraction-eval

**Pulls the merchant, date and total out of scanned receipts (OCR, then a rules-based or LLM extractor), validates every output with Pydantic, and scores each run field by field against a labelled golden set. A regression test fails the build if accuracy on the quick split drops below a threshold set from a real run.**

### Headline results (all measured on Oct 24, 2026)

| Golden set (split, n) | Fields scored | OCR | Extractor | Merchant | Date | Total | Overall (micro) |
|---|---|---|---|---|---|---|---|
| SROIE task 3 train, first 100 receipts (local only) | merchant, date, total | docTR 1.1.0 | `deepseek-chat+extract_v1` (DeepSeek API, temp 0, JSON mode) | 69/100 (69%) | 92/100 (92%) | 98/99 (99.0%) | **259/299 (86.6%)** |
| Same 100 SROIE receipts | merchant, date, total | docTR 1.1.0 | `deepseek-chat+extract_v2` ² (tuned on these same 100 receipts) | 80/100 (80%) | 98/100 (98%) | 98/99 (99.0%) | 276/299 (92.3%)² |
| Same 100 SROIE receipts | merchant, date, total | docTR 1.1.0 | `rules-v1` (regex + keywords, free, no LLM) | 50/100 (50%) | 89/100 (89%) | 51/99 (51.5%) | **190/299 (63.5%)** |
| CORD v2 test, `quick` split (20 receipts, 19 with a gold total) | **total only** | docTR 1.1.0 | `rules-v2` (default: rules-v1 merchant/date + a better total picker) | n/a | n/a | 19/19 (100.0%) | **19/19 (100.0%)**: the CI baseline. Caveat: 19/19 on quick (seen during rules-v2 development, so not a held-out estimate; held-out CORD is 69/76 = 90.8%) |
| Same CORD quick split | **total only** | docTR 1.1.0 | `rules-v1` (previous default, still `--provider rules`) | n/a | n/a | 16/19 (84.2%) | 16/19 (84.2%) |

- CORD has no merchant or date labels, so a CORD figure covers the total only. **CORD and SROIE numbers are never combined into one figure.**
- "Overall (micro)" = correct fields / scored fields. A field with no gold value isn't scored (one SROIE receipt and one CORD quick receipt have no gold total).
- Sources: `data/local/sroie/eval_runs/sroie_full_deepseek__full.json`, `data/local/sroie/eval_runs/sroie_full_deepseek_v2__full.json`, `data/local/sroie/eval_runs/rescored_2026-10-24_232b916/sroie_all_doctr_rules__full.json`, `eval_runs/quick_rules_doctr__quick.json` (rules-v1, 16/19), and the committed CI fixture `tests/fixtures/ci_results_quick.json` (rules-v2, 19/19; threshold in `eval_config.json`).
- All three SROIE rows are scored with the same evaluator and normalizer (including the registration-number strip from `a0b8914`). The rules run was re-scored for this, and its original pre-normalization score is in the journal.
- ² `extract_v2` was written after reading extract_v1's failures on these same 100 receipts, and there's no held-out set, so its gain over v1 is optimistic. v1 is the bold headline.

---

## The problem

Teams that want LLMs to read invoices and receipts usually can't tell whether a prompt, model or OCR change made extraction better or worse. This project treats extraction like tested software. It runs a fixed golden set, scores each field separately, saves every run with the OCR engine, extractor and prompt version that produced it, and runs a regression check in CI.

## Architecture

```mermaid
flowchart LR
    A[Receipt image] --> B["ocr.py<br/>docTR default, Tesseract fallback<br/>(EasyOCR, PaddleOCR optional)<br/>text cached per engine"]
    B --> C["extract.py providers<br/>rules-v2 (default) | rules-v1 | deepseek | cached replay<br/>ollama / openai / anthropic: implemented, not measured"]
    C --> D{"schema.py<br/>Pydantic + normalize.py"}
    D -- "invalid JSON / schema<br/>(retry once)" --> C
    D -- "still invalid" --> X["valid=false, error kept<br/>needs_review: invalid_output"]
    D -- valid --> E{"review_reasons<br/>total or date missing/unparseable,<br/>or total guessed"}
    E --> R["results/&lt;run_id&gt;.json<br/>(pipeline.py: OCR engine, extractor,<br/>prompt version)"]
    X --> R
    C -. "raw LLM responses" .-> K[(LLM response cache)]
    K -. replay .-> C
    R --> V["evaluate.py<br/>vs labels.json"] --> W["eval_runs/*.json"]
    R --> L["load_db.py"] --> P[("PostgreSQL star schema<br/>+ review_queue table<br/>(analytics, 7 queries)")]
    R --> LM["load_mongo.py (optional)"] --> M[("MongoDB receipts collection<br/>one document per receipt,<br/>line items embedded")]
    P -. "mongo_parity.py<br/>queries 1, 2, 5, 6 must match" .- M
    F["tests/fixtures/ci_results_quick.json<br/>+ eval_config.json (0.94)"] --> T["test_regression.py<br/>(GitHub Actions eval.yml)"]
```

## What's in it

- **Stack:** Python 3.11, Pydantic, docTR / Tesseract OCR (EasyOCR and PaddleOCR optional), PostgreSQL 16 (star schema), MongoDB 7 through PyMongo (optional document store; local Docker and CI service containers only, no Atlas), Docker Compose, GitHub Actions and pytest. DeepSeek is the only paid LLM I measured, on local SROIE only.
- **OCR (`src/ocr.py`):** four engines behind one function, and OCR text is cached so each image is read only once. `--ocr-engine auto` uses docTR if it's installed and falls back to Tesseract otherwise. docTR was chosen from a measured comparison (see Results).
- **Extraction (`src/extract.py`):** providers are `rules-v2` (the free default), `rules` (the frozen rules-v1, kept so earlier numbers reproduce), `deepseek` (paid, OpenAI-compatible API, `temperature=0`, JSON mode, key read only from the environment or `.env`), `cached` (replays saved raw LLM responses with no network; it refuses a cache made with a different prompt version), `ollama`, `openai` and `anthropic`. Prompts live in `src/prompts/extract_v1.txt` and `extract_v2.txt`.
- **Validation (`src/schema.py`):** Pydantic models whose validators call the shared normalizers. Invalid JSON or a failed validation is retried once. After that the receipt is stored as `valid=false` with the error.
- **Review rule (`review_reasons` in `src/extract.py`):** a receipt is flagged `needs_review` if the total is missing or unparseable, if the total was guessed without a total keyword, if the date is missing or unparseable, or if the output was invalid. The reasons are recorded and loaded into a `review_queue` table.
- **Evaluation (`src/evaluate.py`):** exact match after normalization (`src/normalize.py`, shared by the extractor and the evaluator):
  - dates → `YYYY-MM-DD`, read day-first (month-first only when day-first is impossible)
  - totals → a 2-decimal string with currency stripped and separators resolved (`60.000` → `60000.00`, `1,234.50` → `1234.50`)
  - merchant → casefolded, punctuation stripped, whitespace collapsed, and a trailing company registration number like `(123456-X)` removed
  - A secondary `merchant (fuzzy>=0.90)` score (difflib ratio) is reported separately. It's never part of the overall score, the receipt-exact score or the CI threshold.
  - An optional `merchant (post-proc)` score (`merchant_pp`) is reported the same way. It's an exact match after the pp-merchant-v1 merchant post-processing and is also never part of overall, receipt exact or the CI threshold; the pipeline doesn't apply it. See `docs/experiments/merchant_postprocess.md`.
  - Each run is saved to `eval_runs/` with its metadata. `--compare A B` diffs two runs.
- **Regression gate:** `tests/test_regression.py` fails if overall accuracy on the CORD quick split is below `min_overall_accuracy: 0.94` in `eval_config.json`. CI scores a saved results file from a real rules-extractor run (docTR 1.1.0 + `rules-v2`, committed as `tests/fixtures/ci_results_quick.json`, 19/19 = 100.0%). 0.94 allows one wrong total (18/19 = 94.7%) and fails at two (17/19 = 89.5%); it isn't rounded down to 1.00 because that would be too brittle for 19 receipts, and the CORD holdout estimate for rules-v2 is 90.8%. The first gate was 0.84, from the first real run (docTR + `rules-v1`, 16/19 = 84.2%, rounded down). It doesn't use cached LLM outputs and makes no API calls.
  - **Shown to work:** PR #1 in my private development archive deliberately made the rules extractor pick SUBTOTAL over TOTAL and regenerated the fixture with a real run (commit `671d91f`). According to its GitHub eval run, the check failed at overall 0.7368 (14/19) against the 0.84 threshold in force then. The PR was closed unmerged. Running pytest locally on `671d91f` gives the same result: `test_regression` fails at 0.7368, and the unit test `test_rules_total` also catches the bug (2 failed, 205 passed, 2 skipped).
  - **Status on main:** in my private development archive, both workflows were green at `232b916` and at `305973f`, the MongoDB merge. I checked those run pages on Oct 27, 2026. This public repo starts from a single commit of that tree; both workflows passed on that commit, `b6bf3ae` ([ci](https://github.com/AbhishekRamma/doc-extraction-eval/actions/runs/40589867368): 305 passed, 2 skipped, `PARITY OK`; [eval](https://github.com/AbhishekRamma/doc-extraction-eval/actions/runs/40589867458): 19/19).
- **Database (`sql/`, `src/load_db.py`):** a PostgreSQL star schema (`fact_receipt_item`, `dim_merchant`, `dim_date`, `dim_receipt`) plus `review_queue`, an idempotent loader (`INSERT ... ON CONFLICT`), and 7 queries in `sql/queries.sql`: top merchants, spend by month, average basket, rank within month (window function), review share per run, review reasons, and a running total.
- **Document store (`src/load_mongo.py`, `src/mongo_queries.py`):** optional MongoDB loader (one `receipts` collection, one document per receipt with line items embedded, `Decimal128` money, idempotent bulk upserts) and 4 aggregation pipelines checked against `sql/queries.sql` by `scripts/mongo_parity.py`. Queries 3, 4 and 7 (percentile, window function, running total) stay SQL-only. See [Document store (MongoDB)](#document-store-mongodb).
- **Packaging and CI:** a Python 3.11 + Tesseract `Dockerfile` and a `docker-compose.yml` with `postgres:16` and an optional `mongo:7` service, both with healthchecks (the app depends only on `db`). Two GitHub Actions workflows: `ci.yml` runs pytest with Postgres 16 and MongoDB 7 service containers and then the Postgres/MongoDB parity check, and `eval.yml` runs the evaluator plus the regression test on the saved rules-extractor results file.
- **Tests:** 308 in total. Offline (no `DATABASE_URL` or `MONGO_URL`) a full `pytest` run gives 297 passed, 11 skipped; the 11 skips are the Postgres and MongoDB tests. In CI on `305973f`, `ci.yml` gave 305 passed, 2 skipped (`openai` and docTR aren't installed there; `test_regression.py` is excluded) and `eval.yml` ran `test_regression.py`: 1 passed.

## How to run

```bash
python3 -m venv .venv && . .venv/bin/activate
cp .env.example .env                         # only needed for --provider deepseek (DEEPSEEK_API_KEY); the default rules-v2 provider needs no key
pip install -e ".[dev]"                      # core + pytest (add ,mongo for PyMongo)
pytest                                       # Postgres/Mongo tests skip unless DATABASE_URL / MONGO_URL are set (offline: 297 passed, 11 skipped)

# Free pipeline on the CORD quick split (rules-v2 extractor, the default), then score it
python -m src.pipeline --split quick                           # writes results/<run_id>.json; --provider rules = frozen rules-v1
python -m src.evaluate results/<run_id>.json --split quick      # per-field table + failure list

# Optional: docTR OCR (the measured default); CPU torch wheels keep it smaller
pip install -e ".[ocr-doctr]" --extra-index-url https://download.pytorch.org/whl/cpu

# Compare OCR engines (each engine can live in its own venv)
python scripts/compare_ocr.py --engines tesseract easyocr --python easyocr=/path/to/easyocr-venv/bin/python

# Database: load a results file, then run the queries
DATABASE_URL=postgresql://user:pass@host:5432/db python -m src.load_db results/<run_id>.json

# MongoDB document store (optional): see "Document store (MongoDB)" below
MONGO_URL=mongodb://user:pass@host:27017/receipts?authSource=admin python -m src.load_mongo results/<run_id>.json

# Docker: Postgres + app (cached provider on synthetic fixtures, then rules on the quick split, then DB load).
# Plain `up` also starts the optional mongo service; the app doesn't use it (`docker compose up db app` skips it).
docker compose up
docker compose exec db psql -U receipts -d receipts -f /sql/queries.sql
```

SROIE (local only, never committed):

```bash
python scripts/fetch_sroie.py                                   # -> data/local/sroie/ (gitignored)
# Paid run: needs pip install -e ".[llm]" and DEEPSEEK_API_KEY set in .env (from .env.example) or the environment
python -m src.pipeline --labels data/local/sroie/labels.json --split full --provider deepseek \
    --prompt-version v1 --llm-cache-dir <dir> --out-dir data/local/sroie/results
# Free, offline replay of a saved run from its raw-response cache
python -m src.pipeline --labels data/local/sroie/labels.json --split full --provider cached \
    --cached-dir <dir> --prompt-version v1 --ocr-engine doctr --ocr-cache-dir data/local/sroie/ocr_cache
python -m src.evaluate data/local/sroie/results/<run_id>.json --labels data/local/sroie/labels.json \
    --out-dir data/local/sroie/eval_runs

# Frozen held-out test set (sroie_100-299): fetch once, verify hashes, score only with --final
python scripts/fetch_sroie.py --test            # writes labels_test.json + test_manifest.json; refuses if already frozen
python scripts/fetch_sroie.py --verify-test
python -m src.evaluate <results.json> --labels data/local/sroie/labels_test.json --split test --final
python scripts/heldout_stats.py --labels data/local/sroie/labels_test.json --split test --final \
    --run v1=<v1 results> --run v2=<v2 results> --pair v1 v2   # bootstrap CIs + McNemar
```

Splits: CORD `quick` (20, the CI gate) and `holdout` (the other 80, never tuned on); SROIE dev `sroie_000`–`099` (`labels.json`, `quick` + `full` rows) and the frozen `test` set `sroie_100`–`299` (`labels_test.json`). `--split full`/`all` means every row of the labels file. Any `test` row is refused by `src/evaluate.py` unless `--final` is passed.

Environment variables: `DATABASE_URL` (DB loader and DB tests), `MONGO_URL` (Mongo loader, Mongo tests and the parity check), `DEEPSEEK_API_KEY` (only for `--provider deepseek`), `OLLAMA_HOST` / `OLLAMA_MODEL` (for `--provider ollama`), `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` (unmeasured adapters, `.[paid-llm]` extra), `DB_PORT` (compose, if 5432 is taken) and `MONGO_PORT` (compose, if 27017 is taken). `.env.example` lists `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (all empty) and example local `DATABASE_URL` and `MONGO_URL` values (local demo credentials only). `.env` is gitignored, and none of the keys are needed for the free rules/cached path.

## Document store (MongoDB)

An optional second store sits next to the Postgres star schema. `src/load_mongo.py` takes the same `results/<run_id>.json` file as `src/load_db.py` and writes **one document per receipt** to the `receipts` collection, with its line items embedded as an array:

```text
{receipt_id, run_id, model, prompt_version, ocr_engine, extractor_version, run_created_at,
 merchant, merchant_norm, date, total, valid, needs_review, review_reasons, error,
 line_items: [{line_no, description, quantity, unit_price, amount}]}
```

- **Types:** `date` is a BSON date (UTC midnight; null when no valid date was extracted). Money (`total`, `unit_price`, `amount`) is `Decimal128` taken from the 2-dp strings, so sums are exact and match Postgres `NUMERIC`. `merchant_norm` uses `src/normalize.py`, with `"(unknown)"` for a missing merchant, as in `dim_merchant`.
- **Indexes:** unique `uq_receipt_run` on `(receipt_id, run_id)`, plus `ix_merchant_norm` and `ix_date`.
- **Idempotent:** each receipt is upserted on `(receipt_id, run_id)` with `$set` in one `bulk_write` of `UpdateOne` operations. Loading the CI fixture twice gave 20 inserted and then 0 inserted / 20 matched / 0 modified, still 20 documents. The CI parity step on `305973f` printed the same counts.
- **Queries:** `src/mongo_queries.py` has aggregation pipelines mirroring `sql/queries.sql` query 1 (top merchants by spend), 2 (spend by month), 5 (review share per run) and 6 (review reasons). `scripts/mongo_parity.py` loads the same files into a scratch Postgres schema and a scratch Mongo database, runs each SQL query verbatim alongside its pipeline, and fails on any difference. CI runs it on `tests/fixtures/ci_results_quick.json` (the archive's run on `305973f`: all 4 queries identical, `PARITY OK`). Locally I also ran it on 8 CORD and synthetic results files, 204 receipts in total, and all 4 queries returned identical rows (details in `docs/PROJECT_JOURNAL.md`).
- **SQL-only:** queries 3 (average and median basket, a percentile), 4 (rank within month, a window function) and 7 (running total) have no pipeline. They stay in Postgres.

**Why I added it.** A receipt is naturally one nested document (header plus line items). Storing it whole gives a single indexed lookup per receipt with no joins, which suits a review UI or an API serving one receipt at a time. Different extractors and prompt versions also emit slightly different fields, and a document store takes a new field without a migration.

**Trade-offs.** The data is denormalized and duplicated: run metadata is copied into every receipt document, and a merchant's name is stored per receipt, not once. Cross-entity questions (merchants across runs, date-dimension attributes such as weekday or quarter) are weaker without joins. Two stores also have to be kept in sync, which is why the parity check exists. The **star schema stays the place for analytics.** Window functions, percentiles and dimension attributes are simpler and better tested in SQL, and Mongo is the per-receipt read model.

**How to run it**

```bash
pip install -e ".[mongo]"
docker compose up -d mongo            # mongo:7, local demo credentials, volume mongodata
export MONGO_URL='mongodb://receipts:receipts@localhost:27017/receipts?authSource=admin'
python -m src.load_mongo results/<run_id>.json      # re-running upserts, never duplicates

# Parity with Postgres (needs DATABASE_URL too); exits 1 on any difference
python scripts/mongo_parity.py tests/fixtures/ci_results_quick.json
```

On my build machine, compose started `mongo:7` (7.0.45) on its default bridge network, with port 27017 published to the host. The loader, the tests and the parity script connected from the host through that mapping; no host networking was needed. Container-to-container access to `mongo` by service name was not tested, since the app container doesn't use Mongo. The Postgres side of the local parity runs was the machine's native PostgreSQL 17 on port 5432, not the compose `db` service. CI uses `postgres:16` and `mongo:7` service containers. Everything ran on local Docker or CI service containers; I didn't use MongoDB Atlas or any hosted database.

**Not yet verified by me**
- The app container reaching Mongo by service name (`mongo:27017`) inside the compose network. Nothing in the app uses Mongo yet, and I only connected from the host.
- Any latency or performance figure. I haven't measured Mongo reads or writes, or compared them with the Postgres joins, so the "one indexed read per receipt" point above is a design reason, not a measured speed-up.

## Results

Every run in `eval_runs/` and `data/local/sroie/eval_runs/`, as saved. All runs are from Oct 24, 2026 (Toronto time).

**CORD v2 test, quick split (20 receipts, 19 with a gold total; total only), extractor `rules-v1`: OCR engine comparison**

| OCR engine | Total correct (rules-v1) | Mean s/image* | Gold total found in OCR text* | Accuracy source |
|---|---|---|---|---|
| PaddleOCR 3.7.0 | 17/19 (89.5%) | 8.803 | 100% | `eval_runs/quick_rules_paddleocr__quick.json` |
| **docTR 1.1.0 (default)** | **16/19 (84.2%)** | **0.454** | **100%** | `eval_runs/quick_rules_doctr__quick.json` |
| Tesseract 5.5.0 | 8/19 (42.1%) | 0.297 | 47.4% | `eval_runs/quick_rules_tesseract__quick.json` |
| EasyOCR 1.7.2 | 7/19 (36.8%) | 1.654 | 42.1% | `eval_runs/quick_rules_easyocr__quick.json` |

\* From `results/ocr_comparison.json` (origin/main `e85a8db`): a fresh run on the same 20 CORD quick images, on an 8-core CPU with no GPU. Timing is the mean seconds per image, excluding model load. Its rules-total counts (17, 16, 8, 7 of 19) match the accuracy column. docTR is one total behind PaddleOCR and about 19× faster (8.803 / 0.454 s), which is why it's the default. The Docker image and `ci.yml` use Tesseract to stay small, so a Docker run of the same split scores like the Tesseract row (`results/docker_rules_quick.json`: 8/19 when re-scored with `src/evaluate.py`).

**SROIE task 3 train, first 100 receipts (local only; merchant, date, total), OCR docTR 1.1.0**

| Run | Extractor | Prompt | n | Merchant | Date | Total | Overall (micro) | Receipt fully right | Needs review | Merchant fuzzy ≥0.90 (secondary) |
|---|---|---|---|---|---|---|---|---|---|---|
| `sroie_quick_doctr_rules` | rules-v1 | none | 20 (quick) | 5/20 | 20/20 | 9/20 | 34/60 (56.7%) | 1/20 | 5/20 | not recorded |
| `sroie_all_doctr_rules` ¹ | rules-v1 | none | 100 | 50/100 | 89/100 | 51/99 | 190/299 (63.5%) | 16/100 | 21/100 | 61/100 |
| `sroie_quick_deepseek` | deepseek-chat | extract_v1 | 20 (quick) | 16/20 | 20/20 | 19/20 | 55/60 (91.7%) | 15/20 | 0/20 | not recorded |
| `sroie_full_deepseek` | deepseek-chat | extract_v1 | 100 | 69/100 | 92/100 | 98/99 | 259/299 (86.6%) | 64/100 | 1/100 | 80/100 |
| `sroie_full_deepseek_v2` ² | deepseek-chat | extract_v2 | 100 | 80/100 | 98/100 | 98/99 | 276/299 (92.3%) | 78/100 | 1/100 | 93/100 |

- Every SROIE run returned schema-valid output for all receipts (`valid_rate` 100%). The saved raw DeepSeek responses show no receipt needed the retry.
- Token usage saved in the response cache: 69,468 total tokens for the 100-receipt extract_v1 run and 90,751 for extract_v2. Latency and cost weren't recorded.
- ¹ Re-scored with the current normalizer: `data/local/sroie/eval_runs/rescored_2026-10-24_232b916/sroie_all_doctr_rules__full.json`. The quick-split rows were scored before the registration-number strip was added.
- ² **extract_v2 was written after reading extract_v1's failures on these same 100 receipts** (day-first dates, prefer the legal entity name, drop the registration number), so the in-sample +17-field gain (259 → 276) is optimistic. See the held-out results below.

**Held-out results** (scored once with `--final`; 95% CI = percentile bootstrap over receipts, 10,000 resamples, seed 20261024, `scripts/heldout_stats.py`)

SROIE frozen test, `sroie_100`–`299` (200 receipts, local only; 199 dates scored), OCR docTR 1.1.0:

| Extractor | Merchant | Date | Total | Overall (micro) | Receipt fully right |
|---|---|---|---|---|---|
| rules-v1 | 58.5% [51.5, 65.0] | 96.5% [93.9, 99.0] | 48.0% [41.0, 55.0] | 405/599 **67.6%** [64.5, 70.7] | 47/200 |
| rules-v2 (default) | 58.5% [51.5, 65.0] | 96.5% [93.9, 99.0] | 80.5% [75.0, 85.5] | 470/599 **78.5%** [75.2, 81.7] | 97/200 |
| deepseek extract_v1 | 77.0% [71.0, 83.0] | 98.0% [96.0, 99.5] | 95.0% [92.0, 98.0] | 539/599 **90.0%** [87.6, 92.3] | 145/200 |
| deepseek extract_v2 | 87.0% [82.0, 91.5] | 99.5% [98.5, 100.0] | 94.5% [91.0, 97.5] | 561/599 **93.7%** [91.5, 95.7] | 166/200 |

- **v1 → v2 held up, but smaller:** +22 fields (+3.7 points, 95% CI [+1.7, +5.5]) vs +5.7 points in-sample. Paired McNemar over the 599 field checks: 30 fixed vs 8 broken, exact p = 0.00047, almost all from merchant (27 vs 7). Date (3 vs 0) and total (0 vs 1) are not significant.
- Both DeepSeek runs: 200/200 valid, no retries. Tokens: 138,276 (v1) + 181,138 (v2) = 319,414, about $0.06, estimated from token counts at DeepSeek's Flash off-peak list price (not a billed figure).
- **Serving model changed.** The repo requests `deepseek-chat`, which no longer appears in DeepSeek's `/models` list. All 400 test responses were served by `deepseek-flash` (V4.1 Flash). The in-sample dev runs didn't record the serving model, so whether the in-sample and held-out numbers come from the same backend is uncertain. The LLM cache now records it per attempt (`response_model`).
- **DeepSeek totals dropped on test:** about 95% (95.0% v1, 94.5% v2) vs 99% in-sample (98/99). This wasn't investigated, to avoid tuning on test.
- CORD holdout (80 non-quick receipts, 76 with a gold total), rules-v1 + docTR: **64/76 = 84.2%**, 95% CI [75.6, 92.1] (`results/heldout/`, committed).
- **Follow-up experiments, now merged** (scored once on the same test set; free, from cached OCR/responses):
  - **rules-v2 total picker** (from `exp/rules-total-picker`, now the default extractor):
    - SROIE test total field 48.0% → 80.5%, +32.5 points [+25.0, +40.0] (73 fixed vs 8 broken, McNemar p = 3.0e-14), and overall (micro) 67.6% → 78.5%, +10.9 points [+8.3, +13.3]. Merchant and date are unchanged (rules-v2 reuses rules-v1 for them).
    - CORD holdout total 84.2% → 90.8% (64 → 69 of 76), +6.6 points [−1.3, +14.5], p = 0.18. Not significant.
  - **pp-merchant-v1 merchant post-processing** (from `exp/merchant-postprocess`): +0.5 to +1.5 points merchant on test, and every CI crosses 0. It's kept only as an optional, separately reported score, never part of overall or the CI gate.
  - rules-v2 was tuned on SROIE dev, and its last code and fixture edits came 1–2 minutes after the test freeze; no test failure lists were read (see the journal).

## Failure analysis

**CORD quick, docTR + rules-v1 (3 of 19 totals wrong, from `eval_runs/quick_rules_doctr__quick.json`)**
- A total keyword matched but the wrong amount was taken from it (1): `cord_test_002`, gold `28000.00`, predicted `1.00` (not flagged, since the total wasn't guessed).
- No total keyword found, and the guess merged a stray digit into the amount (1): `cord_test_012`, gold `120000.00`, predicted `8120000.00` (flagged `total_guessed_no_keyword`).
- No total keyword found, and the guess was a zero-value line (1): `cord_test_018`, gold `156000.00`, predicted `0.00` (flagged `total_guessed_no_keyword`).
- With Tesseract and EasyOCR, most failures were OCR problems: Tesseract returned no total at all on 7 of its 11 misses. Of EasyOCR's 12 misses, 5 were tiny values (e.g. `31.00` for `31000.00` on `cord_test_005`), consistent with the thousands part being lost, and 4 were `0.00`.
- `needs_review` is 20/20 on CORD because CORD has no dates, so the "date missing" rule always fires. On CORD it measures the label gap, not extraction quality.

**SROIE, 100 receipts.** Patterns only. Per-receipt SROIE values aren't republished because its licence is unverified. The counts come from my own grouping of the failure lists, all scored with the current normalizer (rules-v1: `data/local/sroie/eval_runs/rescored_2026-10-24_232b916/`).
- **Rules-v1, totals (48 of 99 wrong):** the most common pattern (19) is a value about 6% below the gold total, which looks like the pre-tax subtotal being picked on receipts with Malaysian GST. 13 were higher than gold (possibly cash tendered or another amount; not checked one by one), 12 were lower for other reasons, 3 were `0.00`, and 1 was off by a rounding adjustment.
- **Rules-v1, merchant:** the rules take a line near the top, which often isn't the registered company name. Failures split between a substring of the right name, e.g. a missing branch or suffix (19); a different name entirely, e.g. a brand or logo line instead of the legal entity (19); and OCR near-misses that the fuzzy score would accept (11).
- **Rules-v1, dates:** 9 missing, 2 wrong.
- **DeepSeek extract_v1:** totals were nearly solved (98/99; the one miss is a rounding adjustment). Remaining errors: merchant (31), split into partial names (12), OCR near-misses (11), different names such as brand vs legal entity (6) and others (2). Dates (8): 4 had day and month swapped, 3 were otherwise wrong, 1 missing.
- **DeepSeek extract_v2:** the date swaps are gone (2 date errors left). 13 of the 20 remaining merchant errors are OCR-level near-misses (≥0.90 similar), 5 are partial names, and 1 is a different name. What's left is mostly OCR noise in the name, not the extractor choosing the wrong line.

## What I'd improve

1. **Keep the frozen SROIE test set untouched**: tune only on dev (`sroie_000`–`099`) and score `test` with `--final` only for a final comparison. Re-score and save the rules-v1 SROIE dev run with the current normalizer so all dev rows use the same scorer.
2. ~~**Rules extractor totals:** prefer the amount after tax and rounding, and skip subtotal, cash and change lines more reliably.~~ Done as `rules-v2` (now the default): SROIE test total 48.0% → 80.5%. The CORD holdout gain (84.2% → 90.8%) isn't significant yet.
3. **Merchant:** many remaining LLM merchant errors are OCR near-misses (11 of 31 for extract_v1, 13 of 20 for extract_v2), so try better OCR on the header region or give the LLM the image crop. Keep strict exact as the headline, since `dim_merchant` joins need exact names.
4. **Make the review rule dataset-aware**, so CORD's missing dates don't flag every receipt, and record OCR and LLM latency and cost per run alongside accuracy.
5. **Measure the local Ollama path** once the model can be downloaded, and run the LLM extractors on CORD totals for a like-for-like comparison with rules-v1.
6. ~~**Raise the CI threshold** when a better CORD run is committed~~ (done: 0.94 with the rules-v2 fixture), and add a small SROIE-like public set with merchant and date labels to CI if a confirmed-licence source is found.
7. **MongoDB document store:** test the app container reaching `mongo` by service name before anything in the app depends on it; measure per-receipt read latency against the Postgres joins before claiming any speed-up; run the parity check in CI on more than the 20-receipt fixture (for example the committed CORD holdout run); and decide whether queries 3, 4 and 7 ever need a pipeline or stay SQL-only.

## Limitations

- **The Docker Compose path was verified only with a host-network workaround** on my build machine. Its firewall dropped container-to-container traffic (my own observation; no file records it), so plain `docker compose up` timed out when the app connected to `db` by service name. A temporary, uncommitted override that ran the app with host networking and `DB_PORT=5433` let the pipeline run, the DB load and all 7 queries run. The committed `docker-compose.yml` is unchanged, and the app-to-db connection by service name is still unverified.
- **The local LLM path (Ollama) is untested and unmeasured.** Ollama installed, but the model download was blocked by the build machine's network. The OpenAI and Anthropic adapters are implemented but unmeasured. The only measured LLM is DeepSeek (`deepseek-chat`), and only on the local SROIE set. Every CORD number, including the CI gate, comes from the rules-based extractors (`rules-v2` for the gate, `rules-v1` for the OCR comparison and older rows).
- **MongoDB is optional and only partly verified.** The loader, pipelines and parity check ran from the host against `mongo:7` (local compose and the CI service container). The app container reaching `mongo` by service name and any latency or performance numbers are not yet verified by me. Queries 3, 4 and 7 are SQL-only.
- **SROIE's licence is unverified** from the official ICDAR RRC site (a third-party mirror says CC BY 4.0). It stays local only (`data/local/` is gitignored), isn't redistributed, and is never used in CI. Anyone reproducing the SROIE numbers has to fetch it themselves.
- The `results/` and `eval_runs/` folders are gitignored. The committed run outputs are the CI fixture `tests/fixtures/ci_results_quick.json` and the CORD holdout run in `results/heldout/`. Other numbers here come from the local run files named above.
- Small samples: 19 scored CORD quick totals, 76 CORD holdout totals, 100 SROIE dev and 200 SROIE test receipts. One receipt moves the CORD quick score by about 5 points; see the confidence intervals above.
- `deepseek-chat` is no longer in DeepSeek's `/models` list. All held-out test responses were served by `deepseek-flash` (V4.1 Flash). The in-sample runs didn't record the serving model, so in-sample vs held-out comparability is uncertain. The cache records it from now on (`response_model`).
- extract_v2 was tuned on the dev receipts; its held-out gain is smaller than in-sample (see Held-out results).
- CI status comes from GitHub Actions run pages, not from a file in the repo. I checked the archive's runs on Oct 27, 2026: the PR #1 eval run failed, and the main runs at `232b916` and `305973f` passed.
- This public repo starts from one commit of the tree at `96d0bfa`. The development history (commits, PRs #1–#3 and their CI runs) lives in my private archive, so commit hashes quoted here and in `docs/` don't resolve in this repo, and those PR and run pages aren't public.

## Data and licences

- **Code:** MIT licence, see [`LICENSE`](LICENSE). It covers my code and docs, not the datasets.
- **`data/golden/`** is the CORD dataset, under CC BY 4.0, not MIT. Attribution and the list of changes are in [`data/golden/ATTRIBUTION.md`](data/golden/ATTRIBUTION.md).
- **CORD v2** (NAVER Clova AI), test split, 100 receipts, CC BY 4.0. Committed under `data/golden/` with attribution and the list of changes in `data/golden/ATTRIBUTION.md`. Only the total is labelled, because the public release removed store name and date.
- **SROIE** (Huang et al. 2019, *ICDAR2019 Competition on Scanned Receipt OCR and Information Extraction*, arXiv:2103.10213). The first 300 task-3 train receipts (keys sorted by name: 100 dev, 200 frozen test) were fetched by `scripts/fetch_sroie.py` from `zzzDavid/ICDAR-2019-SROIE` at commit `27be427`. Local only, as explained above.
- No client or employer documents are used.
