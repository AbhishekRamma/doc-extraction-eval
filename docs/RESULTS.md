# Detailed results

Every run in `eval_runs/` and `data/local/sroie/eval_runs/`, as saved. All runs are from Oct 24, 2026 (Toronto time). The headline held-out numbers are in the [README](../README.md#held-out-results); this file holds the development runs, the OCR comparison and the failure analysis.

## OCR engine comparison (CORD quick split, total only, rules-v1)

| OCR engine | Total correct | Mean s/image* | Gold total found in OCR text* | Accuracy source |
|---|---|---|---|---|
| PaddleOCR 3.7.0 | 17/19 (89.5%) | 8.803 | 100% | `eval_runs/quick_rules_paddleocr__quick.json` |
| **docTR 1.1.0 (default)** | **16/19 (84.2%)** | **0.454** | **100%** | `eval_runs/quick_rules_doctr__quick.json` |
| Tesseract 5.5.0 | 8/19 (42.1%) | 0.297 | 47.4% | `eval_runs/quick_rules_tesseract__quick.json` |
| EasyOCR 1.7.2 | 7/19 (36.8%) | 1.654 | 42.1% | `eval_runs/quick_rules_easyocr__quick.json` |

\* From `results/ocr_comparison.json`: a fresh run on the same 20 CORD quick images, on an 8-core CPU with no GPU, model load excluded. Its rules-total counts (17, 16, 8, 7 of 19) match the accuracy column. docTR is one total behind PaddleOCR and about 19× faster, which is why it's the default. The Docker image and `ci.yml` use Tesseract to stay small, so a Docker run of the same split scores like the Tesseract row (`results/docker_rules_quick.json`: 8/19).

## CORD quick split, extractors (total only, docTR 1.1.0)

| Extractor | Total | Source |
|---|---|---|
| `rules-v2` (default) | 19/19 (100.0%) | `tests/fixtures/ci_results_quick.json` (the CI baseline) |
| `rules-v1` | 16/19 (84.2%) | `eval_runs/quick_rules_doctr__quick.json` |

19/19 is not a held-out estimate: the quick split was seen while rules-v2 was developed. Held-out CORD for rules-v2 is 69/76 = 90.8%.

## SROIE dev set (first 100 task-3 train receipts, local only, docTR 1.1.0)

| Run | Extractor | Prompt | n | Merchant | Date | Total | Overall (micro) | Receipt fully right | Needs review | Merchant fuzzy ≥0.90 |
|---|---|---|---|---|---|---|---|---|---|---|
| `sroie_quick_doctr_rules` | rules-v1 | none | 20 | 5/20 | 20/20 | 9/20 | 34/60 (56.7%) | 1/20 | 5/20 | not recorded |
| `sroie_all_doctr_rules` ¹ | rules-v1 | none | 100 | 50/100 | 89/100 | 51/99 | 190/299 (63.5%) | 16/100 | 21/100 | 61/100 |
| `sroie_quick_deepseek` | deepseek-chat | extract_v1 | 20 | 16/20 | 20/20 | 19/20 | 55/60 (91.7%) | 15/20 | 0/20 | not recorded |
| `sroie_full_deepseek` | deepseek-chat | extract_v1 | 100 | 69/100 | 92/100 | 98/99 | 259/299 (86.6%) | 64/100 | 1/100 | 80/100 |
| `sroie_full_deepseek_v2` ² | deepseek-chat | extract_v2 | 100 | 80/100 | 98/100 | 98/99 | 276/299 (92.3%) | 78/100 | 1/100 | 93/100 |

- Every SROIE run returned schema-valid output for all receipts. The saved raw DeepSeek responses show no receipt needed the retry.
- Token usage saved in the response cache: 69,468 tokens for the 100-receipt extract_v1 run and 90,751 for extract_v2. Latency and cost weren't recorded.
- ¹ Re-scored with the current normalizer (`data/local/sroie/eval_runs/rescored_2026-10-24_232b916/`). Its original pre-normalization score was 182/299 (60.9%). The quick-split rows were scored before the registration-number strip was added.
- ² extract_v2 was written after reading extract_v1's failures on these same 100 receipts (day-first dates, prefer the legal entity name, drop the registration number), so the in-sample +17-field gain is optimistic.

## Held-out detail (SROIE frozen test, 200 receipts)

| Extractor | Merchant | Date (n=199) | Total | Overall (micro) | Receipt exact | Merchant fuzzy |
|---|---|---|---|---|---|---|
| rules-v1 | 117/200 58.5% [51.5, 65.0] | 192/199 96.5% [93.9, 99.0] | 96/200 48.0% [41.0, 55.0] | 405/599 67.6% [64.5, 70.7] | 47/200 | 127/200 |
| rules-v2 | 58.5% [51.5, 65.0] | 96.5% [93.9, 99.0] | 161/200 80.5% [75.0, 85.5] | 470/599 78.5% [75.2, 81.7] | 97/200 | — |
| deepseek extract_v1 | 154/200 77.0% [71.0, 83.0] | 195/199 98.0% [96.0, 99.5] | 190/200 95.0% [92.0, 98.0] | 539/599 90.0% [87.6, 92.3] | 145/200 | 167/200 |
| deepseek extract_v2 | 174/200 87.0% [82.0, 91.5] | 198/199 99.5% [98.5, 100.0] | 189/200 94.5% [91.0, 97.5] | 561/599 93.7% [91.5, 95.7] | 166/200 | 183/200 |

- **v1 → v2:** +22 fields (+3.7 points, 95% CI [+1.7, +5.5]) vs +5.7 points in-sample (CI [+2.7, +9.0]). Paired McNemar over the 599 field checks: 30 fixed vs 8 broken, exact p = 0.00047. By field: merchant 27 vs 7 (p = 0.00082), date 3 vs 0 (p = 0.25), total 0 vs 1 (p = 1).
- **rules-v1 → rules-v2:** SROIE test total +32.5 points [+25.0, +40.0] (73 fixed vs 8 broken, McNemar p = 3.0e-14); overall +10.9 points [+8.3, +13.3]. CORD holdout total 64 → 69 of 76, +6.6 points [−1.3, +14.5], p = 0.18, not significant.
- **pp-merchant-v1:** +0.5 to +1.5 points merchant on test, every CI crosses 0. Kept only as a separately reported score ([experiment write-up](experiments/merchant_postprocess.md)).
- **DeepSeek totals dropped on test** (95.0% / 94.5% vs 99% in-sample). Not investigated, to avoid tuning on test.

## Failure analysis

**CORD quick, docTR + rules-v1 (3 of 19 totals wrong, `eval_runs/quick_rules_doctr__quick.json`)**
- A total keyword matched but the wrong amount was taken (1): `cord_test_002`, gold `28000.00`, predicted `1.00`.
- No total keyword, and the guess merged a stray digit into the amount (1): `cord_test_012`, gold `120000.00`, predicted `8120000.00` (flagged `total_guessed_no_keyword`).
- No total keyword, and the guess was a zero-value line (1): `cord_test_018`, gold `156000.00`, predicted `0.00` (flagged).
- With Tesseract and EasyOCR most failures were OCR problems: Tesseract returned no total at all on 7 of its 11 misses; of EasyOCR's 12 misses, 5 were tiny values (e.g. `31.00` for `31000.00`) and 4 were `0.00`.
- `needs_review` is 20/20 on CORD because CORD has no dates, so the "date missing" rule always fires.

**SROIE dev, 100 receipts.** Patterns only; per-receipt SROIE values aren't republished because its licence is unconfirmed.
- **Rules-v1 totals (48 of 99 wrong):** the most common pattern (19) is a value about 6% below gold, which looks like the pre-tax subtotal on receipts with Malaysian GST. 13 were higher than gold, 12 lower for other reasons, 3 were `0.00` and 1 was off by a rounding adjustment.
- **Rules-v1 merchant:** the rules take a line near the top, which often isn't the registered company name: a substring of the right name (19), a different name such as a brand line (19), and OCR near-misses (11).
- **Rules-v1 dates:** 9 missing, 2 wrong.
- **DeepSeek extract_v1:** totals 98/99 (the miss is a rounding adjustment). Merchant errors (31): partial names (12), OCR near-misses (11), brand vs legal entity (6), other (2). Dates (8): 4 day/month swaps, 3 otherwise wrong, 1 missing.
- **DeepSeek extract_v2:** the date swaps are gone (2 date errors left). 13 of the 20 remaining merchant errors are OCR near-misses (≥0.90 similar), 5 partial names and 1 a different name.
