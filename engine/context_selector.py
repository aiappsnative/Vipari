from .models import RelevanceResult, SemanticContextMode


FULL_ARTIFACT_TYPES = {
    "prompt",
    "system_prompt",
    "policy",
    "guardrail",
}

SECTION_CONTEXT_TYPES = {
    "model_config",
    "tooling",
    "retrieval",
    "ai_code",
}


def determine_context_mode(artifact_type: str) -> SemanticContextMode:
    if artifact_type in FULL_ARTIFACT_TYPES:
        return SemanticContextMode.FULL_ARTIFACT_COMPARE
    if artifact_type in SECTION_CONTEXT_TYPES:
        return SemanticContextMode.SECTION_CONTEXT
    return SemanticContextMode.DIFF_ONLY


def apply_context_mode(result: RelevanceResult) -> RelevanceResult:
    return RelevanceResult(
        path=result.path,
        artifact_type=result.artifact_type,
        reason=result.reason,
        context_mode=determine_context_mode(result.artifact_type),
        heuristic_score=result.heuristic_score,
        confidence_tier=result.confidence_tier,
        matched_signals=list(result.matched_signals),
        micro_classifier=result.micro_classifier,
    )
