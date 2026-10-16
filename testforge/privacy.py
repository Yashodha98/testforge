"""Masks confidential values in requirement text before it is sent to the AI.

Client stories sometimes include real e-mail addresses, phone numbers,
internal URLs or test credentials. In healthcare projects they may also
contain patient identifiers (MRN, National ID / Iqama). All of these are
replaced with placeholders.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Tuple

PATTERNS = [
    ("API_KEY", re.compile(r"\b(?:AIza[\w-]{30,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("CREDENTIAL", re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*[^\s,;\"']+")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@(?!example\.)[\w-]+\.[\w.-]+\b")),  # keep @example.* test addresses
    ("PHONE", re.compile(r"(?<!\d)(?:(?:\+94|0)7\d|(?:\+966|0)5\d)[ -]?\d{3}[ -]?\d{4}\b")),  # Sri Lanka / Saudi mobiles
    ("MRN", re.compile(r"(?i)\b(?:MRN|medical\s+record\s+(?:no|number))[\s:#-]*\d{4,}\b")),
    ("SAUDI_ID", re.compile(r"\b[12]\d{9}\b")),  # Saudi National ID / Iqama
    ("NIC", re.compile(r"\b(?:\d{9}[VvXx]|(?:19|20)\d{10})\b")),
    ("INTERNAL_URL", re.compile(r"\bhttps?://(?:[\w-]+\.)*(?:internal|intranet|corp|local|lan)\b[^\s]*", re.I)),
    ("IPV4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


def mask(text: str) -> Tuple[str, Dict[str, int]]:
    counts: Counter = Counter()
    out = text or ""
    for label, pat in PATTERNS:
        def _sub(m, _label=label):
            counts[_label] += 1
            if _label == "CREDENTIAL":
                return "{}=[MASKED]".format(m.group(1))
            return "[{}]".format(_label)
        out = pat.sub(_sub, out)
    return out, dict(counts)
