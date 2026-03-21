from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .audit_records import (
    ArtifactDriftLeaderboardEntry,
    RepoStaticDriftSummary,
    get_repo_static_drift_summary,
    list_pull_request_audits_for_repo,
    list_top_drifting_artifacts_for_repo,
)
from .onboarding_records import (
    RepositoryOnboardingRecord,
    get_latest_repository_onboarding,
    list_historical_backfill_jobs_for_repo,
    list_latest_repository_onboardings,
    list_onboarded_artifacts_for_onboarding,
    list_onboarding_baseline_versions_for_onboarding,
)


@dataclass(frozen=True)
class RepoDashboardIndexEntry:
    repo_full: str
    default_branch: str
    onboarding_status: str
    discovered_artifact_count: int
    last_onboarded_at: float


@dataclass(frozen=True)
class RepoDashboardBackfillSummary:
    job_count: int
    planned_job_count: int
    processing_job_count: int
    completed_job_count: int
    failed_job_count: int
    total_historical_versions: int
    total_historical_profiles: int


@dataclass(frozen=True)
class RepoDashboardArtifactEntry:
    artifact_path: str
    artifact_type: str
    discovery_reason: str
    discovery_confidence: float
    baseline_line_count: int
    historical_version_count: int
    historical_profile_count: int
    latest_historical_semantic_distance: float
    latest_historical_drift_magnitude: float
    pr_profile_count: int
    latest_pr_semantic_distance: float
    latest_pr_capability_shift: float
    latest_pr_guardrail_shift: float
    leaderboard_drift_magnitude: float


@dataclass(frozen=True)
class RepoDashboardInsightEntry:
    title: str
    artifact_path: str
    artifact_type: str
    priority: str
    score: float
    rationale: str
    recommended_action: str


@dataclass(frozen=True)
class RepoDashboardControlSurfaceGroup:
    group_key: str
    label: str
    artifact_count: int
    high_confidence_count: int
    top_artifact_paths: list[str]


@dataclass(frozen=True)
class RepoDashboardView:
    repo_full: str
    onboarding: RepositoryOnboardingRecord | None
    backfill: RepoDashboardBackfillSummary
    pull_request_audit_count: int
    baseline_version_count: int
    drift_summary: RepoStaticDriftSummary
    top_drifting_artifacts: list[ArtifactDriftLeaderboardEntry]
    insights: list[RepoDashboardInsightEntry]
    control_surface_groups: list[RepoDashboardControlSurfaceGroup]
    artifacts: list[RepoDashboardArtifactEntry]


def list_repo_dashboard_index(db_path: str) -> list[RepoDashboardIndexEntry]:
    onboardings = list_latest_repository_onboardings(db_path)
    return [
        RepoDashboardIndexEntry(
            repo_full=onboarding.repo_full,
            default_branch=onboarding.default_branch,
            onboarding_status=onboarding.status,
            discovered_artifact_count=onboarding.discovered_artifact_count,
            last_onboarded_at=onboarding.updated_at,
        )
        for onboarding in onboardings
    ]


