"""OCR text -> validated Receipt, behind a provider-agnostic interface.

Providers (all return raw JSON text; validation is shared):
- RulesExtractor   : free, local, regex + heuristics (rules-v1, frozen, --provider rules)
- RulesExtractorV2 : rules-v1 merchant/date + a better total picker (rules-v2, the default,
                     --provider rules-v2)
- OllamaClient     : free, local LLM, only if an Ollama server is reachable
- CachedExtractor  : replays saved LLM JSON from data/cached_llm/<id>.json (CI/offline)
- DeepSeekClient   : paid, OpenAI-compatible (api.deepseek.com, JSON mode). Refuses to
  start without DEEPSEEK_API_KEY. Saves every raw response (all attempts + token usage)
  to <llm_cache_dir>/<id>.json so the 'cached' provider can replay it offline.
- OpenAIClient / AnthropicClient : dormant paid adapters; only built when
  OPENAI_API_KEY / ANTHROPIC_API_KEY exist (in env or .env). Never used by tests.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from src.normalize import normalize_date, normalize_total
from src.schema import Receipt, ReceiptResult

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "extract_v1"  # default; keep stable so CI fixtures don't change
PROMPT_VERSIONS = ["extract_v1", "extract_v2"]
EXTRACTOR_VERSION = "1"  # bump when parsing/validation/retry logic changes
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
CACHED_LLM_DIR = ROOT / "data" / "cached_llm"


def resolve_prompt_version(version: str | None) -> str:
    """'v2' or 'extract_v2' -> 'extract_v2'; None -> the default."""
    if not version:
        return PROMPT_VERSION
    v = version if version.startswith("extract_") else f"extract_{version}"
    if v not in PROMPT_VERSIONS:
        raise ValueError(f"unknown prompt version {version!r}; choose from {PROMPT_VERSIONS}")
    return v


def load_prompt(version: str = PROMPT_VERSION) -> str:
    version = resolve_prompt_version(version)
    return (PROMPT_DIR / f"{version}.txt").read_text(encoding="utf-8")


def build_prompt(ocr_text: str, version: str = PROMPT_VERSION) -> str:
    # str.replace, not .format: the template contains literal JSON braces
    return load_prompt(version).replace("{ocr_text}", ocr_text)


class LLMClient(Protocol):
    """Anything that turns (receipt id, prompt, ocr text) into raw JSON text."""

    name: str

    def complete(self, receipt_id: str, prompt: str, ocr_text: str) -> str: ...


# ---- free local providers -------------------------------------------------

_AMOUNT = re.compile(r"\d[\d.,]*\d|\d")
# Total keywords, strongest first (SROIE English/Malay, CORD English/Indonesian)
_TOTAL_KEYS = [
    re.compile(r"grand\s*total", re.I),
    re.compile(r"total\s*bayar", re.I),
    re.compile(r"\btotal\b", re.I),
    re.compile(r"\bjumlah\b", re.I),
    re.compile(r"amount\s*(?:due|payable)", re.I),
]
# Lines that mention a total-ish word but are NOT the amount paid
_BAD_TOTAL = re.compile(
    r"sub\s*-?\s*total|total\s*(?:qty|quantity|items?|disc\w*|tax|gst|excl\w*|saving)|"
    r"\bcash\b|change|kembali|tunai|tender|rounding|\bdisc|\bqty\b|\bitems?\b", re.I)
# Amounts that are never the total (used by the no-keyword fallback)
_NOT_PAID = re.compile(r"sub\s*-?\s*total|\bcash\b|change|kembali|tunai|tender|rounding|disc", re.I)
_DATE_HINT = re.compile(
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|"
    r"\d{1,2}[\s\-/]*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s\-/,]*\d{2,4}",
    re.I)
_SKIP_MERCHANT = re.compile(r"^(tax\s*invoice|invoice|receipt|welcome|official|cash\s*sale|"
                            r"simplified|gst|tel|fax|no\.?\s|co\.?\s*reg|\W*$)", re.I)


def _amounts(s: str) -> list[str]:
    vals = [normalize_total(x) for x in _AMOUNT.findall(s)]
    return [v for v in vals if v is not None]


def _find_total(lines: list[str]) -> tuple[str | None, bool]:
    """Return (total, guessed). guessed=True when no total keyword matched."""
    for key in _TOTAL_KEYS:
        cands = []
        for i, line in enumerate(lines):
            m = key.search(line)
            if not m or _BAD_TOTAL.search(line):
                continue
            vals = _amounts(line[m.end():])
            if not vals and i + 1 < len(lines) and not _BAD_TOTAL.search(lines[i + 1]):
                vals = _amounts(lines[i + 1])  # value printed on the next line
            if vals:
                cands.append(vals[-1])
        if cands:
            return cands[-1], False  # the last matching line is usually the final total
    # fallback: the largest amount not on a cash/change/subtotal line
    vals = [v for line in lines if not _NOT_PAID.search(line)
            for v in _amounts(line) if "." in line or "," in line]
    return (max(vals, key=float), True) if vals else (None, False)


def rules_extract(text: str) -> dict:
    """Free baseline extractor: regexes + layout heuristics."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    merchant = None
    for l in lines[:8]:
        if sum(c.isalpha() for c in l) >= 3 and not _SKIP_MERCHANT.match(l) and not _DATE_HINT.search(l):
            merchant = l
            break

    date = None
    for l in lines:
        for m in _DATE_HINT.finditer(l):
            date = normalize_date(m.group(0))
            if date:
                break
        if date:
            break

    total, guessed = _find_total(lines)
    out = {"merchant": merchant, "date": date, "total": total, "line_items": []}
    if guessed:
        out["_total_guessed"] = True  # extra key: ignored by Receipt, used by review_reasons
    return out


