"""Parses a free-text user story into role / goal / benefit and acceptance criteria."""
from __future__ import annotations

import re
from typing import List

from .models import AcceptanceCriterion, UserStory

MAX_CHARS = 15_000
MIN_CHARS = 25
MAX_CRITERIA = 25

STORY_RE = re.compile(
    r"as an?\s+(?P<role>.+?),?\s+i\s+(?:want|need|would like)\s+(?:to\s+)?(?P<goal>.+?)"
    r"(?:,?\s+so\s+that\s+(?P<benefit>.+?))?(?:\.|\n|$)",
    re.IGNORECASE | re.DOTALL,
)
AC_HEADER_RE = re.compile(r"^\s*(acceptance criteria|acceptance criterion|criteria|ac)\s*:?\s*$", re.IGNORECASE)
TITLE_RE = re.compile(r"^\s*(?:title|story|user story)\s*[:\-]\s*(.+)$", re.IGNORECASE)
BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|AC\s*\d+\s*[:.)\-]|\[\s?\]|scenario\s*\d*\s*:)\s*(.+)$", re.IGNORECASE)
GWT_START_RE = re.compile(r"^\s*given\b", re.IGNORECASE)
GWT_CONT_RE = re.compile(r"^\s*(when|then|and|but)\b", re.IGNORECASE)


class StoryParseError(ValueError):
    """Raised when the input cannot be used as a requirement."""


def parse_story(text: str) -> UserStory:
    if text is None or not text.strip():
        raise StoryParseError("The requirement is empty. Paste a user story with acceptance criteria.")
    text = text.strip()
    if len(text) < MIN_CHARS:
        raise StoryParseError("The requirement is too short to design tests from ({} characters).".format(len(text)))
    if len(text) > MAX_CHARS:
        raise StoryParseError("The requirement is longer than {:,} characters. Split it into smaller stories.".format(MAX_CHARS))
    if not re.search(r"[A-Za-z]{3,}", text):
        raise StoryParseError("No readable words were found in the requirement.")

    lines = [ln.rstrip() for ln in text.splitlines()]
    warnings: List[str] = []

    title = None
    for ln in lines:
        m = TITLE_RE.match(ln)
        if m:
            title = m.group(1).strip()
            break

    m = STORY_RE.search(text)
    role = goal = benefit = None
    if m:
        role = m.group("role").strip()
        goal = m.group("goal").strip().rstrip(".")
        benefit = (m.group("benefit") or "").strip().rstrip(".") or None
    else:
        warnings.append("No 'As a <role>, I want <goal>, so that <benefit>' sentence was found.")

    criteria = _extract_criteria(lines)
    if not criteria:
        # Fall back: use each sentence after the story sentence as a criterion.
        body = text[m.end():] if m else text
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", body) if len(s.strip()) > 15]
        criteria = [AcceptanceCriterion("AC{}".format(i), s) for i, s in enumerate(sentences[:MAX_CRITERIA], 1)]
        if criteria:
            warnings.append("No explicit acceptance criteria list found - each sentence was treated as a criterion.")
    if not criteria:
        if goal:
            criteria = [AcceptanceCriterion("AC1", "User can " + goal)]
            warnings.append("No acceptance criteria found - tests will be derived from the goal only (low coverage).")
        else:
            raise StoryParseError(
                "No user story or acceptance criteria were recognised. Use the format 'As a..., I want..., so that...' "
                "followed by a list of acceptance criteria."
            )
    if len(criteria) > MAX_CRITERIA:
        warnings.append("Only the first {} acceptance criteria are used.".format(MAX_CRITERIA))
        criteria = criteria[:MAX_CRITERIA]

    if not title:
        title = (goal[:70] if goal else criteria[0].text[:70]).strip()
    return UserStory(title=title, role=role, goal=goal, benefit=benefit, criteria=criteria, raw=text, warnings=warnings)


def _extract_criteria(lines: List[str]) -> List[AcceptanceCriterion]:
    items: List[str] = []
    in_ac = False
    current_gwt = None
    for ln in lines:
        if not ln.strip():
            if current_gwt:
                items.append(current_gwt)
                current_gwt = None
            continue
        if AC_HEADER_RE.match(ln):
            in_ac = True
            continue
        if STORY_RE.match(ln.strip()) or TITLE_RE.match(ln):
            continue
        stripped = ln.strip()
        bullet = BULLET_RE.match(ln)
        content = bullet.group(1).strip() if bullet else stripped
        if GWT_START_RE.match(content):
            if current_gwt:
                items.append(current_gwt)
            current_gwt = content
            continue
        if current_gwt and GWT_CONT_RE.match(content):
            current_gwt += " " + content
            continue
        if current_gwt:
            items.append(current_gwt)
            current_gwt = None
        if bullet or in_ac:
            if len(content) > 5:
                items.append(content)
    if current_gwt:
        items.append(current_gwt)
    return [AcceptanceCriterion("AC{}".format(i), t) for i, t in enumerate(items, 1)]
