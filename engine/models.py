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


class VerifierTrigger(str, Enum):
    HIGH_IMPACT = "high_impact"
    LOW_CONFIDENCE = "low_confidence"
    RISK_DISAGREEMENT = "risk_disagreement"
    MERGE_BLOCKING = "merge_blocking"


class RelevanceConfidenceTier(str, Enum):
    CLEAR_YES = "clear_yes"
    UNCERTAIN = "uncertain"
    CLEAR_NO = "clear_no"


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
    artifact_type: str
    reason: str
    context_mode: SemanticContextMode
    heuristic_score: int = 0
    confidence_tier: RelevanceConfidenceTier = RelevanceConfidenceTier.CLEAR_NO
    matched_signals: List["RelevanceSignal"] = field(default_factory=list)
    micro_classifier: "MicroClassifierResult | None" = None

    @property
    def ai_relevant(self) -> bool:
        if self.micro_classifier is not None:
            return self.micro_classifier.is_relevant
        return self.confidence_tier != RelevanceConfidenceTier.CLEAR_NO

    @property
    def needs_micro_classifier(self) -> bool:
        return self.confidence_tier == RelevanceConfidenceTier.UNCERTAIN and self.micro_classifier is None


@dataclass(frozen=True)
class RelevanceSignal:
    source: str
    label: str
    weight: int
    artifact_type: str
    reason: str
    matched_value: str | None = None


@dataclass(frozen=True)
class MicroClassifierResult:
    is_relevant: bool
    reason: str
    status: str = "completed"
    provider: str | None = None
    model: str | None = None
    latency_ms: float | None = None


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


@dataclass(frozen=True)
class VerifierInvocationDecision:
    should_invoke: bool
    trigger: VerifierTrigger | None = None
    reason: str = ""


@dataclass(frozen=True)
class VerifierReviewRequest:
    path: str
    artifact_type: str
    review_scope: str
    review_objective: str
    key_questions: List[str] = field(default_factory=list)
    deterministic_findings: List[str] = field(default_factory=list)
    added_lines: List[str] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)
    proposed_risk_level: RiskLevel = RiskLevel.LOW
    proposed_confidence: str = "Medium"
    proposed_summary: str = ""
    proposed_recommendation: str = ""


@dataclass(frozen=True)
class VerifierReviewResult:
    risk_level: RiskLevel
    confidence: str
    summary: str
    rationale: List[str] = field(default_factory=list)
    requires_escalation: bool = False
