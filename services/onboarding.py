from __future__ import annotations

import hashlib

import re
from dataclasses import dataclass, replace

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
STRONG_DISCOVERY_HINTS = {
    "prompt",
    "prompts",
    "system",
    "policy",
    "guardrail",
    "safety",
    "model",
    "rag",
    "retriev",
    "llm",
    "openai",
    "anthropic",
    "claude",
    "inference",
    "chat",
    "completion",
}
SECONDARY_DISCOVERY_HINTS = {
    "tool",
    "mcp",
    "ai",
    "assistant",
    "agent",
    "copilot",
    "eval",
    "evaluation",
}
TEXT_HEAVY_DISCOVERY_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".json", ".toml"}
GROUP_REPRESENTATIVE_EXTENSION_RANK = {
    ".md": 0,
    ".txt": 1,
    ".yaml": 2,
    ".yml": 2,
    ".toml": 3,
    ".json": 4,
    ".py": 5,
}
LOW_SIGNAL_DISCOVERY_CONFIDENCE = 0.78
NOISY_DISCOVERY_SEGMENTS = {
    ".github",
    "__pycache__",
    "docs",
    "doc",
    "example",
    "examples",
    "fixture",
    "fixtures",
    "reference",
    "references",
    "sample",
    "samples",
    "schema",
    "schemas",
    "skill",
    "skills",
    "test",
    "tests",
    "vendor",
}


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
    if not any(lowered.endswith(ext) for ext in DISCOVERY_EXTENSIONS):
        return False

    score = _candidate_path_score(path)
    if any(lowered.endswith(ext) for ext in TEXT_HEAVY_DISCOVERY_EXTENSIONS):
        return score >= 1
    return score >= 3


def _path_segments(path: str) -> list[str]:
    return [segment for segment in path.lower().replace("\\", "/").split("/") if segment]


def _candidate_path_score(path: str) -> int:
    lowered = path.lower()
    segments = _path_segments(path)
    score = 0

    strong_hits = sum(1 for hint in STRONG_DISCOVERY_HINTS if hint in lowered)
    secondary_hits = sum(1 for hint in SECONDARY_DISCOVERY_HINTS if hint in lowered)
    noisy_hits = sum(1 for segment in segments if segment in NOISY_DISCOVERY_SEGMENTS)

    score += strong_hits * 3
    score += secondary_hits
    score -= noisy_hits * 2

    filename = segments[-1] if segments else lowered
    if filename.startswith("test_") or filename.endswith("_test.py"):
        score -= 2

    return score


def _discovery_confidence(path: str, reason: str) -> float:
    score = _candidate_path_score(path)
    if "Content indicates" in reason:
        return 0.82 if score >= 1 else 0.74
    if score >= 6:
        return 0.95
    if score >= 3:
        return 0.88
    return 0.72


def _low_signal_group_key(artifact: DiscoveredArtifactInput) -> tuple[str, str] | None:
    if artifact.confidence >= LOW_SIGNAL_DISCOVERY_CONFIDENCE:
        return None

    if any(artifact.artifact_path.lower().endswith(ext) for ext in TEXT_HEAVY_DISCOVERY_EXTENSIONS):
        family = _low_signal_family_label(artifact.artifact_path)
        if family is not None:
            return artifact.artifact_type, family

    segments = _path_segments(artifact.artifact_path)
    parent = "/".join(segments[:-1]) if len(segments) > 1 else "."
    return artifact.artifact_type, parent


def _low_signal_family_label(path: str) -> str | None:
    tokens = [token for token in re.split(r"[^a-z0-9]+", path.lower()) if token]
    for hint in STRONG_DISCOVERY_HINTS:
        if hint in tokens:
            return hint
    for hint in SECONDARY_DISCOVERY_HINTS:
        if hint in tokens:
            return hint
    return None