def build_repo_dashboard_view(db_path: str, repo_full: str) -> RepoDashboardView:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    drift_summary = get_repo_static_drift_summary(db_path, repo_full)
    top_drifting_artifacts = list_top_drifting_artifacts_for_repo(db_path, repo_full)
    pull_request_audit_count = len(list_pull_request_audits_for_repo(db_path, repo_full))

    if onboarding is None:
        return RepoDashboardView(
            repo_full=repo_full,
            onboarding=None,
            backfill=RepoDashboardBackfillSummary(
                job_count=0,
                planned_job_count=0,
                processing_job_count=0,
                completed_job_count=0,
                failed_job_count=0,
                total_historical_versions=0,
                total_historical_profiles=0,
            ),
            pull_request_audit_count=pull_request_audit_count,
            baseline_version_count=0,
            drift_summary=drift_summary,
            top_drifting_artifacts=top_drifting_artifacts,
            insights=[],
            control_surface_groups=[],
            artifacts=[],
        )

    artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    baseline_versions = list_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    baseline_by_path = {baseline.artifact_path: baseline for baseline in baseline_versions}
    jobs = list_historical_backfill_jobs_for_repo(db_path, repo_full)
    leaderboard_by_path = {entry.artifact_path: entry for entry in top_drifting_artifacts}
    metrics_by_path = _load_repo_artifact_metrics(db_path, repo_full)

    artifact_entries: list[RepoDashboardArtifactEntry] = []
    total_historical_versions = sum(metrics["historical_version_count"] for metrics in metrics_by_path.values())
    total_historical_profiles = sum(metrics["historical_profile_count"] for metrics in metrics_by_path.values())

    for artifact in artifacts:
        baseline = baseline_by_path.get(artifact.artifact_path)
        metrics = metrics_by_path.get(artifact.artifact_path, _empty_artifact_metrics())
        leaderboard_entry = leaderboard_by_path.get(artifact.artifact_path)

        artifact_entries.append(
            RepoDashboardArtifactEntry(
                artifact_path=artifact.artifact_path,
                artifact_type=artifact.artifact_type,
                discovery_reason=artifact.discovery_reason,
                discovery_confidence=artifact.confidence,
                baseline_line_count=baseline.line_count if baseline is not None else 0,
                historical_version_count=metrics["historical_version_count"],
                historical_profile_count=metrics["historical_profile_count"],
                latest_historical_semantic_distance=metrics["latest_historical_semantic_distance"],
                latest_historical_drift_magnitude=metrics["latest_historical_drift_magnitude"],
                pr_profile_count=metrics["pr_profile_count"],
                latest_pr_semantic_distance=metrics["latest_pr_semantic_distance"],
                latest_pr_capability_shift=metrics["latest_pr_capability_shift"],
                latest_pr_guardrail_shift=metrics["latest_pr_guardrail_shift"],
                leaderboard_drift_magnitude=(leaderboard_entry.drift_magnitude if leaderboard_entry is not None else 0.0),
            )
        )

    artifact_entries.sort(
        key=lambda entry: (
            -max(entry.leaderboard_drift_magnitude, entry.latest_historical_drift_magnitude),
            entry.artifact_path,
        )
    )

    insights = _build_repo_insights(artifact_entries)
    control_surface_groups = _build_control_surface_groups(artifact_entries)

    return RepoDashboardView(
        repo_full=repo_full,
        onboarding=onboarding,
        backfill=RepoDashboardBackfillSummary(
            job_count=len(jobs),
            planned_job_count=sum(1 for job in jobs if job.status == "planned"),
            processing_job_count=sum(1 for job in jobs if job.status == "processing"),
            completed_job_count=sum(1 for job in jobs if job.status == "completed"),
            failed_job_count=sum(1 for job in jobs if job.status == "failed"),
            total_historical_versions=total_historical_versions,
            total_historical_profiles=total_historical_profiles,
        ),
        pull_request_audit_count=pull_request_audit_count,
        baseline_version_count=len(baseline_versions),
        drift_summary=drift_summary,
        top_drifting_artifacts=top_drifting_artifacts,
        insights=insights,
        control_surface_groups=control_surface_groups,
        artifacts=artifact_entries,
    )


def _connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _empty_artifact_metrics() -> dict[str, float | int]:
    return {
        "historical_version_count": 0,
        "historical_profile_count": 0,
        "latest_historical_semantic_distance": 0.0,
        "latest_historical_drift_magnitude": 0.0,
        "pr_profile_count": 0,
        "latest_pr_semantic_distance": 0.0,
        "latest_pr_capability_shift": 0.0,
        "latest_pr_guardrail_shift": 0.0,
    }


