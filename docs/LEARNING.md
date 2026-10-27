# Learning notes

Plain-English notes for each phase: what the idea is, why companies care, what we
built, and which file to read first. Phases 1 and 3 (golden set and evaluation) are
owned by Eval & QA; their notes are kept unchanged in the second half of this file.

## Phase 0: project scaffold
**Concept.** A repeatable project layout: one `pyproject.toml` that lists the
dependencies, a virtual environment, a `.gitignore` that keeps secrets and big
generated files out of git, and a test runner that works from day one.
**Why it matters.** Anyone (a reviewer, CI, you in six months) can clone the repo, run
`pip install -e ".[dev]"` and `pytest`, and get the same result.
**What we built.** `pyproject.toml` with optional extras (`dev`, `data`, one per OCR
engine, `paid-llm`), a README stub, and `.gitignore` covering `.env`, caches and results.
**Read:** `pyproject.toml`.

## Phase 1: golden dataset (Eval & QA)
See "What is a golden dataset?" and "Why CORD" below. **Read:** `data/golden/ATTRIBUTION.md`.

## Phase 2: OCR plus structured extraction
**Concept.** Turn a receipt photo into checked, structured data in two steps. OCR
reads the pixels into plain text, then an extractor turns that text into
`{merchant, date, total, line_items}`. A **schema** (Pydantic) is a contract: every
output is checked against it, so bad data is caught at the edge and not three systems later.
**Why it matters.** Document AI (invoices, receipts, forms, claims) is a common business
use of ML. Real systems never trust the model's output blindly. They validate it, retry
once, and send unsure cases to a human ("needs review") instead of silently storing them.
**What we built.**
- `src/ocr.py`: pluggable OCR engines (Tesseract, docTR, EasyOCR, PaddleOCR) behind one
  function, with a text cache in `data/ocr_cache/<engine>/<id>.txt` so each image is
  read only once.
- `src/normalize.py`: small *pure* functions that turn `"Rp 38.000"` into `38000.00` and
  `"14/03/19"` into `2019-03-14`. They return `None` for bad input, never raise, and are
  idempotent (running one twice gives the same answer as running it once). Tests prove all three.
- `src/schema.py`: `Receipt` / `ReceiptResult` / `RunResult` models. Validators call the
  normalizers, so whatever the extractor returns ends up in one canonical format.
- `src/extract.py`: a provider-agnostic extractor. The default is a rule-based one
  (regex plus keyword priorities: GRAND TOTAL > Total Bayar > TOTAL > Jumlah, skipping
  SUBTOTAL / CASH / CHANGE / KEMBALI lines). It also has a cached-output provider, an
  Ollama adapter and dormant OpenAI/Anthropic adapters. If the output is not valid JSON
  or fails the schema, it retries once. A receipt is flagged `needs_review` if the total
  or date is missing or unparseable, or if the total was only guessed.
- `src/pipeline.py`: runs OCR and extraction over a split and writes `results/<run_id>.json`.
  The file records which OCR engine (with its real version) and which extractor produced it.
  A missing image does not crash the run. It is recorded as `valid=false, error="image not found"`.
**Read:** `src/extract.py`, then `src/pipeline.py`.

## Phase 3: evaluation (Eval & QA)
See "Per-field accuracy" and "Regression tests" below. **Read:** `src/evaluate.py`.

## Phase 4: making it measurable and repeatable
**Concept.** Choose components by measuring them, not by reputation, and keep a frozen
results file so the score can be re-checked for free.
**What we built.** `scripts/compare_ocr.py` runs every installed OCR engine on the same
images. Each engine runs in its own virtual environment, so their heavy dependencies
don't clash. It reports seconds per image and a quality proxy: the share of gold
totals, and of the gold price amounts, that appear anywhere in the OCR text.
`tests/fixtures/ci_results_quick.json` is the untouched output of a real run (default
OCR plus the rules extractor on the quick split), which the eval CI scores.
**Read:** `scripts/compare_ocr.py`.

