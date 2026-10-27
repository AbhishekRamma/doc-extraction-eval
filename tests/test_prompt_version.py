"""Prompt versioning: v1 stays the default and unchanged, v2 is selectable end to end."""
import hashlib
import json
import re

import pytest

from src import pipeline
from src.extract import (PROMPT_DIR, PROMPT_VERSION, DeepSeekClient,
                         build_prompt, extract_receipt, load_prompt, resolve_prompt_version)
from conftest import FIXTURES
from test_deepseek import FAKE_KEY, GOOD, fake_client, no_network  # noqa: F401 (autouse fixture)

# sha256 of src/prompts/extract_v1.txt as used for every committed v1 run (incl. SROIE).
V1_SHA256 = "ca6b8d0a3d17b45b54cb645d2e14be0941839fe11622defd90326b46ff41eb46"


def _schema_block(text):
    return text[text.index("{"):text.index("Rules:")]


def test_default_is_v1():
    assert PROMPT_VERSION == "extract_v1"
    assert resolve_prompt_version(None) == "extract_v1"
    assert load_prompt() == load_prompt("v1") == load_prompt("extract_v1")


def test_v1_file_unchanged():
    assert hashlib.sha256((PROMPT_DIR / "extract_v1.txt").read_bytes()).hexdigest() == V1_SHA256


def test_v2_keeps_schema_and_json_word():
    v1, v2 = load_prompt("v1"), load_prompt("v2")
    assert v1 != v2
    assert "json" in v2.lower() and "{ocr_text}" in v2
    assert _schema_block(v2) == _schema_block(v1)
    for key in ('"merchant"', '"date"', '"total"', '"line_items"', '"description"',
                '"quantity"', '"unit_price"', '"amount"'):
        assert key in v2
    assert "OCR-TEXT-HERE" in build_prompt("OCR-TEXT-HERE", "v2")


def test_v2_is_general_rules_only():
    v2 = load_prompt("v2")
    assert not re.search(r"sroie_\d+|cord_\d+|synthetic_\d+", v2, re.I)  # no receipt ids
    assert "day-first" in v2 and "20YY" in v2 and "SDN BHD" in v2


@pytest.mark.parametrize("bad", ["v3", "extract_v9", "../etc/passwd"])
def test_unknown_prompt_version(bad):
    with pytest.raises(ValueError):
        resolve_prompt_version(bad)


def test_deepseek_version_follows_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    client, comp = fake_client([GOOD])
    ds = DeepSeekClient(client=client, cache_dir=tmp_path, prompt_version="v2")
    assert ds.version == "deepseek-chat+extract_v2"
    extract_receipt("synthetic_001", "TOTAL 1.00", ds, prompt_version="extract_v2")
    sent = comp.calls[0]["messages"][-1]["content"]
    assert "day-first" in sent  # the v2 template was sent
    env = json.loads((tmp_path / "synthetic_001.json").read_text())
    assert env["prompt_version"] == "extract_v2"
    assert env["extractor_version"] == "deepseek-chat+extract_v2"
    assert DeepSeekClient(client=fake_client([])[0], cache_dir=tmp_path / "d").version \
        == "deepseek-chat+extract_v1"


def test_pipeline_v2_then_replay_and_mismatch_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    labels = FIXTURES / "labels.json"
    n_images = sum(1 for r in json.loads(labels.read_text())
                   if (FIXTURES.parent.parent / r["image"]).exists())
    client, comp = fake_client([GOOD] * n_images)
    llm, ocr_cache = tmp_path / "llm", tmp_path / "ocr"
    ds = DeepSeekClient(client=client, cache_dir=llm, prompt_version="v2")
    out = pipeline.run(labels, "full", "deepseek", None, engine="tesseract", out_dir=tmp_path,
                       run_id="live_v2", ocr_cache_dir=ocr_cache, client=ds,
                       prompt_version="v2")
    live = json.loads(out.read_text())
    assert live["prompt_version"] == "extract_v2"
    assert live["extractor_version"] == "deepseek-chat+extract_v2"

    common = ["--labels", str(labels), "--split", "full", "--provider", "cached",
              "--llm-cache-dir", str(llm), "--ocr-engine", "tesseract",
              "--ocr-cache-dir", str(ocr_cache), "--out-dir", str(tmp_path)]
    assert pipeline.main(common + ["--prompt-version", "v2", "--run-id", "replay_v2"]) == 0
    replay = json.loads((tmp_path / "replay_v2.json").read_text())
    assert replay["prompt_version"] == "extract_v2"
    assert replay["extractor_version"] == "deepseek-chat+extract_v2"
    # replaying a v2 cache while claiming v1 is refused instead of mislabelling the run
    assert pipeline.main(common + ["--run-id", "mislabelled"]) == 2
    assert not (tmp_path / "mislabelled.json").exists()
    assert len(comp.calls) == n_images


def test_rules_run_unaffected_by_flag(tmp_path):
    labels = FIXTURES / "labels.json"
    out = pipeline.run(labels, "quick", "rules", None, engine="tesseract", out_dir=tmp_path,
                       run_id="r", prompt_version="v2")
    data = json.loads(out.read_text())
    assert data["prompt_version"] == "none" and data["extractor_version"] == "rules-v1"


def test_cli_rejects_unknown_prompt_version(capsys):
    with pytest.raises(SystemExit):
        pipeline.main(["--prompt-version", "v3"])
