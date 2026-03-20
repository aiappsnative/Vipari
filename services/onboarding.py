from __future__ import annotations

import hashlib
from dataclasses import dataclass

from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile
from engine.models import ChangedFile
from engine.relevance import PATH_RULES, classify_changed_file
from .github_integration import fetch_file_content, get_repo_default_branch, list_file_commits, list_repository_files
from .onboarding_records import (
    DiscoveredArtifactInput,
    HistoricalArtifactSnapshotInput,
    HistoricalBackfillJobInput,
    HistoricalBackfillJobRecord,
    HistoricalArtifactVersionRecord,
    HistoricalStaticProfileRecord,
    OnboardedArtifactRecord,
    OnboardingBaselineVersionRecord,
    RepositoryOnboardingRecord,
    create_historical_backfill_jobs,
    get_historical_backfill_job,
    get_latest_repository_onboarding,
    list_historical_backfill_jobs_for_repo,
    list_onboarded_artifacts_for_onboarding,
    list_onboarding_baseline_versions_for_onboarding,
    record_historical_backfill_versions,
    record_repository_onboarding,
    update_historical_backfill_job_status,
)


DISCOVERY_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".json", ".py", ".toml"}


@dataclass(frozen=True)
class RepositoryOnboardingResult:
    onboarding: RepositoryOnboardingRecord
    artifacts: list[OnboardedArtifactRecord]
    baseline_versions: list[OnboardingBaselineVersionRecord]


@dataclass(frozen=True)
class HistoricalBackfillExecutionResult:
    job: HistoricalBackfillJobRecord
    versions: list[HistoricalArtifactVersionRecord]
    profiles: list[HistoricalStaticProfileRecord]


def _is_candidate_path(path: str) -> bool:
    lowered = path.lower()
    if any(keyword in lowered for keywords, _, _ in PATH_RULES for keyword in keywords):
        return True
    return any(lowered.endswith(ext) for ext in DISCOVERY_EXTENSIONS)


def discover_ai_artifacts(file_contents: dict[str, str]) -> list[DiscoveredArtifactInput]:
    discovered: list[DiscoveredArtifactInput] = []
    for path, content in sorted(file_contents.items()):
        changed_file = ChangedFile(old_path=path, new_path=path, diff_lines=content.splitlines())
        relevance = classify_changed_file(changed_file)
        if not relevance.ai_relevant:
            continue
        confidence = 0.9 if "Path indicates" in relevance.reason else 0.7
        discovered.append(
            DiscoveredArtifactInput(
                artifact_path=path,
                artifact_type=relevance.artifact_type,
                discovery_reason=relevance.reason,
                confidence=confidence,
                baseline_content=content,
            )
        )
    return discovered


