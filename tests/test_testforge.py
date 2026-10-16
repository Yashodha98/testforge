import json

import pytest

from conftest import SAMPLES
from testforge.ai import build_prompt, design_tests, validate_item
from testforge.automation import check_cypress, check_script, generate_script, to_csv, to_gherkin, traceability
from testforge.boundaries import extract_boundaries
from testforge.document import DocumentError, extract_text, split_stories
from testforge.models import AcceptanceCriterion
from testforge.privacy import mask
from testforge.story_parser import StoryParseError, parse_story


def login():
    """Main sample: patient registration story (6 ACs)."""
    s = parse_story((SAMPLES / "01_patient_registration.txt").read_text())
    return s, extract_boundaries(s.criteria)


# ---------------- parsing
def test_story_and_criteria_parsed():
    s, _ = login()
    assert s.role == "front-desk receptionist" and s.benefit.startswith("the patient can be booked")
    assert [c.ac_id for c in s.criteria] == ["AC1", "AC2", "AC3", "AC4", "AC5", "AC6"]


def test_bullet_list_criteria_parsed():
    s = parse_story((SAMPLES / "02_appointment_booking.txt").read_text())
    assert len(s.criteria) == 6 and "15-minute slots" in s.criteria[0].text


@pytest.mark.parametrize("bad", ["", "   ", None, "lorem ipsum 12345", "x" * 20000])
def test_invalid_input_rejected(bad):
    with pytest.raises(StoryParseError):
        parse_story(bad)


# ---------------- boundary value analysis
def test_range_boundaries():
    b = extract_boundaries([AcceptanceCriterion("AC1", "Password must be between 8 and 20 characters.")])[0]
    assert [v["value"] for v in b.values] == [7, 8, 9, 19, 20, 21]
    assert b.values[0]["expect"] == "reject" and b.values[-1]["expect"] == "reject"


def test_min_with_thousands_separator():
    b = extract_boundaries([AcceptanceCriterion("AC2", "Accepted when the subtotal is at least 2,500 LKR")])[0]
    assert (b.minimum, b.unit) == (2500, "LKR") and [v["value"] for v in b.values] == [2499, 2500, 2501]


def test_exact_length_boundary():
    b = extract_boundaries([AcceptanceCriterion("AC4", "The National ID must be exactly 10 digits")])[0]
    assert [(v["value"], v["expect"]) for v in b.values] == [(9, "reject"), (10, "accept"), (11, "reject")]


def test_no_boundaries_in_plain_text():
    assert extract_boundaries([AcceptanceCriterion("AC1", "An error message is shown.")]) == []


# ---------------- privacy
def test_masking():
    out, counts = mask("Contact nimal@abc.lk or 0771234567, password=Secret123, see http://jira.internal/x")
    assert "nimal@abc.lk" not in out and "0771234567" not in out and "Secret123" not in out and "jira.internal" not in out
    assert mask("use test@example.com")[0] == "use test@example.com"  # example.* test data is kept
    out, counts = mask("Patient MRN: 0045821, Iqama 2345678901, mobile 0551234567")
    assert counts == {"MRN": 1, "SAUDI_ID": 1, "PHONE": 1} and "2345678901" not in out


# ---------------- AI integration and fallback
def fake_ai(prompt):
    assert "AC6" in prompt and "61=reject" in prompt  # BVA hints are sent
    return json.dumps([
        {"title": "Verify login with valid credentials", "ac_refs": ["AC1"], "test_type": "positive", "priority": "High",
         "preconditions": "Verified account", "steps": ["Open login", "Enter valid email/password", "Click Log in"],
         "test_data": "user@example.com / Passw0rd", "expected_result": "Dashboard shown within 3 s"},
        {"title": "Verify login with valid credentials", "ac_refs": ["AC1"], "test_type": "Positive", "priority": "High",
         "preconditions": "x", "steps": ["a", "b"], "test_data": "-", "expected_result": "Dashboard shown within 3 s"},
        {"title": "Bad item", "ac_refs": ["AC99"], "steps": ["a"], "expected_result": "x"},
    ])


