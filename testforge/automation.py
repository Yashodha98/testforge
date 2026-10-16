"""Feature 2 - traceability, export and AI test-automation scripts.

Takes the reviewed test cases from Feature 1 and produces:
  * a requirements traceability matrix and coverage figures
  * a TestRail / Jira-Xray compatible CSV and a Gherkin .feature file
  * a Cypress (default - the professional's tool) or Playwright automation
    spec written by Gemini, statically checked (syntax, structure, unsafe
    calls) before it is shown; generated code is never executed
"""
from __future__ import annotations

import ast
import csv
import io
import re
import time
from typing import Dict, List, Optional, Tuple

from .ai import DEFAULT_MODEL, GenerateFn, _client, friendly_error, provider
from .models import TestCase, UserStory
from .privacy import mask

UNSAFE_BUILTINS = {"eval", "exec", "__import__"}
UNSAFE_OS_CALLS = {"system", "popen", "remove", "unlink", "rmdir", "removedirs", "kill", "execv", "execl", "spawnl"}
UNSAFE_MODULES = {"subprocess", "shutil", "socket", "ctypes", "pickle"}


# ------------------------------------------------------------------ traceability
def traceability(story: UserStory, tests: List[TestCase]) -> Tuple[List[Dict], float]:
    rows = []
    for ac in story.criteria:
        linked = [t for t in tests if ac.ac_id in t.ac_refs]
        types = sorted({t.test_type for t in linked})
        rows.append({
            "AC": ac.ac_id, "Criterion": ac.text, "Tests": ", ".join(t.tc_id for t in linked) or "-",
            "# Tests": len(linked), "Types": ", ".join(types) or "-",
            "Status": "Covered" if len(linked) >= 2 else ("Partial" if linked else "NOT COVERED"),
        })
    covered = sum(1 for r in rows if r["# Tests"] > 0)
    return rows, (100.0 * covered / len(rows)) if rows else 0.0


# ------------------------------------------------------------------ exports
def to_csv(story: UserStory, tests: List[TestCase]) -> str:
    """TestRail 'Test Cases (Steps)' style CSV; also importable to Jira Xray / Zephyr with column mapping."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID", "Title", "Section", "Type", "Priority", "References", "Preconditions", "Steps", "Test Data", "Expected Result"])
    for t in tests:
        w.writerow([t.tc_id, t.title, story.title, t.test_type, t.priority, " ".join(t.ac_refs), t.preconditions,
                    "\n".join("{}. {}".format(i, s) for i, s in enumerate(t.steps, 1)), t.test_data, t.expected_result])
    return buf.getvalue()


def to_gherkin(story: UserStory, tests: List[TestCase]) -> str:
    lines = ["Feature: {}".format(story.title)]
    if story.role:
        lines += ["  As a {}".format(story.role), "  I want to {}".format(story.goal or "")]
        if story.benefit:
            lines.append("  So that {}".format(story.benefit))
    for t in tests:
        tags = " ".join(["@" + t.tc_id, "@" + t.test_type.lower(), "@priority-" + t.priority.lower()] + ["@" + r for r in t.ac_refs])
        lines += ["", "  " + tags, "  Scenario: {}".format(t.title)]
        lines.append("    Given {}".format(_lc(t.preconditions)))
        for i, s in enumerate(t.steps):
            lines.append("    {} {}".format("When" if i == 0 else "And", _lc(s)))
        lines.append("    Then {}".format(_lc(t.expected_result)))
    return "\n".join(lines) + "\n"


def _lc(s: str) -> str:
    s = (s or "-").strip().rstrip(".")
    return s[0].lower() + s[1:] if s and not s[:2].isupper() else s


# ------------------------------------------------------------------ automation script
SCRIPT_PROMPT = """You are a senior QA automation engineer. Write a Python Playwright test module using pytest-playwright
(the built-in `page` fixture and `from playwright.sync_api import Page, expect`).

Requirements:
- One test function per test case, named test_<tc_id in lowercase with underscores>_<short_slug>, with a docstring containing the TC id and AC refs.
- BASE_URL = os.getenv("BASE_URL", "{base_url}") at module level; read any credentials from environment variables, never hard-code real passwords.
- Prefer accessible locators: page.get_by_role, get_by_label, get_by_text, get_by_placeholder. Where the real selector is unknown, use the most likely accessible locator and add a "# TODO: confirm selector" comment.
- Use expect(...) assertions that check the expected result of each test case (exact messages where given).
- Use pytest.mark.parametrize for Boundary tests of the same field where sensible.
- Do not use subprocess, os.system, eval/exec, file deletion or network calls other than through `page`.
- Output ONLY the Python code, no explanations, no markdown fences.

User story: {story}

Test cases (JSON):
{tests}
"""


CYPRESS_PROMPT = """You are a senior QA automation engineer. Write ONE Cypress end-to-end spec file in JavaScript (Cypress 13+).

