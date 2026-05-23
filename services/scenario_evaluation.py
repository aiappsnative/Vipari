from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from engine.analysis import DiffAnalysis
from engine.models import FindingSeverity


DEFAULT_SCENARIO_EVAL_ROLLOUT_MODE = "off"
DEFAULT_SCENARIO_EVAL_MAX_ARTIFACTS_PER_REVIEW = 2
DEFAULT_SCENARIO_EVAL_ARTIFACT_TYPES = (
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
class ScenarioEvalPlan:
    rollout_mode: str
    should_run: bool
    reason: str
    artifact_paths: tuple[str, ...]

    @property
    def artifact_count(self) -> int:
        return len(self.artifact_paths)


def normalize_scenario_eval_rollout_mode(mode: str | None) -> str:
    candidate = str(mode or DEFAULT_SCENARIO_EVAL_ROLLOUT_MODE).strip().lower()
    if candidate == "shadow":
        return "shadow"
    return DEFAULT_SCENARIO_EVAL_ROLLOUT_MODE


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


def build_scenario_eval_plan(
    deterministic_analysis: DiffAnalysis,
    *,
    repo_full: str,
    rollout_mode: str,
    max_artifacts_per_review: int,
    allowed_repos: str | tuple[str, ...] | list[str] | None = None,
    allowed_artifact_types: str | tuple[str, ...] | list[str] | None = None,
) -> ScenarioEvalPlan:
    normalized_mode = normalize_scenario_eval_rollout_mode(rollout_mode)
    if normalized_mode == DEFAULT_SCENARIO_EVAL_ROLLOUT_MODE:
        return ScenarioEvalPlan(
            rollout_mode=normalized_mode,
            should_run=False,
            reason="Scenario eval rollout is disabled for this worker.",
            artifact_paths=(),
        )

    allow_patterns = _normalize_csv_values(allowed_repos)
    if not _repo_allowed(repo_full, allow_patterns):
        return ScenarioEvalPlan(
            rollout_mode=normalized_mode,
            should_run=False,
            reason="Repository is outside the configured scenario-eval allowlist.",
            artifact_paths=(),
        )

    configured_types = _normalize_csv_values(allowed_artifact_types)
    eligible_types = tuple(item.lower() for item in (configured_types or DEFAULT_SCENARIO_EVAL_ARTIFACT_TYPES))
    bounded_limit = max(0, int(max_artifacts_per_review or 0))
    if bounded_limit == 0:
        return ScenarioEvalPlan(
            rollout_mode=normalized_mode,
            should_run=False,
            reason="Scenario eval cap is 0 artifacts per review.",
            artifact_paths=(),
        )

    ranked_candidates = []
    for artifact in deterministic_analysis.artifacts:
        artifact_type = str(artifact.relevance.artifact_type or "").strip().lower()
        if eligible_types and artifact_type not in eligible_types:
            continue
        severity_score = 0
        if artifact.findings:
            severity_score = max(_SEVERITY_SCORES.get(finding.severity.value, 0) for finding in artifact.findings)
        ranked_candidates.append((severity_score, artifact.change.changed_hunks, artifact.change.added_count, artifact.relevance.path))

    if not ranked_candidates:
        return ScenarioEvalPlan(
            rollout_mode=normalized_mode,
            should_run=False,
            reason="No changed artifacts matched the configured scenario-eval participation controls.",
            artifact_paths=(),
        )

    selected_paths = tuple(
        item[3]
        for item in sorted(ranked_candidates, key=lambda item: (-item[0], -item[1], -item[2], item[3]))[:bounded_limit]
    )
    artifact_label = "artifact" if len(selected_paths) == 1 else "artifacts"
    return ScenarioEvalPlan(
        rollout_mode=normalized_mode,
        should_run=True,
        reason=f"Shadow-mode scenario eval would review {len(selected_paths)} {artifact_label} on this PR.",
        artifact_paths=selected_paths,
    )