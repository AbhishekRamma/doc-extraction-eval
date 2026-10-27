"""Regression check: fail the build if accuracy drops below the agreed threshold.

Inputs:
  tests/fixtures/ci_results_quick.json  - REAL cached pipeline output for the quick
                                          split (committed after the first real run)
  eval_config.json                      - {"min_overall_accuracy": <number or null>, ...}

Until both exist, this test is skipped on purpose: we never guess a threshold.
"""
import json
from pathlib import Path

import pytest

from src.evaluate import DEFAULT_LABELS, load_gold, load_results, score

ROOT = Path(__file__).resolve().parent.parent
CACHED = ROOT / "tests" / "fixtures" / "ci_results_quick.json"
CONFIG = ROOT / "eval_config.json"


def test_quick_split_overall_accuracy_not_below_threshold():
    if not CACHED.exists():
        pytest.skip(f"no cached results yet ({CACHED.relative_to(ROOT)}); "
                    "threshold not yet set from a real run")
    config = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    threshold = config.get("min_overall_accuracy")
    if threshold is None:
        pytest.skip("threshold not yet set from a real run (eval_config.json)")

    metrics = score(load_gold(DEFAULT_LABELS, "quick"), load_results(CACHED))
    accuracy = metrics["overall"]["accuracy"]
    assert accuracy is not None, "no scored fields on the quick split"
    assert accuracy >= threshold, (
        f"overall accuracy {accuracy:.4f} on quick split is below threshold {threshold} "
        f"(set from run {config.get('set_from_run')})")
