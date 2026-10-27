# Experiment: rules-v2 total picker

Branch `exp/rules-total-picker`. New extractor version **`rules-v2`** (`--provider rules-v2`).
`rules-v1` is unchanged and still selectable (`--provider rules`), so every earlier rules number reproduces. **Update:** rules-v2 is now the default extractor.

## What changed

Only the **total** picker. Merchant and date are taken from rules-v1, so those fields cannot move.

- **Multi-line label → value.** docTR often puts `TOTAL` and its amount on separate lines, sometimes with a bare `RM` or a short noise line in between. v2 looks up to 3 lines ahead, skips currency-only/noise lines and stops at the next label.
- **After-tax / rounded beats pre-tax.** `SUBTOTAL`, `TOTAL QTY/ITEMS`, `TOTAL DISC/SAVING`, tax totals, `EXCL GST`, cash/change/tender lines are not totals. Among total candidates v2 prefers one printed after the last rounding adjustment, then the last "final" label (rounded, nett, payable, due, incl. GST, grand), then the largest.
- **GST/tax summary tables are ignored.** Everything from the tax summary onward is cut off, and so is anything after the first payment line.
- **Money parsing.** v2 needs two decimals or thousands separators. It ignores percentages and negative or zero amounts. Bare integers count only if they are ≤7 digits, so barcodes, phone numbers and registration numbers are rejected.
- **Fallback when no total label is found** (flagged `_total_guessed`): cash − change if positive; cash if change is printed as 0; otherwise the largest amount before payment. Lines labelled subtotal/tax/qty/etc. and the line after such a label are excluded, and separated amounts are preferred over bare integers.
- `src/pipeline.py`: every `rules*` provider records `prompt_version="none"`.
- `scripts/paired_eval.py` (new): paired before/after statistics for any two results files.
- Tests: `tests/test_rules_v2.py` and `tests/test_paired_eval.py`, synthetic text only.

## Results

Exact match after normalisation (`src/evaluate.py`). CIs: receipt-level percentile bootstrap, 10,000 resamples, seed 0 (`scripts/paired_eval.py`). Fixed = v1 wrong → v2 right; broken = v1 right → v2 wrong. p = exact two-sided McNemar.

### Dev (tuned on these; optimistic)

| Set | Field | rules-v1 | rules-v2 | Δ (95% CI) | fixed / broken | p |
|---|---|---|---|---|---|---|
| SROIE 100 | total | 51/99 (51.5%) [41.8, 61.2] | 97/99 (98.0%) [94.9, 100] | +46.5 pp [+36.4, +56.6] | 47 / 1 | 3.5e-13 |
| SROIE 100 | overall | 190/299 (63.5%) [59.4, 67.7] | 236/299 (78.9%) [75.3, 82.6] | +15.4 pp [+12.0, +18.7] | 47 / 1 | 3.5e-13 |
| CORD quick 20 | total | 16/19 (84.2%) [68.4, 100] | 19/19 (100%) [100, 100] | +15.8 pp [+0.0, +31.6] | 3 / 0 | 0.25 |

Merchant (50/100) and date (89/100) are identical between the two versions by construction.

### Held-out: CORD test, the 80 non-quick receipts (76 with a gold total), scored once (10:07:03 America/Toronto; rules-v2 code last edited 09:59:07)

| Field | rules-v1 | rules-v2 | Δ (95% CI) | fixed / broken | p |
|---|---|---|---|---|---|
| total | 64/76 (84.2%) [76.3, 92.1] | 69/76 (90.8%) [84.2, 96.1] | +6.6 pp [−1.3, +14.5] | 7 / 2 | 0.18 |

The OCR for these 80 receipts was a fresh docTR 1.1.0 run, cached outside the repo. Their per-receipt failures were not inspected, so the set can stay held out for later experiments.

## Reading the numbers

- **Overfitting.** The rules were written while looking at dev failures, so the dev gain (+46 pp on SROIE totals) is an upper bound. On held-out CORD the gain is smaller (+6.6 pp) and not significant: the CI crosses 0, with 7 fixed and 2 broken. v1 already did well on CORD (84%), so there was less room to improve.
- **Rules that fixed only one dev receipt.** The noise-line skip and the skip of a currency-only line after the rounding adjustment each fixed one SROIE receipt. The QTY-label exclusion from the noise rule exists only to protect one CORD quick receipt. They are general in form but weakly supported. The "candidate after the last rounding line wins" rule fixed 4 receipts.
- **Known regression on dev.** One SROIE receipt has no total label and OCR has put a space inside its amount. v1 got it right by luck and v2 doesn't.
- **SROIE held-out confirmation: done** (frozen test split, scored once with `--final`; see "SROIE test 200" below). Test totals 96/200 → 161/200, +32.5 pp [+25.0, +40.0], 73 fixed / 8 broken (exact McNemar p = 3.0e-14). Source: `data/local/sroie/eval_runs/confirm_exp23/paired_fields.json` (`rules->rules-v2`, total field; local only).