class RulesExtractor:
    name = "rules"
    model = "rules"
    version = "rules-v1"  # bump when the regexes/heuristics change

    def complete(self, receipt_id: str, prompt: str, ocr_text: str) -> str:
        return json.dumps(rules_extract(ocr_text))


# ---- rules-v2: total picker (rules-v1 above is frozen so its runs stay reproducible) ----
# A money-looking amount: 2 decimals (47.15 / 47,15), or thousands groups (28,000 / 60.000),
# or a bare integer of 3+ digits (CORD prints 156000). Never part of a longer number, so a
# stray digit merged in front ("8120,000") or a percentage ("6%") is not read as money.
_MONEY_V2 = re.compile(
    r"(?<![\w.,])-?(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\d+[.,]\d{2}|\d{3,})(?![\w%]|[.,]\d)")
_PERCENT_V2 = re.compile(r"@?\s*\d+(?:[.,]\d+)?\s*%")
# Total labels: rules-v1's keywords plus common OCR misreads of TOTAL (TATAL, T0TAL, TOTA1)
_TOTAL_V2 = re.compile(r"grand\s*total|total\s*bayar|\bt[o0a]ta[l1]|\bjumlah\b|"
                       r"amount\s*(?:due|payable)|\bnett?\b", re.I)
# Labels that contain a total word but are not the amount paid
_NOT_TOTAL_V2 = re.compile(
    r"sub\s*-?\s*t[o0a]ta[l1]|t[o0a]ta[l1]\s*(?:[qoc][tl]?y\b|quantity|ite\w*|disc\w*|saving\w*|"
    r"(?:gst|tax|vat)\b(?!\s*inc)|excl?\w*|\(?\s*excl?\w*|sales\s*\(?\s*excl?\w*|weight|point)|"
    r"excl(?:uding|\.|usive)?\s*(?:of\s*)?(?:gst|tax)|tax\s*t[o0a]ta[l1]|before\s*(?:gst|tax)|"
    r"\bdisc|\bqty\b|kembali|tender|\bchange\b|\bcash\b|rounding",
    re.I)
# Labels that mark the final, after-tax / after-rounding amount
_FINAL_V2 = re.compile(r"round(?:ed|ing\s*total)|after\s*(?:adj|round)|payable|grand|\bnett?\b|"
                       r"\bdue\b|bayar|incl\w*|inclusive", re.I)
_ROUNDING_V2 = re.compile(r"rounding|round\s*adj|\badj(?:ustment)?\b", re.I)
# Payment / change lines: amounts at or after these are tendered cash or change, not the total
_PAYMENT_V2 = re.compile(r"\bcash\b(?!ier)|tunai|\bchange\b|kembali\w*|tender\w*|\bvisa\b|"
                         r"master\s*card|credit|debit|\bcard\b", re.I)
_SUMMARY_V2 = re.compile(r"(?:gst|tax)\s*summ?ar", re.I)  # GST summary table repeats the ex-tax total
_CURRENCY_ONLY_V2 = re.compile(r"^\W*(?:rm|rp|myr|idr)?\W*$", re.I)


