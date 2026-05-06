from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, Request

from config import Settings
from services.access_state import WorkspaceAccessSnapshot, resolve_workspace_access_state
from services.control_plane_records import (
    count_workspace_repo_allocations,
    get_github_identity_for_user,
    get_repo_allocation_for_workspace,
    get_repo_connection_for_workspace,
    get_user_by_id,
    get_user_session,
    get_workspace_by_id,
    get_workspace_entitlement,
    get_workspace_installation,
    get_workspace_membership,
    get_workspace_subscription,
)
from services.onboarding_records import get_latest_repository_onboarding


def get_session(settings: Settings, db_path: str, request: Request):
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        return None
    return get_user_session(db_path, session_id)


def build_access_context(db_path: str, session) -> dict[str, object]:
    if session is None:
        resolution = resolve_workspace_access_state(WorkspaceAccessSnapshot(is_authenticated=False))
        return {"session": None, "user": None, "identity": None, "workspace": None, "resolution": resolution}

    user = get_user_by_id(db_path, session.user_id)
    identity = get_github_identity_for_user(db_path, session.user_id)
    workspace = get_workspace_by_id(db_path, session.workspace_id) if session.workspace_id else None
    membership = get_workspace_membership(db_path, workspace.id, session.user_id) if workspace else None
    subscription = get_workspace_subscription(db_path, workspace.id) if workspace else None
    entitlement = get_workspace_entitlement(db_path, workspace.id) if workspace else None
    installation = get_workspace_installation(db_path, workspace.id) if workspace else None
    allocated_repo_count, onboarded_repo_count = count_workspace_repo_allocations(db_path, workspace.id) if workspace else (0, 0)

    subscription_status = (subscription.status if subscription else "").lower()
    snapshot = WorkspaceAccessSnapshot(
        is_authenticated=True,
        has_workspace=workspace is not None,
        invitation_pending=bool(membership and membership.invitation_state != "accepted"),
        has_membership=membership is not None,
        role=membership.role if membership else None,
        has_subscription_record=subscription is not None,
        billing_pending_confirmation=subscription_status in {"incomplete", "pending", "trialing_pending"},
        payment_failed=subscription_status in {"past_due", "unpaid", "payment_failed"},
        dashboard_enabled=bool(entitlement.dashboard_enabled) if entitlement else subscription_status in SUPPORTED_ACTIVE_PLAN_STATUSES,
        pr_comments_enabled=bool(entitlement.pr_comments_enabled) if entitlement else subscription_status in SUPPORTED_ACTIVE_PLAN_STATUSES,
        has_linked_installation=installation is not None,
        allocated_repo_count=allocated_repo_count,
        onboarded_repo_count=onboarded_repo_count,
        cancel_at_period_end=bool(subscription.cancel_at_period_end) if subscription else False,
        subscription_expired=subscription_status in {"incomplete_expired", "expired"},
    )
    resolution = resolve_workspace_access_state(snapshot)
    return {
        "session": session,
        "user": user,
        "identity": identity,
        "workspace": workspace,
        "membership": membership,
        "subscription": subscription,
        "entitlement": entitlement,
        "installation": installation,
        "resolution": resolution,
    }


SUPPORTED_ACTIVE_PLAN_STATUSES = {"active", "trialing", "canceled", "free_active"}


def current_workspace_context(
    settings: Settings,
    db_path: str,
    request: Request,
    *,
    allow_local_debug: bool = False,
    local_debug_context_factory: Callable[[], dict[str, object] | None] | None = None,
) -> dict[str, object]:
    session = get_session(settings, db_path, request)
    if session is None:
        debug_context = local_debug_context_factory() if allow_local_debug and local_debug_context_factory is not None else None
        if debug_context is not None:
            return debug_context
        raise HTTPException(status_code=401, detail="Authentication required.")
    access_context = build_access_context(db_path, session)
    if access_context["workspace"] is None:
        raise HTTPException(status_code=400, detail="Workspace context is required.")
    return access_context


def current_authenticated_identity_context(settings: Settings, db_path: str, request: Request) -> dict[str, object]:
    session = get_session(settings, db_path, request)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user = get_user_by_id(db_path, session.user_id)
    identity = get_github_identity_for_user(db_path, session.user_id)
    if user is None or identity is None:
        raise HTTPException(status_code=403, detail="Authenticated GitHub identity is required.")
    return {"session": session, "user": user, "identity": identity}


def require_dashboard_access(
    settings: Settings,
    db_path: str,
    request: Request,
    *,
    allow_local_debug: bool = False,
    local_debug_context_factory: Callable[[], dict[str, object] | None] | None = None,
) -> dict[str, object]:
    access_context = current_workspace_context(
        settings,
        db_path,
        request,
        allow_local_debug=allow_local_debug,
        local_debug_context_factory=local_debug_context_factory,
    )
    if not access_context["resolution"].can_access_dashboard:
        raise HTTPException(status_code=403, detail="Dashboard access is not available for this workspace.")
    return access_context


def require_dashboard_read_access(
    settings: Settings,
    db_path: str,
    request: Request,
    *,
    allow_local_debug: bool = False,
    local_debug_context_factory: Callable[[], dict[str, object] | None] | None = None,
) -> dict[str, object]:
    return require_dashboard_access(
        settings,
        db_path,
        request,
        allow_local_debug=allow_local_debug,
        local_debug_context_factory=local_debug_context_factory,
    )


def require_repo_dashboard_read_access(
    settings: Settings,
    db_path: str,
    request: Request,
    repo_full: str,
    *,
    allow_local_debug: bool = False,
    local_debug_context_factory: Callable[[], dict[str, object] | None] | None = None,
) -> dict[str, object]:
    access_context = require_dashboard_read_access(
        settings,
        db_path,
        request,
        allow_local_debug=allow_local_debug,
        local_debug_context_factory=local_debug_context_factory,
    )
    workspace = access_context["workspace"]
    allocation = get_repo_allocation_for_workspace(db_path, workspace.id, repo_full)
    if allocation is not None and allocation.allocation_status in {"active", "onboarded"}:
        return {**access_context, "dashboard_repo_scope": "allocated", "dashboard_repo_allocation_status": allocation.allocation_status}
    connection = get_repo_connection_for_workspace(db_path, workspace.id, repo_full)
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if connection is not None and connection.status == "available" and onboarding is not None:
        return {**access_context, "dashboard_repo_scope": "connected_history", "dashboard_repo_allocation_status": None}
    raise HTTPException(status_code=404, detail="Repository is not visible in this workspace dashboard.")


def require_repo_dashboard_mutation_access(
    settings: Settings,
    db_path: str,
    request: Request,
    repo_full: str,
    *,
    allow_local_debug: bool = False,
    local_debug_context_factory: Callable[[], dict[str, object] | None] | None = None,
) -> dict[str, object]:
    access_context = require_dashboard_access(
        settings,
        db_path,
        request,
        allow_local_debug=allow_local_debug,
        local_debug_context_factory=local_debug_context_factory,
    )
    workspace = access_context["workspace"]
    allocation = get_repo_allocation_for_workspace(db_path, workspace.id, repo_full)
    if allocation is not None and allocation.allocation_status in {"active", "onboarded"}:
        return {**access_context, "dashboard_repo_scope": "allocated", "dashboard_repo_allocation_status": allocation.allocation_status}
    raise HTTPException(status_code=404, detail="Repository is not allocated to this workspace.")
