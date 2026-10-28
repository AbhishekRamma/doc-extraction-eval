"""Pydantic v2 models for receipts and pipeline results.

`Receipt` / `LineItem` describe what an extractor returns for one receipt.
`ReceiptResult` / `RunResult` describe the results/<run_id>.json file that
evaluate.py reads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_serializer

from src.normalize import normalize_date, normalize_total


class LineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[str] = None
    amount: Optional[str] = None

    @field_validator("unit_price", "amount", mode="before")
    @classmethod
    def _money(cls, v):
        if v is None:
            return None
        return normalize_total(v)


class Receipt(BaseModel):
    """Fields an extractor (LLM or rules) must produce."""

    merchant: Optional[str] = None
    date: Optional[str] = Field(None, description="YYYY-MM-DD")
    total: Optional[str] = Field(None, description="decimal string, 2 dp")
    line_items: list[LineItem] = Field(default_factory=list)

    @field_validator("merchant", mode="before")
    @classmethod
    def _merchant(cls, v):
        if v is None:
            return None
        v = str(v).strip()
        return v or None

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, v):
        # Keep unparseable dates as None; the confidence rule flags them.
        return None if v is None else normalize_date(v)

    @field_validator("total", mode="before")
    @classmethod
    def _total(cls, v):
        return None if v is None else normalize_total(v)


class ReceiptResult(Receipt):
    """One receipt in a results file."""

    id: str
    valid: bool = True
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    # token counts from the LLM API, summed over attempts. Optional extension to the
    # agreed results format: the key is only written when a paid provider reported it.
    usage: Optional[dict[str, int]] = None

    @model_serializer(mode="wrap")
    def _drop_empty_usage(self, handler):
        d = handler(self)
        if isinstance(d, dict) and d.get("usage") is None:
            d.pop("usage", None)
        return d


class RunResult(BaseModel):
    run_id: str
    model: str
    prompt_version: str
    ocr_engine: str = "unknown"
    extractor_version: str = "unknown"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    receipts: list[ReceiptResult] = Field(default_factory=list)