def _money_v2(s: str, bare: bool = True) -> list[str]:
    """Money amounts in s (percentages removed), normalised, zero and negatives dropped.
    A bare integer (no separator) counts only if bare=True and it has at most 7 digits:
    longer runs are phone numbers, GST/reg IDs or barcodes."""
    out = []
    for m in _MONEY_V2.finditer(_PERCENT_V2.sub(" ", s)):
        tok = m.group(0)
        if tok.startswith("-"):
            continue  # a rounding adjustment or discount, never the total
        if tok.isdigit() and (not bare or len(tok) > 7):
            continue
        v = normalize_total(tok)
        if v is not None and float(v) > 0:
            out.append(v)
    return out


def _is_label(line: str) -> bool:
    return sum(c.isalpha() for c in line) >= 3 and not _CURRENCY_ONLY_V2.match(
        re.sub(r"[\d.,\s-]", "", line))


def _is_noise(line: str) -> bool:
    """A short OCR fragment with no digits (e.g. a misread 'RM'), at most 3 characters,
    that is not itself a short label such as QTY / PCS / TAX / GST."""
    core = re.sub(r"\W", "", line)
    return (len(core) <= 3 and not re.search(r"\d", core)
            and not re.fullmatch(r"(?i)qty|pcs|tax|gst|vat|sst|no|x", core))


def _value_after(lines: list[str], i: int, tail: str, look: int = 3) -> str | None:
    """Amount for the label on line i: on the same line after the label, else the first of
    the next few lines, skipping currency-only lines ('RM', ':') and stopping at a new label."""
    vals = _money_v2(tail)
    if vals:
        return vals[-1]
    for j in range(i + 1, min(i + 1 + look, len(lines))):
        nxt = lines[j]
        vals = _money_v2(nxt)
        if vals:
            return None if _is_label(nxt) and (_TOTAL_V2.search(nxt) or _PAYMENT_V2.search(nxt)) \
                else vals[0]
        if _CURRENCY_ONLY_V2.match(nxt) or not _is_label(nxt) or _is_noise(nxt):
            continue
        return None
    return None


def _payment_value(lines: list[str], pattern: re.Pattern, start: int, end: int) -> str | None:
    for i in range(start, end):
        m = pattern.search(lines[i])
        if m:
            return _value_after(lines, i, lines[i][m.end():])
    return None


