from __future__ import annotations

from typing import Iterable

from .models import RiskLevel, SemanticReviewPackage, VerifierInvocationDecision, VerifierReviewRequest, VerifierTrigger


RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


def _normalize_risk_level(value: str | RiskLevel | None) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value
    candidate = str(value or RiskLevel.LOW.value).strip().lower()
    if candidate == "high":
        return RiskLevel.HIGH
    if candidate == "medium":
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _normalize_confidence_level(value: str | None) -> str:
    candidate = str(value or "Medium").strip().lower()
    if candidate == "high":
        return "High"
    if candidate == "low":
        return "Low"
    return "Medium"


def should_invoke_verifier(
    deterministic_risk: str | RiskLevel | None,
    semantic_risk: str | RiskLevel | None,
    semantic_confidence: str | None,
    *,
    semantic_requires_escalation: bool = False,
) -> VerifierInvocationDecision:
    normalized_deterministic = _normalize_risk_level(deterministic_risk)
    normalized_semantic = _normalize_risk_level(semantic_risk)
    normalized_confidence = _normalize_confidence_level(semantic_confidence)

    if semantic_requires_escalation:
        return VerifierInvocationDecision(
            should_invoke=True,
            trigger=VerifierTrigger.MERGE_BLOCKING,
            reason="Semantic review recommended merge blocking, so verifier confirmation is required.",
        )

    if normalized_deterministic == RiskLevel.HIGH:
        return VerifierInvocationDecision(
            should_invoke=True,
            trigger=VerifierTrigger.HIGH_IMPACT,
            reason="Deterministic analysis already marks this as high impact.",
        )

    if normalized_confidence == "Low":
        return VerifierInvocationDecision(
            should_invoke=True,
            trigger=VerifierTrigger.LOW_CONFIDENCE,
            reason="Semantic review returned low confidence, so the verifier should arbitrate.",
        )

    if normalized_deterministic != normalized_semantic:
        return VerifierInvocationDecision(
            should_invoke=True,
            trigger=VerifierTrigger.RISK_DISAGREEMENT,
            reason="Deterministic and semantic risk assessments disagree.",
        )

    return VerifierInvocationDecision(
        should_invoke=False,
        reason="Deterministic and semantic signals are aligned without a high-impact or low-confidence trigger.",
    )


def build_verifier_review_request(
    package: SemanticReviewPackage,
    *,
    proposed_risk_level: str | RiskLevel,
    proposed_confidence: str | None,
    proposed_summary: str,
    proposed_recommendation: str,
) -> VerifierReviewRequest:
    return VerifierReviewRequest(
        path=package.path,
        artifact_type=package.artifact_type,
        review_scope=package.review_scope,
        review_objective=package.review_objective,
        key_questions=list(package.key_questions),
        deterministic_findings=list(package.deterministic_findings),
        added_lines=list(package.added_lines),
        removed_lines=list(package.removed_lines),
        proposed_risk_level=_normalize_risk_level(proposed_risk_level),
        proposed_confidence=_normalize_confidence_level(proposed_confidence),
        proposed_summary=proposed_summary.strip(),
        proposed_recommendation=proposed_recommendation.strip(),
    )


def build_verifier_review_requests(
    packages: Iterable[SemanticReviewPackage],
    *,
    proposed_risk_level: str | RiskLevel,
    proposed_confidence: str | None,
    proposed_summary: str,
    proposed_recommendation: str,
) -> list[VerifierReviewRequest]:
    return [
        build_verifier_review_request(
            package,
            proposed_risk_level=proposed_risk_level,
            proposed_confidence=proposed_confidence,
            proposed_summary=proposed_summary,
            proposed_recommendation=proposed_recommendation,
        )
        for package in packages
    ]