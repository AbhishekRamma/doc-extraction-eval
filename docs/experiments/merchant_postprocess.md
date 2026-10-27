# Experiment: deterministic merchant post-processing (pp-merchant-v1)

Branch `exp/merchant-postprocess`. `src/postprocess.py` runs after extraction, on the merchant only.

## Status: optional, off by default (decided 2026-10-24)

**Verdict.** Dev: +3 to +5 pp merchant, 0 broken. SROIE test 200: +0.5 to +1.5 pp, 1–2 broken per extractor, and every CI crosses 0 (details below). Golden Ace decided to keep pp-merchant-v1 **only as an optional, separately reported score**.

**What "off by default" means:**
- The pipeline never applies it. `src/pipeline.py` and `src/extract.py` don't import it, and results files keep the raw merchant.
- `src/evaluate.py` always computes a secondary `merchant_pp` metric, the same pattern as `merchant_fuzzy`. It post-processes a copy of each merchant prediction and then compares exactly.
- In the report it appears as the row `merchant (post-proc) … [optional pp-merchant-v1, not in overall]`, saved under `metrics.merchant_pp`. `--compare` shows it as its own row; older reports show `n/a`.
- It never changes:
  - `per_field.merchant`, overall (micro) or receipt exact;
  - the failure list, which shows the raw prediction;
  - the CI gate. `tests/test_regression.py` reads `metrics.overall` on the quick split against `min_overall_accuracy` (0.84 when this was written; now 0.94), and `eval.yml` scores the raw `tests/fixtures/ci_results_quick.json` (16/19 with rules-v1 then; now 19/19 with rules-v2).
- `tests/test_merchant_pp_metric.py` checks all of this. It also checks that the strict metrics are identical when the post-processor is replaced by anything.
- `scripts/postprocess_results.py` is still there as an explicit opt-in. It writes a separate `+pp-merchant-v1` results file for paired comparisons and never overwrites the raw file.
- Re-scoring the saved dev outputs with the new metric reproduces the dev table below:
  - rules-v1: 50 → 53;
  - v1: 69 → 73;
  - v2: 80 → 85.

`scripts/postprocess_results.py` writes a separate post-processed results file. It keeps the raw value in `merchant_raw` and lists the rules that fired. Its version is `<extractor>+pp-merchant-v1`.

The raw results and raw scores are never overwritten: the post-processed score is reported next to them as its own row. `scripts/paired_eval.py` (identical to the copy on `exp/rules-total-picker`) computes the statistics.

## Rules

1. **Legal-suffix OCR confusions**, applied only to the suffix at the end of the name (an optional bracketed registration number may follow):
   - `SON/SIN/S0N/5DN… BHD` and `SDN 8HD/BH0…` → `SDN BHD`
   - a suffix glued to the previous word → split off (`…TRADINGSDN BHD`, `SDNBHD`)
   - `S/D`, `5/B`, `S/8` → `S/B`
2. **Joined-word re-split.** A 7+ letter token that isn't a dictionary word is split when it segments fully into 2–3 words from a small generic shop/company dictionary. At most one common OCR confusion (l/i, 1/i, 0/o, 5/s, 8/b) is allowed. The dictionary was written by hand and not taken from gold labels.

## Results: SROIE dev (100 receipts), merchant, exact match after normalisation

These are free replays of cached outputs: rules-v1 was re-run from the OCR cache, and DeepSeek v1/v2 come from the saved results files. No API calls were made.

CIs: receipt-level percentile bootstrap, 10,000 resamples, seed 0. Fixed = raw wrong → post-processed right; broken = the reverse. p = exact two-sided McNemar.

| Extractor | Raw | Post-processed | Δ (95% CI) | fixed / broken | p |
|---|---|---|---|---|---|
| rules-v1 | 50/100 (50%) [40, 60] | 53/100 (53%) [43, 63] | +3 pp [0, +7] | 3 / 0 | 0.25 |
| deepseek-chat + extract_v1 | 69/100 (69%) [60, 78] | 73/100 (73%) [64, 81] | +4 pp [+1, +8] | 4 / 0 | 0.12 |
| deepseek-chat + extract_v2 | 80/100 (80%) [72, 88] | 85/100 (85%) [78, 92] | +5 pp [+1, +9] | 5 / 0 | 0.062 |

