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
class RepoDashboardView:
    repo_full: str
    onboarding: RepositoryOnboardingRecord | None
    backfill: RepoDashboardBackfillSummary
    pull_request_audit_count: int
    baseline_version_count: int
    drift_summary: RepoStaticDriftSummary
    top_drifting_artifacts: list[ArtifactDriftLeaderboardEntry]
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