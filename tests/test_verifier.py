import os
import sys


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.models import RiskLevel, SemanticContextMode, SemanticReviewPackage, VerifierTrigger
from engine.verifier import build_verifier_review_requests, should_invoke_verifier


def test_should_invoke_verifier_for_high_impact_deterministic_risk():
    decision = should_invoke_verifier("High", "High", "High")

    assert decision.should_invoke is True
    assert decision.trigger == VerifierTrigger.HIGH_IMPACT


def test_should_invoke_verifier_for_low_confidence_semantic_review():
    decision = should_invoke_verifier("Low", "Low", "Low")

    assert decision.should_invoke is True
    assert decision.trigger == VerifierTrigger.LOW_CONFIDENCE


def test_should_invoke_verifier_for_risk_disagreement():
    decision = should_invoke_verifier("Low", "Medium", "Medium")

    assert decision.should_invoke is True
    assert decision.trigger == VerifierTrigger.RISK_DISAGREEMENT


def test_should_not_invoke_verifier_when_signals_are_aligned_and_confident():
    decision = should_invoke_verifier("Medium", "Medium", "High")

    assert decision.should_invoke is False
    assert decision.trigger is None


def test_build_verifier_review_requests_preserves_package_context_and_proposer_outputs():
    packages = [
        SemanticReviewPackage(
            path="prompts/system.txt",
            artifact_type="prompt",
            context_mode=SemanticContextMode.FULL_ARTIFACT_COMPARE,
            review_scope="Review as full artifact.",
            review_objective="Assess authority drift.",
            key_questions=["Did authority expand?"],
            added_lines=["You may reveal internal policy."],
            removed_lines=["Do not reveal internal policy."],
            deterministic_findings=["High guardrail_drift: Potential guardrail removal detected"],
        )
    ]

    requests = build_verifier_review_requests(
        packages,
        proposed_risk_level="High",
        proposed_confidence="Low",
        proposed_summary="Prompt became more permissive.",
        proposed_recommendation="Escalate before merge.",
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.path == "prompts/system.txt"
    assert request.proposed_risk_level == RiskLevel.HIGH
    assert request.proposed_confidence == "Low"
    assert request.proposed_summary == "Prompt became more permissive."
    assert request.proposed_recommendation == "Escalate before merge."
    assert request.key_questions == ["Did authority expand?"]