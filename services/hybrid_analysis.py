from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from engine.analysis import DiffAnalysis
from engine.models import FindingSeverity


DEFAULT_HYBRID_STATIC_ANALYSIS_ROLLOUT_MODE = "off"
DEFAULT_HYBRID_STATIC_ANALYSIS_MAX_ARTIFACTS_PER_REVIEW = 2
DEFAULT_HYBRID_STATIC_ANALYSIS_ARTIFACT_TYPES = (
    "prompt",
    "policy",
    "model_config",
    "agent_config",
    "tooling",
)

_SEVERITY_SCORES = {
    FindingSeverity.LOW.value: 1,
    FindingSeverity.MEDIUM.value: 2,
    FindingSeverity.HIGH.value: 3,
}


@dataclass(frozen=True)
class HybridAnalyzerRequest:
    analyzer_key: str
    artifact_path: str
    artifact_type: str
    rationale: str


@dataclass(frozen=True)
class HybridAnalysisPlan:
    rollout_mode: str
    should_run: bool
    reason: str
    requests: tuple[HybridAnalyzerRequest, ...]

    @property
    def request_count(self) -> int:
        return len(self.requests)


def normalize_hybrid_static_analysis_rollout_mode(mode: str | None) -> str:
    candidate = str(mode or DEFAULT_HYBRID_STATIC_ANALYSIS_ROLLOUT_MODE).strip().lower()
    if candidate == "shadow":
        return "shadow"
    return DEFAULT_HYBRID_STATIC_ANALYSIS_ROLLOUT_MODE


def _normalize_csv_values(values: str | tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        items = values.split(",")
    else:
        items = list(values)
    normalized = []
    for item in items:
        value = str(item).strip()
        if value:
            normalized.append(value)
    return tuple(normalized)


def _repo_allowed(repo_full: str, allow_patterns: tuple[str, ...]) -> bool:
    if not allow_patterns:
        return True
    lowered = repo_full.strip().lower()
    for pattern in allow_patterns:
        normalized = pattern.strip().lower()
        if normalized and fnmatchcase(lowered, normalized):
            return True
    return False


def _analyzer_key_for_artifact_type(artifact_type: str) -> str:
    normalized = artifact_type.strip().lower()
    if normalized in {"prompt", "policy"}:
        return "prompt_policy_static_scan"
    if normalized in {"model_config", "agent_config"}:
        return "config_contract_scan"
    return "tooling_surface_scan"


def _request_rationale(artifact_path: str, artifact_type: str, severity_score: int, finding_titles: list[str]) -> str:
    if finding_titles:
        headline = ", ".join(finding_titles[:2])
        return f"Hybrid static analysis would inspect {artifact_path} [{artifact_type}] because deterministic findings include {headline}."
    if severity_score > 0:
        return f"Hybrid static analysis would inspect {artifact_path} [{artifact_type}] because high-priority changes were detected."
    return f"Hybrid static analysis would inspect {artifact_path} [{artifact_type}] because it matches the configured control-surface allowlist."


def build_hybrid_analysis_plan(
    deterministic_analysis: DiffAnalysis,
    *,
    repo_full: str,
    rollout_mode: str,
    max_artifacts_per_review: int,
    allowed_repos: str | tuple[str, ...] | list[str] | None = None,
    allowed_artifact_types: str | tuple[str, ...] | list[str] | None = None,
) -> HybridAnalysisPlan:
    normalized_mode = normalize_hybrid_static_analysis_rollout_mode(rollout_mode)
    if normalized_mode == DEFAULT_HYBRID_STATIC_ANALYSIS_ROLLOUT_MODE:
        return HybridAnalysisPlan(
            rollout_mode=normalized_mode,
            should_run=False,
            reason="Hybrid static analysis rollout is disabled for this worker.",
            requests=(),
        )

    allow_patterns = _normalize_csv_values(allowed_repos)
    if not _repo_allowed(repo_full, allow_patterns):
        return HybridAnalysisPlan(
            rollout_mode=normalized_mode,
            should_run=False,
            reason="Repository is outside the configured hybrid-analysis allowlist.",
            requests=(),
        )

    configured_types = _normalize_csv_values(allowed_artifact_types)
    eligible_types = tuple(item.lower() for item in (configured_types or DEFAULT_HYBRID_STATIC_ANALYSIS_ARTIFACT_TYPES))
    bounded_limit = max(0, int(max_artifacts_per_review or 0))
    if bounded_limit == 0:
        return HybridAnalysisPlan(
            rollout_mode=normalized_mode,
            should_run=False,
            reason="Hybrid static analysis cap is 0 artifacts per review.",
            requests=(),
        )

    ranked_requests = []
    for artifact in deterministic_analysis.artifacts:
        artifact_type = str(artifact.relevance.artifact_type or "").strip().lower()
        if eligible_types and artifact_type not in eligible_types:
            continue
        finding_titles = [finding.title for finding in artifact.findings]
        severity_score = 0
        if artifact.findings:
            severity_score = max(_SEVERITY_SCORES.get(finding.severity.value, 0) for finding in artifact.findings)
        request = HybridAnalyzerRequest(
            analyzer_key=_analyzer_key_for_artifact_type(artifact_type),
            artifact_path=artifact.relevance.path,
            artifact_type=artifact.relevance.artifact_type,
            rationale=_request_rationale(artifact.relevance.path, artifact.relevance.artifact_type, severity_score, finding_titles),
        )
        ranked_requests.append((severity_score, artifact.change.changed_hunks, artifact.change.added_count, artifact.relevance.path, request))

    if not ranked_requests:
        return HybridAnalysisPlan(
            rollout_mode=normalized_mode,
            should_run=False,
            reason="No changed artifacts matched the configured hybrid-analysis participation controls.",
            requests=(),
        )

    requests = tuple(
        item[4]
        for item in sorted(ranked_requests, key=lambda item: (-item[0], -item[1], -item[2], item[3]))[:bounded_limit]
    )
    request_label = "artifact" if len(requests) == 1 else "artifacts"
    return HybridAnalysisPlan(
        rollout_mode=normalized_mode,
        should_run=True,
        reason=f"Shadow-mode hybrid static analysis would inspect {len(requests)} {request_label} on this PR.",
        requests=requests,
    )