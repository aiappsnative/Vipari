from __future__ import annotations

from dataclasses import dataclass

from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile

from .github_integration import fetch_file_content, generate_jwt, get_installation_token
from .onboarding_records import (
    OnboardedArtifactRecord,
    OnboardingBaselineVersionRecord,
    RepositoryOnboardingRecord,
    create_onboarding_baseline_version,
    get_latest_repository_onboarding,
    list_latest_onboarding_baseline_versions_for_onboarding,
    list_onboarded_artifacts_for_onboarding,
    record_baseline_audit_log,
    update_onboarding_baseline_review,
    update_repository_onboarding_approval_status,
)
from .repo_journey import build_repo_journey, get_repo_snapshot_detail


@dataclass(frozen=True)
class BaselineReviewArtifact:
    artifact_path: str
    artifact_type: str
    approval_status: str
    approval_note: str | None
    approved_by: str | None
    approved_at: float | None
    profile: dict[str, float]
    line_count: int


@dataclass(frozen=True)
class RepoBaselineReviewPanel:
    repo_full: str
    onboarding_status: str
    is_pending_review: bool
    artifact_count: int
    approved_count: int
    pending_count: int
    rejected_count: int
    artifacts: list[BaselineReviewArtifact]


def _profile_payload(profile) -> dict[str, float]:
    return {
        "guardrail_robustness": profile.guardrail_robustness,
        "capability_risk": profile.capability_risk,
        "autonomy_level": profile.autonomy_level,
        "stability_vs_creativity": profile.stability_vs_creativity,
        "governance_strength": profile.governance_strength,
        "change_frequency": profile.change_frequency,
        "semantic_density": profile.semantic_density,
    }


def _latest_baselines_by_path(baselines: list[OnboardingBaselineVersionRecord]) -> dict[str, OnboardingBaselineVersionRecord]:
    latest: dict[str, OnboardingBaselineVersionRecord] = {}
    for baseline in baselines:
        latest[baseline.artifact_path] = baseline
    return latest


def _invalidate_dashboard_caches() -> None:
    from .dashboard_views import invalidate_dashboard_caches

    invalidate_dashboard_caches()


def build_repo_baseline_review_panel(db_path: str, repo_full: str) -> RepoBaselineReviewPanel | None:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        return None

    latest_baselines = list_latest_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    artifacts = [
        BaselineReviewArtifact(
            artifact_path=baseline.artifact_path,
            artifact_type=baseline.artifact_type,
            approval_status=baseline.approval_status,
            approval_note=baseline.approval_note,
            approved_by=baseline.approved_by,
            approved_at=baseline.approved_at,
            profile=_profile_payload(baseline.profile),
            line_count=baseline.line_count,
        )
        for baseline in latest_baselines
    ]
    approved_count = sum(1 for baseline in latest_baselines if baseline.approval_status == "approved")
    pending_count = sum(1 for baseline in latest_baselines if baseline.approval_status == "pending")
    rejected_count = sum(1 for baseline in latest_baselines if baseline.approval_status == "rejected")
    return RepoBaselineReviewPanel(
        repo_full=repo_full,
        onboarding_status=onboarding.status,
        is_pending_review=onboarding.status == "pending_baseline_approval",
        artifact_count=len(latest_baselines),
        approved_count=approved_count,
        pending_count=pending_count,
        rejected_count=rejected_count,
        artifacts=artifacts,
    )


def approve_repo_baseline_artifact(
    db_path: str,
    *,
    repo_full: str,
    artifact_path: str,
    actor_login: str | None,
    approval_note: str | None,
) -> OnboardingBaselineVersionRecord:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        raise ValueError("Repository onboarding was not found.")
    latest_by_path = _latest_baselines_by_path(list_latest_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id))
    baseline = latest_by_path.get(artifact_path)
    if baseline is None:
        raise ValueError("Pending baseline candidate was not found for this artifact.")

    updated = update_onboarding_baseline_review(
        db_path,
        baseline_version_id=baseline.id,
        approval_status="approved",
        actor_login=actor_login,
        approval_note=approval_note,
    )
    record_baseline_audit_log(
        db_path,
        repo_full=repo_full,
        onboarding_id=onboarding.id,
        artifact_path=artifact_path,
        action="approve",
        actor_login=actor_login,
        note=approval_note,
        baseline_version_id=updated.id,
    )

    latest_versions = list_latest_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    if latest_versions and all(item.approval_status == "approved" for item in latest_versions):
        update_repository_onboarding_approval_status(
            db_path,
            onboarding_id=onboarding.id,
            status="baseline_approved",
            approved_by=actor_login,
            approved_at=updated.approved_at,
        )
    else:
        update_repository_onboarding_approval_status(
            db_path,
            onboarding_id=onboarding.id,
            status="pending_baseline_approval",
            approved_by=None,
            approved_at=None,
        )
    _invalidate_dashboard_caches()
    build_repo_journey(db_path, repo_full)
    return updated


