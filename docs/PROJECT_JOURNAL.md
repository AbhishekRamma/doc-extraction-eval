# Project journal: doc-extraction-eval

Build journal from `git log` (all commit times are America/Toronto, UTC-4, as recorded in the commits), `docs/LEARNING.md`, the run files in `results/`, `eval_runs/` and `data/local/sroie/eval_runs/`, the GitHub Actions logs, and the code. Commit hashes and PR numbers refer to my development history, which lives in a private archive; this public repo starts from a single commit of the tree at `96d0bfa`, so they don't resolve here. The core build happened on Oct 24, 2026; the MongoDB document store was added on Oct 27, 2026. Facts that no file backs are marked as my own observation or attributed ("per Eval & QA"), and claims nobody has confirmed are marked `[UNVERIFIED: ...]`.

## Timeline (commits)

| Time (Toronto) | Commit | What |
|---|---|---|
| 06:55 | `0586792` | Phase 0 scaffold: venv, `.gitignore`, `pyproject.toml`, placeholder test |
| 06:58 | `2de2499` | Phase 2: OCR (Tesseract, pluggable), Pydantic schema, normalisers, rules/cached/Ollama extractors with retry and review flag |
| 07:00 | `560fe90` | Phases 1 + 3: CORD v2 golden set (100 receipts, CC BY 4.0), loader, per-field evaluator, `eval.yml` |
| 07:00 | `d260032` | `--split full` means the whole golden set in both evaluate and pipeline |
| 07:15 | `e14dfc6` | Measured OCR comparison (`scripts/compare_ocr.py`), docTR as `auto` default, real quick-split CI fixture (docTR + rules-v1) |
| 07:15 | `86ab467` | Phase 5: Postgres star schema, idempotent loader, 7 queries, DB tests |
| 07:15 | `8169647` | Phase 6: Dockerfile, compose with postgres:16 healthcheck, `ci.yml` with a Postgres service |
| 07:15 | `e13f32c` | Learning notes for phases 0–6 |
| 07:17 | `b510b5e` | Regression threshold set to 0.84 from the first real quick run (16/19) |
| 08:03 | `be918fd` | SROIE local-only golden set (fetch script pinned to zzzDavid@27be427, first 100 train keys) |
| 08:16 | `2eb7680` | DeepSeek provider (OpenAI-compatible, JSON mode, temp 0), key guard, raw-response cache replayable by `cached`, token usage |
| 08:25 | `9f4db98` | Secondary `merchant_fuzzy` metric (difflib ≥ 0.90), outside overall |
| 08:27 | `a0b8914` | Prompt v2 behind `--prompt-version`; merchant normaliser strips trailing `(123456-X)` reg no |
| 08:34 | `671d91f` | On branch `demo/worse-extractor` (not main): throwaway demo that makes the rules extractor prefer SUBTOTAL, to show the regression gate failing. Opened as PR #1 (`refs/pull/1/head` on origin = `671d91f`) and closed unmerged; the branch is now deleted locally and on origin |
| 08:36 | `232b916` | Add `.env.example` (empty API key placeholders, example local `DATABASE_URL`); `.env` stays gitignored |
| 08:42 | `8f32289` | (Eval & QA) Replace SROIE-derived merchant names in eval docs/tests with synthetic examples |
| 08:48 | `589f23d` | Replace SROIE-derived merchant names and reg numbers in normalize tests/comments with synthetic ones |
| 08:48 | `e85a8db` | `compare_ocr` writes `results/ocr_comparison.json` (per-engine timing, text-quality rates, rules total accuracy); a fresh CORD quick run of 4 engines committed |
| Oct 27, 10:29 | `765316d` | (branch `feat/mongo-store`) MongoDB document store: per-receipt loader, aggregation pipelines for queries 1, 2, 5, 6, Postgres parity check, `mongo:7` compose service, Mongo CI service container |
| Oct 27, 10:36 | `305973f` | Merge of PR #2 (`feat/mongo-store`) into main |

