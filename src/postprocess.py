"""Deterministic merchant post-processing applied after extraction (pp-merchant-v1).

Two narrow, general OCR repairs. Nothing here looks at the receipt or the gold label:

1. Malaysian legal-suffix OCR confusions, only inside the suffix at the end of the name
   (optionally followed by a bracketed registration number):
   - "SON BHD", "SIN BHD", "S0N BHD", "5DN BHD", "SDN 8HD", ... -> "SDN BHD"
   - suffix glued to the previous word ("TRADINGSDN BHD", "...SDNBHD") -> split off
   - "S/D", "5/B", "S/8" -> "S/B"
   A "SIN" or "SON" that isn't directly followed by a BHD-like token is never touched.
2. Re-splitting a joined word. An alphabetic token of 7+ letters that isn't a dictionary
   word is split if it can be segmented entirely into 2-3 dictionary words, allowing at
   most one common OCR character confusion (l/i, 1/i, 0/o, 5/s, 8/b). The dictionary is a
   small, hand-written list of generic shop/company words (not taken from any gold set).

The raw prediction is never overwritten: callers store the result next to it, and the
post-processed score is reported separately from the raw one.
"""
from __future__ import annotations

import re
from functools import lru_cache

VERSION = "pp-merchant-v1"

_TAIL = r"(?=\s*(?:\([^)]*\)\s*)?[.,]?\s*$)"  # end of name, optional "(123456-X)"

# SDN BHD with OCR confusions in either word; at least one word must look right-ish.
_SDN = r"(?:S[DO0I1L]N|5DN|SDM|SDH)"
_BHD = r"(?:[B8][HN][DO0]|BHP)"
_SDN_BHD = re.compile(rf"(?i)(?<![A-Z0-9])({_SDN})\s*\.?\s*({_BHD})\b\.?{_TAIL}")
_GLUED_SDN_BHD = re.compile(rf"(?i)(?<=[A-Z]{{3}})(SDN)\s*\.?\s*(BHD)\b\.?{_TAIL}")
_GLUED_SDNBHD = re.compile(rf"(?i)\b(SDN)(BHD)\b\.?{_TAIL}")
_S_B = re.compile(rf"(?i)(?<![A-Z0-9/])([S5])\s*/\s*([BD8])\b\.?{_TAIL}")

DICTIONARY = frozenset("""
sdn bhd enterprise enterprises trading traders hardware stationery restaurant restoran
cafe bistro kitchen bakery bakeries mini market markets supermarket hypermarket mart store
stores shop shops centre center international industries industry services service station
petrol book books gift gifts home deco electrical electronics machinery motor motors auto
pharmacy clinic food foods fresh mall plaza holdings group company marketing retail
wholesale grocer grocery beauty fashion textile furniture optical medical hotel bar pub
house sports trade mart bookstore florist laundry tailor tailors printing hair salon
garden kopitiam noodle noodles seafood steamboat corner
""".split())

_CONFUSIONS = {"l": "i", "i": "l", "1": "i", "0": "o", "5": "s", "8": "b"}


def fix_legal_suffix(name: str) -> tuple[str, list[str]]:
    fired = []
    s = name
    new = _GLUED_SDNBHD.sub("SDN BHD", s)
    if new != s:
        fired.append("suffix:sdnbhd-glued"); s = new
    new = _GLUED_SDN_BHD.sub(" SDN BHD", s)
    if new != s:
        fired.append("suffix:glued-to-word"); s = new
    m = _SDN_BHD.search(s)
    if m and (m.group(1).upper(), m.group(2).upper()) != ("SDN", "BHD"):
        s = s[:m.start()] + "SDN BHD" + s[m.end():]
        fired.append("suffix:sdn-bhd-ocr")
    m = _S_B.search(s)
    if m and (m.group(1).upper(), m.group(2).upper()) != ("S", "B"):
        s = s[:m.start()] + "S/B" + s[m.end():]
        fired.append("suffix:s/b-ocr")
    return s, fired


@lru_cache(maxsize=4096)
def _segment(word: str, max_parts: int = 3) -> tuple[str, ...] | None:
    """Split `word` into the fewest dictionary words (each >= 3 letters), or None."""
    if word in DICTIONARY:
        return (word,)
    if max_parts == 1:
        return None
    best = None
    for i in range(3, len(word) - 2):
        head = word[:i]
        if head in DICTIONARY:
            rest = _segment(word[i:], max_parts - 1)
            if rest and (best is None or 1 + len(rest) < len(best)):
                best = (head, *rest)
    return best


def _resplit_token(tok: str) -> str | None:
    low = tok.lower()
    if len(low) < 7 or not low.isalnum() or not any(c.isalpha() for c in low) or low in DICTIONARY:
        return None
    candidates = [low] + [low[:i] + _CONFUSIONS[c] + low[i + 1:]
                          for i, c in enumerate(low) if c in _CONFUSIONS]
    for cand in candidates:  # exact first, then one confusion
        if not cand.isalpha():
            continue
        seg = _segment(cand)
        if seg and len(seg) >= 2:
            out = " ".join(seg)
            return out.upper() if tok.isupper() else out
    return None


def resplit_joined(name: str) -> tuple[str, list[str]]:
    fired = []
    def repl(m):
        new = _resplit_token(m.group(0))
        if new is None:
            return m.group(0)
        fired.append("resplit")
        return new
    return re.sub(r"[A-Za-z0-9]+", repl, name), fired


def postprocess_merchant(name):
    """Return (post-processed name, list of rules that fired). None stays None."""
    if name is None:
        return None, []
    s, f1 = fix_legal_suffix(str(name))
    s, f2 = resplit_joined(s)
    return s, f1 + f2