def _find_total_v2(lines: list[str]) -> tuple[str | None, bool]:
    """rules-v2 total picker. Returns (total, guessed).

    1. Ignore the GST/tax summary table (it repeats the pre-tax amount).
    2. Candidates: amounts belonging to a total label that is not a subtotal, tax, quantity,
       discount, cash or change label. The amount may sit a few lines below its label.
    3. Drop candidates at or after the first payment line (CASH / CHANGE / card) that
       follows the first candidate: those are tendered or change amounts.
    4. A bare amount printed right after a rounding line's value is the rounded total.
    5. If any candidate is labelled as final (rounded, after adjustment, incl. GST, payable,
       grand, nett, due), take the last such one; otherwise take the largest candidate.
    6. No total label at all: total = cash - change when both are printed, else the largest
       money amount before the payment lines (guessed=True).
    """
    cut = next((i for i, l in enumerate(lines) if _SUMMARY_V2.search(l)), len(lines))
    lines = lines[:cut]
    cands: list[tuple[int, str, bool]] = []  # (line index, amount, is_final)
    for i, line in enumerate(lines):
        m = _TOTAL_V2.search(line)
        if not m or _NOT_TOTAL_V2.search(line):
            continue
        v = _value_after(lines, i, line[m.end():])
        if v is not None:
            cands.append((i, v, bool(_FINAL_V2.search(line))))
    first = cands[0][0] if cands else 0
    pay = next((i for i in range(first, len(lines))
                if _PAYMENT_V2.search(lines[i]) and not _TOTAL_V2.search(lines[i])), len(lines))
    cands = [c for c in cands if c[0] < pay]
    # rounded total printed without a label: "ROUNDING ADJ / -0.02 / 83.45"
    for r in range(len(lines[:pay])):
        if not _ROUNDING_V2.search(lines[r]) or _TOTAL_V2.search(lines[r]):
            continue
        j, seen_adj = r + 1, bool(re.search(r"\d", lines[r]))
        while j < pay and not seen_adj and not _is_label(lines[j]):
            seen_adj = bool(re.search(r"\d", lines[j]))  # the adjustment's own value
            j += 1
        while j < pay and _CURRENCY_ONLY_V2.match(lines[j]):  # "RM" on its own line
            j += 1
        if j < pay and not _is_label(lines[j]) and _money_v2(lines[j]) and cands:
            cands.append((j, _money_v2(lines[j])[0], True))
        break
    if cands:
        cands.sort()
        rounding = [i for i in range(pay) if _ROUNDING_V2.search(lines[i])]
        after = [c for c in cands if rounding and c[0] > rounding[-1]]
        if after:  # the amount printed after the rounding line is the rounded total
            return after[0][1], False
        finals = [c for c in cands if c[2]]
        if finals:
            return finals[-1][1], False
        return max((c[1] for c in cands), key=float), False
    # fallback (no total label)
    pay = next((i for i, l in enumerate(lines) if _PAYMENT_V2.search(l)), len(lines))
    cash = _payment_value(lines, re.compile(r"\bcash\b(?!ier)|tunai", re.I), pay, len(lines))
    change = _payment_value(lines, re.compile(r"\bchange\b|kembali\w*", re.I), pay, len(lines))
    if cash and change is not None and float(cash) - float(change) > 0:
        return f"{float(cash) - float(change):.2f}", True
    if cash and change is None:
        m = re.search(r"kembali\w*|\bchange\b", "\n".join(lines[pay:]), re.I)
        rest = "\n".join(lines[pay:])[m.end():] if m else ""
        if m and re.match(r"\W*(?:rp|rm)?\W*0\b", rest, re.I):  # change printed as 0
            return cash, True
    def pool(bare: bool) -> list[str]:
        return [v for i, l in enumerate(lines[:pay])
                if not _NOT_PAID.search(l)
                and not (i and _NOT_PAID.search(lines[i - 1]) and _is_label(lines[i - 1]))
                and (not bare or not _is_label(l))  # bare integers only on amount-only lines
                for v in _money_v2(l, bare=bare)]
    # if the receipt prints amounts with separators, a bare integer is an ID, not money
    vals = pool(bare=False) or pool(bare=True)
    return (max(vals, key=float), True) if vals else (None, False)


def rules_extract_v2(text: str) -> dict:
    """rules-v1 merchant/date heuristics with the rules-v2 total picker."""
    out = rules_extract(text)
    out.pop("_total_guessed", None)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    total, guessed = _find_total_v2(lines)
    out["total"] = total
    if guessed:
        out["_total_guessed"] = True
    return out


class RulesExtractorV2:
    name = "rules-v2"
    model = "rules"
    version = "rules-v2"  # rules-v1 merchant/date + new total picker

    def complete(self, receipt_id: str, prompt: str, ocr_text: str) -> str:
        return json.dumps(rules_extract_v2(ocr_text))


LLM_CACHE_FORMAT = "llm-cache-v1"


def _is_llm_cache(obj) -> bool:
    return isinstance(obj, dict) and obj.get("format") == LLM_CACHE_FORMAT


class CachedExtractor:
    """Replays saved LLM output from <dir>/<id>.json. No network.

    Two file shapes are accepted:
    - the raw model output itself (hand-written fixtures in tests/fixtures/cached_llm)
    - an llm-cache-v1 file written by a live provider (DeepSeekClient): every attempt
      is replayed in order, so a run that needed a retry replays the same retry.
    """

    name = "cached"

    def __init__(self, cache_dir: Path | None = None, model: str = "cached"):
        self.cache_dir = Path(cache_dir or CACHED_LLM_DIR)
        self.model = model
        self.prompt_version: str | None = None  # set when the cache records it
        self._calls: dict[str, int] = {}
        # Adopt the original model/extractor version so a replay is labelled like the live run
        for f in sorted(self.cache_dir.glob("*.json")) if self.cache_dir.is_dir() else []:
            try:
                obj = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if _is_llm_cache(obj):
                self.model = obj.get("model") or self.model
                self.prompt_version = obj.get("prompt_version")
                if obj.get("extractor_version"):
                    self.version = obj["extractor_version"]
            break

    def complete(self, receipt_id: str, prompt: str, ocr_text: str) -> str:
        p = self.cache_dir / f"{receipt_id}.json"
        if not p.exists():
            raise FileNotFoundError(f"no cached LLM output for {receipt_id} at {p}")
        text = p.read_text(encoding="utf-8")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return text  # raw (invalid) model output: let validation handle it
        if not _is_llm_cache(obj):
            return text
        attempts = obj.get("attempts") or []
        if not attempts:
            raise FileNotFoundError(f"cache file {p} has no attempts")
        i = self._calls.get(receipt_id, 0)
        self._calls[receipt_id] = i + 1
        att = attempts[min(i, len(attempts) - 1)]
        if att.get("raw") is None:
            raise RuntimeError(f"replayed provider error: {att.get('error', 'no response')}")
        return att["raw"]


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