Runs (from the `created_at` / `evaluated_at` fields, converted from UTC): OCR comparison runs at 07:05; CI fixture created at 07:06; SROIE rules runs at 08:01 (scored 08:03 and again at 08:25); SROIE DeepSeek quick at 08:17; DeepSeek extract_v1 full created at 08:21 and scored at 08:28; extract_v2 created at 08:28 and scored at 08:30.

## 1. Decisions and why

- **Fresh public repo, private archive.** I built this in a private repo, created private at the first push (`0586792`). On Oct 27, 2026 I chose to publish it. Older commits there contain SROIE-derived strings, so instead of making that repo public I renamed it to a private archive and started this public repo from a single commit of the cleaned tree at `96d0bfa`.
- **Local OCR over paid APIs.** All four OCR engines run locally on CPU. `docs/LEARNING.md` ("why everything is free and local") gives three reasons: anyone can reproduce the results, CI costs nothing, and no receipt data leaves the machine.
- **docTR as the default OCR, from a 4-engine comparison** (`e14dfc6`). On the CORD quick split with rules-v1: PaddleOCR 3.7.0 got 17/19, docTR 1.1.0 16/19, Tesseract 5.5.0 8/19, EasyOCR 1.7.2 7/19 (`eval_runs/quick_rules_*__quick.json`). docTR is one total behind PaddleOCR and about 19× faster. Mean s/image from `results/ocr_comparison.json` (`e85a8db`; a fresh run on 20 images, 8-core CPU, no GPU, model load excluded): PaddleOCR 8.803, docTR 0.454, Tesseract 0.297, EasyOCR 1.654. Its rules-total counts match (17/16/8/7 of 19). Gold total found in OCR text: 100% / 100% / 47.4% / 42.1%. The timings saved at decision time weren't kept; this later run backs the numbers, and the older `docs/LEARNING.md` figures aren't used. Tesseract stays as the fallback and as the engine in Docker/CI, to keep images small.
- **Rules-based extraction as the free default** (`2de2499`). Regex plus keyword priority (GRAND TOTAL > Total Bayar > TOTAL > Jumlah, skipping SUBTOTAL/CASH/CHANGE/KEMBALI). Free, instant and deterministic, so CI and Docker need no keys.
- **CORD committed, SROIE kept local.** CORD v2 is CC BY 4.0 and committed with `data/golden/ATTRIBUTION.md` (`560fe90`). SROIE's licence couldn't be confirmed from the official ICDAR RRC site, so `scripts/fetch_sroie.py` writes to the gitignored `data/local/` and refuses to run if that path isn't ignored (`be918fd`). The two sets have separate labels files, so "overall" always means one thing.
- **A saved results file in CI (not cached LLM outputs).** CI never calls a model. `eval.yml` scores the committed `tests/fixtures/ci_results_quick.json`, the untouched output of a real docTR + rules-v1 run, so every build gets the same score for free. Strictly, this is a cached *results file* from the rules extractor, not cached LLM responses. Cached raw LLM responses exist too (the `cached` provider, used in tests and Docker with synthetic fixtures, and for offline replay of the SROIE DeepSeek runs), but they aren't part of the CI gate.
- **Threshold 0.84 from the first real run** (`b510b5e`). `eval_config.json`: "measured 16/19 = 84.2% on quick split", rounded down, so one more lost total (15/19 = 78.9%) fails. Before that the threshold was `null` and the test skipped instead of guessing.
- **DeepSeek adapter** (`2eb7680`). Uses the OpenAI-compatible API with JSON mode and temperature 0, a hard guard that refuses to run without `DEEPSEEK_API_KEY`, a key that's never logged, and every attempt cached to disk before parsing, so a paid run can be replayed for free. Measured on SROIE only. Why DeepSeek: I first went with "use best local OCR for now", which led to the free rules default. I then saved a `DEEPSEEK_API_KEY` and (per Eval & QA) approved a paid run. Ollama, the free LLM option, couldn't download its model, and the OpenAI and Anthropic adapters had no key. No cost rationale was recorded; rationale beyond key availability: not recorded.
- **`merchant_fuzzy` metric** (`9f4db98`). Strict exact match counted OCR typos as wrong, so a secondary difflib ≥ 0.90 score is reported next to it but kept out of overall, receipt-exact and the CI threshold, because `dim_merchant` joins need exact names (`docs/LEARNING.md`).
- **Prompt v2** (`a0b8914`). Written after extract_v1's SROIE failures: day-first dates ("never swap day and month"), copy the printed year, prefer the legal entity name (SDN BHD etc.), and drop reg numbers, tax IDs and addresses. Same JSON schema, selected with `--prompt-version`. `extractor_version` records the prompt, and a cached replay refuses a prompt mismatch. Result on the same 100 SROIE receipts: 259/299 → 276/299. Effect of the reg-no normaliser alone (same v1 outputs, re-scored): before it, `data/local/sroie/eval_runs/prenorm_9f4db98/sroie_full_deepseek__full.json` gives merchant 67/100, fuzzy 78, overall 257/299 (86.0%), receipt exact 62. After it, merchant is 69/100 and overall 259/299 (86.6%). For rules-v1, the original pre-normalisation score was 182/299 (60.9%, merchant 42/100). Re-scored at `data/local/sroie/eval_runs/rescored_2026-10-24_232b916/sroie_all_doctr_rules__full.json` it's 190/299 (63.5%, merchant 50/100). Rules vs DeepSeek is compared only under the same normaliser. That gain is optimistic, because v2 was tuned on those receipts and there's no held-out set.

