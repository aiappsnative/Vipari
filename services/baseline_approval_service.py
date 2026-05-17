from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.error import HTTPError, URLError

from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile

from .github_integration import fetch_file_content, generate_jwt, get_installation_token
from .onboarding_records import (
    BaselineAuditLogRecord,
    OnboardedArtifactRecord,
    OnboardingBaselineVersionRecord,
    RepositoryOnboardingRecord,
    create_onboarding_baseline_version,
    get_latest_repository_onboarding,
    get_latest_rebaseline_snapshot_id_for_onboarding,
    list_baseline_audit_log_for_onboarding,
    list_latest_approved_onboarding_baseline_versions_for_onboarding,
    list_latest_onboarding_baseline_versions_for_onboarding,
    list_onboarded_artifacts_for_onboarding,
    record_baseline_audit_log,
    select_effective_onboarding_baseline_versions,
    update_onboarding_baseline_review,
    update_repository_onboarding_approval_status,
)
from .provenance_labels import artifact_provenance_label
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
    provenance_kind: str = "supporting_repository_artifact"
    provenance_label: str = "Supporting repository artifact"


@dataclass(frozen=True)
class BaselineReviewDecision:
    action: str
    decision_type: str | None
    actor_login: str | None
    rationale: str | None
    artifact_path: str | None
    linked_findings: list[str]
    created_at: float


@dataclass(frozen=True)
class RepoBaselineReviewPanel:
    repo_full: str
    onboarding_status: str
    is_pending_review: bool
    artifact_count: int
    authoritative_artifact_count: int
    approved_count: int
    pending_count: int
    rejected_count: int
    artifacts: list[BaselineReviewArtifact]
    recent_decisions: list[BaselineReviewDecision]


class RebaselineExternalError(RuntimeError):
    pass


def build_repo_baseline_review_panel_from_records(
    repo_full: str,
    onboarding: RepositoryOnboardingRecord,
    latest_baselines: list[OnboardingBaselineVersionRecord],
    authoritative_artifact_count: int,
    baseline_audit_logs: list[BaselineAuditLogRecord],
) -> RepoBaselineReviewPanel:
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
            provenance_kind=artifact_provenance_label(baseline.artifact_type).kind,
            provenance_label=artifact_provenance_label(baseline.artifact_type).label,
        )
        for baseline in latest_baselines
    ]
    approved_count = sum(1 for baseline in latest_baselines if baseline.approval_status == "approved")
    pending_count = sum(1 for baseline in latest_baselines if baseline.approval_status == "pending")
    rejected_count = sum(1 for baseline in latest_baselines if baseline.approval_status == "rejected")
    recent_decisions = [
        BaselineReviewDecision(
            action=log.action,
            decision_type=log.decision_type,
            actor_login=log.actor_login,
            rationale=log.note,
            artifact_path=log.artifact_path,
            linked_findings=log.linked_findings,
            created_at=log.created_at,
        )
        for log in reversed(baseline_audit_logs[-5:])
    ]
    return RepoBaselineReviewPanel(
        repo_full=repo_full,
        onboarding_status=onboarding.status,
        is_pending_review=onboarding.status == "pending_baseline_approval",
        artifact_count=len(latest_baselines),
        authoritative_artifact_count=authoritative_artifact_count,
        approved_count=approved_count,
        pending_count=pending_count,
        rejected_count=rejected_count,
        artifacts=artifacts,
        recent_decisions=recent_decisions,
    )


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


def _refresh_repo_posture_views_best_effort(db_path: str, repo_full: str) -> None:
    try:
        build_repo_journey(db_path, repo_full)
    except Exception:
        # Derived snapshot rebuilds should not roll back a baseline mutation that already committed.
        _invalidate_dashboard_caches()


def build_repo_baseline_review_panel(db_path: str, repo_full: str) -> RepoBaselineReviewPanel | None:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        return None

    latest_baselines = list_latest_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    authoritative_count = len(list_latest_approved_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id))
    return build_repo_baseline_review_panel_from_records(
        repo_full=repo_full,
        onboarding=onboarding,
        latest_baselines=latest_baselines,
        authoritative_artifact_count=authoritative_count,
        baseline_audit_logs=list_baseline_audit_log_for_onboarding(db_path, onboarding.id),
    )


def _resolve_repo_level_approval_metadata(
    baselines: list[OnboardingBaselineVersionRecord],
) -> tuple[str | None, float | None]:
    approved_baselines = [baseline for baseline in baselines if baseline.approval_status == "approved"]
    if not approved_baselines:
        return (None, None)
    approved_by_values = {baseline.approved_by for baseline in approved_baselines if baseline.approved_by}
    approved_at_values = {baseline.approved_at for baseline in approved_baselines if baseline.approved_at is not None}
    approved_by = next(iter(approved_by_values)) if len(approved_by_values) == 1 else None
    approved_at = max(approved_at_values) if approved_at_values else None
    return (approved_by, approved_at)


def approve_repo_baseline(
    db_path: str,
    *,
    repo_full: str,
    actor_login: str | None,
    approval_note: str | None,
) -> list[OnboardingBaselineVersionRecord]:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        raise ValueError("Repository onboarding was not found.")

    latest_versions = list_latest_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    if not latest_versions:
        raise ValueError("No baseline candidate is available for this repository.")

    updated_versions = [
        update_onboarding_baseline_review(
            db_path,
            baseline_version_id=baseline.id,
            approval_status="approved",
            actor_login=actor_login,
            approval_note=approval_note,
        )
        for baseline in latest_versions
    ]
    approved_at = max(
        (baseline.approved_at for baseline in updated_versions if baseline.approved_at is not None),
        default=None,
    )
    update_repository_onboarding_approval_status(
        db_path,
        onboarding_id=onboarding.id,
        status="baseline_approved",
        approved_by=actor_login,
        approved_at=approved_at,
    )
    approved_snapshot_id = get_latest_rebaseline_snapshot_id_for_onboarding(db_path, onboarding.id)
    record_baseline_audit_log(
        db_path,
        repo_full=repo_full,
        onboarding_id=onboarding.id,
        artifact_path=None,
        action="approve_repo_baseline",
        decision_type="human_review_approved",
        actor_login=actor_login,
        note=approval_note,
        snapshot_id=approved_snapshot_id,
    )
    _invalidate_dashboard_caches()
    _refresh_repo_posture_views_best_effort(db_path, repo_full)
    return updated_versions


