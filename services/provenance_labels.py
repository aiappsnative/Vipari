from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactProvenanceLabel:
    family: str
    kind: str
    label: str


@dataclass(frozen=True)
class ReviewOutputProvenanceLabel:
    kind: str
    label: str


def artifact_family(artifact_type: str) -> str:
    lowered = str(artifact_type or "other").lower()
    if "prompt" in lowered:
        return "prompt"
    if "tool" in lowered:
        return "tool"
    if "model" in lowered:
        return "model"
    if "config" in lowered:
        return "config"
    if "policy" in lowered or "guard" in lowered or "govern" in lowered:
        return "governance"
    return "other"


def artifact_provenance_label(artifact_type: str) -> ArtifactProvenanceLabel:
    family = artifact_family(artifact_type)
    if family == "prompt":
        return ArtifactProvenanceLabel(family=family, kind="ai_control_surface", label="AI control surface")
    if family == "tool":
        return ArtifactProvenanceLabel(family=family, kind="ai_tool_surface", label="AI-assisted tool surface")
    if family in {"model", "config"}:
        return ArtifactProvenanceLabel(family=family, kind="model_behavior_surface", label="Model and config surface")
    if family == "governance":
        return ArtifactProvenanceLabel(family=family, kind="human_governance_surface", label="Governance and policy surface")
    return ArtifactProvenanceLabel(family=family, kind="supporting_repository_artifact", label="Supporting repository artifact")


def review_output_provenance_label(output_mode: str, semantic_review_completed: bool) -> ReviewOutputProvenanceLabel:
    normalized_mode = str(output_mode or "").lower()
    if normalized_mode == "full_review" and semantic_review_completed:
        return ReviewOutputProvenanceLabel(
            kind="ai_assisted_review_narrative",
            label="AI-assisted review narrative grounded in deterministic evidence",
        )
    if normalized_mode in {"preliminary_fallback", "fallback"}:
        return ReviewOutputProvenanceLabel(
            kind="deterministic_fallback_review",
            label="Deterministic fallback review output",
        )
    if normalized_mode == "no_comment":
        return ReviewOutputProvenanceLabel(
            kind="no_reviewer_output",
            label="No reviewer-facing comment generated",
        )
    if semantic_review_completed:
        return ReviewOutputProvenanceLabel(
            kind="ai_assisted_review_narrative",
            label="AI-assisted review narrative",
        )
    return ReviewOutputProvenanceLabel(
        kind="deterministic_review_record",
        label="Deterministic review record",
    )