## Phase 5: SQL analytics (star schema)
**Concept.** A *star schema* puts the measurable facts (receipt totals, line items) in a
central fact table that points to small *dimension* tables (merchant, date, receipt/run).
Analysts can then slice by any dimension with simple joins. *Window functions*
(`RANK() OVER (PARTITION BY month ...)`, running totals) answer "top N per group"
questions without self-joins. An *idempotent upsert* loader can be re-run safely:
loading the same results file twice leaves the same rows.
**Why it matters.** Almost every data/ML role asks for SQL, and extracted data is only
useful once people can query it.
**What we built.** `sql/schema.sql` creates `dim_merchant`, `dim_date`, `dim_receipt`,
`fact_receipt_item` and a `review_queue` (the human-in-the-loop to-do list). `src/load_db.py`
loads results JSON with `INSERT ... ON CONFLICT`. `sql/queries.sql` has 7 example
queries: top merchants, spend by month, average/median basket, rank within month,
% needing review per run (and per OCR engine), review reasons, and a running total.
**Read:** `sql/schema.sql`, then `sql/queries.sql`.

## Phase 7: a document store next to the star schema (MongoDB)
**Concept.** A *document store* keeps each record as one nested document instead of
rows spread over several tables. A receipt is naturally one document: a header plus its
line items. Reading one receipt is one indexed lookup with no joins, and a new field
doesn't need a schema migration. The cost is *denormalization*: run metadata and merchant
names are copied into every document, and cross-entity analytics are harder without joins.
**Why it matters.** Many teams keep a relational store for analytics and a document store
for serving one record at a time. Two stores can drift apart, so you need a check that
they agree.
**What I built.** `src/load_mongo.py` loads the same results JSON as `src/load_db.py` into
one `receipts` collection, one document per receipt, line items embedded as an array.
Money is `Decimal128` (exact, like Postgres `NUMERIC`). Indexes: unique `(receipt_id, run_id)`
plus `ix_merchant_norm` and `ix_date`. Loading is an idempotent bulk upsert: the first load
of the CI fixture inserted 20, a re-run gave 0 inserted / 20 matched / 0 modified.
`src/mongo_queries.py` mirrors SQL queries 1, 2, 5 and 6 (top merchants, spend by month,
review share per run, review reasons) as aggregation pipelines, and `scripts/mongo_parity.py`
runs both sides on the same data and fails on any difference. They matched on 204 CORD and
synthetic receipts locally, and CI runs the check on the 20-receipt fixture. Queries 3, 4
and 7 (percentile, window function, running total) stay SQL-only. It runs on `mongo:7` in
local Docker and as a CI service container, through PyMongo; no Atlas. Not yet verified by
me: the app container reaching `mongo` by service name, and any latency numbers (there are none).
**Read:** `src/load_mongo.py`, then `src/mongo_queries.py` and `scripts/mongo_parity.py`.

## Phase 6: packaging (Docker) and CI
**Concept.** A container packs the app with its system dependencies (here the
Tesseract binary), so it runs the same on any machine. Docker Compose starts the app
together with Postgres and waits until a *healthcheck* says the database is ready.
CI (GitHub Actions) runs the tests on every push, so a broken change is caught
before it is merged.
**What we built.** `Dockerfile` (python:3.11-slim plus tesseract), `docker-compose.yml`
(postgres:16 with a healthcheck, and the schema mounted as an init script), and
`scripts/docker_entrypoint.sh`, which runs the pipeline with the cached and rules
providers and loads the results. Compose also has an optional `mongo:7` service (see
Phase 7); the app doesn't depend on it. `.github/workflows/ci.yml` runs pytest with
Postgres 16 and MongoDB 7 service containers, so the DB tests run too, then the
Postgres/MongoDB parity check. The evaluation regression check lives, unchanged,
in Eval & QA's `.github/workflows/eval.yml`.
**Read:** `docker-compose.yml`.

## Extraction and OCR backends: why everything is free and local
Everything here runs on a laptop CPU with no API keys, for three reasons. Anyone can
reproduce the results, CI costs nothing, and no receipt data leaves the machine.

**OCR engines, measured** (`scripts/compare_ocr.py`, CORD quick split, 20 receipts,
19 with a gold total; CPU only, 8 cores, no GPU). The numbers below come from the
committed `results/ocr_comparison.json` (re-timed with `--no-cache` on 2026-10-24), which is
the source of truth; rerun `scripts/compare_ocr.py --no-cache` to regenerate it. Install
size and time are from the original venv setup and are not in that file:

