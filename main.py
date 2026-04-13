import asyncio
import io
import base64
import hashlib
import hmac
import json
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from github.GithubException import GithubException
from openai import OpenAI
from pydantic import BaseModel

from config import get_settings
from engine.relevance import needs_audit as engine_needs_audit
from services.access_state import WorkspaceAccessSnapshot, resolve_workspace_access_state
from services.audit_jobs import create_audit_job, init_db, update_job_pr_state
from services.audit_records import update_pull_request_audit_state
from services.audit_worker import AuditWorker, WorkerSettings
from services.baseline_approval_service import (
    approve_repo_baseline,
    approve_repo_baseline_artifact,
    build_repo_baseline_review_panel,
    reject_repo_baseline,
    rebaseline_repo_from_snapshot,
    reject_repo_baseline_artifact,
)
from services.branch_scan_jobs import create_branch_scan_job
from services.branch_scan_worker import BranchScanWorker, BranchScanWorkerSettings
from services.auth_service import (
    GithubOAuthToken,
    GithubUserProfile,
    build_github_oauth_authorize_url,
    exchange_code_for_access_token,
    fetch_github_user_profile,
    generate_csrf_secret,
    generate_oauth_state,
    generate_session_id,
)
from services.billing_service import (
    create_billing_portal_session,
    create_checkout_session,
    derive_billing_projection,
    parse_stripe_event,
    verify_stripe_signature,
)
from services.control_plane_frontend import (
    render_control_plane_admin_page,
    render_control_plane_billing_page,
    render_control_plane_install_page,
    render_control_plane_login_page,
    render_control_plane_marketing_page,
    render_control_plane_placeholder_page,
    render_control_plane_profile_page,
    render_control_plane_settings_page,
    render_control_plane_pricing_page,
    render_control_plane_repo_setup_page,
    render_repo_inventory_cards,
    render_repo_onboarded_summary_cards,
    render_repo_onboarding_metrics,
    render_control_plane_workspace_new_page,
)
from services.control_plane_records import (
    activate_billing_handoff_claim,
    allocate_repo_to_workspace,
    get_billing_customer_by_stripe_customer_id,
    count_workspace_repo_allocations,
    count_workspaces,
    create_billing_handoff_claim,
    create_user_session,
    create_workspace,
    get_billing_customer_for_workspace,
    get_billing_handoff_claim_by_token,
    get_github_installation_by_installation_id,
    get_repo_allocation_for_installation,
    get_repo_allocation_for_workspace,
    get_github_identity_for_user,
    get_repo_connection_for_workspace,
    get_user_by_id,
    get_user_session,
    get_workspace_by_id,
    get_workspace_entitlement,
    get_workspace_installation,
    get_workspace_membership,
    get_workspace_subscription,
    get_subscription_by_stripe_subscription_id,
    has_processed_webhook_event,
    list_admin_workspace_users,
    list_billing_handoff_claims,
    list_repo_allocations_for_workspace,
    list_repo_connections_for_workspace,
    list_unclaimed_installations,
    list_workspace_memberships_for_user,
    record_webhook_event,
    replace_repo_connections,
    revoke_user_session,
    update_repo_allocation_status,
    update_session_workspace,
    update_user_profile_preferences,
    update_workspace_pr_comments_setting,
    upsert_billing_customer,
    upsert_entitlement,
    upsert_github_identity,
    upsert_github_installation,
    upsert_subscription,
)
from services.dashboard_frontend import DASHBOARD_STATIC_DIR, render_dashboard_index_page, render_repo_dashboard_page
from services.dashboard_views import build_dashboard_overview_view, build_repo_artifact_storyline, build_repo_dashboard_view, list_repo_dashboard_index
from services.entitlements import derive_entitlement_payload, get_plan_definition
from services.export_jobs import create_export_job, get_export_job, list_export_jobs_for_requester, update_export_job_status
from services.compliance_export_service import ComplianceExportRequest as ComplianceExportServiceRequest, build_compliance_export
from services.github_integration import fetch_commit_pair_diff, fetch_file_content, fetch_pr_diff, generate_jwt, get_installation_token
from services.github_provisioning import get_live_github_install_url, sync_installation_repositories
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from services.onboarding_records import get_latest_repository_onboarding, promote_latest_source_to_onboarding_baseline
from services.persistence import get_persistence_status
from services.repo_journey import build_repo_journey, compare_repo_snapshots, get_repo_snapshot_detail, snapshot_to_public_payload
from services.secure_store import encrypt_text
from services.static_assets import FingerprintedStaticFiles

settings = get_settings()

GITHUB_APP_ID = settings.github_app_id
GITHUB_PRIVATE_KEY_PATH = settings.github_private_key_path
GITHUB_WEBHOOK_SECRET = settings.github_webhook_secret
OPENAI_API_KEY = settings.openai_api_key
FOUNDRY_API_KEY = settings.foundry_api_key
AZURE_OPENAI_ENDPOINT = settings.azure_openai_endpoint
AI_MODEL = settings.ai_model
AI_API_KEY = settings.ai_api_key
AUDIT_DB_PATH = settings.resolved_db_path
AUDIT_WORKER_ENABLED = settings.audit_worker_enabled and bool(
    settings.has_github_app_credentials and GITHUB_WEBHOOK_SECRET and AI_API_KEY
)
LLM_TIMEOUT_SECONDS = settings.llm_timeout_seconds
AUDIT_MAX_ATTEMPTS = settings.audit_max_attempts
AUDIT_MAX_RETRY_WINDOW_SECONDS = settings.audit_max_retry_window_seconds
AUDIT_WORKER_POLL_SECONDS = settings.audit_worker_poll_seconds
PR_DIFF_FETCH_ATTEMPTS = settings.pr_diff_fetch_attempts
PR_DIFF_FETCH_RETRY_SECONDS = settings.pr_diff_fetch_retry_seconds
CONTROL_PLANE_OAUTH_STATE_COOKIE = "promptdrift_oauth_state"
CONTROL_PLANE_OAUTH_CONTEXT_COOKIE = "promptdrift_oauth_context"
CONTROL_PLANE_PENDING_INSTALL_COOKIE = "promptdrift_pending_install"
SUPPORTED_ACTIVE_PLAN_STATUSES = {"active", "trialing", "canceled", "free_active"}

client = OpenAI(api_key=AI_API_KEY, base_url=AZURE_OPENAI_ENDPOINT or None) if AI_API_KEY else None
worker: AuditWorker | None = None
branch_scan_worker: BranchScanWorker | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker, branch_scan_worker
    init_db(AUDIT_DB_PATH)
    if AUDIT_WORKER_ENABLED:
        assert client is not None
        worker = AuditWorker(
            WorkerSettings(
                db_path=AUDIT_DB_PATH,
                github_app_id=GITHUB_APP_ID,
                github_private_key_path=GITHUB_PRIVATE_KEY_PATH,
                github_app_private_key=settings.resolved_github_private_key,
                llm_client=client,
                model=AI_MODEL,
                llm_timeout_seconds=LLM_TIMEOUT_SECONDS,
                max_attempts=AUDIT_MAX_ATTEMPTS,
                max_retry_window_seconds=AUDIT_MAX_RETRY_WINDOW_SECONDS,
                poll_interval_seconds=AUDIT_WORKER_POLL_SECONDS,
            )
        )
        worker.start()
    if settings.has_github_app_credentials and GITHUB_WEBHOOK_SECRET:
        branch_scan_worker = BranchScanWorker(
            BranchScanWorkerSettings(
                db_path=AUDIT_DB_PATH,
                github_app_id=GITHUB_APP_ID,
                github_private_key_path=GITHUB_PRIVATE_KEY_PATH,
                github_app_private_key=settings.resolved_github_private_key,
                max_attempts=AUDIT_MAX_ATTEMPTS,
                max_retry_window_seconds=AUDIT_MAX_RETRY_WINDOW_SECONDS,
                poll_interval_seconds=AUDIT_WORKER_POLL_SECONDS,
            )
        )
        branch_scan_worker.start()
    try:
        yield
    finally:
        if branch_scan_worker is not None:
            branch_scan_worker.stop()
            branch_scan_worker = None
        if worker is not None:
            worker.stop()
            worker = None


app = FastAPI(lifespan=lifespan)
app.mount("/static", FingerprintedStaticFiles(directory=str(DASHBOARD_STATIC_DIR)), name="static")


class RepositoryOnboardingRequest(BaseModel):
    installation_id: int
    commit_limit_per_artifact: int = 10
    plan_backfill: bool = True
    execute_backfill: bool = False


class RepositoryBackfillRequest(BaseModel):
    installation_id: int


class BaselineDecisionRequest(BaseModel):
    note: str | None = None


class RepoRebaselineRequest(BaseModel):
    snapshot_id: int
    rationale: str | None = None


class BillingHandoffActivationRequest(BaseModel):
    provider: str = "base44"
    external_purchase_id: str
    plan_code: str
    billing_status: str = "active"
    billing_email: str | None = None
    source: str | None = None
    next_payment_at: float | str | None = None


class ComplianceExportRequest(BaseModel):
    from_ts: float | None = None
    to_ts: float | None = None
    from_date: str | None = None
    to_date: str | None = None
    export_mode: str
    include_artifact_content: bool = False


def _control_plane_active() -> bool:
    try:
        return count_workspaces(AUDIT_DB_PATH) > 0
    except sqlite3.Error:
        return False


def _get_session(request: Request):
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        return None
    return get_user_session(AUDIT_DB_PATH, session_id)


def _build_access_context(session) -> dict[str, object]:
    if session is None:
        resolution = resolve_workspace_access_state(WorkspaceAccessSnapshot(is_authenticated=False))
        return {"session": None, "user": None, "identity": None, "workspace": None, "resolution": resolution}

    user = get_user_by_id(AUDIT_DB_PATH, session.user_id)
    identity = get_github_identity_for_user(AUDIT_DB_PATH, session.user_id)
    workspace = get_workspace_by_id(AUDIT_DB_PATH, session.workspace_id) if session.workspace_id else None
    membership = get_workspace_membership(AUDIT_DB_PATH, workspace.id, session.user_id) if workspace else None
    subscription = get_workspace_subscription(AUDIT_DB_PATH, workspace.id) if workspace else None
    entitlement = get_workspace_entitlement(AUDIT_DB_PATH, workspace.id) if workspace else None
    installation = get_workspace_installation(AUDIT_DB_PATH, workspace.id) if workspace else None
    allocated_repo_count, onboarded_repo_count = count_workspace_repo_allocations(AUDIT_DB_PATH, workspace.id) if workspace else (0, 0)

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


