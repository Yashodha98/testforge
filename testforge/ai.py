"""Generative-AI test design (Feature 1) using Anthropic Claude or Google Gemini.

story + BVA hints -> masked prompt -> JSON test cases -> validation ->
de-duplication -> coverage-gap filling -> numbered test suite.
Any failure falls back to the rule-based generator, and the UI states which
engine produced each test case.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, List, Optional, Tuple

from .boundaries import Boundary
from .fallback import fallback_generate, generate_for_criterion
from .models import PRIORITIES, TEST_TYPES, GenerationMeta, TestCase, UserStory
from .privacy import mask

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
# low = faster and cheaper (useful for live demos); medium = default; high = most thorough
TESTFORGE_EFFORT = os.getenv("TESTFORGE_EFFORT", "medium")
TIMEOUT_MS = 60_000
MAX_TESTS = 40

GenerateFn = Callable[[str], str]

# Running total of Claude token usage in this process (used for the cost estimate).
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

TEST_DESIGN_PROMPT = """You are a senior software QA engineer (ISTQB Advanced Test Analyst).
Design a concise but complete manual test suite for the user story below.

Rules:
- Every acceptance criterion (AC) must be covered by at least one Positive test, and by a Negative test wherever invalid input or failure is possible.
- Use the boundary values provided (Boundary Value Analysis) as Boundary tests; do not invent different limits.
- Add Edge or Security tests only where clearly relevant (e.g. lockout, injection, session, concurrency, duplicate records,
  and in healthcare systems: patient safety, wrong-patient errors and access to patient data).
- Steps must be concrete and executable by a tester; expected results must be observable and specific (exact messages where the story gives them).
- Do not invent features that are not in the story. If something is unclear, write the test for the stated behaviour only.
- Between 8 and 30 test cases in total. No duplicates.

Return ONLY a JSON array. Each element must have exactly these keys:
  "title": string (starts with "Verify"),
  "ac_refs": array of AC ids, e.g. ["AC1"],
  "test_type": one of "Positive","Negative","Boundary","Edge","Security","Usability",
  "priority": one of "High","Medium","Low",
  "preconditions": string,
  "steps": array of 2-6 short imperative strings,
  "test_data": string,
  "expected_result": string