def reject_repo_baseline_artifact(
    db_path: str,
    *,
    repo_full: str,
    artifact_path: str,
    actor_login: str | None,
    approval_note: str | None,
) -> OnboardingBaselineVersionRecord:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        raise ValueError("Repository onboarding was not found.")
    latest_by_path = _latest_baselines_by_path(list_latest_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id))
    baseline = latest_by_path.get(artifact_path)
    if baseline is None:
        raise ValueError("Baseline candidate was not found for this artifact.")

    updated = update_onboarding_baseline_review(
        db_path,
        baseline_version_id=baseline.id,
        approval_status="rejected",
        actor_login=actor_login,
        approval_note=approval_note,
    )
    record_baseline_audit_log(
        db_path,
        repo_full=repo_full,
        onboarding_id=onboarding.id,
        artifact_path=artifact_path,
        action="reject",
        actor_login=actor_login,
        note=approval_note,
        baseline_version_id=updated.id,
    )
    update_repository_onboarding_approval_status(
        db_path,
        onboarding_id=onboarding.id,
        status="pending_baseline_approval",
        approved_by=None,
        approved_at=None,
    )
    _invalidate_dashboard_caches()
    build_repo_journey(db_path, repo_full)
    return updated


def rebaseline_repo_from_snapshot(
    db_path: str,
    *,
    repo_full: str,
    snapshot_id: int,
    rationale: str,
    actor_login: str | None,
    github_app_id: str,
    github_private_key_path: str,
    generate_jwt_fn=generate_jwt,
    get_installation_token_fn=get_installation_token,
    fetch_file_content_fn=fetch_file_content,
) -> list[OnboardingBaselineVersionRecord]:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        raise ValueError("Repository onboarding was not found.")
    snapshot = get_repo_snapshot_detail(db_path, repo_full, snapshot_id)
    if snapshot is None:
        raise ValueError("Repository snapshot was not found.")
    if not snapshot.commit_sha:
        raise ValueError("This snapshot cannot be re-baselined because it is not tied to a concrete commit.")

    jwt_token = generate_jwt_fn(github_app_id, github_private_key_path)
    installation_token = get_installation_token_fn(jwt_token, onboarding.installation_id)
    onboarded_artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    created: list[OnboardingBaselineVersionRecord] = []
    for artifact in onboarded_artifacts:
        content = fetch_file_content_fn(repo_full, artifact.artifact_path, installation_token, ref=snapshot.commit_sha)
        created.append(
            create_onboarding_baseline_version(
                db_path,
                onboarding_id=onboarding.id,
                onboarded_artifact_id=artifact.id,
                repo_full=repo_full,
                artifact_path=artifact.artifact_path,
                artifact_type=artifact.artifact_type,
                content_text=content,
                profile=build_attribute_profile(content),
                signal_terms=extract_signal_terms_from_text(content),
                approval_status="pending",
            )
        )

    update_repository_onboarding_approval_status(
        db_path,
        onboarding_id=onboarding.id,
        status="pending_baseline_approval",
        approved_by=None,
        approved_at=None,
    )
    record_baseline_audit_log(
        db_path,
        repo_full=repo_full,
        onboarding_id=onboarding.id,
        artifact_path=None,
        action="rebaseline",
        actor_login=actor_login,
        note=rationale,
        snapshot_id=snapshot_id,
    )
    _invalidate_dashboard_caches()
    build_repo_journey(db_path, repo_full)
    return created