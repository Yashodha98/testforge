"""Boundary Value Analysis (BVA) extracted from acceptance-criteria text.

Finds numeric constraints such as "between 8 and 20 characters",
"at least 1,000 LKR", "maximum 5 attempts" or "up to 10 items" and
produces the classic BVA test values (min-1, min, min+1, max-1, max, max+1).
This is deterministic, so the values are exact even when the AI is off,
and they are passed to the AI as hints.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .models import AcceptanceCriterion, Boundary

NUM = r"(\d[\d,]*(?:\.\d+)?)"
UNIT = r"(characters?|chars?|digits?|letters?|items?|products?|attempts?|tries|times|minutes?|mins?|seconds?|secs?|hours?|days?|years?|MB|KB|GB|files?|LKR|Rs\.?|USD|%|percent|words?|users?|rows?|units?)?"

PATTERNS = [
    ("exact", re.compile(r"(?:exactly|must\s+be|of)\s+" + NUM + r"\s*" + r"(digits?|characters?|chars?|letters?)", re.I)),
    ("range", re.compile(r"between\s+" + NUM + r"\s*" + UNIT + r"\s+and\s+" + NUM + r"\s*" + UNIT, re.I)),
    ("range", re.compile(NUM + r"\s*" + UNIT + r"\s*(?:to|-|–)\s*" + NUM + r"\s*" + UNIT, re.I)),
    ("min", re.compile(r"(?:at\s+least|minimum(?:\s+of)?|min\.?|no\s+(?:less|fewer)\s+than|not\s+less\s+than)\s+" + NUM + r"\s*" + UNIT, re.I)),
    ("max", re.compile(r"(?:at\s+most|maximum(?:\s+of)?|max\.?|up\s+to|no\s+more\s+than|not\s+more\s+than|not\s+exceed(?:ing)?|limit(?:ed)?\s+(?:of|to))\s+" + NUM + r"\s*" + UNIT, re.I)),
    ("max", re.compile(r"(?:maximum|max\.?)(?:\s+[a-z]+){1,4}\s+(?:is|of|=)\s+" + NUM + r"\s*" + UNIT, re.I)),
    ("min", re.compile(r"(?:minimum|min\.?)(?:\s+[a-z]+){1,4}\s+(?:is|of|=)\s+" + NUM + r"\s*" + UNIT, re.I)),
    ("gt", re.compile(r"(?:more|greater|over|above)\s+than\s+" + NUM + r"\s*" + UNIT, re.I)),
    ("gt", re.compile(r"(?:after|exceeds?)\s+" + NUM + r"\s*" + UNIT, re.I)),
    ("lt", re.compile(r"(?:less|fewer|under|below)\s+than\s+" + NUM + r"\s*" + UNIT, re.I)),
    ("max", re.compile(r"within\s+" + NUM + r"\s*" + UNIT, re.I)),
]


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _fmt(v: float):
    return int(v) if float(v).is_integer() else round(v, 2)


def _step(unit: str, *vals) -> float:
    if any(not float(v).is_integer() for v in vals if v is not None):
        return 0.01
    return 1


STOP = {"the", "a", "an", "must", "be", "is", "are", "should", "shall", "only", "and", "when", "of", "for",
        "in", "per", "to", "that", "it", "contain", "contains", "have", "has", "accepted", "keeps", "can",
        "will", "then", "they", "there", "with", "on", "at", "by", "which", "or", "long", "logged"}


def _clean(words):
    out = []
    for w in words:
        w = w.strip(",.:;()\"'/").lower()
        if w and w not in STOP and not re.match(r"^[\d,.]+$", w) and w not in ("maximum", "minimum", "max", "min", "least", "most", "up", "exactly", "start"):
            out.append(w)
    return out


def _subject(text: str, start: int, end: int, unit: str) -> str:
    """Best-effort name of the constrained quantity (used only as a label/hint)."""
    clause = re.split(r"[.;:,]", text[:start])[-1]
    before = _clean(clause.split())
    inside = _clean(text[start:end].split())
    inside = [w for w in inside if not unit or not w.startswith(unit.lower()[:4])]
    if inside and inside[0] not in ("between", "than", "within", "to"):
        return " ".join(inside[:3])  # e.g. "maximum discount per order is 5,000"
    if not unit:  # e.g. "at least 1 uppercase letter" -> noun follows the number
        after = _clean(text[end:].split()[:2])
        if after:
            return " ".join(after)
    return " ".join(before[:3]) or "value"


def extract_boundaries(criteria: List[AcceptanceCriterion]) -> List[Boundary]:
    found: List[Boundary] = []
    for c in criteria:
        taken = []
        for kind, pat in PATTERNS:
            for m in pat.finditer(c.text):
                if any(m.start() < e and m.end() > s for s, e in taken):
                    continue  # overlaps an earlier, more specific match
                taken.append((m.start(), m.end()))
                g = m.groups()
                if kind == "exact":
                    n = _num(g[0])
                    b = _make(c, n, n, (g[1] or "").strip(), m, kind)
                elif kind == "range":
                    lo, hi = _num(g[0]), _num(g[2])
                    unit = (g[1] or g[3] or "").strip()
                    if lo > hi:
                        lo, hi = hi, lo
                    b = _make(c, lo, hi, unit, m, kind)
                elif kind in ("min", "gt"):
                    v = _num(g[0])
                    lo = v + _step("", v) if kind == "gt" else v
                    b = _make(c, lo, None, (g[1] or "").strip(), m, kind)
                else:  # max / lt
                    v = _num(g[0])
                    hi = v - _step("", v) if kind == "lt" else v
                    b = _make(c, None, hi, (g[1] or "").strip(), m, kind)
                if b:
                    found.append(b)
    return found


def _make(c: AcceptanceCriterion, lo: Optional[float], hi: Optional[float], unit: str, m, kind: str) -> Optional[Boundary]:
    step = _step(unit, lo, hi)
    vals = []
    if kind == "exact":
        vals = [(lo - step, "reject", "length-1"), (lo, "accept", "exact"), (lo + step, "reject", "length+1")]
        lo = hi = None
    if lo is not None:
        vals += [(lo - step, "reject", "min-1"), (lo, "accept", "min"), (lo + step, "accept", "min+1")]
    if hi is not None:
        vals += [(hi - step, "accept", "max-1"), (hi, "accept", "max"), (hi + step, "reject", "max+1")]
    seen = set()
    values = []
    for v, exp, label in vals:
        if v < 0 or v in seen:
            continue
        seen.add(v)
        values.append({"value": _fmt(v), "expect": exp, "label": label})
    if not values:
        return None
    # "after 5 failed attempts the account is locked": crossing the limit triggers the action,
    # so the expectation is "triggers" rather than "reject".
    if kind == "gt":
        for v in values:
            v["expect"] = "no action" if v["expect"] == "reject" else "action triggered"
    return Boundary(
        ac_id=c.ac_id, subject=_subject(c.text, m.start(), m.end(), unit), unit=unit,
        minimum=_fmt(lo) if lo is not None else None, maximum=_fmt(hi) if hi is not None else None,
        source_text=m.group(0), values=values,
    )