## 2. Challenges

1. **CORD has no merchant or date labels.** NAVER removed `store_info` from the public release (`data/golden/ATTRIBUTION.md`), so CORD can only score totals, and the review rule flags every CORD receipt for a missing date (needs_review 20/20 on every CORD run).
2. **SROIE's licence is unclear.** It's reportedly CC BY 4.0 per a third-party mirror, but unverified at the official source (`data/local/sroie/SOURCE.txt`, `docs/LEARNING.md`).
3. **Docker container networking on the build machine.** My build machine's firewall dropped container-to-container traffic, so plain `docker compose up` timed out when the app connected to `db` by service name. No file or commit records this. The files that show Docker ran are `results/docker_cached_fixtures.json` and `results/docker_rules_quick.json`, written by root at 07:12.
4. **Ollama model download blocked.** Ollama installed, but the build machine's network blocked the model download, so the `ollama` provider has no timing or accuracy number (`docs/LEARNING.md`).
5. **Malaysian date formats and company reg numbers in merchant names.** With extract_v1, 4 of the 8 SROIE date errors were day/month swaps, and merchant names often carried a trailing `(123456-X)` registration number or a brand instead of the legal name (re-scored failure lists, `data/local/sroie/eval_runs/`).
6. **SROIE strings in committed files.** Some committed docs/tests used merchant names and a registration number taken from SROIE receipts. Eval & QA replaced theirs with synthetic names in `8f32289` (08:42, on origin/main). The remaining ones in `tests/test_normalize.py` and a `src/normalize.py` comment were replaced in `589f23d` (08:48). The tracked tree on origin/main is now clean. I checked every tracked file against the local SROIE merchant labels and found no names. The registration numbers left are synthetic and don't appear in the SROIE data, and `scripts/fetch_sroie.py` only mentions a filename pattern. **Limitation:** older git history still contains the original strings (e.g. in `a0b8914`); see "Before making the repo public".
7. (Also recorded) PaddleOCR needed `enable_mkldnn=False` on this CPU to avoid a oneDNN error (`docs/LEARNING.md`).
8. **rules-v2 fixtures overlapping SROIE OCR (branch `exp/rules-total-picker`).** Some test fixtures and comments were copied from dev SROIE OCR. One rounding fixture matches test receipt `sroie_188`, and it was edited at 09:59:31, after the 09:57:30 test freeze. No test failure lists were inspected, and the overlap looks coincidental but can't be ruled out. All of these were replaced with synthetic values, and the logic and scores are unchanged (see `docs/experiments/rules_v2_total_picker.md`, "Fixture provenance").

## 3. How each was overcome