class OllamaClient:
    """Free local LLM via an Ollama server (default http://localhost:11434)."""

    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None, timeout: float = 300):
        _load_env()
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
        self.host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.timeout = timeout

    def reachable(self) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def complete(self, receipt_id: str, prompt: str, ocr_text: str) -> str:
        import urllib.request
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False,
                           "format": "json", "options": {"temperature": 0}}).encode()
        req = urllib.request.Request(self.host + "/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())["response"]


# ---- paid adapters (never called in tests/CI; tests inject fake clients) --

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
# DeepSeek JSON mode requires the word "json" in the prompt; the system message
# guarantees it independently of the prompt template.
DEEPSEEK_SYSTEM = ("You are a receipt information extractor. "
                   "Reply with a single valid json object and nothing else.")
DEFAULT_LLM_CACHE_DIR = ROOT / "data" / "llm_cache"


def _usage_dict(usage) -> dict | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return None
    keep = ("prompt_tokens", "completion_tokens", "total_tokens",
            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    return {k: usage[k] for k in keep if isinstance(usage.get(k), int)} or None


class DeepSeekClient:
    """DeepSeek chat model via its OpenAI-compatible API, in JSON mode, temperature 0.

    The API key is read only from the environment (or .env) and is never printed,
    logged or written to the cache. Every attempt is saved to
    <cache_dir>/<receipt_id>.json (format llm-cache-v1) before it is parsed.
    """

    name = "deepseek"

    def __init__(self, model: str | None = None, cache_dir: Path | None = None,
                 client=None, max_tokens: int = 1024, timeout: float = 60,
                 prompt_version: str | None = None):
        _load_env()
        key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        if not key:  # hard guard: nothing below may run without a key
            raise RuntimeError("DEEPSEEK_API_KEY is not set (put it in the environment or .env); "
                               "refusing to call the DeepSeek API")
        self._key = key
        self.model = model or DEEPSEEK_MODEL
        self.prompt_version = resolve_prompt_version(prompt_version)
        self.version = f"{self.model}+{self.prompt_version}"
        self.cache_dir = Path(cache_dir or DEFAULT_LLM_CACHE_DIR / "deepseek")
        self.max_tokens = max_tokens
        if client is None:
            from openai import OpenAI  # pip install -e ".[llm]"
            client = OpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=2)
        self._client = client
        self._attempts: dict[str, list[dict]] = {}

    def _redact(self, msg: str) -> str:
        return msg.replace(self._key, "***") if self._key else msg

    def request_params(self, prompt: str) -> dict:
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": DEEPSEEK_SYSTEM},
                         {"role": "user", "content": prompt}],
        }

    def usage_for(self, receipt_id: str) -> dict | None:
        """Token usage summed over this run's attempts for one receipt."""
        total: dict[str, int] = {}
        for a in self._attempts.get(receipt_id, []):
            for k, v in (a.get("usage") or {}).items():
                total[k] = total.get(k, 0) + v
        return total or None

    def _save(self, receipt_id: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        doc = {"format": LLM_CACHE_FORMAT, "provider": self.name, "model": self.model,
               "prompt_version": self.prompt_version, "extractor_version": self.version,
               "receipt_id": receipt_id, "attempts": self._attempts[receipt_id]}
        tmp = self.cache_dir / f".{receipt_id}.json.tmp"
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cache_dir / f"{receipt_id}.json")

    def complete(self, receipt_id: str, prompt: str, ocr_text: str) -> str:
        attempts = self._attempts.setdefault(receipt_id, [])
        rec: dict = {"attempt": len(attempts) + 1}
        attempts.append(rec)
        try:
            r = self._client.chat.completions.create(**self.request_params(prompt))
        except Exception as e:
            rec.update(raw=None, error=self._redact(f"{type(e).__name__}: {e}")[:500])
            self._save(receipt_id)
            raise RuntimeError(rec["error"]) from None
        choice = r.choices[0]
        rec.update(raw=choice.message.content or "",
                   finish_reason=getattr(choice, "finish_reason", None),
                   usage=_usage_dict(getattr(r, "usage", None)),
                   response_id=getattr(r, "id", None),
                   # the model the API says served the request (aliases can be re-routed)
                   response_model=getattr(r, "model", None))
        self._save(receipt_id)
        return rec["raw"]