def _group_low_signal_artifacts(discovered: list[DiscoveredArtifactInput]) -> list[DiscoveredArtifactInput]:
    grouped: list[DiscoveredArtifactInput] = []
    buffered: dict[tuple[str, str], list[DiscoveredArtifactInput]] = {}

    for artifact in discovered:
        group_key = _low_signal_group_key(artifact)
        if group_key is None:
            grouped.append(artifact)
            continue
        buffered.setdefault(group_key, []).append(artifact)

    for (_, group_label), artifacts in sorted(buffered.items(), key=lambda item: item[0]):
        if len(artifacts) == 1:
            grouped.append(artifacts[0])
            continue

        representative = min(artifacts, key=_group_representative_sort_key)
        grouped.append(
            replace(
                representative,
                discovery_reason=(
                    f"{representative.discovery_reason} "
                    f"Grouped {len(artifacts)} low-signal candidates under {group_label} to reduce exploratory queue noise."
                ),
            )
        )

    return sorted(grouped, key=lambda artifact: artifact.artifact_path)


def _group_representative_sort_key(artifact: DiscoveredArtifactInput) -> tuple[int, str]:
    lowered = artifact.artifact_path.lower()
    extension_rank = min((rank for ext, rank in GROUP_REPRESENTATIVE_EXTENSION_RANK.items() if lowered.endswith(ext)), default=9)
    return extension_rank, artifact.artifact_path


def discover_ai_artifacts(file_contents: dict[str, str]) -> list[DiscoveredArtifactInput]:
    discovered: list[DiscoveredArtifactInput] = []
    for path, content in sorted(file_contents.items()):
        changed_file = ChangedFile(old_path=path, new_path=path, diff_lines=content.splitlines())
        relevance = classify_changed_file(changed_file)
        if not relevance.ai_relevant:
            continue
        discovered.append(
            DiscoveredArtifactInput(
                artifact_path=path,
                artifact_type=relevance.artifact_type,
                discovery_reason=relevance.reason,
                confidence=_discovery_confidence(path, relevance.reason),
                baseline_content=content,
            )
        )
    return _group_low_signal_artifacts(discovered)


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
        status="baseline_approved",
        discovered_artifacts=discovered,
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    baselines = list_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    from .repo_journey import materialize_repo_journey

    materialize_repo_journey(db_path, repo_full)
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

    from .repo_journey import materialize_repo_journey

    materialize_repo_journey(db_path, repo_full)
    return execution_results


def sync_on_pr_merge_artifact_changes(
    db_path: str,
    *,
    repo_full: str,
    artifact_snapshots: dict[str, str] | None = None,
    added_paths: set[str] | None = None,
    removed_paths: set[str] | None = None,
) -> RepositoryOnboardingResult | None:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        return None

    artifact_snapshots = artifact_snapshots or {}
    added_paths = added_paths or set()
    removed_paths = removed_paths or set()

    existing_artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    existing_baselines = {
        baseline.artifact_path: baseline
        for baseline in list_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    }

    discovered_by_path: dict[str, DiscoveredArtifactInput] = {}
    for artifact in existing_artifacts:
        if artifact.artifact_path in removed_paths:
            continue
        baseline = existing_baselines.get(artifact.artifact_path)
        if baseline is None or baseline.content_text is None:
            continue
        discovered_by_path[artifact.artifact_path] = DiscoveredArtifactInput(
            artifact_path=artifact.artifact_path,
            artifact_type=artifact.artifact_type,
            discovery_reason=artifact.discovery_reason,
            confidence=artifact.confidence,
            baseline_content=baseline.content_text,
        )

    added_file_contents = {
        path: artifact_snapshots[path]
        for path in sorted(added_paths)
        if path not in removed_paths and path in artifact_snapshots and _is_candidate_path(path)
    }
    for discovered in discover_ai_artifacts(added_file_contents):
        discovered_by_path[discovered.artifact_path] = discovered

    updated_onboarding = record_repository_onboarding(
        db_path,
        repo_full=repo_full,
        installation_id=onboarding.installation_id,
        default_branch=onboarding.default_branch,
        status="baseline_approved",
        discovered_artifacts=sorted(discovered_by_path.values(), key=lambda artifact: artifact.artifact_path),
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    artifacts = list_onboarded_artifacts_for_onboarding(db_path, updated_onboarding.id)
    baselines = list_onboarding_baseline_versions_for_onboarding(db_path, updated_onboarding.id)
    from .repo_journey import materialize_repo_journey

    materialize_repo_journey(db_path, repo_full)
    return RepositoryOnboardingResult(onboarding=updated_onboarding, artifacts=artifacts, baseline_versions=baselines)