1. CORD is used for totals only, and SROIE was added as a second, separate golden set (`--labels`) for merchant, date and total. CORD and SROIE are never combined.
2. SROIE stays local: gitignored, fetched by a pinned script, never in CI, and no per-receipt values are published. Only aggregate numbers are reported.
3. This was a test on my build machine only, not a project change. A temporary, uncommitted compose override ran the app with host networking and `DB_PORT=5433`. With it the pipeline ran, the DB loaded and all 7 queries ran. The override wasn't kept, and the committed `docker-compose.yml` is unchanged. [UNVERIFIED: the default app-to-db connection by service name has not been shown to work on any machine.]
4. The Ollama path is documented as unmeasured, and the paid DeepSeek provider was added (with caching, so it can be replayed) to get a measured LLM number.
5. Prompt v2 states day-first dates and the legal-name preference. `normalize_merchant` strips a trailing reg no (with optional CO. REG / COMPANY NO label) for both gold and prediction. Dates were already parsed day-first by `normalize_date`. Effect: dates 92 → 98/100 and merchant 69 → 80/100 (v1 → v2, same receipts).
6. Replaced with synthetic examples (`8f32289`, `589f23d`). The history issue is deferred to the checklist below, because it only matters if the repo goes public.
7. The flag was set in the PaddleOCR worker.

## 4. How it could be improved

- ~~Add a held-out SROIE split before trusting the v2 gain.~~ Done on `exp/heldout-split`, see "Held-out evaluation" below.
- Fix the rules total picker (19 of its 48 SROIE total misses are about 6% below gold, which looks like a pre-GST subtotal). Tried on branch `exp/rules-total-picker` as `rules-v2`; see `docs/experiments/rules_v2_total_picker.md` (dev SROIE total 51/99 → 97/99, tuned on dev; held-out CORD 80 total 64/76 → 69/76, CI on the gain crosses 0).
- Merchant OCR near-misses: tried deterministic post-processing on branch `exp/merchant-postprocess` (`docs/experiments/merchant_postprocess.md`). SROIE dev merchant, raw → post-processed: rules 50 → 53, v1 69 → 73, v2 80 → 85 /100, 0 broken. Held-out confirmation pending.
- Make the review rule dataset-aware, so CORD's missing dates don't flag everything.
- Record latency and cost per run, and measure Ollama once the model is available.
- Commit the Docker networking fix (or document it in the README) so compose works without manual changes.
- Add a CI badge to the README so CI status is visible in the repo itself. Current evidence (in my private archive): the red gate on PR #1: the eval check failed at 0.7368 (14/19) < 0.84 according to its eval run. I reproduced it locally on `671d91f`: `test_regression` fails at 0.7368, and the unit test `test_rules_total` also fails (2 failed, 205 passed, 2 skipped). Main was green at `232b916` and at `305973f` in the archive; I checked those run pages on Oct 27, 2026. `a0b8914` was green too, from my own notes (I didn't recheck that run).
- Re-test `docker compose up` with the committed file on a machine whose firewall allows container-to-container traffic.
- MongoDB: test the app container reaching `mongo` by service name inside the compose network (not yet verified by me), measure per-receipt read latency against the Postgres joins before claiming any speed-up (no latency or performance numbers exist yet), and run the parity check in CI on more than the 20-receipt fixture. Queries 3, 4 and 7 (percentile, window function, running total) stay SQL-only unless a real need comes up.

## Held-out evaluation (branch `exp/heldout-split`, Oct 24 2026, 09:57–10:20 Toronto)

Aggregate numbers only; per-receipt SROIE values stay in `data/local/`.

