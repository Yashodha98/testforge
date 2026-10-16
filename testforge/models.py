from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

TEST_TYPES = ("Positive", "Negative", "Boundary", "Edge", "Security", "Usability")
PRIORITIES = ("High", "Medium", "Low")


@dataclass
class AcceptanceCriterion:
    ac_id: str  # "AC1"
    text: str


@dataclass
class UserStory:
    title: str
    role: Optional[str]
    goal: Optional[str]
    benefit: Optional[str]
    criteria: List[AcceptanceCriterion]
    raw: str
    warnings: List[str] = field(default_factory=list)


@dataclass
class QualityIssue:
    target: str  # "Story" or "AC2"
    kind: str  # "Ambiguous term", "Missing benefit", ...
    detail: str
    suggestion: str


@dataclass
class Boundary:
    ac_id: str
    subject: str  # "password length"
    unit: str
    minimum: Optional[float]
    maximum: Optional[float]
    source_text: str
    values: List[Dict]  # [{"value": 7, "expect": "reject", "label": "min-1"}]


@dataclass
class TestCase:
    tc_id: str
    title: str
    ac_refs: List[str]
    test_type: str
    priority: str
    preconditions: str
    steps: List[str]
    test_data: str
    expected_result: str
    source: str = "gemini"  # "gemini" or "rule-based"

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class GenerationMeta:
    source: str  # "gemini", "mixed", "fallback"
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    fallback_reason: Optional[str] = None
    dropped_invalid: int = 0
    duplicates_removed: int = 0
    gaps_filled: List[str] = field(default_factory=list)
