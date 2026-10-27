import json

import pytest

from src import ocr, pipeline
from src.schema import RunResult
from tests.conftest import FIXTURES

needs_tess = pytest.mark.skipif(not ocr.tesseract_available(), reason="tesseract not installed")


@needs_tess
@pytest.mark.parametrize("provider,kw", [("cached", {"cached_dir": FIXTURES / "cached_llm"}),
                                         ("rules", {})])
def test_pipeline_end_to_end(tmp_path, provider, kw):
    out = pipeline.run(FIXTURES / "labels.json", "full", provider, None,
                       engine="tesseract", out_dir=tmp_path, run_id="t", ocr_cache_dir=tmp_path / "ocr", **kw)
    run = RunResult.model_validate_json(out.read_text())
    assert run.ocr_engine.startswith("tesseract-")
    by_id = {r.id: r for r in run.receipts}
    assert len(by_id) == 4
    assert by_id["synthetic_001"].total == "11.20"
    assert by_id["synthetic_002"].total == "60000.00"
    miss = by_id["synthetic_missing"]
    assert not miss.valid and miss.error == "image not found"


def test_missing_images_do_not_crash(tmp_path):
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([{"id": "x", "image": "nowhere/x.png", "source": "CORD-v2",
                                   "split": "quick", "merchant": None, "date": None, "total": "1.00"}]))
    out = pipeline.run(labels, "quick", "rules", None, engine="tesseract",
                       out_dir=tmp_path, run_id="m")
    rec = json.loads(out.read_text())["receipts"][0]
    assert rec["valid"] is False and rec["error"] == "image not found"


def test_split_and_limit():
    rows = pipeline.load_labels(FIXTURES / "labels.json", "quick", None)
    assert len(rows) == 3
    assert len(pipeline.load_labels(FIXTURES / "labels.json", "full", 2)) == 2


def test_cli_missing_labels(tmp_path):
    assert pipeline.main(["--labels", str(tmp_path / "none.json")]) == 2