**Splits.** CORD's 80 non-quick rows are relabelled `full` → `holdout` (CORD held-out set, totals only; `--split full`/`all` still means every row). SROIE `sroie_000`–`099` stay the permanent dev set. `sroie_100`–`299` (zzzDavid@27be427, train keys sorted by name, positions 100–299) were fetched with `scripts/fetch_sroie.py --test` into `data/local/sroie/` and frozen at 2026-10-24T13:57:30Z (09:57 Toronto): `test_manifest.json` holds the source commit, the freeze time and the sha256 of every image and canonical label row; `--verify-test` re-checks them (200 checked, 0 problems). Labels are in a separate `labels_test.json` (split `test`). One test receipt's gold date could not be parsed, so it is null and not scored (199 dates scored). `src/evaluate.py` and `scripts/heldout_stats.py` refuse any `test` row without `--final` (`src/splits.py`, tests in `tests/test_splits.py`). Prompts and rules were not changed after looking at the test set, and the test failure lists were not read.

**Paid runs (approved: exactly extract_v1 and extract_v2 on the 200 test receipts).** Pre-call estimate from OCR text length (test OCR 128,532 chars, mean 643/receipt vs dev 642), using a per-prompt linear fit of dev prompt tokens on OCR chars, adding the worst dev residual to every receipt, +50% completion and +10% retries: ≤323k prompt + 84k completion tokens. That comes to $0.10 at the Flash off-peak list price, $0.20 at Flash peak, and $0.76 if billed at V4-Pro peak with every input token a cache miss, all under the $0.80 cap. The API no longer lists `deepseek-chat` in `/models` (only `deepseek-flash`, `deepseek-v4-pro`). The repo still sends `deepseek-chat`; every response reported `model = deepseek-flash` (now saved per attempt as `response_model`). The in-sample runs at 08:17–08:30 didn't record the serving model, so whether the backend was identical is unverified. Runs: 14:07–14:12 UTC on a Saturday (off-peak). Caches: `data/local/sroie/llm_cache_test_v1/`, `llm_cache_test_v2/`.

| Run | Attempts (retries) | Prompt tokens (cache hit / miss) | Completion | Total | Estimated cost (token counts × Flash off-peak list price; not a billed figure) |
|---|---|---|---|---|---|
| extract_v1 | 200 (0) | 111,922 (25,600 / 86,322) | 26,354 | 138,276 | $0.0288 |
| extract_v2 | 200 (0) | 155,922 (54,144 / 101,778) | 25,216 | 181,138 | $0.0306 |
| **both** | 400 (0) | 267,844 (79,744 / 188,100) | 51,570 | **319,414** | **$0.0594** |

Prices used: per 1M tokens, input cache hit $0.003, cache miss $0.15, output $0.60 (DeepSeek pricing page, Flash, off-peak, read 2026-10-24). The account balance shown by `/user/balance` (2-decimal display) was $3.10 before the runs, $3.07 right after and $3.04 about ten minutes later (billing lags): a $0.06 drop, consistent with the computed $0.0594.

**Results, SROIE frozen test (200 receipts, docTR 1.1.0; 95% CI = percentile bootstrap over receipts, 10,000 resamples, seed 20261024, `scripts/heldout_stats.py`).**

| Extractor | Merchant | Date | Total | Overall (micro) | Receipt exact | Merchant fuzzy |
|---|---|---|---|---|---|---|
| rules-v1 | 117/200 58.5% [51.5, 65.0] | 192/199 96.5% [93.9, 99.0] | 96/200 48.0% [41.0, 55.0] | 405/599 67.6% [64.5, 70.7] | 47/200 | 127/200 |
| deepseek extract_v1 | 154/200 77.0% [71.0, 83.0] | 195/199 98.0% [96.0, 99.5] | 190/200 95.0% [92.0, 98.0] | 539/599 90.0% [87.6, 92.3] | 145/200 | 167/200 |
| deepseek extract_v2 | 174/200 87.0% [82.0, 91.5] | 198/199 99.5% [98.5, 100.0] | 189/200 94.5% [91.0, 97.5] | 561/599 93.7% [91.5, 95.7] | 166/200 | 183/200 |

**CORD holdout (80 receipts, 76 with a gold total), rules-v1 + docTR:** 64/76 = 84.2%, 95% CI [75.6, 92.1] (`results/heldout/`). Same as the quick split's 16/19 (84.2%).

