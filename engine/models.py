from dataclasses import dataclass, field
from enum import Enum
from typing import List


class SemanticContextMode(str, Enum):
    DIFF_ONLY = "diff_only"
    SECTION_CONTEXT = "section_context"
    FULL_ARTIFACT_COMPARE = "full_artifact_compare"


class FindingSeverity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


@dataclass(frozen=True)
class ChangedFile:
    old_path: str
    new_path: str
    diff_lines: List[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        if self.new_path and self.new_path != "/dev/null":
            return self.new_path
        return self.old_path

    @property
    def raw_diff(self) -> str:
        return "\n".join(self.diff_lines)


@dataclass(frozen=True)
class RelevanceResult:
    path: str
    ai_relevant: bool
    artifact_type: str
    reason: str
    context_mode: SemanticContextMode


@dataclass(frozen=True)
class StructuredChange:
    path: str
    artifact_type: str
    context_mode: SemanticContextMode
    added_lines: List[str] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)
    changed_hunks: int = 0
    added_count: int = 0
    removed_count: int = 0
    added_terms: List[str] = field(default_factory=list)
    removed_terms: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuleFinding:
    rule_id: str
    title: str
    severity: FindingSeverity
    rationale: str
    evidence: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SemanticReviewPackage:
    path: str
    artifact_type: str
    context_mode: SemanticContextMode
    review_scope: str
    review_objective: str
    key_questions: List[str] = field(default_factory=list)
    added_lines: List[str] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)
    deterministic_findings: List[str] = field(default_factory=list)
