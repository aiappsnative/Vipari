from __future__ import annotations

from typing import List

from .analysis import ArtifactAnalysis, DiffAnalysis
from .models import SemanticContextMode, SemanticReviewPackage


ARTIFACT_OBJECTIVES = {
    "prompt": "Assess whether prompt behavior, authority, or disclosure scope materially changed.",
    "system_prompt": "Assess whether system-level instructions now permit riskier or more permissive assistant behavior.",
    "policy": "Assess whether policy wording weakens safety boundaries or changes compliance expectations.",
    "guardrail": "Assess whether guardrail logic or refusal requirements were weakened.",
    "model_config": "Assess whether model or generation settings materially change safety, consistency, or control.",
    "tooling": "Assess whether tool access or execution authority expanded.",
    "retrieval": "Assess whether retrieval scope now exposes broader or more sensitive context.",
    "ai_code": "Assess whether the AI-related code path changes model behavior or execution authority.",
}


ARTIFACT_QUESTIONS = {
    "prompt": [
        "Did the assistant's authority or operating scope expand?",
        "Did the prompt introduce sensitive-data disclosure or policy-sharing behavior?",
        "Is the change likely behavior-changing rather than editorial?",
    ],
    "system_prompt": [
        "Did instruction hierarchy shift toward compliance over safety?",
        "Were internal policy or sensitive-data access boundaries broadened?",
        "Will the assistant likely behave more permissively after this change?",
    ],
    "policy": [
        "Were refusal or restriction requirements weakened?",
        "Did the policy become more permissive around disclosure or execution?",
        "Is reviewer intervention warranted before rollout?",
    ],
    "guardrail": [
        "Was guardrail wording removed or softened?",
        "Does the change enable bypass or override behavior?",
        "Could this materially reduce safe refusal behavior?",
    ],
    "model_config": [
        "Do the model or parameter changes alter safety or consistency expectations?",
        "Does the new configuration require reviewer attention before release?",
    ],
    "tooling": [
        "Did the system gain new execution or action authority?",
        "Can the assistant now access or trigger riskier tools?",
    ],
    "retrieval": [
        "Did the retrieval source broaden to more sensitive or internal data?",
        "Will the model now see context that changes disclosure risk?",
    ],
    "ai_code": [
        "Does the code change alter model choice, prompt composition, or tool access?",
        "Could the runtime behavior become materially more permissive?",
    ],
}


def _review_scope(context_mode: SemanticContextMode) -> str:
    if context_mode == SemanticContextMode.FULL_ARTIFACT_COMPARE:
        return "Review as a full artifact comparison because unchanged instructions may affect meaning."
    if context_mode == SemanticContextMode.SECTION_CONTEXT:
        return "Review in local section context because surrounding configuration or code matters."
    return "Review as a localized diff because the semantic signal appears contained to the changed lines."


def build_semantic_review_package(artifact: ArtifactAnalysis) -> SemanticReviewPackage:
    objective = ARTIFACT_OBJECTIVES.get(
        artifact.relevance.artifact_type,
        "Assess whether this AI-relevant change materially alters behavior, authority, or safety.",
    )
    questions = ARTIFACT_QUESTIONS.get(
        artifact.relevance.artifact_type,
        [
            "Is this change behavior-changing rather than editorial?",
            "Does it broaden risk, authority, or disclosure scope?",
        ],
    )
    deterministic_findings = [
        f"{finding.severity.value} {finding.rule_id}: {finding.title}"
        for finding in artifact.findings
    ] or ["No deterministic rule findings yet."]

    return SemanticReviewPackage(
        path=artifact.relevance.path,
        artifact_type=artifact.relevance.artifact_type,
        context_mode=artifact.relevance.context_mode,
        review_scope=_review_scope(artifact.relevance.context_mode),
        review_objective=objective,
        key_questions=questions,
        added_lines=artifact.change.added_lines[:8],
        removed_lines=artifact.change.removed_lines[:8],
        deterministic_findings=deterministic_findings,
    )


def build_semantic_review_packages(diff_analysis: DiffAnalysis) -> List[SemanticReviewPackage]:
    return [build_semantic_review_package(artifact) for artifact in diff_analysis.artifacts]


def format_semantic_review_packages(packages: List[SemanticReviewPackage]) -> str:
    if not packages:
        return "No semantic review packages were generated."

    lines: List[str] = ["Semantic review packages:"]
    for package in packages:
        lines.append(
            f"- Artifact: {package.path} [{package.artifact_type}] via {package.context_mode.value}"
        )
        lines.append(f"  - Scope: {package.review_scope}")
        lines.append(f"  - Objective: {package.review_objective}")
        lines.append("  - Key questions:")
        for question in package.key_questions:
            lines.append(f"    - {question}")
        lines.append("  - Deterministic findings:")
        for finding in package.deterministic_findings:
            lines.append(f"    - {finding}")
        if package.removed_lines:
            lines.append("  - Removed lines:")
            for line in package.removed_lines:
                lines.append(f"    - {line}")
        if package.added_lines:
            lines.append("  - Added lines:")
            for line in package.added_lines:
                lines.append(f"    - {line}")
    return "\n".join(lines)