"""


def build_prompt(story: UserStory, boundaries: List[Boundary], context: str = "") -> Tuple[str, dict]:
    ac_lines = "\n".join("{}: {}".format(c.ac_id, c.text) for c in story.criteria)
    bva = "\n".join(
        "{}: {} ({}) -> {}".format(
            b.ac_id, b.subject, b.source_text.strip(),
            ", ".join("{}={}".format(v["value"], v["expect"]) for v in b.values))
        for b in boundaries) or "(none detected)"
    ctx = "\n\n## Page context (from the Confluence page - background only)\n{}".format(context.strip()[:1500]) if context.strip() else ""
    body = "\n\n## User story\nTitle: {}\nAs a {}, I want to {}, so that {}.\n\n## Acceptance criteria\n{}\n\n## Boundary values\n{}".format(
        story.title, story.role or "user", story.goal or "(not stated)",
        story.benefit or "(not stated)", ac_lines, bva)
    return mask(TEST_DESIGN_PROMPT + ctx + body)


def provider(api_key: Optional[str]) -> str:
    """Claude keys start with 'sk-ant-'; anything else is treated as a Gemini key."""
    return "claude" if (api_key or "").strip().startswith("sk-ant-") else "gemini"


def model_for(api_key: Optional[str], model: str = DEFAULT_MODEL) -> str:
    return CLAUDE_MODEL if provider(api_key) == "claude" else model


def _claude_client(api_key: str, model: str) -> GenerateFn:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_MS / 1000 * 3)

    def _generate(prompt: str) -> str:
        with client.messages.stream(
            model=model, max_tokens=64000,
            output_config={"effort": TESTFORGE_EFFORT},
            messages=[{"role": "user", "content": prompt}],
            # If a safety classifier declines, the server retries on a suitable fallback model.
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        ) as stream:
            msg = stream.get_final_message()
        USAGE["calls"] += 1
        USAGE["input_tokens"] += msg.usage.input_tokens
        USAGE["output_tokens"] += msg.usage.output_tokens
        if msg.stop_reason == "refusal":
            raise RuntimeError("Claude declined the request (refusal).")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("Claude answer was cut off (max_tokens).")
        return "".join(b.text for b in msg.content if b.type == "text")

    return _generate


def _client(api_key: str, model: str, json_mode: bool) -> GenerateFn:
    if provider(api_key) == "claude":
        return _claude_client(api_key, CLAUDE_MODEL)

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))

    def _generate(prompt: str) -> str:
        cfg = types.GenerateContentConfig(
            temperature=0.3, response_mime_type="application/json" if json_mode else "text/plain")
        return client.models.generate_content(model=model, contents=prompt, config=cfg).text or ""

    return _generate


def _claude_error(exc: Exception) -> Optional[str]:
    try:
        import anthropic
    except ImportError:
        return None
    if isinstance(exc, anthropic.AuthenticationError):
        return "Claude rejected the API key (invalid or revoked)."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "Claude API key is not permitted to use this model."
    if isinstance(exc, anthropic.NotFoundError):
        return "Claude model not found - check the CLAUDE_MODEL setting."
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude rate limit reached (HTTP 429)."
    if isinstance(exc, anthropic.BadRequestError) and "credit" in str(exc).lower():
        return "Claude account has no credit left - add credits in the Anthropic Console."
    if isinstance(exc, anthropic.APITimeoutError):
        return "Claude request timed out."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach the Claude API (network problem)."
    if isinstance(exc, anthropic.APIStatusError):
        return "Claude call failed (HTTP {}).".format(exc.status_code)
    return None


def friendly_error(exc: Exception) -> str:
    claude = _claude_error(exc)
    if claude:
        return claude
    if str(exc).startswith("Claude "):
        return str(exc)
    low = str(exc).lower()
    if "api key" in low or "api_key_invalid" in low or "permission" in low or "401" in low or "403" in low:
        return "Gemini rejected the API key (invalid or not permitted)."
    if "429" in low or "quota" in low or "resource_exhausted" in low:
        return "Gemini free-tier quota/rate limit reached (HTTP 429)."
    if "404" in low or "not found" in low:
        return "Gemini model not found - check the GEMINI_MODEL setting."
    if "timeout" in low or "timed out" in low or "deadline" in low:
        return "Gemini request timed out."
    if "connect" in low or "network" in low or "resolution" in low:
        return "Could not reach the Gemini API (network problem)."
    return "Gemini call failed: {}".format(str(exc)[:160])


def _parse_json(text: str):
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("test_cases") or data.get("tests") or [data]
    if not isinstance(data, list):
        raise ValueError("not a list")
    return data


def _norm_choice(value, options, default):
    v = str(value or "").strip().lower()
    for o in options:
        if v == o.lower():
            return o
    return default


def validate_item(item: dict, valid_acs: set) -> Optional[TestCase]:
    if not isinstance(item, dict):
        return None
    try:
        refs = item.get("ac_refs") or []
        if isinstance(refs, str):
            refs = re.findall(r"AC\d+", refs, re.I)
        refs = [str(r).upper().replace(" ", "") for r in refs]
        refs = [r for r in refs if r in valid_acs]
        steps = item.get("steps") or []
        if isinstance(steps, str):
            steps = [s.strip() for s in re.split(r"\n|\d+\.\s", steps) if s.strip()]
        steps = [re.sub(r"^\s*\d+[.)]\s*", "", str(s)).strip() for s in steps if str(s).strip()][:8]
        title = str(item.get("title") or "").strip()
        expected = str(item.get("expected_result") or "").strip()
        if not refs or not steps or not title or not expected:
            return None
        return TestCase(
            tc_id="", title=title[:160], ac_refs=sorted(set(refs), key=lambda r: int(r[2:])),
            test_type=_norm_choice(item.get("test_type"), TEST_TYPES, "Positive"),
            priority=_norm_choice(item.get("priority"), PRIORITIES, "Medium"),
            preconditions=str(item.get("preconditions") or "-").strip(),
            steps=steps, test_data=str(item.get("test_data") or "-").strip(),
            expected_result=expected, source="gemini")
    except (TypeError, ValueError, AttributeError):
        return None


def _dedupe(tests: List[TestCase]) -> Tuple[List[TestCase], int]:
    seen, out = set(), []
    for t in tests:
        key = re.sub(r"[^a-z0-9]+", " ", (t.title + "|" + t.expected_result).lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out, len(tests) - len(out)


TYPE_ORDER = {t: i for i, t in enumerate(TEST_TYPES)}


def number(tests: List[TestCase]) -> List[TestCase]:
    order = {"High": 0, "Medium": 1, "Low": 2}
    tests.sort(key=lambda t: (int(t.ac_refs[0][2:]) if t.ac_refs else 99, TYPE_ORDER.get(t.test_type, 9),
                              order.get(t.priority, 3)))
    for i, t in enumerate(tests, 1):
        t.tc_id = "TC-{:03d}".format(i)
    return tests


def design_tests(
    story: UserStory,
    boundaries: List[Boundary],
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    offline: bool = False,
    generate_fn: Optional[GenerateFn] = None,
    context: str = "",
) -> Tuple[List[TestCase], GenerationMeta]:
    engine = provider(api_key)
    model = model_for(api_key, model)
    meta = GenerationMeta(source=engine, model=model)

    def _fallback(reason: str):
        meta.source, meta.fallback_reason = "fallback", reason
        return number(fallback_generate(story, boundaries)), meta

    if offline:
        return _fallback("Offline mode selected by the user.")
    if generate_fn is None:
        if not api_key:
            return _fallback("No AI API key configured (Claude or Gemini).")
        try:
            generate_fn = _client(api_key, model, json_mode=True)
        except Exception as exc:
            return _fallback(friendly_error(exc))

    prompt, _ = build_prompt(story, boundaries, context)
    start = time.time()
    try:
        data = _parse_json(generate_fn(prompt))
    except json.JSONDecodeError:
        return _fallback("{} returned text that was not valid JSON.".format(engine.title()))
    except Exception as exc:
        return _fallback(friendly_error(exc))
    finally:
        meta.latency_ms = int((time.time() - start) * 1000)

    valid_acs = {c.ac_id for c in story.criteria}
    tests = []
    for item in data[:MAX_TESTS * 2]:
        tc = validate_item(item, valid_acs)
        if tc:
            tc.source = engine
            tests.append(tc)
        else:
            meta.dropped_invalid += 1
    if not tests:
        return _fallback("{} answer did not contain any valid test cases.".format(engine.title()))
    tests, meta.duplicates_removed = _dedupe(tests)
    tests = tests[:MAX_TESTS]

    # Coverage guarantee: every AC must have at least one test.
    covered = {r for t in tests for r in t.ac_refs}
    for ac in story.criteria:
        if ac.ac_id not in covered:
            tests.extend(generate_for_criterion(story, ac, boundaries, len(tests))[:2])
            meta.gaps_filled.append(ac.ac_id)
    if meta.gaps_filled:
        meta.source = "mixed"
    return number(tests), meta