def reject_repo_baseline(
    db_path: str,
    *,
    repo_full: str,
    actor_login: str | None,
    approval_note: str | None,
) -> list[OnboardingBaselineVersionRecord]:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        raise ValueError("Repository onboarding was not found.")

    latest_versions = list_latest_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    if not latest_versions:
        raise ValueError("No baseline candidate is available for this repository.")

    updated_versions = [
        update_onboarding_baseline_review(
            db_path,
            baseline_version_id=baseline.id,
            approval_status="rejected",
            actor_login=actor_login,
            approval_note=approval_note,
        )
        for baseline in latest_versions
    ]

    effective_versions = select_effective_onboarding_baseline_versions(
        list_latest_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    )
    onboarded_paths = {
        artifact.artifact_path for artifact in list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    }
    approved_paths = {
        baseline.artifact_path for baseline in effective_versions if baseline.approval_status == "approved"
    }
    has_authoritative_baseline = bool(onboarded_paths) and approved_paths == onboarded_paths
    approved_by, approved_at = _resolve_repo_level_approval_metadata(effective_versions)
    update_repository_onboarding_approval_status(
        db_path,
        onboarding_id=onboarding.id,
        status=("baseline_approved" if has_authoritative_baseline else "pending_baseline_approval"),
        approved_by=approved_by,
        approved_at=approved_at,
    )
    record_baseline_audit_log(
        db_path,
        repo_full=repo_full,
        onboarding_id=onboarding.id,
        artifact_path=None,
        action="reject_repo_baseline",
        decision_type="human_review_rejected",
        actor_login=actor_login,
        note=approval_note,
    )
    _invalidate_dashboard_caches()
    _refresh_repo_posture_views_best_effort(db_path, repo_full)
    return updated_versions


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
        decision_type="human_review_approved",
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
    _refresh_repo_posture_views_best_effort(db_path, repo_full)
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
        decision_type="human_review_rejected",
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
    _refresh_repo_posture_views_best_effort(db_path, repo_full)
    return updated


def rebaseline_repo_from_snapshot(
    db_path: str,
    *,
    repo_full: str,
    snapshot_id: int,
    rationale: str | None,
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

    try:
        jwt_token = generate_jwt_fn(github_app_id, github_private_key_path)
        installation_token = get_installation_token_fn(jwt_token, onboarding.installation_id)
    except (HTTPError, URLError, OSError, RuntimeError) as exc:
        raise RebaselineExternalError(
            f"Unable to access repository contents for snapshot {snapshot.commit_sha}."
        ) from exc
    onboarded_artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    tracked_paths = set((snapshot.artifact_coverage or {}).get("tracked_paths") or [])
    candidate_artifacts = [artifact for artifact in onboarded_artifacts if not tracked_paths or artifact.artifact_path in tracked_paths]
    if not candidate_artifacts:
        raise ValueError("The selected snapshot does not include any currently tracked artifacts that can be re-baselined.")

    def _load_candidate(artifact: OnboardedArtifactRecord):
        try:
            content = fetch_file_content_fn(repo_full, artifact.artifact_path, installation_token, ref=snapshot.commit_sha)
        except HTTPError as exc:
            if exc.code == 404:
                return (artifact, None, None, None)
            raise RebaselineExternalError(
                f"Unable to fetch {artifact.artifact_path} from snapshot {snapshot.commit_sha}."
            ) from exc
        except (URLError, OSError, RuntimeError) as exc:
            raise RebaselineExternalError(
                f"Unable to fetch {artifact.artifact_path} from snapshot {snapshot.commit_sha}."
            ) from exc
        return (
            artifact,
            content,
            build_attribute_profile(content),
            extract_signal_terms_from_text(content),
        )

    created: list[OnboardingBaselineVersionRecord] = []
    missing_artifacts: list[str] = []
    approved_at = snapshot.created_at
    max_workers = max(1, min(8, len(candidate_artifacts)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        loaded_candidates = list(executor.map(_load_candidate, candidate_artifacts))

    for artifact, content, profile, signal_terms in loaded_candidates:
        if content is None or profile is None or signal_terms is None:
            missing_artifacts.append(artifact.artifact_path)
            continue
        created.append(
            create_onboarding_baseline_version(
                db_path,
                onboarding_id=onboarding.id,
                onboarded_artifact_id=artifact.id,
                repo_full=repo_full,
                artifact_path=artifact.artifact_path,
                artifact_type=artifact.artifact_type,
                content_text=content,
                profile=profile,
                signal_terms=signal_terms,
                approval_status="pending",
            )
        )

    if not created:
        if missing_artifacts:
            raise ValueError(
                "The selected snapshot does not contain any of the tracked artifacts needed to create a new baseline candidate."
            )
        raise ValueError("No baseline candidates could be created from the selected snapshot.")

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
        decision_type="baseline_reset_requested",
        actor_login=actor_login,
        note=rationale,
        snapshot_id=snapshot_id,
    )
    _invalidate_dashboard_caches()
    _refresh_repo_posture_views_best_effort(db_path, repo_full)
    return created