| engine | version | install (venv size / time) | sec per image | model load | gold total found in text | gold amounts found |
|---|---|---|---|---|---|---|
| tesseract | 5.5.0 | apt package | 0.30 | none | 47% | 38% |
| easyocr | 1.7.2 | 1.37 GB / 27 s | 1.65 | 4.2 s | 42% | 39% |
| doctr | 1.1.0 | 1.46 GB / 29 s | 0.45 | 4.9 s | 100% | 97% |
| paddleocr | 3.7.0 | 1.35 GB / 26 s | 8.8 | 2.3 s | 100% | 98% |

The same 20 receipts through OCR plus the rules extractor, scored by `src/evaluate.py`
(total accuracy; CORD has no merchant or date):
tesseract 8/19 (42.1%), easyocr 7/19 (36.8%), doctr 16/19 (84.2%), paddleocr 17/19 (89.5%).
That is why the default OCR engine (`--ocr-engine auto`) is **docTR**: it is within one
receipt of PaddleOCR and about 20 times faster. Install it with `pip install -e ".[ocr-doctr]"`
(add `--extra-index-url https://download.pytorch.org/whl/cpu` for the small CPU torch).
If docTR is not installed, `auto` falls back to Tesseract. The Docker image and CI use
Tesseract to stay small, so their numbers are lower.
PaddleOCR needs `enable_mkldnn=False` on this CPU (otherwise it fails with a oneDNN error).

**Extractors.** `--provider rules-v2` (the default) and `--provider rules` (the frozen rules-v1) are free, instant and deterministic.
`--provider ollama` talks to a local Ollama server (`OLLAMA_HOST`, `OLLAMA_MODEL`,
default `qwen2.5:1.5b`). It was **not measured**: Ollama installed fine, but the model
download was blocked by the build machine's network, so there is no timing or accuracy
number for it yet. `--provider cached` replays saved LLM outputs (used in tests and Docker).

**Swapping in a paid LLM later.** The OpenAI and Anthropic adapters exist but are
dormant. To use one, run `pip install -e ".[paid-llm]"`, put `OPENAI_API_KEY=...`
(or `ANTHROPIC_API_KEY`) in a `.env` file (gitignored), then run
`python -m src.pipeline --split quick --provider openai`. Every result file records
the provider, model, prompt version and OCR engine, so you can compare it fairly with
the free runs using the same evaluator.

---

# Learning notes: the evaluation side

## What is a golden dataset?
A golden dataset is a small set of inputs where we already know the right answer,
checked by humans. Every time the pipeline changes (new prompt, new model, new OCR
engine) we run it on the same golden inputs and compare its answers with the known
ones. Because the inputs never change, any change in the score is caused by our change.
Ours is `data/golden/labels.json`: 100 receipts, with 20 marked `quick` for cheap runs
(and the CI gate) and the other 80 marked `holdout`. Nothing is tuned on `holdout`; it is
the CORD held-out set for totals. `--split full` (alias `all`) still means every row.

## Dev, held-out and frozen test splits
If you change a prompt after reading its failures on some receipts, the score on those
same receipts goes up partly because you fitted them. That's "in-sample" and is
optimistic. The fix is a set you never look at while tuning:
- SROIE `sroie_000`–`099` (`data/local/sroie/labels.json`) are the permanent dev set: read
  failures and tune here.
- SROIE `sroie_100`–`299` (`data/local/sroie/labels_test.json`, split `test`) are a frozen
  test set. `data/local/sroie/test_manifest.json` stores the sha256 of every image and
  label row, the source commit and the freeze date; `scripts/fetch_sroie.py --verify-test`
  re-checks them. `src/evaluate.py` refuses to score any `test` row unless `--final` is
  passed (`src/splits.py`), so scoring it is a deliberate, one-off act. The labels live in
  a separate file so `--split full` on the dev file can never touch them.
- Uncertainty: with 200 receipts, one field is 0.17 points. `scripts/heldout_stats.py`
  gives bootstrap 95% intervals (resampling whole receipts, because the three fields of a
  receipt share one OCR text and are not independent) and McNemar's test for two
  extractors on the same fields (it only looks at the fields where they disagree).