**v1 → v2.** Held-out: +22 fields (539 → 561 of 599, +3.7 pts, bootstrap 95% CI [+1.7, +5.5]). Paired McNemar on the 599 field checks: v2 fixed 30 that v1 missed and broke 8 that v1 had right, exact two-sided p = 0.00047. By field: merchant 27 vs 7 (p = 0.00082), date 3 vs 0 (p = 0.25), total 0 vs 1 (p = 1). In-sample on dev with the same script: +17 fields (259 → 276 of 299, +5.7 pts, CI [+2.7, +9.0]), McNemar 19 vs 2, p = 0.00022. **Read:** v2's gain held up on receipts it was never tuned on, and it is statistically clear, but it is smaller (+3.7 vs +5.7 points), consistent with some in-sample optimism. On held-out data the gain is almost all merchant (legal-name preference); the date fix is too rare to test here (v1 already got 98% of dates). Totals are lower on held-out than in-sample for both prompts (95.0% / 94.5% vs 99.0%); the serving model may also have changed (see above), and test failures were deliberately not inspected.

**Serving model (comparability caveat).** The repo requests `deepseek-chat`, and that alias no longer appears in DeepSeek's `/models` list. Every one of the 400 held-out test attempts (v1 + v2) was answered by `deepseek-flash` (DeepSeek V4.1 Flash), per the `model` field of each response. The earlier in-sample runs (dev caches `llm_cache_full/`, `llm_cache_full_v2/`) didn't record the serving model; all 200 dev attempts have no `response_model`. So it's uncertain whether the in-sample and held-out numbers come from the same backend, and the in-sample vs held-out gap can't be attributed to prompt overfitting alone. `src/extract.py` now saves the serving model per attempt as `response_model` in the cache.

**Test total drop, not investigated.** DeepSeek total accuracy is about 95% on test (95.0% v1, 94.5% v2) vs 99% in-sample (98/99). It wasn't investigated on purpose: reading test failures to explain it would be tuning on test. Possible causes (harder receipts, the serving-model change above, or chance at n = 200) are unverified.

**Follow-up experiments, scored once on SROIE test 200 with `--final` (free, cached OCR/responses; squash-merged to main as `7daaf84`, `85af11c`, `5bad905`).** Timing (America/Toronto): the test set was frozen at 09:57:30. The last rules-v2 code edit was at 09:59:07 (`src/extract.py`) and its fixtures at 09:59:31, both after the freeze; rules-v2 was scored on test at 10:16:27. pp-merchant-v1's last code commit (`526ada9`) was at 10:11:07. No test failure lists were inspected, and the test fixtures were later replaced with synthetic values.
- **rules-v2 total picker** (`exp/rules-total-picker`):
  - SROIE test total: 96/200 = 48.0% → 161/200 = 80.5%, +32.5 pts [+25.0, +40.0], 73 fixed vs 8 broken, McNemar p = 3.0e-14.
  - SROIE test overall: 405 → 470 of 599 (67.6% → 78.5%), +10.9 pts [+8.3, +13.3].
  - CORD holdout total: 64/76 = 84.2% → 69/76 = 90.8%, +6.6 pts [−1.3, +14.5], 7 vs 2, p = 0.18. **Not significant.**
  - **Adopted as the default extractor** (branch `prep/rules-v2-default`). rules-v1 stays selectable as `--provider rules`. The CI fixture was regenerated with a real docTR 1.1.0 + rules-v2 run on the CORD quick split (19/19 = 100.0%, vs 16/19 for rules-v1; caveat: 19/19 on quick (seen during rules-v2 development, so not a held-out estimate; held-out CORD is 69/76 = 90.8%)), and `min_overall_accuracy` went from 0.84 to 0.94: one wrong total (18/19 = 94.7%) passes, two (17/19 = 89.5%) fail. It isn't rounded down to 1.00 because the CORD holdout estimate (90.8%) says 19/19 is partly luck.
- **pp-merchant-v1 merchant post-processing** (`exp/merchant-postprocess`):
  - Test gain: +0.5 to +1.5 pts merchant across extractors; every CI crosses 0, with 1–2 receipts broken per extractor.
  - Dev gain was +3 to +5 pts with 0 broken.
  - Decision: keep it only as an optional, separately reported `merchant_pp` score (like `merchant_fuzzy`), never in overall or the CI gate.

