from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import json

from services.control_plane_records import list_repo_allocations_for_workspace
from services.dashboard_views import build_dashboard_overview_view, build_workspace_escalation_queue, filter_dashboard_overview_view, list_repo_dashboard_index


def build_repo_index_payload(
    db_path: str,
    *,
    allowed_repo_fulls: set[str] | None = None,
    repo_scope_by_full: dict[str, str] | None = None,
    allocation_status_by_full: dict[str, str | None] | None = None,
    list_repo_dashboard_index_fn: Callable[..., list] = list_repo_dashboard_index,
) -> dict[str, object]:
    return {
        "repos": [
            asdict(item)
            for item in list_repo_dashboard_index_fn(
                db_path,
                allowed_repo_fulls=allowed_repo_fulls,
                repo_scope_by_full=repo_scope_by_full,
                allocation_status_by_full=allocation_status_by_full,
            )
        ]
    }


def build_dashboard_overview_payload(
    db_path: str,
    *,
    allowed_repo_fulls: set[str] | None = None,
    repo_scope_by_full: dict[str, str] | None = None,
    allocation_status_by_full: dict[str, str | None] | None = None,
    active_filter: str = "all",
    active_range: str = "7d",
    access_context: dict[str, object] | None = None,
    build_dashboard_overview_view_fn: Callable[..., object] = build_dashboard_overview_view,
) -> dict[str, object]:
    overview_view = build_dashboard_overview_view_fn(
        db_path,
        allowed_repo_fulls=allowed_repo_fulls,
        repo_scope_by_full=repo_scope_by_full,
        allocation_status_by_full=allocation_status_by_full,
    )
    normalized_filter = active_filter.strip().lower() if active_filter else "all"
    if normalized_filter not in {"all", "critical", "mine"}:
        normalized_filter = "all"

    owned_repo_fulls: set[str] | None = None
    if normalized_filter == "mine":
        owned_repo_fulls = set()
        if access_context:
            workspace = access_context.get("workspace")
            session = access_context.get("session")
            if workspace is not None and session is not None:
                owned_repo_fulls = {
                    allocation.repo_full
                    for allocation in list_repo_allocations_for_workspace(db_path, workspace.id)
                    if allocation.activated_by_user_id == session.user_id
                }

    normalized_range = active_range.strip().lower() if active_range else "7d"
    if normalized_range not in {"24h", "7d", "30d"}:
        normalized_range = "7d"

    filtered_overview_view = filter_dashboard_overview_view(
        overview_view,
        normalized_filter,
        overview_range=normalized_range,
        allowed_repo_fulls=owned_repo_fulls,
    )
    payload = asdict(filtered_overview_view)
    nav_repos = filtered_overview_view.repos if normalized_filter == "mine" else overview_view.repos
    payload["nav_repos"] = [asdict(repo) for repo in nav_repos]
    return payload


def build_dashboard_escalation_queue_payload(
    db_path: str,
    *,
    allowed_repo_fulls: set[str] | None = None,
    include_watch: bool = False,
    build_workspace_escalation_queue_fn: Callable[..., dict[str, object]] = build_workspace_escalation_queue,
) -> dict[str, object]:
    return build_workspace_escalation_queue_fn(
        db_path,
        allowed_repo_fulls=allowed_repo_fulls,
        include_watch=include_watch,
    )


def build_pending_proposals_payload(
    db_path: str,
    repo_full: str,
    *,
    workspace_id: int | None,
    list_pending_proposals_fn: Callable[..., list],
    get_latest_repository_onboarding_fn: Callable[..., object | None],
    list_onboarded_artifacts_for_onboarding_fn: Callable[..., list],
    get_machine_principal_by_id_fn: Callable[..., object | None],
    service_account_principal_kind: str,
) -> dict[str, object]:
    if workspace_id is None:
        return {"proposals": [], "pending_count": 0}

    proposals = list_pending_proposals_fn(db_path, repo_full, workspace_id)
    if not proposals:
        return {"proposals": [], "pending_count": 0}

    onboarding = get_latest_repository_onboarding_fn(db_path, repo_full)
    artifact_path_by_id: dict[int, str] = {}
    if onboarding:
        for artifact in list_onboarded_artifacts_for_onboarding_fn(db_path, onboarding.id):
            artifact_path_by_id[artifact.id] = artifact.artifact_path

    principals_cache: dict[int, object | None] = {}
    proposals_out: list[dict[str, object]] = []
    for proposal in proposals:
        if proposal.proposer_principal_id not in principals_cache:
            principals_cache[proposal.proposer_principal_id] = get_machine_principal_by_id_fn(
                db_path,
                proposal.proposer_principal_id,
            )
        proposer = principals_cache.get(proposal.proposer_principal_id)
        is_agent = (
            proposer is not None
            and getattr(proposer, "principal_kind", None) == service_account_principal_kind
        )
        proposals_out.append(
            {
                "proposal_id": proposal.id,
                "artifact_id": proposal.artifact_id,
                "artifact_path": artifact_path_by_id.get(proposal.artifact_id, ""),
                "status": proposal.status,
                "rationale": proposal.rationale,
                "proposer_principal_id": proposal.proposer_principal_id,
                "is_agent_proposal": is_agent,
                "created_at": proposal.created_at,
                "expires_at": proposal.expires_at,
            }
        )

    proposals_out.sort(key=lambda proposal: proposal["created_at"])
    return {"proposals": proposals_out, "pending_count": len(proposals_out)}