def test_ai_tests_validated_deduplicated_and_gaps_filled():
    s, b = login()
    tests, meta = design_tests(s, b, generate_fn=fake_ai)
    assert meta.duplicates_removed == 1 and meta.dropped_invalid == 1
    assert meta.source == "mixed" and set(meta.gaps_filled) == {"AC2", "AC3", "AC4", "AC5", "AC6"}
    assert tests[0].source == "gemini" and tests[0].test_type == "Positive"
    assert traceability(s, tests)[1] == 100.0
    assert [t.tc_id for t in tests][:2] == ["TC-001", "TC-002"]


def test_fallback_when_ai_raises():
    s, b = login()

    def boom(p):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")
    tests, meta = design_tests(s, b, generate_fn=boom)
    assert meta.source == "fallback" and "quota" in meta.fallback_reason
    assert tests and all(t.source == "rule-based" for t in tests)
    assert any(t.test_type == "Boundary" and "61 characters" in t.title for t in tests)


def test_fallback_on_non_json():
    s, b = login()
    tests, meta = design_tests(s, b, generate_fn=lambda p: "Here are your tests: ...")
    assert meta.source == "fallback" and "JSON" in meta.fallback_reason


def test_fallback_without_key():
    s, b = login()
    assert design_tests(s, b, api_key=None)[1].fallback_reason == "No AI API key configured (Claude or Gemini)."


def test_validate_item_normalises_fields():
    tc = validate_item({"title": "Verify x", "ac_refs": "AC1, ac2", "test_type": "BOUNDARY", "priority": "urgent",
                        "steps": "1. Open\n2. Click", "expected_result": "Shown"}, {"AC1", "AC2"})
    assert tc.ac_refs == ["AC1", "AC2"] and tc.test_type == "Boundary" and tc.priority == "Medium"
    assert tc.steps == ["Open", "Click"]


def test_prompt_masks_confidential_data():
    s = parse_story("As a clerk, I want to email reports to boss@acme.lk, so that he knows.\n- Report is sent daily to boss@acme.lk")
    prompt, counts = build_prompt(s, [])
    assert "boss@acme.lk" not in prompt and counts["EMAIL"] >= 1


# ---------------- feature 2
def test_exports_and_script_fallback():
    s, b = login()
    tests, _ = design_tests(s, b, offline=True)
    csv_text = to_csv(s, tests)
    assert csv_text.splitlines()[0].startswith("ID,Title,Section,Type,Priority,References")
    feature = to_gherkin(s, tests)
    assert feature.startswith("Feature: Register a new patient") and feature.count("Scenario:") == len(tests)
    code, info = generate_script(s, tests[:3], offline=True)  # default framework = Cypress
    assert info["source"] == "template fallback" and info["framework"] == "cypress"
    assert check_cypress(code, 3)[0] and code.count("it('TC-") == 3
    code, info = generate_script(s, tests[:3], offline=True, framework="playwright")
    assert check_script(code, 3)[0]


def test_ai_script_accepted_when_valid():
    s, b = login()
    tests, _ = design_tests(s, b, offline=True)
    good = "```python\nimport os\nfrom playwright.sync_api import Page, expect\nBASE_URL=os.getenv('BASE_URL')\n\ndef test_tc_001(page: Page):\n    page.goto(BASE_URL)\n    expect(page.get_by_text('Dashboard')).to_be_visible()\n```"
    code, info = generate_script(s, tests[:1], generate_fn=lambda p: good, framework="playwright")
    assert info["source"] == "gemini" and code.startswith("import os")


def test_ai_cypress_spec_accepted_when_valid():
    s, b = login()
    tests, _ = design_tests(s, b, offline=True)
    spec = "```javascript\ndescribe('Register patient', () => {\n  it('TC-001 (AC1) saves patient', () => {\n    cy.visit('/');\n    cy.get('[data-cy=save]').click();\n    cy.contains('MRN').should('be.visible');\n  });\n});\n```"
    code, info = generate_script(s, tests[:1], generate_fn=lambda p: spec)
    assert info["source"] == "gemini" and info["framework"] == "cypress" and code.startswith("describe(")