def onboard_repository(
    db_path: str,
    *,
    repo_full: str,
    installation_id: int,
    token: str,
    get_default_branch_fn=get_repo_default_branch,
    list_repository_files_fn=list_repository_files,
    fetch_file_content_fn=fetch_file_content,
) -> RepositoryOnboardingResult:
    default_branch = get_default_branch_fn(repo_full, token)
    candidate_paths = [path for path in list_repository_files_fn(repo_full, token, ref=default_branch) if _is_candidate_path(path)]
    file_contents: dict[str, str] = {}
    for path in candidate_paths:
        try:
            file_contents[path] = fetch_file_content_fn(repo_full, path, token, ref=default_branch)
        except Exception:
            continue

    discovered = discover_ai_artifacts(file_contents)
    onboarding = record_repository_onboarding(
        db_path,
        repo_full=repo_full,
        installation_id=installation_id,
        default_branch=default_branch,
        status="completed",
        discovered_artifacts=discovered,
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    baselines = list_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    return RepositoryOnboardingResult(onboarding=onboarding, artifacts=artifacts, baseline_versions=baselines)


def plan_repository_history_backfill(
    db_path: str,
    *,
    repo_full: str,
    token: str,
    commit_limit_per_artifact: int = 10,
    list_file_commits_fn=list_file_commits,
) -> list[HistoricalBackfillJobRecord]:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        return []

    artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    job_inputs: list[HistoricalBackfillJobInput] = []
    for artifact in artifacts:
        commit_shas = list_file_commits_fn(repo_full, artifact.artifact_path, token, branch=onboarding.default_branch, limit=commit_limit_per_artifact)
        if not commit_shas:
            continue
        job_inputs.append(
            HistoricalBackfillJobInput(
                onboarded_artifact_id=artifact.id,
                artifact_path=artifact.artifact_path,
                artifact_type=artifact.artifact_type,
                commit_shas=commit_shas,
            )
        )

    return create_historical_backfill_jobs(
        db_path,
        onboarding_id=onboarding.id,
        repo_full=repo_full,
        jobs=job_inputs,
        status="planned",
    )


def execute_repository_history_backfill(
    db_path: str,
    *,
    repo_full: str,
    token: str,
    fetch_file_content_fn=fetch_file_content,
) -> list[HistoricalBackfillExecutionResult]:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        return []

    baseline_versions = {
        baseline.artifact_path: baseline
        for baseline in list_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    }
    jobs = [job for job in list_historical_backfill_jobs_for_repo(db_path, repo_full) if job.status == "planned"]
    execution_results: list[HistoricalBackfillExecutionResult] = []

    for job in jobs:
        baseline_version = baseline_versions.get(job.artifact_path)
        if baseline_version is None:
            updated_job = update_historical_backfill_job_status(
                db_path,
                job_id=job.id,
                status="failed",
                completed_commit_count=0,
                last_error="No onboarding baseline version was available for this artifact.",
            )
            execution_results.append(HistoricalBackfillExecutionResult(job=updated_job, versions=[], profiles=[]))
            continue

        update_historical_backfill_job_status(db_path, job_id=job.id, status="processing", last_error=None)
        latest_commit_sha = job.commit_shas[0] if job.commit_shas else None
        snapshots: list[HistoricalArtifactSnapshotInput] = []
        previous_snapshot_hash: str | None = None
        fetch_failures: list[str] = []

        for commit_sha in reversed(job.commit_shas):
            try:
                content = fetch_file_content_fn(repo_full, job.artifact_path, token, ref=commit_sha)
            except Exception:
                fetch_failures.append(commit_sha)
                continue

            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if commit_sha == latest_commit_sha and content_hash == baseline_version.version_hash:
                continue
            if content_hash == previous_snapshot_hash:
                continue

            snapshots.append(HistoricalArtifactSnapshotInput(commit_sha=commit_sha, content=content))
            previous_snapshot_hash = content_hash

        versions, profiles = record_historical_backfill_versions(
            db_path,
            backfill_job_id=job.id,
            onboarding_id=job.onboarding_id,
            onboarded_artifact_id=job.onboarded_artifact_id,
            repo_full=repo_full,
            artifact_path=job.artifact_path,
            artifact_type=job.artifact_type,
            snapshots=snapshots,
            extract_signal_terms_fn=extract_signal_terms_from_text,
            build_profile_fn=build_attribute_profile,
        )

        if fetch_failures and not snapshots:
            updated_job = update_historical_backfill_job_status(
                db_path,
                job_id=job.id,
                status="failed",
                completed_commit_count=0,
                last_error=f"Failed to fetch any historical snapshots for commits: {', '.join(fetch_failures)}",
            )
        else:
            update_historical_backfill_job_status(
                db_path,
                job_id=job.id,
                status="completed",
                completed_commit_count=len(versions),
                last_error=None,
            )
            updated_job = get_historical_backfill_job(db_path, job.id)
            if updated_job is None:
                raise RuntimeError("Failed to reload historical backfill job after execution.")

        execution_results.append(HistoricalBackfillExecutionResult(job=updated_job, versions=versions, profiles=profiles))

    return execution_results
