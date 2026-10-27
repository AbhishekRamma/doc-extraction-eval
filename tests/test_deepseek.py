"""DeepSeek provider: everything is mocked, no network, no real key."""
import json
import socket
from types import SimpleNamespace

import pytest

from src import extract, pipeline
from src.extract import CachedExtractor, DeepSeekClient, extract_receipt
from conftest import FIXTURES

FAKE_KEY = "sk-test-not-a-real-key-123"
GOOD = json.dumps({"merchant": "Sunrise Bakery", "date": "2018-12-25", "total": "11.20",
                   "line_items": []})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real socket connection fails the test."""
    def boom(*a, **k):
        raise AssertionError("network access attempted in a test")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(extract, "_load_env", lambda: None)  # ignore any real .env


class FakeCompletions:
    def __init__(self, outputs, usage=True):
        self.outputs = list(outputs)
        self.calls = []
        self.usage = usage

    def create(self, **params):
        self.calls.append(params)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 20,
                                                    "total_tokens": 120}) if self.usage else None
        return SimpleNamespace(id="resp-1", usage=usage, choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=out))])


def fake_client(outputs, **kw):
    comp = FakeCompletions(outputs, **kw)
    return SimpleNamespace(chat=SimpleNamespace(completions=comp)), comp


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_key_guard(monkeypatch, value, tmp_path):
    if value is None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    else:
        monkeypatch.setenv("DEEPSEEK_API_KEY", value)
    client, comp = fake_client([GOOD])
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY is not set"):
        DeepSeekClient(client=client, cache_dir=tmp_path)
    assert comp.calls == []  # nothing was sent
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        extract.get_client("deepseek")


def test_pipeline_cli_refuses_without_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    rc = pipeline.main(["--labels", str(FIXTURES / "labels.json"), "--provider", "deepseek",
                        "--ocr-engine", "tesseract", "--out-dir", str(tmp_path)])
    assert rc == 2
    assert "DEEPSEEK_API_KEY is not set" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_json_mode_params(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    client, comp = fake_client([GOOD])
    ds = DeepSeekClient(client=client, cache_dir=tmp_path)
    res = extract_receipt("r1", "SUNRISE BAKERY TOTAL 11.20", ds)
    assert res.valid and res.total == "11.20"
    (params,) = comp.calls
    assert params["model"] == "deepseek-chat"
    assert params["temperature"] == 0
    assert params["response_format"] == {"type": "json_object"}
    text = " ".join(m["content"] for m in params["messages"])
    assert "json" in text  # lowercase: required by DeepSeek JSON mode
    assert "SUNRISE BAKERY TOTAL 11.20" in params["messages"][-1]["content"]
    assert ds.version == "deepseek-chat+extract_v1"


def test_real_sdk_client_is_configured_for_deepseek(monkeypatch, tmp_path):
    pytest.importorskip("openai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    ds = DeepSeekClient(cache_dir=tmp_path)  # constructing the SDK client makes no request
    assert str(ds._client.base_url).rstrip("/") == "https://api.deepseek.com"


def test_retry_on_invalid_json_and_cache_written(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    client, comp = fake_client(["this is not json", GOOD])
    ds = DeepSeekClient(client=client, cache_dir=tmp_path)
    res = extract_receipt("r1", "ocr", ds)
    assert len(comp.calls) == 2
    assert res.valid and res.merchant == "Sunrise Bakery"
    doc = json.loads((tmp_path / "r1.json").read_text())
    assert doc["format"] == "llm-cache-v1"
    assert doc["model"] == "deepseek-chat"
    assert doc["extractor_version"] == "deepseek-chat+extract_v1"
    assert [a["raw"] for a in doc["attempts"]] == ["this is not json", GOOD]
    assert doc["attempts"][0]["usage"]["total_tokens"] == 120
    assert ds.usage_for("r1") == {"prompt_tokens": 200, "completion_tokens": 40,
                                  "total_tokens": 240}
    assert FAKE_KEY not in (tmp_path / "r1.json").read_text()


def test_two_invalid_answers_give_invalid_result(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    client, comp = fake_client(["nope", '{"total": "abc"'])
    res = extract_receipt("r1", "ocr", DeepSeekClient(client=client, cache_dir=tmp_path))
    assert len(comp.calls) == 2
    assert not res.valid and res.review_reasons == ["invalid_output"]


def test_api_error_is_cached_and_key_redacted(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    client, comp = fake_client([RuntimeError(f"401 bad key {FAKE_KEY}"), GOOD])
    ds = DeepSeekClient(client=client, cache_dir=tmp_path)
    res = extract_receipt("r1", "ocr", ds)
    assert res.valid
    text = (tmp_path / "r1.json").read_text()
    assert FAKE_KEY not in text and "***" in text
    # replay reproduces the error then the good answer
    res2 = extract_receipt("r1", "ocr", CachedExtractor(tmp_path))
    assert res2.model_dump() == res.model_dump()


def test_cached_replay_of_written_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    client, comp = fake_client(["```json\nnot valid", GOOD, GOOD])
    ds = DeepSeekClient(client=client, cache_dir=tmp_path)
    live = [extract_receipt("a", "ocr", ds), extract_receipt("b", "ocr", ds)]
    assert len(comp.calls) == 3

    replay_client = CachedExtractor(tmp_path)
    assert replay_client.model == "deepseek-chat"
    assert replay_client.version == "deepseek-chat+extract_v1"
    replay = [extract_receipt("a", "ocr", replay_client), extract_receipt("b", "ocr", replay_client)]
    assert [r.model_dump() for r in replay] == [r.model_dump() for r in live]
    assert replay_client._calls == {"a": 2, "b": 1}  # the retry was replayed too


def test_legacy_fixture_cache_still_replays():
    c = CachedExtractor(FIXTURES / "cached_llm")
    assert c.model == "cached"
    assert extract_receipt("synthetic_001", "", c).valid


def test_pipeline_deepseek_then_cached_replay(monkeypatch, tmp_path, capsys):
    """End to end through pipeline.run with a mocked SDK, then a zero-network replay."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    labels = FIXTURES / "labels.json"
    n_images = sum(1 for r in json.loads(labels.read_text())
                   if (FIXTURES.parent.parent / r["image"]).exists())
    client, comp = fake_client([GOOD] * n_images)
    llm_cache, ocr_cache = tmp_path / "llm", tmp_path / "ocr"
    ds = DeepSeekClient(client=client, cache_dir=llm_cache)
    out = pipeline.run(labels, "full", "deepseek", None, engine="tesseract", out_dir=tmp_path,
                       run_id="live", ocr_cache_dir=ocr_cache, client=ds)
    live = json.loads(out.read_text())
    assert live["model"] == "deepseek:deepseek-chat"
    assert live["extractor_version"] == "deepseek-chat+extract_v1"
    used = [r for r in live["receipts"] if r.get("usage")]
    assert len(used) == n_images and used[0]["usage"]["total_tokens"] == 120
    assert f"total={120 * n_images}" in capsys.readouterr().err
    assert len(list(llm_cache.glob("*.json"))) == n_images

    rc = pipeline.main(["--labels", str(labels), "--split", "full", "--provider", "cached",
                        "--llm-cache-dir", str(llm_cache), "--ocr-engine", "tesseract",
                        "--ocr-cache-dir", str(ocr_cache), "--out-dir", str(tmp_path),
                        "--run-id", "replay"])
    assert rc == 0
    replay = json.loads((tmp_path / "replay.json").read_text())
    assert replay["model"] == "cached:deepseek-chat"
    assert replay["extractor_version"] == live["extractor_version"]
    strip = lambda rs: [{k: v for k, v in r.items() if k != "usage"} for r in rs]
    assert strip(replay["receipts"]) == strip(live["receipts"])
    assert len(comp.calls) == n_images  # replay made no calls
