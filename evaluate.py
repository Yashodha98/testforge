"""Runs the formal test cases reported in the Technical Evidence Document.

Usage:  python evaluate.py      (uses GEMINI_API_KEY from .env when present)
Writes docs/test_results.md
"""
import os
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

from testforge.ai import DEFAULT_MODEL, design_tests
from testforge.automation import generate_script, traceability
from testforge.boundaries import extract_boundaries
from testforge.document import DocumentError, extract_text, split_stories
from testforge.story_parser import parse_story

load_dotenv()
ROOT = Path(__file__).parent
DOCS = ROOT / "sample_docs"
KEY = os.getenv("ANTHROPIC_API_KEY") or os.getenv("GEMINI_API_KEY")
rows = []


def record(tc, scenario, inp, expected, actual, passed):
    status = "SKIPPED" if passed is None else ("PASS" if passed else "FAIL")
    rows.append((tc, scenario, inp, expected, actual, status))
    print("{} {}: {}\n   -> {}\n".format(status, tc, scenario, actual))


def read(fname):
    text = extract_text(fname, (DOCS / fname).read_bytes())
    context, sections = split_stories(text)
    return context, [(s, parse_story(s.text)) for s in sections]


def run_design(context, stories, key):
    out = []
    for s, p in stories:
        tests, meta = design_tests(p, extract_boundaries(p.criteria), api_key=key, model=DEFAULT_MODEL, context=context)
        out.append((s, p, tests, meta))
    return out


DOCX = "Confluence_HMS_Outpatient_Requirements.docx"
ctx, stories = read(DOCX)

# TC1 - main path: Confluence Word export -> AI test cases (Claude or Gemini)
if KEY:
    res = run_design(ctx, stories, KEY)
    covs = [traceability(p, t)[1] for _, p, t, _ in res]
    engines = sorted({m.source for *_, m in res})
    total = sum(len(t) for _, _, t, _ in res)
    types = sorted({x.test_type for _, _, t, _ in res for x in t})
    ok = all(c == 100 for c in covs) and set(engines) <= {"claude", "gemini", "mixed"} and "Negative" in types and "Boundary" in types
    record("TC1", "Confluence export -> AI test cases (AI key available)", DOCX + " (3 stories, 12 ACs)",
           "Test cases for all 3 stories; 100% AC coverage each; Positive, Negative and Boundary types",
           "{} tests ({}); coverage {}; engine {}; {} ms total".format(
               total, ", ".join("{} {}".format(s.key, len(t)) for s, _, t, _ in res), "/".join("{:.0f}%".format(c) for c in covs),
               "/".join(engines), sum(m.latency_ms or 0 for *_, m in res)), ok)
else:
    record("TC1", "Confluence export -> AI test cases (AI key available)", DOCX,
           "AI test cases for 3 stories", "Not run - set ANTHROPIC_API_KEY or GEMINI_API_KEY in .env", None)

# TC2 - same page exported as PDF is read identically
pctx, pstories = read("Confluence_HMS_Outpatient_Requirements.pdf")
got = [(s.key, len(p.criteria)) for s, p in pstories]
record("TC2", "Confluence PDF export is read and split", "Confluence_HMS_Outpatient_Requirements.pdf",
       "3 stories US-101/102/103 with 5/4/3 acceptance criteria (same as Word export)",
       "Stories found: " + ", ".join("{} ({} ACs)".format(k, n) for k, n in got),
       got == [("US-101", 5), ("US-102", 4), ("US-103", 3)])

# TC3 - AI unavailable -> fallback (real call with an invalid key)
res3 = run_design(ctx, stories, "sk-ant-INVALID-KEY-FOR-TEST")
reasons = {m.fallback_reason for *_, m in res3}
covs3 = [traceability(p, t)[1] for _, p, t, _ in res3]
record("TC3", "Error/fallback: invalid AI API key", DOCX + " + key 'sk-ant-INVALID-KEY-FOR-TEST'",
       "No crash; warning shown; rule-based test cases still cover every AC of all 3 stories",
       "engine={}; reason='{}'; {} tests; coverage {}".format(
           "/".join(sorted({m.source for *_, m in res3})), "; ".join(reasons), sum(len(t) for _, _, t, _ in res3),
           "/".join("{:.0f}%".format(c) for c in covs3)),
       all(m.source == "fallback" for *_, m in res3) and all(c == 100 for c in covs3))

# TC4 - invalid document
try:
    extract_text("requirements.xlsx", b"PK\x03\x04 spreadsheet")
    record("TC4", "Error: wrong file type uploaded", "requirements.xlsx", "Clear message; nothing sent to AI",
           "No error raised", False)
except DocumentError as exc:
    record("TC4", "Error: wrong file type uploaded", "requirements.xlsx (spreadsheet)",
           "Clear message; nothing sent to AI", "DocumentError: " + str(exc), True)

# TC5 - Step 2: automation scripts for every story
res5 = run_design(ctx, stories, KEY) if KEY else [(s, p, design_tests(p, extract_boundaries(p.criteria), offline=True)[0], None)
                                                 for s, p in stories]
scripts = []
for s, p, tests, _ in res5:
    high = [t for t in tests if t.priority == "High"][:10] or tests[:10]
    code, info = generate_script(p, high, "http://localhost:3000", KEY, DEFAULT_MODEL, framework="cypress")
    scripts.append((s.key, info))
ok5 = all(i["messages"] and "syntax OK" in i["messages"][0] and "no unsafe calls" in i["messages"][0] for _, i in scripts)
record("TC5", "Test cases -> Cypress automation scripts", "High-priority test cases of US-101/102/103",
       "One Cypress spec per story; each passes node syntax check and safety rules",
       "; ".join("{}.cy.js by {} ({})".format(k, i["source"], i["messages"][0].split(" · ")[1] if i["messages"] else "?")
                 for k, i in scripts), ok5)

out = ["| ID | Scenario | Input | Expected | Actual | Result |", "|---|---|---|---|---|---|"]
out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "test_results.md").write_text("\n".join(out) + "\n")
print("Saved docs/test_results.md")
