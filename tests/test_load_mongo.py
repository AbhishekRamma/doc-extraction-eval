"""Tests for src/load_mongo.py.

The document-building tests are pure and always run. The collection tests need
a reachable MongoDB in MONGO_URL and are skipped otherwise. Each one uses its
own scratch database and drops it afterwards. (mongomock was tried and dropped:
mongomock 4.3 can't run pymongo 4.x bulk_write(UpdateOne), which the loader
uses.)
"""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

pytest.importorskip("bson")

from bson.decimal128 import Decimal128  # noqa: E402

from src import load_mongo  # noqa: E402
from src.schema import LineItem, ReceiptResult, RunResult  # noqa: E402


def _run(total_b="60000.00"):
    return RunResult(run_id="run1", model="cached:x", prompt_version="extract_v1",
                     ocr_engine="tesseract-5", extractor_version="1",
                     created_at="2026-10-24T17:40:04Z", receipts=[
        ReceiptResult(id="a", merchant="Shop A", date="2018-12-25", total="11.20",
                      line_items=[LineItem(description="x", quantity=2, unit_price="3.50", amount="7.00"),
                                  LineItem(description="y", quantity=1, amount="4.20")]),
        ReceiptResult(id="b", merchant="shop a.", date=None, total=total_b,
                      needs_review=True, review_reasons=["missing_date"]),
        ReceiptResult(id="c", valid=False, needs_review=True, review_reasons=["image_not_found"],
                      error="image not found"),
    ])


@pytest.fixture
def coll():
    url = os.getenv("MONGO_URL")
    if not url:
        pytest.skip("MONGO_URL not set")
    from pymongo.errors import PyMongoError
    client, c = load_mongo.connect(url, db_name=f"test_{uuid.uuid4().hex[:8]}", timeout_ms=3000)
    try:
        client.admin.command("ping")
    except PyMongoError as e:
        client.close()
        pytest.skip(f"MongoDB not reachable: {e}")
    load_mongo.ensure_indexes(c)
    yield c
    client.drop_database(c.database.name)
    client.close()


def test_document_shape():
    run = _run()
    d = load_mongo.receipt_document(run, run.receipts[0])
    assert d["receipt_id"] == "a" and d["run_id"] == "run1"
    assert d["merchant"] == "Shop A" and d["merchant_norm"] == "shop a"
    assert d["date"] == datetime(2018, 12, 25, tzinfo=timezone.utc)
    assert d["total"] == Decimal128(Decimal("11.20"))
    assert d["run_created_at"] == datetime(2026, 10, 24, 17, 40, 4, tzinfo=timezone.utc)
    assert [li["line_no"] for li in d["line_items"]] == [1, 2]
    assert d["line_items"][0]["unit_price"] == Decimal128("3.50")
    assert d["line_items"][1]["unit_price"] is None
    assert d["line_items"][0]["quantity"] == 2.0
    for key in ("model", "prompt_version", "ocr_engine", "extractor_version", "valid",
                "needs_review", "review_reasons", "error"):
        assert key in d


def test_missing_values():
    run = _run()
    d = load_mongo.receipt_document(run, run.receipts[2])
    assert d["merchant"] is None and d["merchant_norm"] == "(unknown)"
    assert d["date"] is None and d["total"] is None and d["line_items"] == []
    assert d["valid"] is False and d["review_reasons"] == ["image_not_found"]


def test_indexes(coll):
    info = coll.index_information()
    assert info["uq_receipt_run"]["key"] == [("receipt_id", 1), ("run_id", 1)]
    assert info["uq_receipt_run"].get("unique") is True
    assert info["ix_merchant_norm"]["key"] == [("merchant_norm", 1)]
    assert info["ix_date"]["key"] == [("date", 1)]


def test_load_and_idempotent(coll):
    first = load_mongo.load_run(coll, _run())
    assert (first["upserted"], first["matched"]) == (3, 0)
    assert first["line_items"] == 2 and first["needs_review"] == 2
    again = load_mongo.load_run(coll, _run())
    assert (again["upserted"], again["matched"], again["modified"]) == (0, 3, 0)
    assert coll.count_documents({}) == 3


def test_reload_updates_in_place(coll):
    load_mongo.load_run(coll, _run())
    res = load_mongo.load_run(coll, _run(total_b="70000.00"))
    assert (res["upserted"], res["modified"]) == (0, 1)
    assert coll.count_documents({}) == 3
    assert coll.find_one({"receipt_id": "b"})["total"] == Decimal128("70000.00")


def test_other_run_is_a_new_document(coll):
    load_mongo.load_run(coll, _run())
    other = _run()
    other.run_id = "run2"
    load_mongo.load_run(coll, other)
    assert coll.count_documents({}) == 6
    assert coll.count_documents({"receipt_id": "a"}) == 2


def test_unique_index_rejects_duplicates(coll):
    from pymongo.errors import DuplicateKeyError

    load_mongo.load_run(coll, _run())
    with pytest.raises(DuplicateKeyError):
        coll.insert_one({"receipt_id": "a", "run_id": "run1"})


def test_cli_needs_url(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.setattr(load_mongo, "ROOT", tmp_path)  # no .env picked up
    f = tmp_path / "r.json"
    f.write_text(_run().model_dump_json(), encoding="utf-8")
    assert load_mongo.main([str(f)]) == 2
    assert "MONGO_URL is not set" in capsys.readouterr().err