def _load_repo_artifact_metrics(db_path: str, repo_full: str) -> dict[str, dict[str, float | int]]:
    metrics_by_path: dict[str, dict[str, float | int]] = {}

    with _connect(db_path) as conn:
        historical_version_rows = conn.execute(
            """
            SELECT artifact_path, COUNT(*) AS version_count
            FROM historical_artifact_versions
            WHERE normalized_artifact_id LIKE ?
            GROUP BY artifact_path
            """,
            (_normalized_id_prefix(repo_full),),
        ).fetchall()
        for row in historical_version_rows:
            metrics_by_path.setdefault(row["artifact_path"], _empty_artifact_metrics())["historical_version_count"] = row["version_count"]

        historical_profile_rows = conn.execute(
            """
            SELECT artifact_path, semantic_distance, attribute_deltas_json
            FROM historical_static_profiles
            WHERE normalized_artifact_id LIKE ?
            ORDER BY created_at ASC, id ASC
            """,
            (_normalized_id_prefix(repo_full),),
        ).fetchall()
        for row in historical_profile_rows:
            metrics = metrics_by_path.setdefault(row["artifact_path"], _empty_artifact_metrics())
            metrics["historical_profile_count"] = int(metrics["historical_profile_count"]) + 1
            attribute_deltas = {key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()}
            semantic_distance = float(row["semantic_distance"])
            metrics["latest_historical_semantic_distance"] = semantic_distance
            metrics["latest_historical_drift_magnitude"] = _drift_magnitude(semantic_distance, attribute_deltas)

        pr_profile_rows = conn.execute(
            """
            SELECT sap.artifact_path, sap.semantic_distance, sap.attribute_deltas_json
            FROM static_artifact_profiles sap
            INNER JOIN pull_request_audits pra ON pra.id = sap.audit_id
            WHERE pra.repo_full = ?
            ORDER BY sap.created_at ASC, sap.id ASC
            """,
            (repo_full,),
        ).fetchall()
        for row in pr_profile_rows:
            metrics = metrics_by_path.setdefault(row["artifact_path"], _empty_artifact_metrics())
            metrics["pr_profile_count"] = int(metrics["pr_profile_count"]) + 1
            attribute_deltas = {key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()}
            metrics["latest_pr_semantic_distance"] = float(row["semantic_distance"])
            metrics["latest_pr_capability_shift"] = attribute_deltas.get("capability_risk", 0.0)
            metrics["latest_pr_guardrail_shift"] = attribute_deltas.get("guardrail_robustness", 0.0)

    return metrics_by_path


def _normalized_id_prefix(repo_full: str) -> str:
    return f"{repo_full.lower()}::%"


def _drift_magnitude(semantic_distance: float, attribute_deltas: dict[str, float]) -> float:
    return round(
        abs(attribute_deltas.get("guardrail_robustness", 0.0))
        + abs(attribute_deltas.get("capability_risk", 0.0))
        + abs(attribute_deltas.get("autonomy_level", 0.0))
        + semantic_distance,
        4,
    )


def _build_repo_insights(artifacts: list[RepoDashboardArtifactEntry]) -> list[RepoDashboardInsightEntry]:
    ranked: list[tuple[float, RepoDashboardInsightEntry]] = []
    for artifact in artifacts:
        score = _insight_score(artifact)
        priority = "review_now" if score >= 1.25 else "watch" if score >= 0.6 else "baseline_review"
        title = _insight_title(artifact, priority)
        rationale = _insight_rationale(artifact, priority)
        recommended_action = _insight_action(artifact, priority)
        ranked.append(
            (
                score,
                RepoDashboardInsightEntry(
                    title=title,
                    artifact_path=artifact.artifact_path,
                    artifact_type=artifact.artifact_type,
                    priority=priority,
                    score=score,
                    rationale=rationale,
                    recommended_action=recommended_action,
                ),
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1].artifact_path))
    return [entry for _, entry in ranked[:8]]


def _insight_score(artifact: RepoDashboardArtifactEntry) -> float:
    type_weight = {
        "guardrail": 0.45,
        "system_prompt": 0.4,
        "prompt": 0.4,
        "tooling": 0.3,
        "model_config": 0.28,
        "retrieval": 0.24,
        "ai_code": 0.22,
        "policy": 0.35,
    }.get(artifact.artifact_type, 0.15)
    drift_signal = max(artifact.leaderboard_drift_magnitude, artifact.latest_historical_drift_magnitude)
    guardrail_regression = abs(min(artifact.latest_pr_guardrail_shift, 0.0))
    capability_expansion = max(artifact.latest_pr_capability_shift, 0.0)
    confidence_bonus = artifact.discovery_confidence * 0.2
    history_bonus = min(artifact.historical_version_count, 5) * 0.04
    return round(type_weight + drift_signal + guardrail_regression + capability_expansion + confidence_bonus + history_bonus, 4)