## CI threshold

**Adopted:** rules-v2 is the default, `tests/fixtures/ci_results_quick.json` was regenerated with a real docTR 1.1.0 + rules-v2 run (19/19 = 100.0%), and `min_overall_accuracy` is now 0.94. Original proposal, kept for the record:

At the time, `eval_config.json` stayed at 0.84 and the CI fixture was still rules-v1 (16/19).

If rules-v2 becomes the default, regenerate `tests/fixtures/ci_results_quick.json` with a real `rules-v2` run (19/19) and raise `min_overall_accuracy` to **0.94**. That allows one wrong total (18/19 = 94.7%) and fails at two (17/19 = 89.5%).

The current "baseline rounded down" convention would give 1.00, which is too brittle for a 19-receipt gate. The held-out estimate (~91%) also says 19/19 is partly luck.

## SROIE test 200: one-off confirmation (frozen test split, `--final`)

- **Setup.** Run once, at 10:16:27 (America/Toronto). The test set was frozen at 09:57:30; the last rules-v2 code edit was at 09:59:07 (`src/extract.py`) and the fixtures at 09:59:31, both after the freeze (see "Fixture provenance"). No rules changed afterwards, no test failure lists were inspected, and the fixtures were later replaced with synthetic values.
- **Code.** A scratch, uncommitted combination of `exp/heldout-split@75b51ea` with this branch (rules-v2 code identical to `2467ec0`).
- **Inputs.** Cached docTR 1.1.0 OCR, no new OCR. Re-running rules-v1 reproduced the reference test predictions exactly.
- **Scoring.** `scripts/heldout_stats.py --final` (bootstrap 10,000 resamples, seed 20261024). The difference CI for `total` comes from `paired_eval.paired_stats` on the same files (seed 0).

| Field | rules-v1 | rules-v2 | Δ (95% CI) | fixed / broken | exact McNemar p |
|---|---|---|---|---|---|
| total | 96/200 (48.0%) [41.0, 55.0] | 161/200 (80.5%) [75.0, 85.5] | +32.5 pp [+25.0, +40.0] | 73 / 8 | 3.0e-14 |
| overall | 405/599 (67.6%) [64.5, 70.7] | 470/599 (78.5%) [75.2, 81.7] | +10.9 pp [+8.3, +13.3] | 73 / 8 | 3.0e-14 |

**Verdict.** The gain holds on held-out SROIE and is clearly significant, but it is smaller than on dev:
- SROIE totals: dev +46.5 pp (51.5 → 98.0%), test +32.5 pp (48.0 → 80.5%).
- The dev 98% was optimistic because the rules were tuned there.
- 8 test receipts that rules-v1 got right are now wrong.
- On CORD held-out the gain was +6.6 pp, with a CI that crosses 0.

## Fixture provenance (Eval & QA note, fixed after review of `8b749f4`)

**Timing.** The SROIE test split was frozen at 09:57:30 (America/Toronto). The last edits to the rules-v2 files came after that: `src/extract.py` at 09:59:07 and `tests/test_rules_v2.py` at 09:59:31.
- No test-split failure lists or per-receipt test outputs were inspected while the rules were written.
- The rules were tuned on the dev failure lists only.
- The extraction logic has **not** been changed in this fix.

**Overlap found.**
- The unlabelled-rounded-total fixture (total, negative rounding adjustment, rounded total, cash, change) matches the OCR of one test receipt.
  - It looks coincidental: it is a common POS layout.
  - The timestamps cannot rule out that it came from the test split.
- A full scan of every changed file against all SROIE OCR text and labels (dev and test) found more overlap.
  - Dev-derived strings: the unlabelled-rounded-total code comment, and five fixtures (the noise-token, `Tatal` misread, GST-summary and money-parsing fixtures, plus the `RM`-on-its-own-line fixture's amounts).
  - The GST-summary fixture's layout also lines up with one test receipt, which has the same POS template as a dev receipt.
  - The example date in two v2 tests equals two dev labels.
- The SROIE strings themselves are not repeated here.

**Fix.** Every one of those fixtures and comments has been replaced with clearly synthetic amounts and wording. The structure is the same: same label/value positions, noise token, unlabelled rounded total, GST-summary cut and `Tatal` misread.
- An execution trace confirms that each new fixture runs exactly the same lines of `src/extract.py` as the one it replaced.
- The CORD-derived fixtures (CC BY) are kept.
- Re-run after the fix: CORD quick total 19/19, SROIE dev total 97/99 and SROIE test total 161/200, all unchanged.
