from __future__ import annotations

import re
from typing import Iterable

from .models import RiskLevel, SemanticReviewPackage, VerifierInvocationDecision, VerifierReviewRequest, VerifierReviewResult, VerifierTrigger


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


def build_verifier_system_prompt() -> str:
    return (
        "You are the verifier in a proposer-verifier AI change review flow. "
        "Review the proposer assessment against the structured artifact evidence and deterministic findings. "
        "Return reviewer notes in Markdown using this structure exactly: 'Summary: ...', 'Risk Level: Low|Medium|High', "
        "'Confidence: Low|Medium|High', 'Detailed Analysis:', 2-4 bullet points, and 'Recommendation: ...'. "
        "If the proposer understates risk or asks for escalation incorrectly, correct it explicitly."
    )


def build_verifier_user_prompt(request: VerifierReviewRequest) -> str:
    deterministic_findings = "\n".join(f"- {item}" for item in request.deterministic_findings) or "- None"
    key_questions = "\n".join(f"- {item}" for item in request.key_questions) or "- None"
    added_lines = "\n".join(f"+ {item}" for item in request.added_lines) or "+ None"
    removed_lines = "\n".join(f"- {item}" for item in request.removed_lines) or "- None"
    return (
        f"Artifact: {request.path}\n"
        f"Artifact type: {request.artifact_type}\n"
        f"Review scope: {request.review_scope}\n"
        f"Review objective: {request.review_objective}\n\n"
        f"Proposer summary: {request.proposed_summary}\n"
        f"Proposer risk level: {request.proposed_risk_level.value}\n"
        f"Proposer confidence: {request.proposed_confidence}\n"
        f"Proposer recommendation: {request.proposed_recommendation}\n\n"
        f"Key questions:\n{key_questions}\n\n"
        f"Deterministic findings:\n{deterministic_findings}\n\n"
        f"Added lines:\n{added_lines}\n\n"
        f"Removed lines:\n{removed_lines}"
    )


def parse_verifier_review_result(content: str, *, request: VerifierReviewRequest) -> VerifierReviewResult:
    risk_level = _extract_risk_level(content, default=request.proposed_risk_level)
    confidence = _extract_confidence_level(content, default=request.proposed_confidence)
    summary = _extract_summary(content, default=request.proposed_summary)
    rationale = _extract_analysis_bullets(content)
    recommendation = _extract_recommendation(content, default=request.proposed_recommendation)
    return VerifierReviewResult(
        risk_level=risk_level,
        confidence=confidence,
        summary=summary,
        rationale=rationale,
        requires_escalation=_recommendation_requires_escalation(recommendation),
        recommendation=recommendation,
    )


def _extract_summary(content: str, *, default: str) -> str:
    match = re.search(r"^summary\s*[:\-]\s*(.+)$", content, re.IGNORECASE | re.MULTILINE)
    if match:
        return _normalize_summary(match.group(1), default=default)
    for raw_line in content.splitlines():
        candidate = raw_line.strip().lstrip("#>*- ")
        if candidate:
            return _normalize_summary(candidate, default=default)
    return _normalize_summary(default, default=default)


def _normalize_summary(value: str, *, default: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" -*_`\t\r\n")
    cleaned = cleaned.rstrip(".")
    if not cleaned:
        cleaned = default.strip().rstrip(".")
    return f"{cleaned}."


def _extract_risk_level(content: str, *, default: str | RiskLevel) -> RiskLevel:
    match = re.search(r"risk level\s*[:\-]\s*\**(low|medium|high)\**", content, re.IGNORECASE)
    if match:
        return _normalize_risk_level(match.group(1))
    return _normalize_risk_level(default)


def _extract_confidence_level(content: str, *, default: str) -> str:
    match = re.search(r"confidence\s*[:\-]\s*\**(low|medium|high)\**", content, re.IGNORECASE)
    if match:
        return _normalize_confidence_level(match.group(1))
    return _normalize_confidence_level(default)


def _extract_recommendation(content: str, *, default: str) -> str:
    match = re.search(r"recommendation\s*[:\-]\s*(.+)$", content, re.IGNORECASE | re.MULTILINE)
    if match:
        return _normalize_sentence(match.group(1), default=default)
    return _normalize_sentence(default, default=default)


def _extract_analysis_bullets(content: str) -> list[str]:
    bullets: list[str] = []
    in_section = False
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        normalized = re.sub(r"^[#>*\s]+", "", stripped).strip()
        if re.match(r"^(\*\*)?detailed analysis(\*\*)?\s*[:\-]?$", normalized, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^(\*\*)?recommendation(\*\*)?\s*[:\-]", normalized, re.IGNORECASE):
            break
        if in_section:
            bullet = re.sub(r"^[-*]\s*", "", stripped).strip()
            if bullet:
                bullets.append(_normalize_sentence(bullet))
    return bullets[:4]


def _normalize_sentence(value: str, *, default: str | None = None) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" -*_`\t\r\n")
    if not cleaned and default is not None:
        cleaned = default.strip()
    cleaned = cleaned.rstrip(".")
    return f"{cleaned}." if cleaned else ""


def _recommendation_requires_escalation(recommendation: str) -> bool:
    lowered = recommendation.lower()
    return any(
        hint in lowered
        for hint in (
            "escalate before merge",
            "revert before merge",
            "do not merge",
            "block merge",
            "hold before merge",
        )
    )