@pytest.mark.parametrize("bad_code,reason", [
    ("describe('x', () => { it('a', () => { cy.visit('/') ) })", "Syntax error"),
    ("describe('x', () => { it('a', () => { cy.exec('rm -rf /') }) })", "Unsafe call"),
    ("const cp = require('child_process'); describe('x', () => { it('a', () => {}) })", "Unsafe call"),
    ("console.log('no tests')", "No Cypress"),
])
def test_unsafe_or_broken_cypress_rejected(bad_code, reason):
    s, b = login()
    tests, _ = design_tests(s, b, offline=True)
    code, info = generate_script(s, tests[:2], generate_fn=lambda p: bad_code)
    assert info["source"] == "template fallback" and reason in info["reason"]


@pytest.mark.parametrize("bad_code,reason", [
    ("def test_a(:\n  pass", "Syntax error"),
    ("import subprocess\ndef test_a(page):\n    subprocess.run(['ls'])", "Unsafe module"),
    ("import os\ndef test_a(page):\n    os.system('rm -rf /')", "Unsafe call"),
    ("print('no tests')", "No pytest test functions"),
])
def test_unsafe_or_broken_ai_script_rejected(bad_code, reason):
    s, b = login()
    tests, _ = design_tests(s, b, offline=True)
    code, info = generate_script(s, tests[:2], generate_fn=lambda p: bad_code, framework="playwright")
    assert info["source"] == "template fallback" and reason in info["reason"]


# ---------------- Confluence document input
DOCS = SAMPLES.parent / "sample_docs"


@pytest.mark.parametrize("fname", [
    "Confluence_HMS_Outpatient_Requirements.docx",
    "Confluence_HMS_Outpatient_Requirements.pdf",
    "Confluence_HMS_Outpatient_Requirements.html",
    "Confluence_HMS_Outpatient_Requirements_word-export.doc",
])
def test_confluence_exports_split_into_stories(fname):
    text = extract_text(fname, (DOCS / fname).read_bytes())
    context, stories = split_stories(text)
    assert "Hospital Management System" in context
    assert [s.key for s in stories] == ["US-101", "US-102", "US-103"]
    assert [len(parse_story(s.text).criteria) for s in stories] == [5, 4, 3]


def test_single_story_text_is_one_section():
    text = (SAMPLES / "02_appointment_booking.txt").read_text()
    _, stories = split_stories(text)
    assert len(stories) == 1 and len(parse_story(stories[0].text).criteria) == 6


@pytest.mark.parametrize("name,data,msg", [
    ("req.xlsx", b"abc", "Unsupported file type"),
    ("req.pdf", b"", "empty"),
    ("req.pdf", b"%PDF-1.4 broken", "Could not read"),
    ("req.txt", b"12 34 !! ??", "No readable text"),
    ("req.doc", b"\xd0\xcf\x11\xe0" + b"0" * 100, "legacy binary Word"),
])
def test_bad_documents_rejected(name, data, msg):
    with pytest.raises(DocumentError) as e:
        extract_text(name, data)
    assert msg in str(e.value)


def test_page_context_is_sent_to_ai():
    text = extract_text("x.html", (DOCS / "Confluence_HMS_Outpatient_Requirements.html").read_bytes())
    context, stories = split_stories(text)
    story = parse_story(stories[0].text)
    seen = {}

    def capture(prompt):
        seen["p"] = prompt
        return "[]"
    design_tests(story, extract_boundaries(story.criteria), generate_fn=capture, context=context)
    assert "Page context" in seen["p"] and "Hospital Management System" in seen["p"]


def test_provider_detection_routes_claude_keys():
    from testforge.ai import model_for, provider
    assert provider("sk-ant-api03-xyz") == "claude" and provider("AIzaSyXYZ") == "gemini" and provider(None) == "gemini"
    assert model_for("sk-ant-api03-xyz").startswith("claude-") and model_for("AIza", "gemini-2.5-flash") == "gemini-2.5-flash"


def test_invalid_claude_key_falls_back(monkeypatch):
    import anthropic
    from testforge import ai

    def boom(api_key, model):
        def _gen(prompt):
            raise anthropic.AuthenticationError("bad key", response=__import__("httpx").Response(
                401, request=__import__("httpx").Request("POST", "https://api.anthropic.com")), body=None)
        return _gen
    monkeypatch.setattr(ai, "_claude_client", boom)
    s, b = login()
    tests, meta = ai.design_tests(s, b, api_key="sk-ant-invalid")
    assert meta.source == "fallback" and "Claude rejected the API key" in meta.fallback_reason and tests
