"""Split names shared by the pipeline, the evaluator and the scripts.

Row labels (the "split" field of each row in a labels.json):
  quick    the small 20-receipt subset (CORD: CI gate; SROIE: first 20 dev receipts)
  holdout  CORD v2: the 80 receipts that are not in quick (the CORD held-out set;
           this label was called "full" before, which was ambiguous)
  full     SROIE dev file only (legacy label for sroie_020..099; all 100 are dev)
  test     a FROZEN held-out test set (SROIE sroie_100..299, see
           data/local/sroie/test_manifest.json). Scoring it needs --final.

Selectors you can pass with --split:
  quick | holdout | test   rows carrying that label
  full (alias all)         every row in the labels file, whatever its label

The test-set guard (check_test_access) is applied to whatever rows end up being
scored, so "--split full" on a labels file that contains test rows is refused too.
"""
from __future__ import annotations

ROW_LABELS = ["quick", "holdout", "test"]
ALL_SELECTORS = ["full", "all"]
SPLIT_CHOICES = ROW_LABELS + ALL_SELECTORS
TEST_LABEL = "test"


class TestSetLocked(PermissionError):
    """Raised when someone tries to score the frozen test split without --final."""

    __test__ = False  # not a pytest test class


def select_rows(rows: list[dict], split: str | None) -> list[dict]:
    """Rows for a --split selector. None/'full'/'all' = every row."""
    if split is None or split in ALL_SELECTORS:
        return list(rows)
    if split not in ROW_LABELS:
        raise ValueError(f"unknown split {split!r}; choose from {SPLIT_CHOICES}")
    return [r for r in rows if r.get("split") == split]


def has_test_rows(rows: list[dict]) -> bool:
    return any(r.get("split") == TEST_LABEL for r in rows)


def check_test_access(rows: list[dict], final: bool = False) -> None:
    """Refuse to score test rows unless this is the final, pre-registered run."""
    if has_test_rows(rows) and not final:
        n = sum(r.get("split") == TEST_LABEL for r in rows)
        raise TestSetLocked(
            f"refusing to score {n} receipt(s) from the frozen 'test' split without --final. "
            "The test set is for one-off final measurements only; tune on dev/quick/holdout.")
