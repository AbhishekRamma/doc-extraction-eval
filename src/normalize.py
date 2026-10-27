"""Pure normalisation helpers shared by extract.py and evaluate.py.

Rules (agreed with Eval & QA):
- pure and deterministic: no I/O, no randomness, no locale or clock use
- return None for empty or unparseable input, never raise
- idempotent: normalize(normalize(x)) == normalize(x)
"""
from __future__ import annotations

import re
import string
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*\.?"


def _valid(y: int, m: int, d: int) -> bool:
    if not (1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
        return False
    days = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return d <= days[m - 1]


def _year(y: str) -> int:
    n = int(y)
    return 2000 + n if len(y) == 2 else n  # receipts in these datasets are 2000s


def _iso(y: int, m: int, d: int) -> str | None:
    return f"{y:04d}-{m:02d}-{d:02d}" if _valid(y, m, d) else None


def normalize_date(value) -> str | None:
    """Return 'YYYY-MM-DD' or None.

    Numeric dates with the year last are read day-first (25/12/2018), as in
    SROIE (Malaysia) and CORD (Indonesia); if day-first is impossible
    (12/25/2018) month-first is tried.
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None

    # 2018-12-25, 2018/12/25, 2018.12.25
    m = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", s)
    if m:
        return _iso(int(m[1]), int(m[2]), int(m[3]))

    # 25/12/2018, 25-12-18, 25.12.2018
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), _year(m[3])
        return _iso(y, b, a) or _iso(y, a, b)

    # 25 Dec 2018, 25-Dec-18, 25 December, 2018
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]*" + _MON + r"[\s\-/.,]*(\d{4}|\d{2})\b", s)
    if m:
        return _iso(_year(m[3]), _MONTHS[m[2]], int(m[1]))

    # Dec 25, 2018
    m = re.search(r"\b" + _MON + r"[\s\-/.]*(\d{1,2})(?:st|nd|rd|th)?[\s,\-/.]+(\d{4})\b", s)
    if m:
        return _iso(int(m[3]), _MONTHS[m[1]], int(m[2]))

    # 20181225
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return _iso(int(m[1]), int(m[2]), int(m[3]))
    return None


def normalize_total(value) -> str | None:
    """Return a canonical 2-dp decimal string like '1234.50', or None.

    Rule (deterministic, works for SROIE ringgit and CORD rupiah):
    - strip currency symbols/codes and spaces, keep digits, '.' and ','
    - if the LAST separator is followed by exactly 3 digits, every separator
      is a thousands separator:  '60.000' -> '60000.00', '1,591,600' -> '1591600.00'
    - if it is followed by 1 or 2 digits, it is the decimal point and any
      earlier separators are thousands: '1,234.50' -> '1234.50', '12,5' -> '12.50'
    - no separator: integer amount: '45' -> '45.00'
    Caveat: a 3-decimal amount such as '1.500' meaning 1.5 is read as 1500.
    """
    if value is None or isinstance(value, bool):
        return None
    s = str(value).strip()
    if not s:
        return None
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = re.sub(r"[^\d.,]", "", s).strip(".,")
    if not s or not re.fullmatch(r"\d+(?:[.,]\d+)*", s):
        return None
    m = re.search(r"[.,](\d+)$", s)
    if m is None:
        num = s
    elif len(m.group(1)) == 3:
        num = re.sub(r"[.,]", "", s)
    elif len(m.group(1)) in (1, 2):
        num = re.sub(r"[.,]", "", s[: m.start()]) + "." + m.group(1)
    else:
        return None  # e.g. '12.3456': ambiguous, let a human look
    try:
        d = Decimal(num)
    except InvalidOperation:
        return None
    if neg:
        d = -d
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


_PUNCT = str.maketrans({c: " " for c in string.punctuation})


# A trailing Malaysian-style company registration number such as "(123456-A)"
# or "(7654321-B)", optionally preceded by a "CO. REG" / "CO. REG. NO:" /
# "COMPANY NO." style label. Only stripped at the very end of the name, and
# before punctuation removal (the parentheses and hyphen are what identify it).
_REG_NO_TAIL = re.compile(
    r"(?:\b(?:co\.?|company)\s*(?:reg(?:istration)?\.?(?:\s*no\.?)?|no\.?)\s*[:.]?\s*)?"
    r"\(\s*\d{5,8}\s*-\s*[a-z]\s*\)\s*$",
    re.IGNORECASE,
)


def normalize_merchant(value) -> str | None:
    """Drop a trailing '(123456-X)' registration number (and its label),
    casefold, replace punctuation with spaces, collapse whitespace."""
    if value is None:
        return None
    try:
        s = str(value)
    except Exception:
        return None
    s = _REG_NO_TAIL.sub("", s)
    s = s.casefold().translate(_PUNCT)
    s = " ".join(s.split())
    return s or None