## Why CORD, and what the licences allow
CORD v2 (NAVER Clova, official Hugging Face copy `naver-clova-ix/cord-v2`) is released
under CC BY 4.0, which lets us use and redistribute it as long as we credit the source
(see `data/golden/ATTRIBUTION.md`). The catch: NAVER removed the store information from
the public release, so CORD has receipt **totals** but **no merchant name and no date**.
SROIE has merchant, date and total, which would suit this project better, but we could
not confirm its licence from a primary source, so it is never committed. It is now used
as a second, local-only golden set (see "SROIE: a local-only second golden set" below).

## Per-field accuracy, and why null gold isn't scored
Instead of one "right/wrong" per receipt, we score each field separately:
accuracy(total) = receipts with the right total / receipts that have a gold total.
This tells us *which* part of the pipeline is weak. If the gold value is null (CORD has
no merchant/date; 5 CORD receipts have no total), we have nothing to compare against, so
that field is skipped for that receipt and the report shows how many were actually
scored ("scored n"). Counting those as right or wrong would make the number meaningless.
A receipt the pipeline failed to return at all counts as wrong on every scored field.

## Normalisation
The same amount can be written many ways. Indonesian receipts use a dot or comma as the
thousands separator, so `60.000` means sixty thousand rupiah, the same as `60000`.
Before comparing, both the gold value and the prediction go through the same functions
in `src/normalize.py` (`60.000` -> `60000.00`, `14/03/2019` -> `2019-03-14`, merchant
names lower-cased with punctuation removed). Without this, the score would punish the
model for formatting, not for being wrong. Using one shared function for both sides
means the two can never drift apart.

## Regression tests, and why CI uses cached outputs
A regression test checks that something that used to work still works. Ours
(`tests/test_regression.py`) fails if overall accuracy on the quick split drops below a
threshold. In CI we don't call the LLM: we score a results file saved from a real run
(`tests/fixtures/ci_results_quick.json`). That keeps builds free, fast and repeatable;
the same commit always gives the same score. When the prompt or model changes, we re-run
the pipeline locally, commit the new results file, and CI tells us if accuracy dropped.

## Why the threshold comes from the first real run
A threshold picked out of the air is either too strict (the build fails for no reason)
or too loose (real drops slip through). So `eval_config.json` starts with
`min_overall_accuracy: null`, and the regression test skips with a clear message until
we have a real measured score. After the first real run we set the threshold at (or a
little below) that measured number and record which run it came from (`set_from_run`).

## SROIE: a local-only second golden set
SROIE (Huang et al. 2019, *ICDAR2019 Competition on Scanned Receipt OCR and Information
Extraction*, arXiv:2103.10213) has labels for company (merchant), date and total on
Malaysian receipts, so it lets us score all three fields. Its licence is reportedly
CC BY 4.0, but that comes from a third-party mirror and we could not verify it on the
official ICDAR RRC site. So we do not redistribute it: `scripts/fetch_sroie.py` downloads
100 receipts into `data/local/sroie/` (gitignored), and nothing derived from it per receipt
(labels, results, eval reports) goes into git. CI never uses it.

## Why two separate golden sets
CORD can only score **totals** (no merchant/date labels); SROIE scores all three fields.
If we mixed them into one file, the "overall" number would silently change meaning
depending on which receipts were in it, and the CI regression threshold (set on CORD's
quick split) would no longer be comparable from run to run. Keeping them as separate
labels files (`data/golden/labels.json` and `data/local/sroie/labels.json`, chosen with
`--labels`) means each accuracy number is about one clearly defined set.

## Strict vs fuzzy merchant match (exact stays the headline)
Strict exact match marks `SUNRISE MINI RNARKET` (an OCR typo, `rn` read for `m`) as wrong against `SUNRISE MINI
MARKET`, even though a human would call it right. So next to the strict number we also
report a secondary `merchant (fuzzy>=0.90)`: the two normalised names count as a match
when Python's `difflib.SequenceMatcher` ratio is at least 0.90 (that typo scores 0.923).
It still rejects a genuinely different name, e.g. the brand `SUNBEAN` vs the legal name
`NORTHWIND TRADERS SDN BHD` (0.12). The gap between the two numbers tells us how much of the
merchant error is small spelling noise versus picking the wrong name. Strict exact
remains the headline: it is the number in "overall (micro)", "receipt exact" and the CI
threshold, because downstream systems (e.g. joining to `dim_merchant`) need exact names
and a fuzzy threshold is a judgement call that could hide real mistakes.
