# Project journal: doc-extraction-eval

The decisions that shaped this project and the problems I hit. Built Oct 24, 2026, with the MongoDB store added Oct 27 (Toronto time). Commit hashes and PR numbers refer to my private development archive; this public repo starts from one commit of a cleaned tree. Full numbers are in [RESULTS.md](RESULTS.md) and the two write-ups in [experiments/](experiments/).

## Decisions

**Local OCR, chosen by measurement.** All four OCR engines run on CPU, so anyone can reproduce the results, CI costs nothing and no receipt leaves the machine. docTR became the default after a 4-engine comparison: one total behind PaddleOCR (16/19 vs 17/19) and about 19× faster. *Trade-off:* the Docker image and CI use Tesseract to stay small, so they score lower on OCR-heavy cases.

**A free, deterministic rules extractor as the default.** Regex plus keyword priority needs no key and gives the same answer every time, which is what CI and Docker need. *Trade-off:* rules-v1 scored 67.6% overall on held-out SROIE, far behind the LLM. rules-v2's total picker closed part of that gap.

**CORD committed, SROIE local only.** CORD v2 is CC BY 4.0 and committed with attribution. I couldn't confirm SROIE's licence at the official source, so the fetch script writes to a gitignored folder and refuses to run otherwise. *Trade-off:* CORD has no merchant or date labels, so the public CI gate covers totals only.

**A committed results file in CI, never a live model.** The gate scores a real docTR + rules-v2 run, so every build is free and deterministic. The threshold came from a measured run (first 0.84 from 16/19, now 0.94 from 19/19), not a guess. *Trade-off:* the gate checks the scorer and the fixture, not new model output.

**A frozen held-out test set before trusting any gain.** extract_v2 was written after reading v1's dev failures, so its in-sample gain was suspect. I froze 200 more SROIE receipts with a sha256 manifest, made the evaluator refuse them without `--final`, and approved exactly two paid runs on them. *Trade-off:* the test set can't be used to explain errors, so the DeepSeek total drop on test stays uninvestigated.

**Strict exact match as the headline.** Fuzzy and post-processed merchant scores are reported but never count, because `dim_merchant` joins need exact names. *Trade-off:* OCR typos count as wrong. pp-merchant-v1 helped on dev (+3 to +5 points) but every held-out CI crossed zero, so it stayed optional.

**MongoDB as a read model, not a replacement.** One document per receipt suits a per-receipt API; the star schema stays the analytics store. *Trade-off:* duplicated data and two stores that can drift, hence the parity check in CI.

**Publish fresh rather than open the old repo.** Older commits contained SROIE-derived strings, so I renamed the development repo to a private archive and published this one from a clean tree. *Trade-off:* the commit and PR history isn't public.

## What went wrong and how I fixed it

- **Docker bridge networking.** My build machine's firewall dropped container-to-container traffic, so the app couldn't reach `db` by service name. I ran with a temporary host-network override and `DB_PORT=5433` and left the committed compose file unchanged.
- **Served-model drift.** The repo asks for `deepseek-chat`, but every test response came from `deepseek-flash`. Dev runs hadn't recorded it, so dev and test may differ in backend. The cache now saves `response_model` on every attempt.
- **A fixture edited after the test freeze.** Two minutes after freezing the test set, a rules-v2 fixture that matched the OCR of one test receipt was edited. No test failure lists had been read, but I couldn't rule out leakage, so I replaced every SROIE-derived fixture with synthetic values and documented the timing.
- **SROIE strings in tracked files.** Some tests and comments used real SROIE merchant names. I replaced them with synthetic ones, which is also why the public repo starts fresh.
- **Upserts reported as changes.** `ReplaceOne` reported an identical reload as 20 modified. I switched to `UpdateOne` with `$set`, so a no-op reload shows 0 modified.
- **Rounding mismatch between stores.** Postgres `ROUND` rounds half away from zero; Mongo `$round` rounds half to even. The pipeline now rounds half-up in `Decimal128`, and a test pins 6.25 → 6.3.
- **Unordered ties.** Query 6 sorts only by count, so ties came back in different orders. The parity check compares rows as a multiset.
- **PaddleOCR crash.** It needed `enable_mkldnn=False` on this CPU.

## Spend

Paid held-out runs: 319,414 tokens, about $0.06 estimated from token counts, under an $0.80 cap. Dev-run cost wasn't recorded.