Requirements:
- One describe() block named after the story, and one it() per test case. The it() title must start with the TC id, e.g. it('TC-003 (AC2) ...', ...).
- Use cy.visit('/') with the baseUrl configured in cypress.config.js (assume baseUrl = "{base_url}"); read any credentials with Cypress.env('...'), never hard-code real passwords or patient data.
- Prefer stable selectors: cy.get('[data-cy=...]') or cy.contains(); where the real selector is unknown, use the most likely data-cy name and add a "// TODO: confirm selector" comment.
- Assert the expected result of each test case with .should(...) (exact messages where given).
- For Boundary tests of the same field, use a data array with forEach to generate the it() blocks where sensible.
- Do not use cy.exec, cy.task, require('child_process'), eval, new Function, or file-system access.
- Output ONLY the JavaScript code, no explanations, no markdown fences.

User story: {story}

Test cases (JSON):
{tests}
"""


def build_script_prompt(story: UserStory, tests: List[TestCase], base_url: str, framework: str = "cypress") -> str:
    import json

    compact = [{"tc_id": t.tc_id, "title": t.title, "ac_refs": t.ac_refs, "type": t.test_type,
                "preconditions": t.preconditions, "steps": t.steps, "test_data": t.test_data,
                "expected_result": t.expected_result} for t in tests]
    template = CYPRESS_PROMPT if framework == "cypress" else SCRIPT_PROMPT
    prompt = template.format(base_url=base_url, story="{} - As a {}, I want to {}".format(
        story.title, story.role or "user", story.goal or ""), tests=json.dumps(compact, indent=1))
    return mask(prompt)[0]


def strip_fences(code: str) -> str:
    m = re.search(r"```(?:python|py|javascript|js|typescript|ts)?\s*\n(.*?)```", code or "", re.S)
    return (m.group(1) if m else (code or "")).strip() + "\n"


def check_script(code: str, expected_tests: int) -> Tuple[bool, List[str]]:
    """Static checks on generated code. Returns (ok, messages). ok=False means unusable."""
    msgs: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, ["Syntax error on line {}: {}".format(exc.lineno, exc.msg)]
    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    if not funcs:
        return False, ["No pytest test functions (def test_...) were generated."]
    imports = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in UNSAFE_BUILTINS:
                return False, ["Unsafe call '{}()' found - script rejected.".format(f.id)]
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "os"
                    and f.attr in UNSAFE_OS_CALLS):
                return False, ["Unsafe call 'os.{}()' found - script rejected.".format(f.attr)]
    bad = imports & UNSAFE_MODULES
    if bad:
        return False, ["Unsafe module import(s) {} - script rejected.".format(", ".join(sorted(bad)))]
    if "playwright" not in imports:
        msgs.append("Warning: playwright is not imported.")
    if len(funcs) < expected_tests:
        msgs.append("Note: {} test functions for {} selected test cases (some may be parametrised).".format(len(funcs), expected_tests))
    if re.search(r"(?i)password\s*=\s*['\"][^'\"]{4,}['\"]", code) and "getenv" not in code:
        msgs.append("Warning: a hard-coded password literal was found - move it to an environment variable.")
    msgs.insert(0, "Syntax OK · {} test functions · no unsafe calls".format(len(funcs)))
    return True, msgs


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


def template_script(story: UserStory, tests: List[TestCase], base_url: str) -> str:
    """Deterministic fallback: a runnable skeleton with steps as comments."""
    out = ['"""Playwright tests for: {}'.format(story.title),
           "Generated by TestForge (rule-based template fallback). Fill in the TODOs.", '"""',
           "import os", "", "from playwright.sync_api import Page, expect", "",
           'BASE_URL = os.getenv("BASE_URL", "{}")'.format(base_url), ""]
    for t in tests:
        out += ["", "def test_{}_{}(page: Page):".format(t.tc_id.lower().replace("-", "_"), _slug(t.title)),
                '    """{} ({}) - {}"""'.format(t.tc_id, ", ".join(t.ac_refs), t.title.replace('"', "'")),
                "    # Preconditions: {}".format(t.preconditions),
                "    # Test data: {}".format(t.test_data),
                "    page.goto(BASE_URL)"]
        for i, s in enumerate(t.steps, 1):
            out.append("    # Step {}: {}  # TODO: implement".format(i, s))
        out += ["    # Expected: {}".format(t.expected_result),
                "    expect(page).to_have_url(re.compile(\".*\"))  # TODO: replace with the real assertion"]
    code = "\n".join(out) + "\n"
    return code.replace("import os\n", "import os\nimport re\n", 1)


CY_UNSAFE = [
    (r"\bcy\.exec\s*\(", "cy.exec()"), (r"\bcy\.task\s*\(", "cy.task()"),
    (r"child_process", "child_process"), (r"\beval\s*\(", "eval()"), (r"new\s+Function\s*\(", "new Function()"),
    (r"require\(\s*['\"]fs['\"]\s*\)", "fs module"), (r"process\.exit", "process.exit"),
]


