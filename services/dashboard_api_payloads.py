from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

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