## Document store: MongoDB next to the star schema (branch `feat/mongo-store`, Oct 27 2026, Toronto)

**What I built.** `src/load_mongo.py` loads the same results JSON as `src/load_db.py` into MongoDB as one document per receipt, with line items embedded. Money is `Decimal128`, `date` is a BSON date and `merchant_norm` comes from `normalize_merchant`. It has a unique index on `(receipt_id, run_id)` plus indexes on `merchant_norm` and `date`, and upserts on `(receipt_id, run_id)`. `src/mongo_queries.py` mirrors `sql/queries.sql` queries 1, 2, 5 and 6 as aggregation pipelines. `scripts/mongo_parity.py` loads the same files into both stores and compares the answers. There's a `mongo:7` service in `docker-compose.yml`, a Mongo service container and a parity step in `ci.yml`, and a `mongo` extra in `pyproject.toml`. `eval.yml`, `eval_config.json`, prompts, the golden/frozen sets and all eval numbers are untouched.

**Why.** A receipt is one nested thing, a header plus line items, so storing it whole means one indexed read per receipt and no joins. That's the access pattern a review screen or an API would have. Extractor outputs also vary by provider and prompt version, and a document takes a new field without a migration. I kept the star schema as the analytics store and treat Mongo as a per-receipt read model.

**Trade-offs I accepted.** Run metadata and merchant names are duplicated in every document. Cross-entity queries are weaker: there's no `dim_date`, so weekday/quarter questions need computed fields. Two stores can drift apart, which is the reason for the parity check. Windowed analytics (queries 4 and 7) and the percentile in query 3 stay in SQL. I didn't port them.

**What I observed** (Mongo 7.0.45 from `docker compose up -d mongo`, native PostgreSQL 17 on the build machine):
- **Idempotency:** the CLI on `tests/fixtures/ci_results_quick.json` gave 20 inserted on the first run and 0 inserted / 20 matched / 0 modified on the second, with 20 documents in total. My first version used `ReplaceOne`, and Mongo reported an identical reload as 20 *modified*. I switched to `UpdateOne` with `$set` of the full document so a no-op reload shows as 0 modified.
- **Parity, CORD and synthetic:** I ran 8 results files (the CI fixture, the four quick OCR-engine runs, the 80-receipt CORD holdout run, the Docker rules run and the synthetic cached-provider run). That's 204 receipts, each loaded twice, giving 204 `dim_receipt` rows and 204 documents. All 4 queries returned identical rows. Query 6 came back in a different *order*: its SQL sorts only by `n DESC`, so ties have no fixed order. The check therefore compares rows as a multiset and reports order separately.
- **Parity, SROIE:** I also ran it on the two local SROIE results files (120 receipts), with all 4 queries identical. That ran only in my local Mongo, in counts-only mode, and nothing from it is committed.
- **Rounding:** query 5 rounds with Postgres `ROUND(numeric, 1)`, which rounds half away from zero, while Mongo's `$round` rounds half to even. The pipeline does a half-up round in Decimal128. A test pins 1/16 = 6.25% → 6.3.
- **Query 1 key:** it groups by `dim_merchant.merchant_name`, the first raw name seen for a `merchant_norm`. The pipeline groups by `merchant_norm`, so the check maps SQL names through `normalize_merchant`.

**Testing.** I tried mongomock first. mongomock 4.3 fails on pymongo 4.x `bulk_write(UpdateOne)` (an unexpected `sort` argument), so I dropped it. The pure document-shape tests always run. The collection, pipeline and parity tests use a real Mongo and skip cleanly without `MONGO_URL` / `DATABASE_URL`. Each one creates and drops its own scratch database and schema.

**Networking.** Compose's default bridge network with port 27017 published to the host worked; I didn't need host networking. I only connected from the host. App-container-to-`mongo` by service name is untested, and nothing needs it yet.

