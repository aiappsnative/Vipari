from __future__ import annotations

from dataclasses import dataclass

from .ai_library_registry import AiLibraryMatch
from .onboarding_records import DiscoveredArtifactInput


@dataclass(frozen=True)
class CapabilityEvidenceItem:
    source: str
    label: str
    capability_tags: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityProfile:
    capability_tags: tuple[str, ...]
    capability_scores: dict[str, float]
    primary_capability: str | None
    secondary_capabilities: tuple[str, ...]
    confidence: float
    evidence_items: tuple[CapabilityEvidenceItem, ...]
    inference_version: str = "v1"


@dataclass(frozen=True)
class CapabilityDelta:
    introduced: tuple[str, ...]
    removed: tuple[str, ...]
    retained: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityDeltaSignal:
    delta: float
    direction: str
    material: bool
    summary: str


def empty_capability_profile() -> CapabilityProfile:
    return CapabilityProfile(
        capability_tags=(),
        capability_scores={},
        primary_capability=None,
        secondary_capabilities=(),
        confidence=0.0,
        evidence_items=(),
    )


def empty_capability_delta() -> CapabilityDelta:
    return CapabilityDelta(introduced=(), removed=(), retained=())


_ARTIFACT_TYPE_CAPABILITY_TAGS: dict[str, tuple[str, ...]] = {
    "prompt": ("generative_ai",),
    "system_prompt": ("generative_ai",),
    "tooling": ("tool_use",),
    "retrieval": ("retrieval",),
    "model_config": ("model_serving",),
    "ai_code": ("agentic",),
    "guardrail": ("moderation",),
}


def infer_capability_profile(
    ai_library_matches: list[AiLibraryMatch],
    discovered_artifacts: list[DiscoveredArtifactInput],
) -> CapabilityProfile:
    evidence_items: list[CapabilityEvidenceItem] = []
    score_points: dict[str, float] = {}

    for match in ai_library_matches:
        if not match.capability_tags:
            continue
        evidence_items.append(
            CapabilityEvidenceItem(
                source="dependency",
                label=f"{match.matched_package} ({match.source_file})",
                capability_tags=tuple(sorted(set(match.capability_tags))),
            )
        )
        for capability in match.capability_tags:
            score_points[capability] = score_points.get(capability, 0.0) + 2.0

    for artifact in discovered_artifacts:
        tags = _ARTIFACT_TYPE_CAPABILITY_TAGS.get(str(artifact.artifact_type or ""), ())
        if not tags:
            continue
        evidence_items.append(
            CapabilityEvidenceItem(
                source="artifact",
                label=artifact.artifact_path,
                capability_tags=tags,
            )
        )
        for capability in tags:
            score_points[capability] = score_points.get(capability, 0.0) + 1.0

    if not score_points:
        return empty_capability_profile()

    max_score = max(score_points.values())
    capability_scores = {
        capability: round(score / max_score, 3)
        for capability, score in sorted(score_points.items())
    }
    sorted_capabilities = sorted(
        capability_scores.items(),
        key=lambda item: (-item[1], item[0]),
    )
    primary = sorted_capabilities[0][0]
    secondary = tuple(capability for capability, _score in sorted_capabilities[1:4])
    confidence = min(1.0, round(0.35 + (0.08 * len(evidence_items)), 3))

    return CapabilityProfile(
        capability_tags=tuple(capability for capability, _score in sorted_capabilities),
        capability_scores=capability_scores,
        primary_capability=primary,
        secondary_capabilities=secondary,
        confidence=confidence,
        evidence_items=tuple(evidence_items[:12]),
    )


def infer_capability_profile_from_artifacts(artifact_entries: list[object]) -> CapabilityProfile:
    discovered_artifacts: list[DiscoveredArtifactInput] = []
    for item in artifact_entries:
        artifact_path = str(getattr(item, "artifact_path", "") or "")
        artifact_type = str(getattr(item, "artifact_type", "") or "")
        if not artifact_path or not artifact_type:
            continue
        discovered_artifacts.append(
            DiscoveredArtifactInput(
                artifact_path=artifact_path,
                artifact_type=artifact_type,
                discovery_reason="Repo artifact signal",
                confidence=1.0,
                baseline_content="",
            )
        )
    return infer_capability_profile([], discovered_artifacts)


def compare_capability_profiles(current: CapabilityProfile, baseline: CapabilityProfile) -> CapabilityDelta:
    current_tags = {str(tag) for tag in current.capability_tags if str(tag)}
    baseline_tags = {str(tag) for tag in baseline.capability_tags if str(tag)}
    introduced = tuple(sorted(current_tags - baseline_tags))
    removed = tuple(sorted(baseline_tags - current_tags))
    retained = tuple(sorted(current_tags.intersection(baseline_tags)))
    return CapabilityDelta(introduced=introduced, removed=removed, retained=retained)


def build_capability_delta_signal(
    delta: float | None,
    *,
    has_measurement: bool = True,
    material_threshold: float = 0.03,
    unavailable_summary: str = "Capability delta is unavailable until Vipari captures comparable baseline evidence.",
) -> CapabilityDeltaSignal:
    if not has_measurement:
        return CapabilityDeltaSignal(
            delta=0.0,
            direction="stable",
            material=False,
            summary=unavailable_summary,
        )

    normalized_delta = round(float(delta or 0.0), 4)
    if normalized_delta > 0:
        direction = "expanded"
    elif normalized_delta < 0:
        direction = "reduced"
    else:
        direction = "stable"

    material = abs(normalized_delta) >= float(material_threshold)
    magnitude_label = "Material" if material else "Minor"
    summary = f"{magnitude_label} capability delta {direction} by {abs(normalized_delta):.3f}."
    return CapabilityDeltaSignal(
        delta=normalized_delta,
        direction=direction,
        material=material,
        summary=summary,
    )
