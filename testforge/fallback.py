"""Offline, rule-based test design used when the AI is unavailable.

Applies classic test-design techniques mechanically:
  * one positive test per acceptance criterion (Given/When/Then split into steps)
  * one negative test per criterion (condition violated)
  * boundary-value tests from the BVA engine
It gives lower-quality, more generic tests than the LLM, but the QA engineer
always gets a traceable starting point.
"""
from __future__ import annotations

import re
from typing import List

from .models import AcceptanceCriterion, Boundary, TestCase, UserStory

GWT_RE = re.compile(r"given\s+(?P<g>.+?),?\s+when\s+(?P<w>.+?),?\s+then\s+(?P<t>.+)", re.IGNORECASE)


def _steps_for(ac: AcceptanceCriterion):
    m = GWT_RE.search(ac.text)
    if m:
        return m.group("g").strip(), ["Perform: " + m.group("w").strip()], m.group("t").strip().rstrip(".")
    return "User is on the relevant screen", ["Perform the action described in {}: {}".format(ac.ac_id, ac.text)], \
        "System behaves as stated: " + ac.text.rstrip(".")


def _short(text: str, n: int = 70) -> str:
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    text = text[0].lower() + text[1:] if text[:1].isupper() and not text[1:2].isupper() else text
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "..."


def generate_for_criterion(story: UserStory, ac: AcceptanceCriterion, boundaries: List[Boundary], start: int) -> List[TestCase]:
    pre, steps, expected = _steps_for(ac)
    role = story.role or "user"
    tcs = [
        TestCase("", "Verify {}".format(_short(expected if GWT_RE.search(ac.text) else ac.text)), [ac.ac_id], "Positive", "High",
                 "{}; logged in as {} where required".format(pre, role), steps + ["Observe the result"],
                 "Valid data that satisfies {}".format(ac.ac_id), expected, "rule-based"),
        TestCase("", "Verify invalid case is handled for {}: {}".format(ac.ac_id, _short(ac.text, 45)), [ac.ac_id], "Negative", "Medium",
                 pre, ["Perform the same action with data that violates: " + ac.text, "Observe the result"],
                 "Invalid / missing data for {}".format(ac.ac_id),
                 "The action is rejected or handled with a clear error message; no data is corrupted", "rule-based"),
    ]
    for b in [b for b in boundaries if b.ac_id == ac.ac_id]:
        for v in b.values:
            unit = (" " + b.unit) if b.unit else ""
            if re.match(r"(second|sec|minute|min|hour|day|year)", b.unit or "", re.I):
                ok = v["expect"] in ("accept", "action triggered")
                exp = "At {}{} the requirement ({}) is {}".format(
                    v["value"], unit, b.source_text.strip(), "met" if ok else "exceeded - behaviour beyond the limit applies (e.g. timeout/expiry) or a defect is raised")
            elif v["expect"] in ("accept", "action triggered"):
                exp = "{} of {}{} is accepted / behaves as specified".format(b.subject.capitalize(), v["value"], unit)
            else:
                exp = "{} of {}{} is rejected with a validation message".format(b.subject.capitalize(), v["value"], unit)
            tcs.append(TestCase(
                "", "BVA {}: {} = {}{} ({})".format(ac.ac_id, b.subject, v["value"], unit, v["label"]), [ac.ac_id],
                "Boundary", "High" if v["label"] in ("min-1", "max+1") else "Medium", pre,
                ["Enter/use {} = {}{}".format(b.subject, v["value"], unit), "Submit", "Observe the result"],
                "{} = {}{}".format(b.subject, v["value"], unit), exp, "rule-based"))
    return tcs


def fallback_generate(story: UserStory, boundaries: List[Boundary]) -> List[TestCase]:
    out: List[TestCase] = []
    for ac in story.criteria:
        out.extend(generate_for_criterion(story, ac, boundaries, len(out)))
    return out