class OpenAIClient:
    name = "openai"

    def __init__(self, model: str | None = None):
        _load_env()
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY not set; paid provider disabled")
        from openai import OpenAI  # pip install openai (not a project dependency)
        self._client = OpenAI()
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def complete(self, receipt_id: str, prompt: str, ocr_text: str) -> str:
        r = self._client.chat.completions.create(
            model=self.model, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content


class AnthropicClient:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        _load_env()
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY not set; paid provider disabled")
        import anthropic  # pip install anthropic (not a project dependency)
        self._client = anthropic.Anthropic()
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")

    def complete(self, receipt_id: str, prompt: str, ocr_text: str) -> str:
        r = self._client.messages.create(
            model=self.model, max_tokens=1024, temperature=0,
            messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


DEFAULT_PROVIDER = "rules-v2"  # rules-v1 stays selectable as --provider rules
PROVIDERS = ["rules", "rules-v2", "ollama", "cached", "deepseek", "openai", "anthropic"]


def get_client(provider: str, **kw) -> LLMClient:
    if provider == "rules":
        return RulesExtractor()
    if provider == "rules-v2":
        return RulesExtractorV2()
    if provider == "cached":
        return CachedExtractor(**kw)
    if provider == "ollama":
        c = OllamaClient(**kw)
        if not c.reachable():
            raise RuntimeError(f"Ollama server not reachable at {c.host}; start it with `ollama serve`")
        return c
    if provider == "deepseek":
        return DeepSeekClient(**kw)
    if provider == "openai":
        return OpenAIClient(**kw)
    if provider == "anthropic":
        return AnthropicClient(**kw)
    raise ValueError(f"unknown provider {provider!r}; choose from {PROVIDERS}")


# ---- parsing, validation, retry, confidence -------------------------------

def parse_json(raw: str) -> dict:
    """Parse model output; tolerate ```json fences and leading prose."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("model output is not a JSON object")
    return obj


def review_reasons(receipt: Receipt, raw: dict | None = None) -> list[str]:
    """Confidence rule: missing/unparseable total or date -> needs review."""
    reasons = []
    raw = raw or {}
    if receipt.total is None:
        reasons.append("total_unparseable" if raw.get("total") not in (None, "") else "missing_total")
    elif raw.get("_total_guessed"):
        reasons.append("total_guessed_no_keyword")
    if receipt.date is None:
        reasons.append("date_unparseable" if raw.get("date") not in (None, "") else "missing_date")
    return reasons


def extract_receipt(receipt_id: str, ocr_text: str, client: LLMClient,
                    prompt_version: str = PROMPT_VERSION, max_attempts: int = 2) -> ReceiptResult:
    """Call the client, validate with Pydantic, retry once, then give up."""
    prompt = build_prompt(ocr_text, prompt_version)
    last_err = None
    for _ in range(max_attempts):
        try:
            raw_text = client.complete(receipt_id, prompt, ocr_text)
            raw = parse_json(raw_text)
            receipt = Receipt.model_validate(raw)
        except FileNotFoundError as e:  # cached output missing: retry won't help
            last_err = f"FileNotFoundError: {e}"
            break
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as e:
            last_err = f"{type(e).__name__}: {str(e)[:300]}"
            continue
        except Exception as e:  # network/provider errors
            last_err = f"{type(e).__name__}: {str(e)[:300]}"
            continue
        reasons = review_reasons(receipt, raw)
        return ReceiptResult(id=receipt_id, **receipt.model_dump(), valid=True,
                             needs_review=bool(reasons), review_reasons=reasons)
    return ReceiptResult(id=receipt_id, valid=False, needs_review=True,
                         review_reasons=["invalid_output"], error=last_err)
