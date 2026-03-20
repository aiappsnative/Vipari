from __future__ import annotations

from dataclasses import dataclass

from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile
from engine.models import ChangedFile
from engine.relevance import PATH_RULES, classify_changed_file
from .github_integration import fetch_file_content, get_repo_default_branch, list_file_commits, list_repository_files
from .onboarding_records import (
    DiscoveredArtifactInput,
    HistoricalBackfillJobInput,
    HistoricalBackfillJobRecord,
    OnboardedArtifactRecord,
    OnboardingBaselineVersionRecord,
    RepositoryOnboardingRecord,
    create_historical_backfill_jobs,
    get_latest_repository_onboarding,
    list_onboarded_artifacts_for_onboarding,
    list_onboarding_baseline_versions_for_onboarding,
    record_repository_onboarding,
)


DISCOVERY_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".json", ".py", ".toml"}


@dataclass(frozen=True)
class RepositoryOnboardingResult:
    onboarding: RepositoryOnboardingRecord
    artifacts: list[OnboardedArtifactRecord]
    baseline_versions: list[OnboardingBaselineVersionRecord]


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