def build_pre_audit_relevance_payload(
    db_path: str,
    repo_full: str,
    *,
    pr_number: int,
    head_sha: str,
    list_pre_audit_relevance_decisions_fn: Callable[..., list],
) -> dict[str, object]:
    decisions = list_pre_audit_relevance_decisions_fn(
        db_path,
        repo_full=repo_full,
        pr_number=pr_number,
        head_sha=head_sha,
    )
    payload_rows: list[dict[str, object]] = []
    for decision in decisions:
        try:
            matched_signals = json.loads(decision.matched_signals_json or "[]")
        except json.JSONDecodeError:
            matched_signals = []
        payload_rows.append(
            {
                "artifact_path": decision.artifact_path,
                "artifact_type": decision.artifact_type,
                "confidence_tier": decision.confidence_tier,
                "heuristic_score": decision.heuristic_score,
                "heuristic_reason": decision.heuristic_reason,
                "matched_signals": matched_signals if isinstance(matched_signals, list) else [],
                "classifier_status": decision.classifier_status,
                "classifier_is_relevant": decision.classifier_is_relevant,
                "classifier_reason": decision.classifier_reason,
                "provider": decision.provider,
                "model": decision.model,
                "latency_ms": decision.latency_ms,
                "changed_artifact_id": decision.changed_artifact_id,
                "created_at": decision.created_at,
                "updated_at": decision.updated_at,
            }
        )
    return {
        "repo_full": repo_full,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "decision_count": len(payload_rows),
        "decisions": payload_rows,
    }


def build_artifact_storyline_payload(
    db_path: str,
    repo_full: str,
    artifact_path: str,
    *,
    build_repo_artifact_storyline_fn: Callable[..., object | None],
) -> dict[str, object]:
    storyline = build_repo_artifact_storyline_fn(db_path, repo_full, artifact_path)
    if storyline is None:
        raise ValueError("No artifact storyline is available for this repo artifact.")
    return {
        "repo_full": repo_full,
        "artifact_path": artifact_path,
        "storyline": asdict(storyline),
    }


def build_repo_journey_payload(
    db_path: str,
    repo_full: str,
    *,
    build_repo_journey_fn: Callable[..., list],
    snapshot_to_public_payload_fn: Callable[..., dict[str, object]],
) -> dict[str, object]:
    return {
        "repo_full": repo_full,
        "snapshots": [snapshot_to_public_payload_fn(item) for item in build_repo_journey_fn(db_path, repo_full)],
    }


def build_repo_snapshot_detail_payload(
    db_path: str,
    repo_full: str,
    snapshot_id: int,
    *,
    get_repo_snapshot_detail_fn: Callable[..., object | None],
    snapshot_to_public_payload_fn: Callable[..., dict[str, object]],
) -> dict[str, object]:
    snapshot = get_repo_snapshot_detail_fn(db_path, repo_full, snapshot_id)
    if snapshot is None:
        raise ValueError("Repo posture snapshot was not found.")
    return {"repo_full": repo_full, "snapshot": snapshot_to_public_payload_fn(snapshot)}


def build_repo_snapshot_compare_payload(
    db_path: str,
    repo_full: str,
    left: int,
    right: int,
    *,
    compare_repo_snapshots_fn: Callable[..., object],
) -> dict[str, object]:
    comparison = compare_repo_snapshots_fn(db_path, repo_full, left, right)
    return asdict(comparison)