Overall (merchant + date + total, 299 fields), raw → post-processed:
- rules-v1: 190 → 193
- v1: 259 → 263
- v2: 276 → 281

Date and total are untouched by construction.

## Reading the numbers

- **The hypothesis was too optimistic.** It expected about 13 of v2's 20 merchant misses to be suffix or joined-word near-misses. Only 5 were. Most of the rest have the wrong entity (a brand or outlet name instead of the legal company, or extra or missing words), or OCR damage outside the suffix, and a safe deterministic rule can't repair those.
- **Nothing broke.** No receipt went from right to wrong on any of the three extractors. On CORD quick, the rules-v1 merchant predictions were left completely unchanged (CORD merchant isn't gold-scored, so this only checks for false fires).
- **Rules supported by a single dev receipt** (flagged as overfitting risks):
  - the joined-word re-split fired on one receipt;
  - the glued-suffix split fired on one receipt (rules-v1 and DeepSeek v1 only);
  - `S/D → S/B` fired on one receipt and helped only DeepSeek v2. For the other two extractors, the same receipt still misses because of an unstripped registration number.
  - Only `SON/SIN BHD → SDN BHD` is backed by several receipts (3).
- **Tuning on dev.** The rules were chosen after reading dev failures. The general form limits overfitting but doesn't remove it, so these gains are an upper bound.
- **Held-out confirmation:** done once on SROIE test 200 (next section). The gain didn't hold up.

## SROIE test 200: one-off confirmation (frozen test split, `--final`)

- **Setup.** Run once, at 10:16:32 (America/Toronto), on code at `526ada9` (committed 10:11:07, after the 09:57:30 test freeze). No rules changed afterwards, no test failure lists were inspected, and SROIE-derived example strings were later replaced with synthetic ones (`78bee71`).
- **Inputs.**
  - The post-processing was applied to the rules-v1 and rules-v2 test predictions (cached docTR OCR).
  - It was also applied to the existing DeepSeek v1/v2 test predictions, which are cached replays (no API calls).
- **Scoring.** `scripts/heldout_stats.py --final` (bootstrap 10,000 resamples, seed 20261024). The merchant difference CIs come from `paired_eval.paired_stats` on the same files (seed 0).

| Extractor | Merchant raw | Merchant post-processed | Δ (95% CI) | fixed / broken | exact McNemar p | Overall raw → pp |
|---|---|---|---|---|---|---|
| rules-v1 | 117/200 (58.5%) [51.5, 65.0] | 120/200 (60.0%) [53.0, 67.0] | +1.5 pp [−0.5, +4.0] | 4 / 1 | 0.38 | 405 → 408 /599 |
| rules-v2 | 117/200 [51.5, 65.0] | 120/200 [53.0, 67.0] | +1.5 pp [−0.5, +4.0] | 4 / 1 | 0.38 | 470 → 473 /599 |
| deepseek v1 | 154/200 (77.0%) [71.0, 83.0] | 157/200 (78.5%) [72.5, 84.0] | +1.5 pp [−1.0, +4.0] | 5 / 2 | 0.45 | 539 → 542 /599 |
| deepseek v2 | 174/200 (87.0%) [82.0, 91.5] | 175/200 (87.5%) [83.0, 92.0] | +0.5 pp [−1.5, +3.0] | 3 / 2 | 1.0 | 561 → 562 /599 |

**Verdict.** The dev gain mostly doesn't hold:
- On test, every extractor has a net gain of only 1–3 receipts. Every difference CI crosses 0.
- Unlike dev (0 broken), the rules now break receipts: 1–2 per extractor.
- On v2 the effect is +0.5 pp (3 fixed, 2 broken), compared with +5 pp on dev.

The suffix and re-split rules are safe enough to keep as an optional, separately reported score. They aren't a reliable improvement.