def _js_syntax_ok(code: str) -> Tuple[Optional[bool], str]:
    """Uses `node --check` when Node.js is installed. Returns (ok|None if unavailable, message)."""
    import os
    import shutil as _sh
    import subprocess as _sp
    import tempfile

    node = _sh.which("node")
    if not node:
        return None, "Node.js not found - JavaScript syntax not verified"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(code)
        path = fh.name
    try:
        r = _sp.run([node, "--check", path], capture_output=True, text=True, timeout=20)
    finally:
        os.unlink(path)
    if r.returncode == 0:
        return True, "JavaScript syntax OK (node --check)"
    err = next((ln for ln in r.stderr.splitlines() if "Error" in ln), r.stderr.strip()[:160])
    return False, "Syntax error: " + err


def check_cypress(code: str, expected_tests: int) -> Tuple[bool, List[str]]:
    """Static checks on a generated Cypress spec. The code itself is never executed."""
    for pat, label in CY_UNSAFE:
        if re.search(pat, code):
            return False, ["Unsafe call '{}' found - script rejected.".format(label)]
    ok, msg = _js_syntax_ok(code)
    if ok is False:
        return False, [msg]
    if ok is None and (code.count("{") != code.count("}") or code.count("(") != code.count(")")):
        return False, ["Unbalanced brackets - script rejected."]
    its = len(re.findall(r"\bit\s*\(", code))
    if "describe(" not in code or its == 0:
        return False, ["No Cypress describe()/it() blocks were generated."]
    msgs = ["{} · {} it() blocks · no unsafe calls".format(msg, its)]
    if its < expected_tests and "forEach" not in code:
        msgs.append("Note: {} it() blocks for {} selected test cases.".format(its, expected_tests))
    if re.search(r"(?i)password['\"]?\s*[:,]\s*['\"][^'\"]{4,}['\"]", code) and "Cypress.env" not in code:
        msgs.append("Warning: a hard-coded password literal was found - use Cypress.env().")
    return True, msgs


def _js(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").replace("\\", "\\\\").replace("'", "\\'")


def _c(s: str) -> str:
    """Make text safe inside a single-line // comment or /* */ block."""
    return re.sub(r"\s+", " ", s or "").replace("*/", "* /")


def template_cypress(story: UserStory, tests: List[TestCase], base_url: str) -> str:
    """Deterministic fallback: a Cypress skeleton with steps as comments."""
    out = ["/**", " * Cypress tests for: {}".format(_c(story.title)),
           " * Generated by TestForge (rule-based template fallback). Fill in the TODOs.",
           " * Set baseUrl = '{}' in cypress.config.js".format(base_url), " */", "",
           "describe('{}', () => {{".format(_js(story.title))]
    for t in tests:
        out += ["  it('{} ({}) {}', () => {{".format(t.tc_id, ", ".join(t.ac_refs), _js(t.title)),
                "    // Preconditions: {}".format(_c(t.preconditions)),
                "    // Test data: {}".format(_c(t.test_data)),
                "    cy.visit('/');"]
        for i, st_ in enumerate(t.steps, 1):
            out.append("    // Step {}: {}  // TODO: implement".format(i, _c(st_)))
        out += ["    // Expected: {}".format(_c(t.expected_result)),
                "    cy.get('body').should('be.visible'); // TODO: replace with the real assertion",
                "  });", ""]
    out.append("});")
    return "\n".join(out) + "\n"


def check_any(code: str, n: int, framework: str):
    return check_cypress(code, n) if framework == "cypress" else check_script(code, n)


def template_any(story, tests, base_url, framework):
    return template_cypress(story, tests, base_url) if framework == "cypress" else template_script(story, tests, base_url)


def generate_script(
    story: UserStory, tests: List[TestCase], base_url: str = "http://localhost:3000",
    api_key: Optional[str] = None, model: str = DEFAULT_MODEL, offline: bool = False,
    generate_fn: Optional[GenerateFn] = None, framework: str = "cypress",
) -> Tuple[str, Dict]:
    """Returns (code, info). framework: "cypress" (default) or "playwright"."""
    if framework not in ("cypress", "playwright"):
        raise ValueError("Unsupported framework: " + framework)
    info: Dict = {"source": provider(api_key), "messages": [], "latency_ms": None, "reason": None, "framework": framework}

    def _fallback(reason: str):
        code = template_any(story, tests, base_url, framework)
        ok, msgs = check_any(code, len(tests), framework)
        info.update(source="template fallback", reason=reason, messages=msgs)
        return code, info

    if not tests:
        raise ValueError("Select at least one test case to automate.")
    if offline:
        return _fallback("Offline mode selected by the user.")
    if generate_fn is None:
        if not api_key:
            return _fallback("No AI API key configured (Claude or Gemini).")
        try:
            generate_fn = _client(api_key, model, json_mode=False)
        except Exception as exc:
            return _fallback(friendly_error(exc))
    start = time.time()
    try:
        code = strip_fences(generate_fn(build_script_prompt(story, tests, base_url, framework)))
    except Exception as exc:
        return _fallback(friendly_error(exc))
    info["latency_ms"] = int((time.time() - start) * 1000)
    ok, msgs = check_any(code, len(tests), framework)
    if not ok:
        return _fallback("AI script failed validation: " + "; ".join(msgs))
    info["messages"] = msgs
    return code, info