def _insight_title(artifact: RepoDashboardArtifactEntry, priority: str) -> str:
    if artifact.latest_pr_capability_shift > 0.05:
        return "Capability expansion needs review"
    if artifact.latest_pr_guardrail_shift < -0.05:
        return "Guardrail regression needs review"
    if artifact.latest_historical_drift_magnitude > 0.35:
        return "Historical drift hotspot"
    if priority == "baseline_review":
        return "High-value control surface to baseline"
    return "Potentially important control surface"


def _insight_rationale(artifact: RepoDashboardArtifactEntry, priority: str) -> str:
    if artifact.latest_pr_capability_shift > 0.05 and artifact.latest_pr_guardrail_shift < -0.05:
        return "Recent pull-request drift suggests broader authority while guardrails weakened."
    if artifact.latest_pr_capability_shift > 0.05:
        return "Recent pull-request history increased capability risk relative to baseline."
    if artifact.latest_pr_guardrail_shift < -0.05:
        return "Recent pull-request history weakened guardrail posture relative to baseline."
    if artifact.latest_historical_drift_magnitude > 0.35:
        return "Historical snapshots show meaningful design movement that deserves a human read."
    if priority == "baseline_review":
        return "This artifact looks like a real AI control surface but does not yet have meaningful drift context."
    return "This artifact is likely part of the AI control surface and should stay on the review radar."


def _insight_action(artifact: RepoDashboardArtifactEntry, priority: str) -> str:
    if artifact.latest_pr_capability_shift > 0.05:
        return "Inspect authority, tool access, and production-facing behavior before accepting future changes here."
    if artifact.latest_pr_guardrail_shift < -0.05:
        return "Review missing constraints, escalation paths, and refusal language in this artifact."
    if artifact.latest_historical_drift_magnitude > 0.35:
        return "Open the artifact history and compare the earliest and latest versions to understand the behavior shift."
    if priority == "baseline_review":
        return "Confirm whether this is a true AI control surface and keep it in the monitored baseline set."
    return "Track this artifact, but prioritize stronger capability or guardrail signals first."


def _build_control_surface_groups(artifacts: list[RepoDashboardArtifactEntry]) -> list[RepoDashboardControlSurfaceGroup]:
    label_by_group = {
        "prompts": "Prompts and instructions",
        "guardrails": "Guardrails and policy",
        "models": "Model and generation config",
        "tools": "Tooling and orchestration",
        "agents": "Agent code and assets",
        "retrieval": "Retrieval and knowledge",
        "other": "Other AI-related artifacts",
    }
    grouped: dict[str, list[RepoDashboardArtifactEntry]] = {}
    for artifact in artifacts:
        grouped.setdefault(_artifact_group_key(artifact), []).append(artifact)

    results: list[RepoDashboardControlSurfaceGroup] = []
    for group_key, entries in grouped.items():
        sorted_entries = sorted(entries, key=lambda item: (-_insight_score(item), item.artifact_path))
        results.append(
            RepoDashboardControlSurfaceGroup(
                group_key=group_key,
                label=label_by_group[group_key],
                artifact_count=len(entries),
                high_confidence_count=sum(1 for entry in entries if entry.discovery_confidence >= 0.85),
                top_artifact_paths=[entry.artifact_path for entry in sorted_entries[:3]],
            )
        )

    results.sort(key=lambda item: (-item.artifact_count, item.label))
    return results


def _artifact_group_key(artifact: RepoDashboardArtifactEntry) -> str:
    if artifact.artifact_type in {"prompt", "system_prompt"}:
        return "prompts"
    if artifact.artifact_type in {"guardrail", "policy"}:
        return "guardrails"
    if artifact.artifact_type == "model_config":
        return "models"
    if artifact.artifact_type == "tooling":
        return "tools"
    if artifact.artifact_type == "retrieval":
        return "retrieval"
    if artifact.artifact_type == "ai_code":
        return "agents"
    return "other"