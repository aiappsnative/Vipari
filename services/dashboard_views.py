from __future__ import annotations

from dataclasses import dataclass

from .audit_records import (
    ArtifactDriftLeaderboardEntry,
    RepoStaticDriftSummary,
    get_repo_static_drift_summary,
    get_latest_static_profile_for_repo_artifact,
    list_pull_request_audits_for_repo,
    list_static_profiles_for_repo_artifact,
    list_top_drifting_artifacts_for_repo,
)
from .onboarding_records import (
    HistoricalBackfillJobRecord,
    OnboardedArtifactRecord,
    OnboardingBaselineVersionRecord,
    RepositoryOnboardingRecord,
    get_latest_repository_onboarding,
    list_historical_artifact_versions_for_repo_artifact,
    list_historical_backfill_jobs_for_repo,
    list_historical_static_profiles_for_repo_artifact,
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

    artifact_entries: list[RepoDashboardArtifactEntry] = []
    total_historical_versions = 0
    total_historical_profiles = 0

    for artifact in artifacts:
        baseline = baseline_by_path.get(artifact.artifact_path)
        historical_versions = list_historical_artifact_versions_for_repo_artifact(db_path, repo_full, artifact.artifact_path)
        historical_profiles = list_historical_static_profiles_for_repo_artifact(db_path, repo_full, artifact.artifact_path)
        pr_profiles = list_static_profiles_for_repo_artifact(db_path, repo_full, artifact.artifact_path)
        latest_historical_profile = historical_profiles[-1] if historical_profiles else None
        latest_pr_profile = get_latest_static_profile_for_repo_artifact(db_path, repo_full, artifact.artifact_path)
        leaderboard_entry = leaderboard_by_path.get(artifact.artifact_path)

        total_historical_versions += len(historical_versions)
        total_historical_profiles += len(historical_profiles)

        artifact_entries.append(
            RepoDashboardArtifactEntry(
                artifact_path=artifact.artifact_path,
                artifact_type=artifact.artifact_type,
                discovery_reason=artifact.discovery_reason,
                discovery_confidence=artifact.confidence,
                baseline_line_count=baseline.line_count if baseline is not None else 0,
                historical_version_count=len(historical_versions),
                historical_profile_count=len(historical_profiles),
                latest_historical_semantic_distance=(latest_historical_profile.semantic_distance if latest_historical_profile is not None else 0.0),
                latest_historical_drift_magnitude=(
                    _drift_magnitude(latest_historical_profile.semantic_distance, latest_historical_profile.attribute_deltas)
                    if latest_historical_profile is not None
                    else 0.0
                ),
                pr_profile_count=len(pr_profiles),
                latest_pr_semantic_distance=(latest_pr_profile.semantic_distance if latest_pr_profile is not None else 0.0),
                latest_pr_capability_shift=(
                    latest_pr_profile.attribute_deltas["capability_risk"] if latest_pr_profile is not None else 0.0
                ),
                latest_pr_guardrail_shift=(
                    latest_pr_profile.attribute_deltas["guardrail_robustness"] if latest_pr_profile is not None else 0.0
                ),
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


def _drift_magnitude(semantic_distance: float, attribute_deltas: dict[str, float]) -> float:
    return round(
        abs(attribute_deltas.get("guardrail_robustness", 0.0))
        + abs(attribute_deltas.get("capability_risk", 0.0))
        + abs(attribute_deltas.get("autonomy_level", 0.0))
        + semantic_distance,
        4,
    )