def _parse_optional_timestamp(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return float(candidate)
        except ValueError:
            try:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    return None


def _dashboard_redirect_for_request(request: Request):
    session = _get_session(request)
    if not _control_plane_active():
        return None, session
    if session is None:
        return RedirectResponse("/login", status_code=303), None
    access_context = _build_access_context(session)
    if not access_context["resolution"].can_access_dashboard:
        return RedirectResponse("/app", status_code=303), session
    return None, session


def _set_session_cookie(response: RedirectResponse, session_id: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        session_id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
    )


def _set_context_cookie(response: RedirectResponse, name: str, payload: dict[str, object], *, max_age: int = 1800) -> None:
    response.set_cookie(
        name,
        _encode_context_cookie(payload),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=max_age,
    )


def _normalize_source_hint(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", normalized):
        return None
    return normalized


def _normalize_plan_hint(value: str | None) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        return get_plan_definition(candidate).code
    except ValueError:
        return None


def _normalize_email(value: str | None) -> str | None:
    candidate = (value or "").strip().lower()
    return candidate or None


def _normalize_theme_preference(value: str | None) -> str | None:
    candidate = (value or "").strip().lower()
    if candidate in {"dark", "light"}:
        return candidate
    return None


def _workspace_pr_comments_allowed_by_plan(access_context: dict[str, object]) -> bool:
    entitlement = access_context.get("entitlement")
    if entitlement is not None:
        return bool(entitlement.pr_comments_enabled)
    subscription = access_context.get("subscription")
    subscription_status = (subscription.status if subscription else "").lower()
    return subscription_status in SUPPORTED_ACTIVE_PLAN_STATUSES


def _require_token_encryption_config() -> None:
    if not settings.has_encryption_key:
        raise HTTPException(status_code=503, detail="APP_ENCRYPTION_KEY must be configured before GitHub OAuth can store user tokens.")


def _encode_context_cookie(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def _decode_context_cookie(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        decoded = base64.urlsafe_b64decode(value.encode("utf-8")).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _flow_context_from_request(request: Request) -> dict[str, str]:
    cookie_payload = _decode_context_cookie(request.cookies.get(CONTROL_PLANE_OAUTH_CONTEXT_COOKIE))
    source = _normalize_source_hint(request.query_params.get("source")) or _normalize_source_hint(str(cookie_payload.get("source") or ""))
    plan = _normalize_plan_hint(request.query_params.get("plan")) or _normalize_plan_hint(str(cookie_payload.get("plan") or ""))
    claim_token = (request.query_params.get("claim") or str(cookie_payload.get("claim") or "")).strip()
    context: dict[str, str] = {}
    if source:
        context["source"] = source
    if plan:
        context["plan"] = plan
    if claim_token:
        context["claim"] = claim_token
    return context


def _pending_install_context_from_request(request: Request) -> dict[str, object]:
    payload = _decode_context_cookie(request.cookies.get(CONTROL_PLANE_PENDING_INSTALL_COOKIE))
    installation_id = payload.get("installation_id")
    workspace_id = payload.get("workspace_id")
    setup_action = payload.get("setup_action")
    context: dict[str, object] = {}
    if isinstance(installation_id, int) or (isinstance(installation_id, str) and str(installation_id).isdigit()):
        context["installation_id"] = int(installation_id)
    if isinstance(workspace_id, int) or (isinstance(workspace_id, str) and str(workspace_id).isdigit()):
        context["workspace_id"] = int(workspace_id)
    if isinstance(setup_action, str) and setup_action.strip():
        context["setup_action"] = setup_action.strip()
    return context


def _flow_query_string(flow_context: dict[str, str]) -> str:
    if not flow_context:
        return ""
    return urlencode(flow_context)


def _path_with_flow_context(base_path: str, flow_context: dict[str, str]) -> str:
    query = _flow_query_string(flow_context)
    if not query:
        return base_path
    separator = "&" if "?" in base_path else "?"
    return f"{base_path}{separator}{query}"


def _auth_start_url(flow_context: dict[str, str]) -> str:
    return _path_with_flow_context("/auth/github/start", flow_context)


def _workspace_new_url(flow_context: dict[str, str]) -> str:
    return _path_with_flow_context("/app/workspaces/new", flow_context)


def _billing_url(flow_context: dict[str, str]) -> str:
    return _path_with_flow_context("/app/billing", flow_context)


def _install_url(flow_context: dict[str, str]) -> str:
    return _path_with_flow_context("/app/setup/install", flow_context)


def _resume_destination_for_session(session, flow_context: dict[str, str]) -> str:
    if session.workspace_id is None:
        return _workspace_new_url(flow_context)
    access_context = _build_access_context(session)
    if flow_context.get("claim") and access_context.get("subscription") is None:
        return _path_with_flow_context("/app/billing/claim", flow_context)
    if flow_context.get("plan") and access_context.get("subscription") is None:
        return _billing_url(flow_context)
    if access_context.get("installation") is None and access_context["resolution"].state == "awaiting_github_install":
        return _install_url(flow_context)
    return _path_with_flow_context("/app", flow_context)


def _coerce_workspace_hint(value: str | None) -> int | None:
    if not value or not str(value).isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _switch_session_workspace_if_allowed(session, workspace_id: int | None):
    if session is None or workspace_id is None or session.workspace_id == workspace_id:
        return session
    membership = get_workspace_membership(AUDIT_DB_PATH, workspace_id, session.user_id)
    if membership is None:
        return session
    update_session_workspace(AUDIT_DB_PATH, session.session_id, workspace_id)
    return get_user_session(AUDIT_DB_PATH, session.session_id)


def _link_installation_to_workspace(
    *,
    workspace_id: int | None,
    installation_id: int,
    account_login: str = "",
    account_type: str = "Organization",
    repo_fulls: str = "",
) -> None:
    repositories: list[dict[str, object]] = []
    account_id = account_login or str(installation_id)
    target_type = account_type or "Organization"

    if repo_fulls.strip():
        for repo_full in [value.strip() for value in repo_fulls.replace("\n", ",").split(",") if value.strip()]:
            repositories.append(
                {
                    "repo_github_id": repo_full,
                    "repo_full": repo_full,
                    "default_branch": "main",
                    "is_private": True,
                    "status": "available",
                }
            )
    elif settings.has_github_app_credentials:
        installation_payload, repositories = sync_installation_repositories(
            app_id=settings.github_app_id,
            private_key_path=settings.github_private_key_path,
            private_key=settings.resolved_github_private_key,
            installation_id=installation_id,
        )
        account = installation_payload.get("account") if isinstance(installation_payload, dict) else {}
        if isinstance(account, dict):
            account_login = str(account.get("login") or account_login)
            account_id = str(account.get("id") or account_id)
            account_type = str(account.get("type") or account_type)
        target_type = str(installation_payload.get("target_type") or target_type)

    upsert_github_installation(
        AUDIT_DB_PATH,
        workspace_id=workspace_id,
        installation_id=installation_id,
        account_id=account_id,
        account_login=account_login or str(installation_id),
        account_type=account_type or "Organization",
        target_type=target_type,
    )
    replace_repo_connections(
        AUDIT_DB_PATH,
        workspace_id=workspace_id,
        installation_id=installation_id,
        repositories=repositories,
    )


def _redirect_with_pending_install(request: Request, *, installation_id: int, workspace_id: int | None, setup_action: str | None) -> RedirectResponse:
    response = RedirectResponse(_auth_start_url(_flow_context_from_request(request)), status_code=303)
    _set_context_cookie(
        response,
        CONTROL_PLANE_PENDING_INSTALL_COOKIE,
        {
            "installation_id": installation_id,
            "workspace_id": workspace_id,
            "setup_action": setup_action or "install",
        },
        max_age=1800,
    )
    return response


def _workspace_slug_candidates(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "workspace"
    return [base, f"{base}-{int(time.time())}"]


def _github_oauth_callback_url(request: Request) -> str:
    if settings.github_oauth_callback_url:
        return settings.github_oauth_callback_url
    return str(request.url_for("github_auth_callback"))


def _current_workspace_context(request: Request) -> dict[str, object]:
    session = _get_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    access_context = _build_access_context(session)
    if access_context["workspace"] is None:
        raise HTTPException(status_code=400, detail="Workspace context is required.")
    return access_context


def _current_authenticated_identity_context(request: Request) -> dict[str, object]:
    session = _get_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user = get_user_by_id(AUDIT_DB_PATH, session.user_id)
    identity = get_github_identity_for_user(AUDIT_DB_PATH, session.user_id)
    if user is None or identity is None:
        raise HTTPException(status_code=403, detail="Authenticated GitHub identity is required.")
    return {"session": session, "user": user, "identity": identity}


def _require_dashboard_access(request: Request) -> dict[str, object]:
    if not _control_plane_active():
        return {}
    access_context = _current_workspace_context(request)
    if not access_context["resolution"].can_access_dashboard:
        raise HTTPException(status_code=403, detail="Dashboard access is not available for this workspace.")
    return access_context


def _current_theme_preference(request: Request) -> str:
    if not _control_plane_active():
        return "dark"
    session = _get_session(request)
    if session is None:
        return "dark"
    user = get_user_by_id(AUDIT_DB_PATH, session.user_id)
    return user.theme_preference if user else "dark"


def _workspace_repo_rows(workspace_id: int) -> list[dict[str, object]]:
    connections = list_repo_connections_for_workspace(AUDIT_DB_PATH, workspace_id)
    allocations = {
        allocation.repo_full: allocation
        for allocation in list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace_id)
    }
    rows: list[dict[str, object]] = []
    seen_repo_fulls: set[str] = set()
    for connection in connections:
        allocation = allocations.get(connection.repo_full)
        status = "Available"
        if allocation is not None:
            status = "Onboarded" if allocation.allocation_status == "onboarded" else "Allocated"
        rows.append(
            {
                "repo_full": connection.repo_full,
                "status": status,
                "branch": connection.default_branch or "unknown",
                "visibility": "Private" if connection.is_private else "Public",
                "href": f"/dashboard/{quote(connection.repo_full, safe='')}",
            }
        )
        seen_repo_fulls.add(connection.repo_full)

    for repo_full, allocation in allocations.items():
        if repo_full in seen_repo_fulls:
            continue
        rows.append(
            {
                "repo_full": repo_full,
                "status": "Onboarded" if allocation.allocation_status == "onboarded" else "Allocated",
                "branch": "unknown",
                "visibility": "Unknown",
                "href": f"/dashboard/{quote(repo_full, safe='')}",
            }
        )

    return sorted(rows, key=lambda item: str(item["repo_full"]).lower())


def _workspace_member_rows(workspace_id: int) -> list[dict[str, object]]:
    rows = [row for row in list_admin_workspace_users(AUDIT_DB_PATH) if row.workspace_id == workspace_id]
    return [
        {
            "display_name": row.user_display_name,
            "github_login": row.github_login,
            "role": row.membership_role,
            "state": "Accepted",
        }
        for row in rows
    ]


def _require_repo_dashboard_read_access(request: Request, repo_full: str) -> dict[str, object]:
    access_context = _require_dashboard_access(request)
    if not access_context:
        return access_context
    workspace = access_context["workspace"]
    allocation = get_repo_allocation_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    if allocation is not None and allocation.allocation_status in {"active", "onboarded"}:
        return {**access_context, "dashboard_repo_scope": "allocated", "dashboard_repo_allocation_status": allocation.allocation_status}
    connection = get_repo_connection_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    onboarding = get_latest_repository_onboarding(AUDIT_DB_PATH, repo_full)
    if connection is not None and connection.status == "available" and onboarding is not None:
        return {**access_context, "dashboard_repo_scope": "connected_history", "dashboard_repo_allocation_status": None}
    raise HTTPException(status_code=404, detail="Repository is not visible in this workspace dashboard.")


def _require_repo_dashboard_mutation_access(request: Request, repo_full: str) -> dict[str, object]:
    access_context = _require_dashboard_access(request)
    if not access_context:
        return access_context
    workspace = access_context["workspace"]
    allocation = get_repo_allocation_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    if allocation is not None and allocation.allocation_status in {"active", "onboarded"}:
        return {**access_context, "dashboard_repo_scope": "allocated", "dashboard_repo_allocation_status": allocation.allocation_status}
    raise HTTPException(status_code=404, detail="Repository is not allocated to this workspace.")


def _dashboard_actor_login(request: Request) -> str | None:
    if not _control_plane_active():
        return None
    identity_context = _current_authenticated_identity_context(request)
    identity = identity_context.get("identity")
    return identity.github_login if identity is not None else None


def _require_export_job_owner_access(request: Request, job) -> dict[str, object]:
    access_context = _require_repo_dashboard_read_access(request, job.repo_full)
    workspace = access_context.get("workspace")
    session = access_context.get("session")
    if workspace is None or session is None:
        raise HTTPException(status_code=403, detail="Workspace context is required for export access.")
    if not job.workspace_id or not job.requested_by_user_id:
        raise HTTPException(status_code=404, detail="Export job ownership metadata is not available.")
    if workspace.id != job.workspace_id or session.user_id != job.requested_by_user_id:
        raise HTTPException(status_code=404, detail="Export job not found")
    return access_context


def _export_download_url(job) -> str | None:
    if not job.download_token:
        return None
    return f"/api/export/{job.id}/download?token={quote(job.download_token)}"


def _export_job_payload(job) -> dict[str, object]:
    payload = asdict(job)
    payload["download_url"] = _export_download_url(job) if job.status == "completed" else None
    return payload


def _dashboard_repo_visibility(access_context: dict[str, object]) -> dict[str, object]:
    if not access_context:
        return {"allowed_repo_fulls": None, "repo_scope_by_full": None, "allocation_status_by_full": None}
    workspace = access_context.get("workspace")
    if workspace is None:
        return {"allowed_repo_fulls": None, "repo_scope_by_full": None, "allocation_status_by_full": None}
    allowed_repo_fulls: set[str] = set()
    repo_scope_by_full: dict[str, str] = {}
    allocation_status_by_full: dict[str, str] = {}
    for connection in list_repo_connections_for_workspace(AUDIT_DB_PATH, workspace.id):
        if connection.status != "available":
            continue
        allowed_repo_fulls.add(connection.repo_full)
        repo_scope_by_full[connection.repo_full] = "connected_history"
    for allocation in list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace.id):
        if allocation.allocation_status not in {"active", "onboarded"}:
            continue
        allowed_repo_fulls.add(allocation.repo_full)
        repo_scope_by_full[allocation.repo_full] = "allocated"
        allocation_status_by_full[allocation.repo_full] = allocation.allocation_status
    return {
        "allowed_repo_fulls": allowed_repo_fulls,
        "repo_scope_by_full": repo_scope_by_full,
        "allocation_status_by_full": allocation_status_by_full,
    }


def _record_server_timing_metric(metrics: list[tuple[str, float]], metric_name: str, started_at: float) -> None:
    metrics.append((metric_name, (time.perf_counter() - started_at) * 1000.0))


def _attach_server_timing(response, metrics: list[tuple[str, float]]):
    if metrics:
        response.headers["Server-Timing"] = ", ".join(
            f"{metric_name};dur={max(duration_ms, 0.0):.2f}" for metric_name, duration_ms in metrics
        )
    return response


def _verify_billing_handoff_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not settings.billing_handoff_secret or not signature_header:
        return False
    expected = hmac.new(settings.billing_handoff_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.strip())


def _is_admin_identity(user, identity) -> bool:
    if not settings.has_admin_access_config:
        return False
    if identity.github_user_id in settings.admin_github_user_id_set:
        return True
    if identity.github_login.lower() in settings.admin_github_login_set:
        return True
    return bool(user.primary_email and user.primary_email.lower() in settings.admin_email_set)


def _require_admin_access(request: Request) -> dict[str, object]:
    context = _current_authenticated_identity_context(request)
    if not _is_admin_identity(context["user"], context["identity"]):
        raise HTTPException(status_code=403, detail="Admin access is not enabled for this GitHub identity.")
    return context


def _has_profile_access(access_context: dict[str, object]) -> bool:
    entitlement = access_context.get("entitlement")
    if entitlement is not None and entitlement.dashboard_enabled:
        return True
    return bool(access_context["resolution"].can_access_dashboard)


def _has_settings_access(access_context: dict[str, object]) -> bool:
    workspace = access_context.get("workspace")
    membership = access_context.get("membership")
    return workspace is not None and membership is not None and membership.invitation_state == "accepted"


def _require_workspace_role(access_context: dict[str, object], *allowed_roles: str) -> None:
    membership = access_context.get("membership")
    if membership is None or membership.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="This action requires a workspace owner or admin role.")


def _validate_csrf_secret(csrf_secret: str | None, submitted_token: str | None) -> None:
    if not csrf_secret or not submitted_token or not hmac.compare_digest(csrf_secret, submitted_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


def _public_session_payload(session) -> dict[str, object] | None:
    if session is None:
        return None
    return {
        "user_id": session.user_id,
        "workspace_id": session.workspace_id,
        "expires_at": session.expires_at,
        "revoked_at": session.revoked_at,
        "last_seen_at": session.last_seen_at,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


@app.get("/", response_class=HTMLResponse)
async def marketing_page():
    return HTMLResponse(render_control_plane_marketing_page())


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    return HTMLResponse(render_control_plane_pricing_page())


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    flow_context = _flow_context_from_request(request)
    selected_plan = flow_context.get("plan")
    source = flow_context.get("source")
    context_note = None
    if selected_plan and source:
        context_note = f"Resuming the {selected_plan.title()} plan handoff from {source}."
    elif selected_plan:
        context_note = f"Resuming the {selected_plan.title()} plan handoff."
    elif source:
        context_note = f"Resuming the handoff from {source}."
    login_error = (request.query_params.get("login_error") or "").strip().lower()
    if login_error == "oauth_not_configured":
        context_note = "GitHub sign-in is not configured for this deployment yet. Set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET to enable login."
    elif login_error == "encryption_not_configured":
        context_note = "GitHub sign-in is blocked because APP_ENCRYPTION_KEY is not configured. Add it before storing OAuth tokens."
    auth_available = settings.has_github_oauth_credentials and settings.has_encryption_key
    if login_error in {"oauth_not_configured", "encryption_not_configured"}:
        auth_available = False
    return HTMLResponse(
        render_control_plane_login_page(
            auth_start_url=_auth_start_url(flow_context),
            context_note=context_note,
            auth_available=auth_available,
        )
    )


@app.get("/auth/github/start")
async def github_auth_start(request: Request):
    existing_session = _get_session(request)
    flow_context = _flow_context_from_request(request)
    if existing_session is not None:
        return RedirectResponse(_resume_destination_for_session(existing_session, flow_context), status_code=303)
    if not settings.has_github_oauth_credentials:
        return RedirectResponse(_path_with_flow_context("/login?login_error=oauth_not_configured", flow_context), status_code=303)
    if not settings.has_encryption_key:
        return RedirectResponse(_path_with_flow_context("/login?login_error=encryption_not_configured", flow_context), status_code=303)
    state = generate_oauth_state()
    authorize_url = build_github_oauth_authorize_url(
        settings.github_oauth_client_id,
        _github_oauth_callback_url(request),
        state,
    )
    response = RedirectResponse(authorize_url, status_code=302)
    if flow_context:
        _set_context_cookie(response, CONTROL_PLANE_OAUTH_CONTEXT_COOKIE, flow_context, max_age=1800)
    response.set_cookie(
        CONTROL_PLANE_OAUTH_STATE_COOKIE,
        state,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=600,
    )
    return response


@app.get("/auth/github/callback")
async def github_auth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    flow_context = _flow_context_from_request(request)
    pending_install = _pending_install_context_from_request(request)
    if error:
        destination = _path_with_flow_context("/login", flow_context)
        if error_description:
            destination = _path_with_flow_context(destination, {"oauth_error": error})
        response = RedirectResponse(destination, status_code=303)
        response.delete_cookie(CONTROL_PLANE_OAUTH_STATE_COOKIE)
        return response
    expected_state = request.cookies.get(CONTROL_PLANE_OAUTH_STATE_COOKIE)
    if not expected_state or not state or state != expected_state:
        raise HTTPException(status_code=400, detail="OAuth state validation failed.")
    if not code:
        raise HTTPException(status_code=400, detail="GitHub OAuth callback is missing the code parameter.")
    _require_token_encryption_config()

    token: GithubOAuthToken = exchange_code_for_access_token(
        settings.github_oauth_client_id,
        settings.github_oauth_client_secret,
        code,
        _github_oauth_callback_url(request),
    )
    profile: GithubUserProfile = fetch_github_user_profile(token.access_token)
    encrypted_token = encrypt_text(token.access_token, settings.app_encryption_key)
    user, _identity = upsert_github_identity(
        AUDIT_DB_PATH,
        github_user_id=profile.github_user_id,
        github_login=profile.login,
        display_name=profile.display_name,
        primary_email=profile.email,
        avatar_url=profile.avatar_url,
        granted_scopes=token.granted_scopes,
        access_token_encrypted=encrypted_token,
    )
    memberships = list_workspace_memberships_for_user(AUDIT_DB_PATH, user.id)
    workspace_id = memberships[0].workspace_id if memberships else None
    session = create_user_session(
        AUDIT_DB_PATH,
        session_id=generate_session_id(),
        user_id=user.id,
        workspace_id=workspace_id,
        csrf_secret=generate_csrf_secret(),
        expires_at=time.time() + settings.session_ttl_seconds,
    )
    session = _switch_session_workspace_if_allowed(session, _coerce_workspace_hint(str(pending_install.get("workspace_id") or "")))
    destination = _resume_destination_for_session(session, flow_context)
    if pending_install and session.workspace_id is not None:
        try:
            access_context = _build_access_context(session)
            _require_workspace_role(access_context, "owner", "admin")
            _link_installation_to_workspace(
                workspace_id=session.workspace_id,
                installation_id=int(pending_install["installation_id"]),
            )
            destination = _path_with_flow_context(
                f"/app/repos?installation_linked=1&setup_action={pending_install.get('setup_action') or 'install'}",
                flow_context,
            )
        except Exception:
            destination = _path_with_flow_context("/app/setup/install?install_error=callback_link_failed", flow_context)
    response = RedirectResponse(destination, status_code=303)
    _set_session_cookie(response, session.session_id)
    response.delete_cookie(CONTROL_PLANE_OAUTH_STATE_COOKIE)
    response.delete_cookie(CONTROL_PLANE_PENDING_INSTALL_COOKIE)
    return response


@app.get("/logout")
@app.post("/logout")
async def logout(request: Request):
    session = _get_session(request)
    if session is not None:
        revoke_user_session(AUDIT_DB_PATH, session.session_id)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(settings.session_cookie_name)
    return response


@app.get("/app", response_class=HTMLResponse)
async def control_plane_app_page_route(request: Request, state: str | None = None):
    session = _get_session(request)
    if session is None:
        return RedirectResponse("/login", status_code=303)
    access_context = _build_access_context(session)
    resolution = access_context["resolution"]
    if _has_profile_access(access_context):
        return RedirectResponse("/app/profile", status_code=303)

    destination_by_state = {
        "authenticated_no_workspace": "/app/workspaces/new",
        "workspace_no_subscription": "/app/billing",
        "billing_pending_confirmation": "/app/billing",
        "payment_failed": "/app/billing",
        "awaiting_github_install": "/app/setup/install",
        "awaiting_repo_onboarding": "/app/repos",
        "active_comments_only": "/app/repos",
        "canceled_active_until_period_end": "/app/billing",
        "expired_read_only": "/app/billing",
        "forbidden": "/dashboard",
    }
    return RedirectResponse(destination_by_state.get(resolution.state, "/login"), status_code=303)


@app.get("/app/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    access_context = _current_workspace_context(request)
    if not _has_profile_access(access_context):
        raise HTTPException(status_code=403, detail="Profile page is available only for Starter tier and above.")

    user = access_context["user"]
    identity = access_context["identity"]
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    membership = access_context["membership"]
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    return HTMLResponse(
        render_control_plane_profile_page(
            display_name=user.display_name if user else "",
            theme_preference=user.theme_preference if user else "dark",
            github_login=identity.github_login if identity else "Unavailable",
            github_user_id=identity.github_user_id if identity else "Unavailable",
            primary_email=user.primary_email if user else None,
            workspace_name=workspace.display_name,
            workspace_role=membership.role if membership else "viewer",
            plan_label=get_plan_definition(plan_code).label,
            next_payment_at=subscription.next_payment_at if subscription else None,
            status_note="Profile updated." if request.query_params.get("updated") else None,
            resolution=access_context["resolution"],
            admin_url="/app/admin" if identity and user and _is_admin_identity(user, identity) else None,
            csrf_token=access_context["session"].csrf_secret,
        )
    )


@app.post("/app/profile")
async def profile_update(request: Request, display_name: str = Form(...), theme_preference: str | None = Form(None), csrf_token: str | None = Form(None)):
    access_context = _current_workspace_context(request)
    if not _has_profile_access(access_context):
        raise HTTPException(status_code=403, detail="Profile page is available only for Starter tier and above.")
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    normalized_name = display_name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Display name cannot be empty.")
    if len(normalized_name) > 120:
        raise HTTPException(status_code=400, detail="Display name must be 120 characters or fewer.")
    normalized_theme = _normalize_theme_preference(theme_preference)
    if theme_preference is not None and normalized_theme is None:
        raise HTTPException(status_code=400, detail="Theme preference must be dark or light.")
    current_user = access_context["user"]
    update_user_profile_preferences(
        AUDIT_DB_PATH,
        access_context["session"].user_id,
        display_name=normalized_name,
        theme_preference=normalized_theme or current_user.theme_preference,
    )
    return RedirectResponse("/app/profile?updated=1", status_code=303)


@app.get("/app/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")

    user = access_context["user"]
    identity = access_context["identity"]
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    membership = access_context["membership"]
    installation = access_context["installation"]
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    return HTMLResponse(
        render_control_plane_settings_page(
            workspace_name=workspace.display_name,
            plan_label=get_plan_definition(plan_code).label,
            theme_preference=user.theme_preference if user else "dark",
            status_note="Settings updated." if request.query_params.get("updated") else None,
            resolution=access_context["resolution"],
            admin_url="/app/admin" if identity and user and _is_admin_identity(user, identity) else None,
            csrf_token=access_context["session"].csrf_secret,
            pr_comments_allowed_by_plan=_workspace_pr_comments_allowed_by_plan(access_context),
            pr_comments_setting_enabled=bool(workspace.pr_comments_setting_enabled),
            can_manage=bool(membership and membership.role in {"owner", "admin"}),
            workspace_role=membership.role if membership else "viewer",
            workspace_members=_workspace_member_rows(workspace.id),
            repo_rows=_workspace_repo_rows(workspace.id),
            next_payment_at=subscription.next_payment_at if subscription else None,
            subscription_status=subscription.status if subscription else None,
            setup_state=workspace.setup_state,
            installation_account_login=installation.account_login if installation else None,
            repo_limit=entitlement.repo_limit if entitlement else None,
            seat_limit=entitlement.seat_limit if entitlement else None,
        )
    )


@app.post("/app/settings")
async def settings_update(request: Request, pr_comments_setting: str = Form(...), csrf_token: str | None = Form(None)):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")

    normalized_setting = (pr_comments_setting or "").strip().lower()
    if normalized_setting not in {"on", "off"}:
        raise HTTPException(status_code=400, detail="PR comments setting must be on or off.")

    update_workspace_pr_comments_setting(
        AUDIT_DB_PATH,
        access_context["workspace"].id,
        enabled=normalized_setting == "on",
    )
    return RedirectResponse("/app/settings?updated=1", status_code=303)


@app.get("/app/policies", response_class=HTMLResponse)
async def policies_page(request: Request):
    access_context = _current_workspace_context(request)
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    user = access_context["user"]
    identity = access_context["identity"]
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    return HTMLResponse(
        render_control_plane_placeholder_page(
            page_title="Policies",
            page_kicker="Workspace policy library",
            page_copy="We are working on this page now. It will become the home for workspace guardrails, policy packs, and audit rules.",
            workspace_name=workspace.display_name,
            plan_label=get_plan_definition(plan_code).label,
            theme_preference=user.theme_preference if user else "dark",
            admin_url="/app/admin" if identity and user and _is_admin_identity(user, identity) else None,
            active_nav="policies",
        )
    )


@app.get("/app/help", response_class=HTMLResponse)
async def help_page(request: Request):
    access_context = _current_workspace_context(request)
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    user = access_context["user"]
    identity = access_context["identity"]
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    return HTMLResponse(
        render_control_plane_placeholder_page(
            page_title="Help",
            page_kicker="Operator assistance",
            page_copy="We are working on this page now. It will collect guided setup, troubleshooting, and operator playbooks for each workspace.",
            workspace_name=workspace.display_name,
            plan_label=get_plan_definition(plan_code).label,
            theme_preference=user.theme_preference if user else "dark",
            admin_url="/app/admin" if identity and user and _is_admin_identity(user, identity) else None,
            active_nav="help",
        )
    )


@app.get("/app/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    admin_context = _require_admin_access(request)
    return HTMLResponse(
        render_control_plane_admin_page(
            actor_github_login=admin_context["identity"].github_login,
            admin_rows=[asdict(row) for row in list_admin_workspace_users(AUDIT_DB_PATH)],
            unclaimed_installations=[asdict(row) for row in list_unclaimed_installations(AUDIT_DB_PATH)],
            billing_claims=[asdict(row) for row in list_billing_handoff_claims(AUDIT_DB_PATH)],
        )
    )


@app.get("/app/workspaces/new", response_class=HTMLResponse)
async def workspace_new_page(request: Request):
    session = _get_session(request)
    if session is None:
        return RedirectResponse("/login", status_code=303)
    flow_context = _flow_context_from_request(request)
    selected_plan = flow_context.get("plan")
    source = flow_context.get("source")
    source_label = source.title() if source else None
    selected_plan_label = get_plan_definition(selected_plan).label if selected_plan else None
    return HTMLResponse(
        render_control_plane_workspace_new_page(
            selected_plan_label=selected_plan_label,
            source_label=source_label,
            csrf_token=session.csrf_secret,
        )
    )


@app.post("/app/workspaces/bootstrap")
async def workspace_bootstrap(request: Request, name: str | None = Form(default=None), csrf_token: str | None = Form(default=None)):
    session = _get_session(request)
    if session is None:
        return RedirectResponse("/login", status_code=303)
    _validate_csrf_secret(session.csrf_secret, csrf_token)
    flow_context = _flow_context_from_request(request)
    pending_install = _pending_install_context_from_request(request)
    workspace_name = (name or request.query_params.get("name") or "DriftGuard Workspace").strip()
    for slug in _workspace_slug_candidates(workspace_name):
        try:
            workspace = create_workspace(
                AUDIT_DB_PATH,
                slug=slug,
                display_name=workspace_name,
                billing_owner_user_id=session.user_id,
            )
            break
        except sqlite3.IntegrityError:
            continue
    else:
        raise HTTPException(status_code=409, detail="Unable to create a unique workspace slug.")

    update_session_workspace(AUDIT_DB_PATH, session.session_id, workspace.id)
    if flow_context.get("claim"):
        try:
            activate_billing_handoff_claim(
                AUDIT_DB_PATH,
                claim_token=flow_context["claim"],
                workspace_id=workspace.id,
                user_id=session.user_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if pending_install and pending_install.get("workspace_id") in (None, workspace.id):
        try:
            _link_installation_to_workspace(
                workspace_id=workspace.id,
                installation_id=int(pending_install["installation_id"]),
            )
            response = RedirectResponse(_path_with_flow_context("/app/repos?installation_linked=1", flow_context), status_code=303)
            response.delete_cookie(CONTROL_PLANE_PENDING_INSTALL_COOKIE)
            return response
        except Exception:
            return RedirectResponse(_path_with_flow_context("/app/setup/install?install_error=callback_link_failed", flow_context), status_code=303)
    return RedirectResponse(_resume_destination_for_session(get_user_session(AUDIT_DB_PATH, session.session_id), flow_context), status_code=303)


@app.get("/app/billing", response_class=HTMLResponse)
async def billing_page(request: Request):
    access_context = _current_workspace_context(request)
    flow_context = _flow_context_from_request(request)
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    customer = get_billing_customer_for_workspace(AUDIT_DB_PATH, workspace.id)
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    current_plan_label = get_plan_definition(plan_code).label if plan_code else "No plan"
    selected_plan_code = _normalize_plan_hint(request.query_params.get("plan")) or plan_code
    portal_url = "/app/billing/portal" if customer else None
    checkout_status_note = None
    if request.query_params.get("checkout_session_id"):
        checkout_status_note = "Checkout returned to DriftGuard. Access remains pending until Stripe webhook confirmation arrives."
    elif request.query_params.get("claim_activated"):
        checkout_status_note = "Billing activation was accepted. GitHub installation is the next required step."
    elif request.query_params.get("free_activated"):
        checkout_status_note = "Free tier activated. Link the GitHub App and allocate one repository to start PR comments."
    elif request.query_params.get("external_checkout_required"):
        checkout_status_note = "Paid plan checkout is handled by the external billing provider before DriftGuard grants access."
    elif request.query_params.get("canceled"):
        checkout_status_note = "Checkout was canceled before payment confirmation."
    return HTMLResponse(
        render_control_plane_billing_page(
            workspace_name=workspace.display_name,
            current_plan_label=current_plan_label,
            subscription_status=subscription.status if subscription else "not_started",
            selected_plan_code=selected_plan_code,
            checkout_status_note=checkout_status_note,
            flow_context=flow_context,
            portal_url=portal_url,
            csrf_token=access_context["session"].csrf_secret,
        )
    )


@app.post("/app/billing/checkout")
async def billing_checkout(request: Request, plan: str | None = Form(default=None), csrf_token: str | None = Form(default=None)):
    access_context = _current_workspace_context(request)
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")
    normalized_plan = _normalize_plan_hint(plan or request.query_params.get("plan"))
    if normalized_plan is None:
        raise HTTPException(status_code=400, detail="Unknown plan code.")
    plan_definition = get_plan_definition(normalized_plan)
    workspace = access_context["workspace"]
    flow_context = _flow_context_from_request(request)
    if not plan_definition.requires_billing:
        synthetic_subscription_id = f"local:free:{workspace.id}:{normalized_plan}"
        upsert_subscription(
            AUDIT_DB_PATH,
            workspace_id=workspace.id,
            stripe_subscription_id=synthetic_subscription_id,
            stripe_price_id=f"local:{normalized_plan}",
            plan_code=normalized_plan,
            status="free_active",
            cancel_at_period_end=False,
            current_period_start_at=time.time(),
            current_period_end_at=None,
            next_payment_at=None,
            trial_ends_at=None,
            last_webhook_event_id=None,
        )
        upsert_entitlement(
            AUDIT_DB_PATH,
            workspace_id=workspace.id,
            payload=derive_entitlement_payload(normalized_plan, "free_active"),
        )
        return RedirectResponse(_path_with_flow_context("/app/setup/install?free_activated=1", flow_context), status_code=303)

    if settings.base44_checkout_url:
        checkout_url = f"{settings.base44_checkout_url}?{urlencode({
            'plan': normalized_plan,
            'workspace_id': workspace.id,
            'workspace_slug': workspace.slug,
            'workspace_name': workspace.display_name,
            'billing_email': (access_context['user'].primary_email if access_context['user'] else '') or '',
            'source': flow_context.get('source') or 'driftguard',
            'return_url': f'{settings.app_base_url}/claim',
        })}"
        return RedirectResponse(checkout_url, status_code=303)

    existing_customer = get_billing_customer_for_workspace(AUDIT_DB_PATH, workspace.id)
    checkout = create_checkout_session(
        settings=settings,
        workspace_id=workspace.id,
        workspace_slug=workspace.slug,
        plan_code=normalized_plan,
        stripe_customer_id=existing_customer.stripe_customer_id if existing_customer else None,
    )
    upsert_billing_customer(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_customer_id=checkout.stripe_customer_id,
        billing_email=(access_context["user"].primary_email if access_context["user"] else None),
    )
    upsert_subscription(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id=checkout.stripe_subscription_id or checkout.session_id,
        stripe_price_id=checkout.stripe_price_id,
        plan_code=checkout.plan_code,
        status="incomplete",
        cancel_at_period_end=False,
        current_period_start_at=None,
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    return RedirectResponse(checkout.checkout_url, status_code=303)


@app.get("/claim")
@app.get("/claim/{claim_token}")
async def claim_entry(request: Request, claim_token: str | None = None):
    token = (claim_token or request.query_params.get("claim") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Claim token is required.")
    claim = get_billing_handoff_claim_by_token(AUDIT_DB_PATH, token)
    if claim is None:
        raise HTTPException(status_code=404, detail="Billing handoff claim was not found.")
    if claim.expires_at < time.time():
        raise HTTPException(status_code=410, detail="Billing handoff claim has expired.")

    flow_context = {
        "claim": claim.claim_token,
        "plan": claim.plan_code,
        "source": _normalize_source_hint(claim.source) or claim.provider,
    }
    session = _get_session(request)
    if session is None:
        destination = _auth_start_url(flow_context)
    elif session.workspace_id is None:
        destination = _workspace_new_url(flow_context)
    else:
        destination = _path_with_flow_context("/app/billing/claim", flow_context)
    response = RedirectResponse(destination, status_code=303)
    _set_context_cookie(response, CONTROL_PLANE_OAUTH_CONTEXT_COOKIE, flow_context, max_age=1800)
    return response


@app.get("/app/billing/claim")
async def billing_claim(request: Request):
    access_context = _current_workspace_context(request)
    _require_workspace_role(access_context, "owner", "admin")
    flow_context = _flow_context_from_request(request)
    claim_token = flow_context.get("claim")
    if not claim_token:
        raise HTTPException(status_code=400, detail="Claim token is required.")

    try:
        activate_billing_handoff_claim(
            AUDIT_DB_PATH,
            claim_token=claim_token,
            workspace_id=access_context["workspace"].id,
            user_id=access_context["session"].user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    next_flow_context = {key: value for key, value in flow_context.items() if key != "claim"}
    return RedirectResponse(_path_with_flow_context("/app/setup/install?claim_activated=1", next_flow_context), status_code=303)


@app.get("/app/billing/portal")
async def billing_portal(request: Request):
    access_context = _current_workspace_context(request)
    _require_workspace_role(access_context, "owner", "admin")
    workspace = access_context["workspace"]
    customer = get_billing_customer_for_workspace(AUDIT_DB_PATH, workspace.id)
    if customer is None:
        return RedirectResponse("/app/billing", status_code=303)
    portal_url = create_billing_portal_session(
        settings=settings,
        stripe_customer_id=customer.stripe_customer_id,
        return_url=f"{settings.app_base_url}/app/billing",
    )
    return RedirectResponse(portal_url, status_code=303)


@app.get("/app/setup/install", response_class=HTMLResponse)
async def install_page(request: Request):
    access_context = _current_workspace_context(request)
    flow_context = _flow_context_from_request(request)
    workspace = access_context["workspace"]
    installation = access_context["installation"]
    install_url = None
    if settings.has_github_app_credentials:
        try:
            install_url = get_live_github_install_url(
                settings.github_app_id,
                settings.github_private_key_path,
                settings.resolved_github_private_key,
                state=str(workspace.id),
            )
        except Exception:
            install_url = None
    installation_summary = (
        f"Connected installation {installation.account_login} ({installation.account_type})." if installation else "No GitHub App installation is linked yet."
    )
    install_hint = "Billing is active. The next gate is granting GitHub App installation authority."
    if request.query_params.get("installation_linked"):
        install_hint = "GitHub installation linked successfully. Review the synced repositories below."
    elif request.query_params.get("install_error"):
        install_hint = "GitHub installation completed, but DriftGuard could not finish linking it automatically. Use the manual fallback form below."
    return HTMLResponse(
        render_control_plane_install_page(
            workspace_name=workspace.display_name,
            install_hint=install_hint,
            installation_summary=installation_summary,
            install_url=install_url,
            install_callback_url=_path_with_flow_context("/app/setup/install/callback", flow_context),
            csrf_token=access_context["session"].csrf_secret,
        )
    )


@app.get("/app/setup/install/callback")
async def install_callback(
    request: Request,
    installation_id: str,
    setup_action: str | None = None,
    state: str | None = None,
):
    if not installation_id.isdigit():
        raise HTTPException(status_code=400, detail="Installation callback is missing a valid installation id.")
    installation_id_int = int(installation_id)
    workspace_hint = _coerce_workspace_hint(state)
    session = _switch_session_workspace_if_allowed(_get_session(request), workspace_hint)
    if session is None:
        try:
            _link_installation_to_workspace(workspace_id=None, installation_id=installation_id_int)
        except Exception:
            pass
        return _redirect_with_pending_install(
            request,
            installation_id=installation_id_int,
            workspace_id=workspace_hint,
            setup_action=setup_action,
        )
    access_context = _build_access_context(session)
    if access_context.get("workspace") is None:
        try:
            _link_installation_to_workspace(workspace_id=None, installation_id=installation_id_int)
        except Exception:
            pass
        response = RedirectResponse(_workspace_new_url(_flow_context_from_request(request)), status_code=303)
        _set_context_cookie(
            response,
            CONTROL_PLANE_PENDING_INSTALL_COOKIE,
            {"installation_id": installation_id_int, "workspace_id": workspace_hint, "setup_action": setup_action or "install"},
            max_age=1800,
        )
        return response
    _require_workspace_role(access_context, "owner", "admin")
    _link_installation_to_workspace(workspace_id=access_context["workspace"].id, installation_id=installation_id_int)
    response = RedirectResponse(
        _path_with_flow_context(
            f"/app/repos?installation_linked=1&setup_action={setup_action or 'install'}",
            _flow_context_from_request(request),
        ),
        status_code=303,
    )
    response.delete_cookie(CONTROL_PLANE_PENDING_INSTALL_COOKIE)
    return response


@app.post("/app/setup/install/link")
async def install_link(
    request: Request,
    installation_id: str = Form(default=""),
    account_login: str = Form(default=""),
    account_type: str = Form(default="Organization"),
    repo_fulls: str = Form(default=""),
    csrf_token: str | None = Form(default=None),
):
    access_context = _current_workspace_context(request)
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")
    workspace = access_context["workspace"]
    if not installation_id.isdigit():
        raise HTTPException(status_code=400, detail="A valid installation id is required.")
    installation_id_int = int(installation_id)
    _link_installation_to_workspace(
        workspace_id=workspace.id,
        installation_id=installation_id_int,
        account_login=account_login,
        account_type=account_type,
        repo_fulls=repo_fulls,
    )
    return RedirectResponse("/app/repos", status_code=303)


@app.get("/app/repos", response_class=HTMLResponse)
async def repo_setup_page(request: Request):
    access_context = _current_workspace_context(request)
    workspace = access_context["workspace"]
    user = access_context["user"]
    connections = [asdict(item) for item in list_repo_connections_for_workspace(AUDIT_DB_PATH, workspace.id)]
    allocations = [asdict(item) for item in list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace.id)]
    allocation_status_by_full = {
        str(item["repo_full"]): str(item["allocation_status"])
        for item in allocations
    }
    visible_repo_fulls = {str(item["repo_full"]) for item in connections} | {str(item["repo_full"]) for item in allocations}
    onboarded_summaries = [
        asdict(item)
        for item in list_repo_dashboard_index(
            AUDIT_DB_PATH,
            allowed_repo_fulls=visible_repo_fulls,
            allocation_status_by_full=allocation_status_by_full,
        )
    ]
    audit_repo_full = (
        (allocations[0]["repo_full"] if allocations else None)
        or (connections[0]["repo_full"] if connections else None)
    )
    audit_href = f"/dashboard/{quote(audit_repo_full, safe='')}" if audit_repo_full else "/dashboard"
    return HTMLResponse(
        render_control_plane_repo_setup_page(
            workspace_name=workspace.display_name,
            inventory_cards=render_repo_inventory_cards(
                connections,
                allocations,
                onboarded_summaries,
                csrf_token=access_context["session"].csrf_secret,
            ),
            onboarding_metrics=render_repo_onboarding_metrics(onboarded_summaries),
            onboarding_summary_cards=render_repo_onboarded_summary_cards(onboarded_summaries),
            audit_href=audit_href,
            theme_preference=user.theme_preference if user else "dark",
        )
    )


@app.post("/app/repos/allocate")
async def repo_allocate(request: Request, repo_full: str, csrf_token: str | None = Form(default=None)):
    access_context = _current_workspace_context(request)
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")
    workspace = access_context["workspace"]
    installation = access_context["installation"]
    entitlement = access_context["entitlement"]
    if installation is None:
        raise HTTPException(status_code=400, detail="GitHub installation is required before repo allocation.")

    connection = get_repo_connection_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    if connection is None:
        raise HTTPException(status_code=404, detail="Repository connection not found for workspace.")

    allocated_count, _onboarded_count = count_workspace_repo_allocations(AUDIT_DB_PATH, workspace.id)
    existing_allocation = next((item for item in list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace.id) if item.repo_full == repo_full), None)
    if entitlement is not None and allocated_count >= entitlement.repo_limit and existing_allocation is None:
        raise HTTPException(status_code=400, detail="Workspace entitlement repo limit has been reached.")

    allocation = allocate_repo_to_workspace(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repo_github_id=connection.repo_github_id,
        repo_full=connection.repo_full,
        baseline_mode="onboarding",
        activated_by_user_id=access_context["session"].user_id,
    )

    if settings.has_github_app_credentials:
        jwt_token = generate_jwt(
            settings.github_app_id,
            settings.github_private_key_path,
            settings.resolved_github_private_key,
        )
        installation_token = get_installation_token(jwt_token, installation.installation_id)
        onboard_repository(
            AUDIT_DB_PATH,
            repo_full=connection.repo_full,
            installation_id=installation.installation_id,
            token=installation_token,
        )

    update_repo_allocation_status(AUDIT_DB_PATH, allocation.id, "onboarded")
    return RedirectResponse("/app", status_code=303)


@app.get("/api/auth/session")
async def auth_session(request: Request):
    session = _get_session(request)
    if session is None:
        resolution = resolve_workspace_access_state(WorkspaceAccessSnapshot(is_authenticated=False))
        return JSONResponse({"authenticated": False, "session": None, "access": asdict(resolution)})
    access_context = _build_access_context(session)
    payload = {
        "authenticated": True,
        "session": _public_session_payload(access_context["session"]),
        "user": asdict(access_context["user"]) if access_context["user"] else None,
        "identity": asdict(access_context["identity"]) if access_context["identity"] else None,
        "workspace": asdict(access_context["workspace"]) if access_context["workspace"] else None,
        "access": asdict(access_context["resolution"]),
    }
    return JSONResponse(payload)


@app.get("/api/workspaces/current/access-state")
async def current_workspace_access_state(request: Request):
    session = _get_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    access_context = _build_access_context(session)
    return JSONResponse(asdict(access_context["resolution"]))


@app.post("/api/billing/handoff/base44")
async def base44_billing_handoff(request: Request):
    raw_body = await request.body()
    if not _verify_billing_handoff_signature(raw_body, request.headers.get("X-DriftGuard-Signature")):
        raise HTTPException(status_code=401, detail="Invalid billing handoff signature.")

    try:
        payload = BillingHandoffActivationRequest.model_validate_json(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid billing handoff payload.") from exc

    normalized_plan = _normalize_plan_hint(payload.plan_code)
    if normalized_plan is None:
        raise HTTPException(status_code=400, detail="Unknown plan code.")
    if not get_plan_definition(normalized_plan).requires_billing:
        raise HTTPException(status_code=400, detail="Free plan does not require billing handoff.")
    billing_email = _normalize_email(payload.billing_email)
    if billing_email is None:
        raise HTTPException(status_code=400, detail="Billing handoff payload must include a billing email.")

    claim = create_billing_handoff_claim(
        AUDIT_DB_PATH,
        claim_token=generate_session_id(),
        provider=_normalize_source_hint(payload.provider) or "base44",
        external_purchase_id=payload.external_purchase_id.strip(),
        plan_code=normalized_plan,
        billing_status=(payload.billing_status or "active").strip().lower(),
        billing_email=billing_email,
        source=_normalize_source_hint(payload.source) or "base44",
        next_payment_at=_parse_optional_timestamp(payload.next_payment_at),
        expires_at=time.time() + settings.billing_handoff_ttl_seconds,
    )
    claim_url = f"{settings.app_base_url.rstrip('/')}/claim/{claim.claim_token}?plan={claim.plan_code}&source={claim.source or claim.provider}"
    return JSONResponse({"status": "created", "claim_token": claim.claim_token, "claim_url": claim_url})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_index_page(request: Request):
    request_started = time.perf_counter()
    timing_metrics: list[tuple[str, float]] = []
    access_started = time.perf_counter()
    redirect, _session = _dashboard_redirect_for_request(request)
    _record_server_timing_metric(timing_metrics, "access", access_started)
    if redirect is not None:
        timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
        return _attach_server_timing(redirect, timing_metrics)
    render_started = time.perf_counter()
    response = HTMLResponse(render_dashboard_index_page(_current_theme_preference(request)))
    _record_server_timing_metric(timing_metrics, "render", render_started)
    timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
    return _attach_server_timing(response, timing_metrics)


@app.get("/dashboard/{repo_full:path}", response_class=HTMLResponse)
async def dashboard_repo_page(request: Request, repo_full: str):
    request_started = time.perf_counter()
    timing_metrics: list[tuple[str, float]] = []
    access_started = time.perf_counter()
    redirect, _session = _dashboard_redirect_for_request(request)
    _record_server_timing_metric(timing_metrics, "access", access_started)
    if redirect is not None:
        timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
        return _attach_server_timing(redirect, timing_metrics)
    render_started = time.perf_counter()
    response = HTMLResponse(render_repo_dashboard_page(repo_full, _current_theme_preference(request)))
    _record_server_timing_metric(timing_metrics, "render", render_started)
    timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
    return _attach_server_timing(response, timing_metrics)


@app.get("/api/repos")
async def list_repos(request: Request):
    request_started = time.perf_counter()
    timing_metrics: list[tuple[str, float]] = []
    access_started = time.perf_counter()
    access_context = _require_dashboard_access(request)
    _record_server_timing_metric(timing_metrics, "access", access_started)
    visibility_started = time.perf_counter()
    visibility = _dashboard_repo_visibility(access_context)
    _record_server_timing_metric(timing_metrics, "visibility", visibility_started)
    list_started = time.perf_counter()
    response = JSONResponse(
        {
            "repos": [
                asdict(item)
                for item in list_repo_dashboard_index(
                    AUDIT_DB_PATH,
                    allowed_repo_fulls=visibility["allowed_repo_fulls"],
                    repo_scope_by_full=visibility["repo_scope_by_full"],
                    allocation_status_by_full=visibility["allocation_status_by_full"],
                )
            ]
        }
    )
    _record_server_timing_metric(timing_metrics, "list", list_started)
    timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
    return _attach_server_timing(response, timing_metrics)


@app.get("/api/dashboard/overview")
def dashboard_overview(request: Request):
    request_started = time.perf_counter()
    timing_metrics: list[tuple[str, float]] = []
    access_started = time.perf_counter()
    access_context = _require_dashboard_access(request)
    _record_server_timing_metric(timing_metrics, "access", access_started)
    visibility_started = time.perf_counter()
    visibility = _dashboard_repo_visibility(access_context)
    _record_server_timing_metric(timing_metrics, "visibility", visibility_started)
    build_started = time.perf_counter()
    overview_view = build_dashboard_overview_view(
        AUDIT_DB_PATH,
        allowed_repo_fulls=visibility["allowed_repo_fulls"],
        repo_scope_by_full=visibility["repo_scope_by_full"],
        allocation_status_by_full=visibility["allocation_status_by_full"],
    )
    _record_server_timing_metric(timing_metrics, "build", build_started)
    json_started = time.perf_counter()
    response = JSONResponse(
        asdict(
            overview_view
        )
    )
    _record_server_timing_metric(timing_metrics, "json", json_started)
    timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
    return _attach_server_timing(response, timing_metrics)


@app.get("/api/persistence")
def persistence_status(request: Request):
    _require_dashboard_access(request)
    payload = asdict(get_persistence_status(AUDIT_DB_PATH))
    payload.pop("database_path", None)
    return JSONResponse(payload)


@app.get("/api/repos/{repo_full:path}/dashboard")
def repo_dashboard(request: Request, repo_full: str):
    request_started = time.perf_counter()
    timing_metrics: list[tuple[str, float]] = []
    access_started = time.perf_counter()
    access_context = _require_repo_dashboard_read_access(request, repo_full)
    _record_server_timing_metric(timing_metrics, "access", access_started)
    build_started = time.perf_counter()
    repo_view = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    _record_server_timing_metric(timing_metrics, "build", build_started)
    json_started = time.perf_counter()
    payload = asdict(repo_view)
    workspace = access_context.get("workspace")
    session = access_context.get("session")
    if workspace is not None and session is not None:
        payload["export_jobs"] = [
            _export_job_payload(job)
            for job in list_export_jobs_for_requester(AUDIT_DB_PATH, repo_full, workspace.id, session.user_id)
        ]
    else:
        payload["export_jobs"] = []
    response = JSONResponse(payload)
    _record_server_timing_metric(timing_metrics, "json", json_started)
    timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
    return _attach_server_timing(response, timing_metrics)


@app.get("/api/repos/{repo_full:path}/export/history")
def export_history(request: Request, repo_full: str):
    access_context = _require_repo_dashboard_read_access(request, repo_full)
    workspace = access_context.get("workspace")
    session = access_context.get("session")
    if workspace is None or session is None:
        return JSONResponse({"repo_full": repo_full, "jobs": []})
    jobs = list_export_jobs_for_requester(AUDIT_DB_PATH, repo_full, workspace.id, session.user_id)
    return JSONResponse({"repo_full": repo_full, "jobs": [_export_job_payload(job) for job in jobs]})


@app.get("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/episodes")
def artifact_storyline(request: Request, repo_full: str, artifact_path: str):
    _require_repo_dashboard_read_access(request, repo_full)
    storyline = build_repo_artifact_storyline(AUDIT_DB_PATH, repo_full, artifact_path)
    if storyline is None:
        raise HTTPException(status_code=404, detail="No artifact storyline is available for this repo artifact.")
    return JSONResponse(
        {
            "repo_full": repo_full,
            "artifact_path": artifact_path,
            "storyline": asdict(storyline),
        }
    )


@app.get("/api/repos/{repo_full:path}/journey")
def repo_journey(request: Request, repo_full: str):
    _require_repo_dashboard_read_access(request, repo_full)
    return JSONResponse(
        {
            "repo_full": repo_full,
            "snapshots": [snapshot_to_public_payload(item) for item in build_repo_journey(AUDIT_DB_PATH, repo_full)],
        }
    )


@app.get("/api/repos/{repo_full:path}/snapshots/{snapshot_id}")
def repo_snapshot_detail(request: Request, repo_full: str, snapshot_id: int):
    _require_repo_dashboard_read_access(request, repo_full)
    snapshot = get_repo_snapshot_detail(AUDIT_DB_PATH, repo_full, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Repo posture snapshot was not found.")
    return JSONResponse({"repo_full": repo_full, "snapshot": snapshot_to_public_payload(snapshot)})


@app.get("/api/repos/{repo_full:path}/compare")
def repo_snapshot_compare(request: Request, repo_full: str, left: int, right: int):
    _require_repo_dashboard_read_access(request, repo_full)
    try:
        comparison = compare_repo_snapshots(AUDIT_DB_PATH, repo_full, left, right)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(asdict(comparison))


@app.post("/api/repos/{repo_full:path}/onboard")
async def run_repo_onboarding(request: Request, repo_full: str, payload: RepositoryOnboardingRequest):
    _require_repo_dashboard_mutation_access(request, repo_full)
    jwt_token = generate_jwt(GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH)
    token = get_installation_token(jwt_token, payload.installation_id)

    onboarding_result = onboard_repository(
        AUDIT_DB_PATH,
        repo_full=repo_full,
        installation_id=payload.installation_id,
        token=token,
    )
    planned_jobs = []
    if payload.plan_backfill:
        planned_jobs = plan_repository_history_backfill(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            token=token,
            commit_limit_per_artifact=payload.commit_limit_per_artifact,
        )
    executed_jobs = []
    if payload.execute_backfill:
        executed_jobs = execute_repository_history_backfill(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            token=token,
        )

    dashboard = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    return JSONResponse(
        {
            "repo_full": repo_full,
            "onboarding_id": onboarding_result.onboarding.id,
            "discovered_artifact_count": len(onboarding_result.artifacts),
            "baseline_version_count": len(onboarding_result.baseline_versions),
            "planned_backfill_job_count": len(planned_jobs),
            "executed_backfill_job_count": len(executed_jobs),
            "dashboard": asdict(dashboard),
        }
    )


@app.post("/api/repos/{repo_full:path}/backfill")
async def run_repo_backfill(request: Request, repo_full: str, payload: RepositoryBackfillRequest):
    _require_repo_dashboard_mutation_access(request, repo_full)
    jwt_token = generate_jwt(GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH)
    token = get_installation_token(jwt_token, payload.installation_id)
    executed_jobs = execute_repository_history_backfill(
        AUDIT_DB_PATH,
        repo_full=repo_full,
        token=token,
    )
    dashboard = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    return JSONResponse(
        {
            "repo_full": repo_full,
            "executed_backfill_job_count": len(executed_jobs),
            "completed_backfill_job_count": sum(1 for result in executed_jobs if result.job.status == "completed"),
            "failed_backfill_job_count": sum(1 for result in executed_jobs if result.job.status == "failed"),
            "dashboard": asdict(dashboard),
        }
    )


@app.post("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/baseline")
async def promote_artifact_baseline(request: Request, repo_full: str, artifact_path: str):
    _require_repo_dashboard_mutation_access(request, repo_full)
    baseline = promote_latest_source_to_onboarding_baseline(AUDIT_DB_PATH, repo_full, artifact_path)
    if baseline is None:
        raise HTTPException(status_code=404, detail="No stored source version is available to promote as baseline.")
    build_repo_journey(AUDIT_DB_PATH, repo_full)
    dashboard = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    return JSONResponse(
        {
            "repo_full": repo_full,
            "artifact_path": artifact_path,
            "baseline": asdict(baseline),
            "dashboard": asdict(dashboard),
        }
    )


@app.get("/api/repos/{repo_full:path}/baseline/pending")
def pending_repo_baselines(request: Request, repo_full: str):
    _require_repo_dashboard_read_access(request, repo_full)
    panel = build_repo_baseline_review_panel(AUDIT_DB_PATH, repo_full)
    if panel is None:
        raise HTTPException(status_code=404, detail="Repository onboarding was not found.")
    return JSONResponse(asdict(panel))


@app.post("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/baseline/approve")
async def approve_artifact_baseline(request: Request, repo_full: str, artifact_path: str, payload: BaselineDecisionRequest):
    _require_repo_dashboard_mutation_access(request, repo_full)
    try:
        baseline = approve_repo_baseline_artifact(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            artifact_path=artifact_path,
            actor_login=_dashboard_actor_login(request),
            approval_note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    dashboard = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    return JSONResponse({"repo_full": repo_full, "artifact_path": artifact_path, "baseline": asdict(baseline), "dashboard": asdict(dashboard)})


@app.post("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/baseline/reject")
async def reject_artifact_baseline(request: Request, repo_full: str, artifact_path: str, payload: BaselineDecisionRequest):
    _require_repo_dashboard_mutation_access(request, repo_full)
    try:
        baseline = reject_repo_baseline_artifact(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            artifact_path=artifact_path,
            actor_login=_dashboard_actor_login(request),
            approval_note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    dashboard = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    return JSONResponse({"repo_full": repo_full, "artifact_path": artifact_path, "baseline": asdict(baseline), "dashboard": asdict(dashboard)})


@app.post("/api/repos/{repo_full:path}/baseline/approve")
async def approve_repo_baseline_candidate(request: Request, repo_full: str, payload: BaselineDecisionRequest):
    _require_repo_dashboard_mutation_access(request, repo_full)
    try:
        baselines = approve_repo_baseline(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            actor_login=_dashboard_actor_login(request),
            approval_note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    dashboard = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    return JSONResponse(
        {
            "repo_full": repo_full,
            "approved_baseline_count": len(baselines),
            "dashboard": asdict(dashboard),
        }
    )


@app.post("/api/repos/{repo_full:path}/baseline/reject")
async def reject_repo_baseline_candidate(request: Request, repo_full: str, payload: BaselineDecisionRequest):
    _require_repo_dashboard_mutation_access(request, repo_full)
    try:
        baselines = reject_repo_baseline(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            actor_login=_dashboard_actor_login(request),
            approval_note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    dashboard = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    return JSONResponse(
        {
            "repo_full": repo_full,
            "rejected_baseline_count": len(baselines),
            "dashboard": asdict(dashboard),
        }
    )


@app.post("/api/repos/{repo_full:path}/baseline/rebaseline")
async def rebaseline_repo(request: Request, repo_full: str, payload: RepoRebaselineRequest):
    _require_repo_dashboard_mutation_access(request, repo_full)
    try:
        baselines = rebaseline_repo_from_snapshot(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            snapshot_id=payload.snapshot_id,
            rationale=payload.rationale,
            actor_login=_dashboard_actor_login(request),
            github_app_id=GITHUB_APP_ID,
            github_private_key_path=GITHUB_PRIVATE_KEY_PATH,
            generate_jwt_fn=lambda app_id, private_key_path: generate_jwt(
                app_id,
                private_key_path,
                settings.resolved_github_private_key,
            ),
            get_installation_token_fn=get_installation_token,
            fetch_file_content_fn=fetch_file_content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dashboard = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    return JSONResponse({"repo_full": repo_full, "snapshot_id": payload.snapshot_id, "created_baseline_count": len(baselines), "dashboard": asdict(dashboard)})


@app.post("/api/repos/{repo_full:path}/export/compliance")
async def create_compliance_export(repo_full: str, payload: ComplianceExportRequest, request: Request):
    access_context = _require_repo_dashboard_mutation_access(request, repo_full)
    try:
        if payload.from_ts is not None and payload.to_ts is not None:
            from_ts = payload.from_ts
            to_ts = payload.to_ts
        elif payload.from_date and payload.to_date:
            from_ts = datetime.fromisoformat(payload.from_date).timestamp()
            to_ts = datetime.fromisoformat(payload.to_date).timestamp()
        else:
            raise HTTPException(status_code=400, detail="Either from_ts/to_ts or from_date/to_date is required")
        if from_ts > to_ts:
            raise HTTPException(status_code=400, detail="The export start date must be earlier than the end date.")
        if payload.export_mode not in ["compliance", "compliance_plus_drift"]:
            raise HTTPException(status_code=400, detail="Invalid export_mode")
        workspace = access_context.get("workspace")
        session = access_context.get("session")
        job = create_export_job(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            from_ts=from_ts,
            to_ts=to_ts,
            export_mode=payload.export_mode,
            include_artifact_content=payload.include_artifact_content,
            workspace_id=workspace.id if workspace is not None else None,
            requested_by_user_id=session.user_id if session is not None else None,
            requested_by_github_login=_dashboard_actor_login(request),
        )
        result = build_compliance_export(
            AUDIT_DB_PATH,
            ComplianceExportServiceRequest(
                repo_full=repo_full,
                from_ts=from_ts,
                to_ts=to_ts,
                export_mode=payload.export_mode,
                include_artifact_content=payload.include_artifact_content,
                export_version=job.export_version,
            ),
        )
        update_export_job_status(
            AUDIT_DB_PATH,
            job.id,
            "completed",
            result_size_bytes=result.total_size_bytes,
        )
        job = get_export_job(AUDIT_DB_PATH, job.id) or job
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="An identical export request is already in progress. Change the date range or wait for it to finish.")
    except Exception as exc:
        if 'job' in locals():
            update_export_job_status(AUDIT_DB_PATH, job.id, "failed", last_error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse({
        "job_id": job.id,
        "status": job.status,
        "download_url": _export_download_url(job),
    })


@app.get("/api/export/{job_id}/status")
async def get_export_status(job_id: int, request: Request):
    try:
        job = get_export_job(AUDIT_DB_PATH, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Export job not found")
        _require_export_job_owner_access(request, job)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(_export_job_payload(job))


@app.get("/api/export/{job_id}/download")
async def download_export(job_id: int, request: Request, token: str | None = None):
    try:
        job = get_export_job(AUDIT_DB_PATH, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Export job not found")
        _require_export_job_owner_access(request, job)
        if not token or not job.download_token or not hmac.compare_digest(token, job.download_token):
            raise HTTPException(status_code=404, detail="Export job not found")
        if job.status != "completed":
            raise HTTPException(status_code=400, detail="Export job not completed")
        if not job.result_size_bytes or not job.download_token:
            raise HTTPException(status_code=400, detail="Export job missing download data")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = build_compliance_export(
            AUDIT_DB_PATH,
            ComplianceExportServiceRequest(
                repo_full=job.repo_full,
                from_ts=job.from_ts,
                to_ts=job.to_ts,
                export_mode=job.export_mode,
                include_artifact_content=job.include_artifact_content,
                export_version=job.export_version,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filename = (
        f"promptdrift-{job.export_mode.replace('_', '-')}-export-"
        f"{job.repo_full.replace('/', '-')}-"
        f"{datetime.fromtimestamp(job.from_ts).strftime('%Y-%m-%d')}-to-"
        f"{datetime.fromtimestamp(job.to_ts).strftime('%Y-%m-%d')}.zip"
    )
    return StreamingResponse(
        io.BytesIO(result.zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


async def verify_signature(request: Request) -> bool:
    signature = request.headers.get("X-Hub-Signature-256")
    if signature is None:
        return False
    raw = await request.body()
    mac = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), raw, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, signature)


def needs_audit(diff: str) -> bool:
    return engine_needs_audit(diff)


def _get_diff_fetch_error_status_code(exc: Exception) -> int | None:
    if isinstance(exc, HTTPError):
        return exc.code
    return getattr(exc, "status", None)


async def fetch_diff_with_retry(
    repo_full: str,
    pr_number: int,
    token: str,
    *,
    use_commit_pair: bool = False,
    base_sha: str | None = None,
    head_sha: str | None = None,
) -> str:
    last_error: Exception | None = None
    fetcher = fetch_pr_diff
    fetch_args = (repo_full, pr_number, token)
    if use_commit_pair and base_sha and head_sha:
        fetcher = fetch_commit_pair_diff
        fetch_args = (repo_full, base_sha, head_sha, token)

    for attempt in range(1, PR_DIFF_FETCH_ATTEMPTS + 1):
        try:
            return fetcher(*fetch_args)
        except (HTTPError, GithubException) as exc:
            if _get_diff_fetch_error_status_code(exc) != 404 or attempt == PR_DIFF_FETCH_ATTEMPTS:
                raise
            last_error = exc
            await asyncio.sleep(PR_DIFF_FETCH_RETRY_SECONDS)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to fetch PR diff after retry attempts.")


def _parse_github_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _resolve_stripe_workspace_id(projection: dict[str, object]) -> int:
    projected_workspace_id = int(projection["workspace_id"])
    resolved_workspace_ids: set[int] = set()

    stripe_customer_id = str(projection.get("stripe_customer_id") or "")
    if stripe_customer_id:
        customer = get_billing_customer_by_stripe_customer_id(AUDIT_DB_PATH, stripe_customer_id)
        if customer is not None:
            resolved_workspace_ids.add(customer.workspace_id)

    stripe_subscription_id = str(projection.get("stripe_subscription_id") or "")
    if stripe_subscription_id:
        subscription = get_subscription_by_stripe_subscription_id(AUDIT_DB_PATH, stripe_subscription_id)
        if subscription is not None:
            resolved_workspace_ids.add(subscription.workspace_id)

    if not resolved_workspace_ids:
        raise ValueError("Stripe webhook event could not be matched to an existing workspace billing record.")
    if len(resolved_workspace_ids) != 1:
        raise ValueError("Stripe webhook event resolved to conflicting workspace billing records.")

    resolved_workspace_id = next(iter(resolved_workspace_ids))
    if projected_workspace_id != resolved_workspace_id:
        raise ValueError("Stripe webhook workspace metadata does not match the stored billing owner.")
    return resolved_workspace_id


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe webhook secret is not configured.")
    payload = await request.body()
    signature_header = request.headers.get("Stripe-Signature", "")
    event_id = ""
    event_type = ""
    try:
        verify_stripe_signature(payload, signature_header, settings.stripe_webhook_secret)
        event = parse_stripe_event(payload)
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        if event_id and has_processed_webhook_event(AUDIT_DB_PATH, "stripe", event_id):
            return JSONResponse({"status": "already_processed"})

        projection = derive_billing_projection(event)
        if projection is None:
            if event_id:
                record_webhook_event(
                    AUDIT_DB_PATH,
                    provider="stripe",
                    event_id=event_id,
                    event_type=event_type,
                    status="processed",
                )
            return JSONResponse({"status": "ignored"})

        resolved_workspace_id = _resolve_stripe_workspace_id(projection)

        if projection["stripe_customer_id"]:
            upsert_billing_customer(
                AUDIT_DB_PATH,
                workspace_id=resolved_workspace_id,
                stripe_customer_id=projection["stripe_customer_id"],
                billing_email=projection["billing_email"],
            )
        upsert_subscription(
            AUDIT_DB_PATH,
            workspace_id=resolved_workspace_id,
            stripe_subscription_id=str(projection["stripe_subscription_id"] or event_id or "stripe-event"),
            stripe_price_id=str(projection["stripe_price_id"] or ""),
            plan_code=str(projection["plan_code"]),
            status=str(projection["status"]),
            cancel_at_period_end=bool(projection["cancel_at_period_end"]),
            current_period_start_at=projection["current_period_start_at"],
            current_period_end_at=projection["current_period_end_at"],
            next_payment_at=projection["current_period_end_at"],
            trial_ends_at=projection["trial_ends_at"],
            last_webhook_event_id=event_id or None,
        )
        upsert_entitlement(
            AUDIT_DB_PATH,
            workspace_id=resolved_workspace_id,
            payload=projection["entitlement"],
        )
        if event_id:
            record_webhook_event(
                AUDIT_DB_PATH,
                provider="stripe",
                event_id=event_id,
                event_type=event_type,
                status="processed",
            )
        return JSONResponse({"status": "processed"})
    except Exception as exc:
        if event_id:
            record_webhook_event(
                AUDIT_DB_PATH,
                provider="stripe",
                event_id=event_id,
                event_type=event_type,
                status="failed",
                error_summary=str(exc),
            )
        raise


@app.post("/webhook")
async def webhook(request: Request):
    if not await verify_signature(request):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event not in {"pull_request", "push"}:
        return JSONResponse({"message": "ignored"})

    payload = await request.json()
    if event == "push":
        installation_id = payload.get("installation", {}).get("id")
        repo_full = payload.get("repository", {}).get("full_name")
        branch_ref = payload.get("ref")
        default_branch = payload.get("repository", {}).get("default_branch")
        commit_sha = payload.get("head_commit", {}).get("id")

        if not all([installation_id, repo_full, branch_ref, default_branch, commit_sha]):
            raise HTTPException(status_code=400, detail="Missing payload data")

        if branch_ref != f"refs/heads/{default_branch}":
            return JSONResponse({"message": "ignored"})

        managed_installation = get_github_installation_by_installation_id(AUDIT_DB_PATH, int(installation_id))
        if _control_plane_active() and managed_installation is not None and managed_installation.workspace_id is not None and managed_installation.status == "active":
            allocation = get_repo_allocation_for_installation(AUDIT_DB_PATH, int(installation_id), str(repo_full))
            if allocation is None:
                return JSONResponse({"message": "ignored: repo not allocated"})

            entitlement = get_workspace_entitlement(AUDIT_DB_PATH, allocation.workspace_id)
            if entitlement is None or not entitlement.dashboard_enabled:
                return JSONResponse({"message": "ignored: workspace not entitled"})

        onboarding = get_latest_repository_onboarding(AUDIT_DB_PATH, str(repo_full))
        if onboarding is None:
            return JSONResponse({"message": "ignored: repo not onboarded"})

        job = create_branch_scan_job(
            AUDIT_DB_PATH,
            repo_full=str(repo_full),
            installation_id=int(installation_id),
            commit_sha=str(commit_sha),
            branch_ref=str(branch_ref),
            triggered_by="push_webhook",
        )
        return JSONResponse({"message": "branch scan queued", "job_id": job.id})

    action = payload.get("action")
    if action not in ("opened", "synchronize", "closed", "reopened"):
        return JSONResponse({"message": "ignored"})

    installation_id = payload.get("installation", {}).get("id")
    repo_full = payload.get("repository", {}).get("full_name")
    pr_number = payload.get("pull_request", {}).get("number")
    pull_request = payload.get("pull_request", {})
    base_sha = pull_request.get("base", {}).get("sha")
    head_sha = pull_request.get("head", {}).get("sha")
    pr_state = pull_request.get("state")
    pr_merged = pull_request.get("merged")
    pr_closed_at = _parse_github_timestamp(pull_request.get("closed_at"))
    pr_merged_at = _parse_github_timestamp(pull_request.get("merged_at"))
    pr_merge_commit_sha = pull_request.get("merge_commit_sha")
    pr_updated_at = _parse_github_timestamp(pull_request.get("updated_at"))

    if not all([installation_id, repo_full, pr_number]):
        raise HTTPException(status_code=400, detail="Missing payload data")

    managed_installation = get_github_installation_by_installation_id(AUDIT_DB_PATH, int(installation_id))
    if _control_plane_active() and managed_installation is not None and managed_installation.workspace_id is not None and managed_installation.status == "active":
        allocation = get_repo_allocation_for_installation(AUDIT_DB_PATH, int(installation_id), str(repo_full))
        if allocation is None:
            return JSONResponse({"message": "ignored: repo not allocated"})

        entitlement = get_workspace_entitlement(AUDIT_DB_PATH, allocation.workspace_id)
        if entitlement is None or not entitlement.pr_comments_enabled:
            return JSONResponse({"message": "ignored: workspace not entitled"})
        workspace = get_workspace_by_id(AUDIT_DB_PATH, allocation.workspace_id)
        if workspace is None or not workspace.pr_comments_setting_enabled:
            return JSONResponse({"message": "ignored: PR comments disabled in settings"})

    if action in ("closed", "reopened"):
        update_job_pr_state(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_state=pr_state,
            pr_merged=pr_merged,
            pr_closed_at=pr_closed_at,
            pr_merged_at=pr_merged_at,
            pr_merge_commit_sha=pr_merge_commit_sha,
            pr_updated_at=pr_updated_at,
        )
        update_pull_request_audit_state(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_state=pr_state,
            pr_merged=pr_merged,
            pr_closed_at=pr_closed_at,
            pr_merged_at=pr_merged_at,
            pr_merge_commit_sha=pr_merge_commit_sha,
            pr_updated_at=pr_updated_at,
        )
        return JSONResponse({"message": "pr state updated"})

    if not head_sha:
        raise HTTPException(status_code=400, detail="Missing payload data")

    jwt_token = generate_jwt(GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH)
    token = get_installation_token(jwt_token, installation_id)
    diff_text = await fetch_diff_with_retry(
        repo_full,
        pr_number,
        token,
        use_commit_pair=action == "synchronize",
        base_sha=base_sha,
        head_sha=head_sha,
    )

    if not needs_audit(diff_text):
        return JSONResponse({"message": "no relevant changes"})

    job = create_audit_job(
        AUDIT_DB_PATH,
        repo_full=repo_full,
        pr_number=pr_number,
        installation_id=installation_id,
        head_sha=head_sha,
        diff_text=diff_text,
        pr_state=pr_state,
        pr_merged=pr_merged,
        pr_closed_at=pr_closed_at,
        pr_merged_at=pr_merged_at,
        pr_merge_commit_sha=pr_merge_commit_sha,
        pr_updated_at=pr_updated_at,
    )

    return JSONResponse({"message": "audit queued", "job_id": job.id})