**Tests and CI.** The suite has 308 tests. Offline (no `DATABASE_URL` / `MONGO_URL`) it gives 297 passed, 11 skipped, and the 11 skips are exactly the Postgres and Mongo tests. On the merge commit `305973f`, the archive's ci run gave 305 passed, 2 skipped (`openai` and docTR aren't installed in CI; `test_regression.py` runs in `eval.yml` instead), and its parity step loaded the CI fixture twice (20 inserted, then 0 inserted / 20 matched / 0 modified), found queries 1, 2, 5 and 6 identical and printed `PARITY OK`. The eval run scored 19/19 and `test_regression.py` passed. Eval numbers are unchanged by the Mongo work.

**Stack choices.** PyMongo (`pymongo>=4.6`, the `mongo` extra), `mongo:7` in local Docker and as a CI service container. No MongoDB Atlas or other hosted database.

**Not yet verified by me.**
- The app container reaching Mongo by service name inside Docker.
- Any latency or performance numbers. There are none: I haven't timed Mongo reads or writes or compared them with Postgres.

## Gaps to confirm

Resolved since the first draft: repo privacy (now a fresh public repo; see Decisions), the Docker workaround (my own observation), why DeepSeek (key availability; no further rationale recorded), CI status and the red-gate PR (run pages checked Oct 27, 2026), the rules re-score and the pre-normalisation DeepSeek v1 figures (Eval & QA, files cited above), OCR timings (now backed by `results/ocr_comparison.json`), and SROIE strings in the tracked tree (clean as of `589f23d`).

| # | Gap | Who should confirm |
|---|---|---|
| 1 | The default compose networking (app reaching `db` by service name) has not been shown to work on any machine. Re-test with the committed `docker-compose.yml` where container-to-container traffic is allowed | Software DEV |
| 2 | ~~CI status and the PR #1 failure come from GitHub run pages that weren't opened during this write-up~~ **Resolved:** checked on Oct 27, 2026. PR #1's eval run failed; main's ci and eval runs passed at `232b916` and `305973f`. `a0b8914` is from my notes and wasn't rechecked | Software DEV |
| 3 | ~~`eval_runs/ci_results_quick__quick.json` (scratch) holds the demo branch's 14/19~~ **Resolved:** it was regenerated from main and held 16/19 (`evaluated_at` 2026-10-24T12:41:47Z, i.e. 08:41 Toronto), the same as main's committed fixture then. It was overwritten again when the rules-v2 fixture was scored (`evaluated_at` 2026-10-24T17:42:43Z, i.e. 13:42 Toronto), and now holds 19/19 | Eval & QA |
| 4 | ~~Older git history contains SROIE-derived strings~~ **Resolved:** that history stays in the private archive; this public repo starts from one commit of the cleaned tree | Software DEV |
| 5 | The app container reaching `mongo` by service name inside the compose network is not yet verified by me | Software DEV |
| 6 | No latency or performance numbers exist for MongoDB (or for Mongo vs Postgres); none should be quoted until measured | Software DEV |

## Before making the repo public

Done on Oct 27, 2026 by publishing a fresh repo. The checklist as it stood:

- [x] **Git history:** older commits (e.g. `a0b8914`, before `8f32289`/`589f23d`) still contain a few SROIE-derived merchant names and a registration number. Rewrite history (e.g. `git filter-repo`) or start a fresh repo from a clean tree first. PR #1's ref (`671d91f`, `refs/pull/1/head`) also contains them (checked locally), and GitHub PR refs survive a history rewrite, so a fresh repo is the safer option. Done: this repo is a fresh one, and the old repo is a private archive.
- [ ] **Compose networking:** the default app-to-db connection by service name is unverified. Test plain `docker compose up` on a machine without the build machine's firewall, or document the limitation in the README.
- [x] `README.md` is the final, reviewed README (no longer the Phase 0 placeholder; there is no separate `README.final.md`).
- [ ] Re-check that `data/local/` and `PROJECT_BRIEF.md` (personal info) are still gitignored and untracked.
- [x] Confirm the PR/run links in the README make sense to public readers once access changes. The old PR and run links are replaced with a note that the history lives in my private archive.
