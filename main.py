import asyncio
import io
import base64
import hashlib
import html
import hmac
import json
import logging
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from github.GithubException import GithubException
from jwt.exceptions import InvalidKeyError
from openai import OpenAI
from pydantic import BaseModel

from config import get_settings
from engine.relevance import needs_audit as engine_needs_audit
from services.access_state import WorkspaceAccessSnapshot, resolve_workspace_access_state
from services.audit_jobs import create_audit_job, init_db, update_job_pr_state
from services.audit_records import (
    get_audit_comment_episode_for_pr_head_sha,
    get_latest_audit_comment_for_pr,
    get_pull_request_audit_by_id,
    record_pr_outcome_feedback_events,
    record_audit_feedback_event,
    refresh_audit_reaction_feedback_for_pr,
    update_pull_request_audit_state,
)
from services.audit_feedback_records import list_recent_feedback_events, list_recent_triage_events
from services.audit_worker import AuditWorker, WorkerSettings
from services.audit_records import list_pre_audit_relevance_decisions
from services.mcp_broker import (
    authenticate_mcp_broker_request,
    invoke_mcp_broker_tool,
    issue_mcp_broker_token_via_client_credentials,
    list_mcp_tools_for_scopes,
    record_mcp_broker_invocation,
)
from services.mcp_package import build_customer_mcp_bundle
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
    GITHUB_REQUIRED_REPO_SCOPES,
    GithubOAuthToken,
    GithubUserProfile,
    build_github_oauth_authorize_url,
    exchange_code_for_access_token,
    fetch_github_user_profile,
    generate_csrf_secret,
    generate_oauth_state,
    generate_session_id,
    list_github_user_repositories,
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
    render_control_plane_compliance_page,
    render_control_plane_help_page,
    render_control_plane_install_page,
    render_control_plane_login_page,
    render_control_plane_mcp_page,
    render_control_plane_policies_page,
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
    AdminActivityLogEntry,
    activate_billing_handoff_claim,
    allocate_repo_to_workspace,
    apply_github_installation_repository_event,
    count_machine_principals_for_workspace,
    create_control_plane_audit_log,
    get_billing_customer_by_stripe_customer_id,
    count_workspace_repo_allocations,
    count_workspaces,
    create_billing_handoff_claim,
    create_machine_principal,
    create_machine_principal_and_flash_secret,
    create_user,
    create_user_session,
    create_workspace,
    delete_user,
    delete_workspace,
    delete_workspace_membership,
    get_billing_customer_for_workspace,
    get_billing_handoff_claim_by_token,
    get_github_installation_by_installation_id,
    get_ai_system_by_id,
    get_ai_system_for_workspace_repo,
    get_repo_allocation_for_installation,
    get_repo_allocation_for_workspace,
    get_github_identity_for_user,
    get_repo_connection_for_workspace,
    get_user_by_id,
    get_user_session,
    get_workspace_by_id,
    get_workspace_entitlement,
    get_latest_workspace_installation,
    get_workspace_installation,
    get_workspace_membership,
    get_workspace_subscription,
    get_machine_principal_by_id,
    get_subscription_by_stripe_subscription_id,
    get_machine_principal_by_client_id,
    has_processed_webhook_event,
    list_admin_workspace_users,
    list_control_plane_audit_logs_for_workspace,
    list_billing_handoff_claims,
    list_ai_systems_for_workspace,
    list_machine_principals_for_workspace,
    list_recent_control_plane_audit_logs,
    list_repo_allocations_for_workspace,
    list_repo_connections_for_workspace,
    list_unclaimed_installations,
    list_webhook_event_receipts,
    list_workspace_invites_for_workspace,
    list_workspace_memberships_for_user,
    read_and_clear_session_flash,
    pop_all_session_flash,
    record_webhook_event,
    replace_repo_connections,
    revoke_machine_principal,
    revoke_user_session,
    accept_workspace_invites_for_github_login,
    apply_github_installation_lifecycle_event,
    update_ai_system_classification,
    update_repo_allocation_status,
    update_session_workspace,
    update_user_admin_fields,
    update_user_profile_preferences,
    update_workspace_admin_fields,
    update_workspace_display_name,
    update_repo_allocation_pr_feedback_mode,
    update_workspace_pr_feedback_mode,
    update_workspace_pr_comments_setting,
    upsert_workspace_membership,
    upsert_workspace_invite,
    upsert_billing_customer,
    upsert_entitlement,
    upsert_github_identity,
    upsert_github_installation,
    upsert_subscription,
    write_session_flash,
)
from services.pr_feedback_mode import resolve_pr_feedback_mode
from services.dashboard_frontend import DASHBOARD_STATIC_DIR, render_dashboard_index_page, render_repo_dashboard_page
from services.dashboard_api_payloads import build_artifact_storyline_payload, build_dashboard_escalation_queue_payload, build_dashboard_overview_payload, build_pending_proposals_payload, build_pre_audit_relevance_payload, build_repo_index_payload, build_repo_journey_payload, build_repo_snapshot_compare_payload, build_repo_snapshot_detail_payload
from services.dashboard_views import build_dashboard_overview_view, build_repo_artifact_storyline, build_repo_dashboard_view, build_repo_dashboard_view_with_timings, build_workspace_escalation_queue, filter_dashboard_overview_view, list_repo_dashboard_index
from services.entitlements import derive_entitlement_payload, get_plan_definition
from services.export_jobs import create_export_job, get_export_job, list_export_jobs_for_requester, update_export_job_status
from services.export_jobs import list_export_jobs_for_workspace_requester
from services.compliance_export_service import ComplianceExportRequest as ComplianceExportServiceRequest, build_compliance_export
from services.compliance_readiness import build_compliance_workspace_view, filter_compliance_evidence_view
from services.cloud_common import evaluate_and_persist_audit_decision
from services.github_integration import fetch_commit_pair_diff, fetch_file_content, fetch_pr_diff, generate_jwt, get_installation_token, list_repository_files
from services.github_provisioning import get_live_github_install_url, sync_installation_repositories
from services.ai_system_registry import sync_ai_system_for_repo
from services.onboarding import add_repo_artifact_to_onboarding, execute_repository_history_backfill, infer_artifact_type_from_path, onboard_repository, plan_repository_history_backfill, remove_repo_artifact_from_onboarding, tracked_artifact_type_options, update_repo_artifact_type
from services.onboarding_records import get_latest_repository_onboarding, list_onboarded_artifacts_for_onboarding, promote_latest_source_to_onboarding_baseline
from services.persistence import connect_sqlite, get_persistence_status, persistence_status_payload
from services.provenance_labels import artifact_family
from services.repo_journey import build_repo_journey, compare_repo_snapshots, get_repo_snapshot_detail, snapshot_to_public_payload
from services.runtime_guardrails import build_runtime_readiness, readiness_json_response, validate_runtime_configuration
from services.secure_store import decrypt_text, encrypt_text
from services.static_assets import FingerprintedStaticFiles
from services.api_models import BaselineDecisionRequest, RepoRebaselineRequest, RepositoryBackfillRequest, RepositoryOnboardingRequest
from services.workspace_access import (
    build_access_context as build_workspace_access_context,
    current_authenticated_identity_context as current_workspace_authenticated_identity_context,
    current_workspace_context as current_workspace_access_context,
    get_session as get_workspace_session,
    require_dashboard_access as require_workspace_dashboard_access,
)
from routers.dashboard import create_compliance_api_router, create_dashboard_page_router, create_dashboard_read_router, create_export_create_router, create_export_job_router, create_repo_baseline_router, create_repo_dashboard_router, create_repo_history_router, create_repo_onboarding_router, create_repo_read_router
from routers.health import create_health_router

settings = get_settings()

GITHUB_APP_ID = settings.github_app_id
GITHUB_PRIVATE_KEY_PATH = settings.github_private_key_path
GITHUB_WEBHOOK_SECRET = settings.github_webhook_secret
OPENAI_API_KEY = settings.openai_api_key
FOUNDRY_API_KEY = settings.foundry_api_key
AZURE_OPENAI_ENDPOINT = settings.azure_openai_endpoint
AI_PROVIDER = settings.resolved_ai_provider.value
AI_MODEL = settings.ai_model
AI_API_KEY = settings.ai_api_key
AI_BASE_URL = settings.ai_base_url
AUDIT_DB_PATH = settings.resolved_db_path
AUDIT_WORKER_ENABLED = settings.audit_worker_enabled and bool(
    settings.has_github_app_credentials and GITHUB_WEBHOOK_SECRET and AI_API_KEY
)
LLM_TIMEOUT_SECONDS = settings.llm_timeout_seconds
RELEVANCE_MICRO_CLASSIFIER_TIMEOUT_SECONDS = min(LLM_TIMEOUT_SECONDS, 5.0)
AUDIT_MAX_ATTEMPTS = settings.audit_max_attempts
AUDIT_MAX_RETRY_WINDOW_SECONDS = settings.audit_max_retry_window_seconds
AUDIT_WORKER_POLL_SECONDS = settings.audit_worker_poll_seconds
PR_DIFF_FETCH_ATTEMPTS = settings.pr_diff_fetch_attempts
PR_DIFF_FETCH_RETRY_SECONDS = settings.pr_diff_fetch_retry_seconds
CONTROL_PLANE_OAUTH_STATE_COOKIE = "promptdrift_oauth_state"
CONTROL_PLANE_OAUTH_CONTEXT_COOKIE = "promptdrift_oauth_context"
CONTROL_PLANE_PENDING_INSTALL_COOKIE = "promptdrift_pending_install"
CONTROL_PLANE_INSTALL_STATE_COOKIE = "promptdrift_install_state"
SUPPORTED_ACTIVE_PLAN_STATUSES = {"active", "trialing", "canceled", "free_active"}

logger = logging.getLogger(__name__)

client = OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL) if AI_API_KEY else None
worker: AuditWorker | None = None
branch_scan_worker: BranchScanWorker | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker, branch_scan_worker
    validate_runtime_configuration(settings)
    init_db(AUDIT_DB_PATH)
    if settings.service_role == "monolith" and AUDIT_WORKER_ENABLED:
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
    if settings.service_role == "monolith" and settings.has_github_app_credentials and GITHUB_WEBHOOK_SECRET:
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
app.include_router(create_health_router(settings))


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


class McpBrokerTokenRequest(BaseModel):
    client_id: str
    client_secret: str


class McpBrokerInvokeRequest(BaseModel):
    tool_name: str
    arguments: dict[str, object] = {}


def _control_plane_active() -> bool:
    if settings.is_production:
        return True
    try:
        has_workspaces = count_workspaces(AUDIT_DB_PATH) > 0
    except sqlite3.Error:
        has_workspaces = False
    if has_workspaces:
        return True
    if settings.service_role != "monolith":
        return True
    if not settings.is_local:
        return True
    app_base_host = (urlparse(settings.app_base_url).hostname or "").strip().lower()
    return app_base_host not in {"127.0.0.1", "localhost", "::1"}


def _get_session(request: Request):
    return get_workspace_session(settings, AUDIT_DB_PATH, request)


def _build_access_context(session) -> dict[str, object]:
    return build_workspace_access_context(AUDIT_DB_PATH, session)


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
        return None, session, None, False
    if session is None:
        return RedirectResponse("/login", status_code=303), None, None, False
    access_context = _build_access_context(session)
    is_deep_link = _is_dashboard_deep_link_request(request)
    resolution = access_context["resolution"]

    # If no dashboard access, deny unless it's a deep link
    if not resolution.can_access_dashboard:
        if is_deep_link or resolution.state == "awaiting_github_install":
            return None, session, access_context, True
        return RedirectResponse("/workspace", status_code=303), session, access_context, False

    return None, session, access_context, False


def _normalize_dashboard_redirect_result(result):
    if isinstance(result, tuple) and len(result) == 4:
        return result
    if isinstance(result, tuple) and len(result) == 2:
        redirect, session = result
        return redirect, session, None, False
    raise ValueError("_dashboard_redirect_for_request returned an unexpected result")


def _is_dashboard_deep_link_request(request: Request) -> bool:
    artifact = str(request.query_params.get("artifact") or "").strip()
    pr = str(request.query_params.get("pr") or "").strip()
    return bool(artifact or pr)


def _dashboard_shell_cta_href(resolution) -> str:
    state = getattr(resolution, "state", "")
    if state in {"workspace_no_subscription", "billing_pending_confirmation", "payment_failed", "active_comments_only", "expired_read_only", "canceled_active_until_period_end"}:
        return "/billing"
    if state == "authenticated_no_workspace":
        return "/workspaces/new"
    if state == "awaiting_github_install":
        return "/setup/install"
    if state == "awaiting_repo_onboarding":
        return "/repos"
    return "/workspace"


def _repo_visible_for_dashboard_shell(access_context: dict[str, object] | None, repo_full: str) -> bool:
    if not access_context:
        return True
    workspace = access_context.get("workspace")
    if workspace is None:
        return False
    allocation = get_repo_allocation_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    if allocation is not None and allocation.allocation_status in {"active", "onboarded"}:
        return True
    connection = get_repo_connection_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    return connection is not None and connection.status == "available"


def _dashboard_shell_copy(access_context: dict[str, object] | None, *, repo_full: str | None = None) -> tuple[str, str, str | None, str | None]:
    resolution = access_context.get("resolution") if access_context else None
    if resolution is None:
        return ("Dashboard setup required", "Sign in to continue into the DriftGuard dashboard.", "/login", "Continue with GitHub")

    shell_title = resolution.ui_title
    shell_body = resolution.ui_body
    shell_cta_label = resolution.primary_cta
    if repo_full and resolution.state == "awaiting_repo_onboarding":
        shell_body = (
            f"{repo_full} has not yet been onboarded in this workspace. Complete the first repository scan to unlock the case file view for this link."
        )
    elif repo_full and resolution.state == "active_comments_only":
        shell_body = (
            f"This workspace can receive PR comments, but dashboard access for {repo_full} requires a paid plan. Upgrade to open the linked case file."
        )
    elif resolution.state == "active_comments_only":
        shell_body = "This workspace can receive PR comments, but dashboard views require a paid plan. Upgrade to open linked dashboard context."
    elif resolution.state == "awaiting_github_install":
        workspace = access_context.get("workspace") if access_context else None
        latest_installation = get_latest_workspace_installation(AUDIT_DB_PATH, workspace.id) if workspace else None
        if latest_installation is not None and latest_installation.status != "active":
            shell_title = "Reconnect Vipari on GitHub"
            shell_body = "GitHub App access for this workspace was removed. Reconnect Vipari on GitHub to restore dashboard access and resume repository automation."
            shell_cta_label = "Reconnect GitHub App"
        else:
            shell_body = "GitHub App access is required before Vipari can reopen this dashboard view. Reinstall or reconnect the GitHub App to continue."
    return (shell_title, shell_body, _dashboard_shell_cta_href(resolution), shell_cta_label)


def _post_billing_activation_redirect(access_context: dict[str, object], flow_context: dict[str, str], *, activation_flag: str) -> RedirectResponse:
    refreshed_access_context = _build_access_context(access_context["session"])
    state = refreshed_access_context["resolution"].state
    if state == "awaiting_github_install":
        return RedirectResponse(
            _path_with_flow_context(f"/setup/install?{activation_flag}=1", flow_context),
            status_code=303,
        )
    if state == "awaiting_repo_onboarding":
        return RedirectResponse(_path_with_flow_context("/repos", flow_context), status_code=303)
    if state in {"active", "active_comments_only", "canceled_active_until_period_end", "expired_read_only"}:
        return RedirectResponse(_path_with_flow_context("/dashboard", flow_context), status_code=303)
    return RedirectResponse(
        _resume_destination_for_session(refreshed_access_context["session"], flow_context),
        status_code=303,
    )


def _free_tier_upgrade_shell_copy(scope_label: str) -> tuple[str, str, str, str]:
    return (
        "Upgrade to Starter",
        f"This workspace can review dashboard and audit surfaces, but {scope_label} requires Starter or above.",
        "/app/billing?plan=starter",
        "Upgrade to Starter",
    )


def _missing_repo_shell_copy(access_context: dict[str, object] | None, repo_full: str) -> tuple[str, str, str, str]:
    workspace = access_context.get("workspace") if access_context else None
    latest_installation = get_latest_workspace_installation(AUDIT_DB_PATH, workspace.id) if workspace else None
    if latest_installation is not None and latest_installation.status != "active":
        return (
            "Reconnect Vipari on GitHub",
            f"{repo_full} is no longer available because GitHub App access for this workspace was removed. Reconnect Vipari on GitHub to restore repository access and reopen this link.",
            "/setup/install",
            "Reconnect GitHub App",
        )
    return (
        "Repository access removed",
        f"{repo_full} is no longer available in this workspace dashboard. It may have been removed from the GitHub App installation scope or disconnected from this workspace. Choose another repository to continue.",
        "/repos",
        "Open Repository Setup",
    )


def _render_missing_repo_dashboard_page(
    request: Request,
    repo_full: str,
    *,
    requested_tab: str,
    access_context: dict[str, object] | None,
    artifact: str | None = None,
    pr: str | None = None,
    head_sha: str | None = None,
    status_code: int = 404,
) -> HTMLResponse:
    shell_title, shell_body, shell_cta_href, shell_cta_label = _missing_repo_shell_copy(access_context, repo_full)
    user = access_context.get("user") if access_context else None
    identity = access_context.get("identity") if access_context else None
    active_tab = requested_tab.strip().lower() if requested_tab else "audit"
    if active_tab not in {"audit", "drift", "version-control", "baseline", "compliance", "reports"}:
        active_tab = "audit"
    return HTMLResponse(
        render_repo_dashboard_page(
            repo_full,
            theme_preference=_current_theme_preference(request),
            active_tab=active_tab,
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=user.display_name if user else None,
                github_login=identity.github_login if identity else None,
            ),
            shell_state="repo_access_removed",
            shell_title=shell_title,
            shell_body=shell_body,
            shell_cta_href=shell_cta_href,
            shell_cta_label=shell_cta_label,
            deep_link_artifact=(artifact or "").strip(),
            deep_link_pr=(pr or "").strip(),
            deep_link_head_sha=(head_sha or "").strip(),
        ),
        status_code=status_code,
    )


def _is_active_comments_only_workspace(access_context: dict[str, object] | None) -> bool:
    resolution = access_context.get("resolution") if access_context else None
    return bool(resolution is not None and getattr(resolution, "state", "") == "active_comments_only")


def _admin_subscription_status_for_plan(plan_code: str) -> str:
    return "free_active" if plan_code == "free" else "active"


def _admin_apply_workspace_plan(workspace_id: int, plan_code: str):
    normalized_plan = get_plan_definition(plan_code).code
    existing_subscription = get_workspace_subscription(AUDIT_DB_PATH, workspace_id)
    subscription_status = _admin_subscription_status_for_plan(normalized_plan)
    now = time.time()
    subscription = upsert_subscription(
        AUDIT_DB_PATH,
        workspace_id=workspace_id,
        stripe_subscription_id=(existing_subscription.stripe_subscription_id if existing_subscription else f"admin:workspace:{workspace_id}"),
        stripe_price_id=f"admin:plan:{normalized_plan}",
        plan_code=normalized_plan,
        status=subscription_status,
        cancel_at_period_end=False,
        current_period_start_at=(existing_subscription.current_period_start_at if existing_subscription else now),
        current_period_end_at=(existing_subscription.current_period_end_at if existing_subscription else None),
        next_payment_at=(existing_subscription.next_payment_at if existing_subscription else None),
        trial_ends_at=(existing_subscription.trial_ends_at if existing_subscription else None),
        last_webhook_event_id=(existing_subscription.last_webhook_event_id if existing_subscription else "admin_plan_override"),
    )
    entitlement = upsert_entitlement(
        AUDIT_DB_PATH,
        workspace_id=workspace_id,
        payload=derive_entitlement_payload(normalized_plan, subscription_status),
    )
    return subscription, entitlement


def _local_debug_dashboard_enabled() -> bool:
    return settings.is_local and settings.service_role == "monolith" and bool(settings.local_debug_disable_login)


def _local_debug_workspace_context() -> dict[str, object] | None:
    if not _local_debug_dashboard_enabled():
        return None
    try:
        with connect_sqlite(AUDIT_DB_PATH) as conn:
            row = conn.execute(
                "SELECT id FROM workspaces ORDER BY id ASC LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    workspace = get_workspace_by_id(AUDIT_DB_PATH, int(row["id"]))
    if workspace is None:
        return None
    allocated_repo_count, onboarded_repo_count = count_workspace_repo_allocations(AUDIT_DB_PATH, workspace.id)
    resolution = resolve_workspace_access_state(
        WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            role="owner",
            has_subscription_record=True,
            dashboard_enabled=True,
            pr_comments_enabled=True,
            has_linked_installation=True,
            allocated_repo_count=max(allocated_repo_count, 1),
            onboarded_repo_count=max(onboarded_repo_count, 1),
        )
    )
    return {
        "session": None,
        "user": None,
        "identity": None,
        "membership": None,
        "subscription": get_workspace_subscription(AUDIT_DB_PATH, workspace.id),
        "entitlement": get_workspace_entitlement(AUDIT_DB_PATH, workspace.id),
        "installation": get_workspace_installation(AUDIT_DB_PATH, workspace.id),
        "workspace": workspace,
        "resolution": resolution,
    }


def _set_session_cookie(response: RedirectResponse, session_id: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        session_id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
    )


def _require_context_cookie_signing_config() -> None:
    if not settings.has_encryption_key:
        raise HTTPException(status_code=503, detail="APP_ENCRYPTION_KEY must be configured before control-plane flow state can be issued.")


def _context_cookie_binding_for_session_id(session_id: str | None) -> str | None:
    normalized = (session_id or "").strip()
    return f"session:{normalized}" if normalized else None


def _context_cookie_binding_for_oauth_state(state: str | None) -> str | None:
    normalized = (state or "").strip()
    return f"oauth:{normalized}" if normalized else None


def _set_context_cookie(
    response: Response,
    name: str,
    payload: dict[str, object],
    *,
    binding: str | None,
    max_age: int = 1800,
) -> None:
    response.set_cookie(
        name,
        _encode_context_cookie(payload, binding=binding, max_age=max_age),
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


def _encode_context_cookie(payload: dict[str, object], *, binding: str | None, max_age: int = 1800) -> str:
    _require_context_cookie_signing_config()
    envelope = {
        "v": 1,
        "iat": int(time.time()),
        "exp": int(time.time()) + max_age,
        "binding": binding,
        "payload": payload,
    }
    raw = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(settings.app_encryption_key.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    encoded = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    return f"{encoded}.{signature}"


def _decode_context_cookie(value: str | None, *, binding: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        encoded_payload, provided_signature = value.split(".", 1)
        padded_payload = encoded_payload + "=" * (-len(encoded_payload) % 4)
        raw = base64.urlsafe_b64decode(padded_payload.encode("utf-8"))
        if not settings.has_encryption_key:
            return {}
        expected_signature = hmac.new(settings.app_encryption_key.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(provided_signature, expected_signature):
            return {}
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("v") != 1:
        return {}
    if payload.get("binding") != binding:
        return {}
    expires_at = payload.get("exp")
    if not isinstance(expires_at, int) or expires_at < int(time.time()):
        return {}
    cookie_payload = payload.get("payload")
    return cookie_payload if isinstance(cookie_payload, dict) else {}


def _decode_bound_context_cookie(request: Request, cookie_name: str) -> dict[str, object]:
    cookie_value = request.cookies.get(cookie_name)
    session = _get_session(request)
    if session is not None:
        payload = _decode_context_cookie(cookie_value, binding=_context_cookie_binding_for_session_id(session.session_id))
        if payload:
            return payload
    oauth_state_binding = _context_cookie_binding_for_oauth_state(request.cookies.get(CONTROL_PLANE_OAUTH_STATE_COOKIE))
    if oauth_state_binding is not None:
        return _decode_context_cookie(cookie_value, binding=oauth_state_binding)
    return {}


def _flow_context_from_request(request: Request) -> dict[str, str]:
    cookie_payload = _decode_bound_context_cookie(request, CONTROL_PLANE_OAUTH_CONTEXT_COOKIE)
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
    payload = _decode_bound_context_cookie(request, CONTROL_PLANE_PENDING_INSTALL_COOKIE)
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


def _pending_install_context_from_query(request: Request) -> dict[str, object]:
    installation_id = request.query_params.get("installation_id")
    workspace_id = request.query_params.get("workspace_id")
    setup_action = request.query_params.get("setup_action")
    context: dict[str, object] = {}
    if installation_id and installation_id.isdigit():
        context["installation_id"] = int(installation_id)
    if workspace_id and workspace_id.isdigit():
        context["workspace_id"] = int(workspace_id)
    if setup_action and setup_action.strip():
        context["setup_action"] = setup_action.strip()
    return context


def _install_callback_context_from_request(request: Request) -> dict[str, object]:
    payload = _decode_bound_context_cookie(request, CONTROL_PLANE_INSTALL_STATE_COOKIE)
    nonce = payload.get("nonce")
    workspace_id = payload.get("workspace_id")
    context: dict[str, object] = {}
    if isinstance(nonce, str) and nonce.strip():
        context["nonce"] = nonce.strip()
    if isinstance(workspace_id, int) or (isinstance(workspace_id, str) and str(workspace_id).isdigit()):
        context["workspace_id"] = int(workspace_id)
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
    return _path_with_flow_context("/workspaces/new", flow_context)


def _billing_url(flow_context: dict[str, str]) -> str:
    return _path_with_flow_context("/billing", flow_context)


def _install_url(flow_context: dict[str, str]) -> str:
    return _path_with_flow_context("/setup/install", flow_context)


def _resume_destination_for_session(session, flow_context: dict[str, str]) -> str:
    if session.workspace_id is None:
        return _workspace_new_url(flow_context)
    access_context = _build_access_context(session)
    if flow_context.get("claim") and access_context.get("subscription") is None:
        return _path_with_flow_context("/billing/claim", flow_context)
    if flow_context.get("plan") and access_context.get("subscription") is None:
        return _billing_url(flow_context)
    if access_context.get("installation") is None and access_context["resolution"].state == "awaiting_github_install":
        return _install_url(flow_context)
    return _path_with_flow_context("/workspace", flow_context)


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
    pending_install = {
        "installation_id": installation_id,
        "setup_action": setup_action or "install",
    }
    if workspace_id is not None:
        pending_install["workspace_id"] = workspace_id
    return RedirectResponse(_path_with_flow_context(_auth_start_url(pending_install), _flow_context_from_request(request)), status_code=303)


def _workspace_slug_candidates(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "workspace"
    return [base, f"{base}-{int(time.time())}"]


def _admin_redirect(status: str) -> RedirectResponse:
    return RedirectResponse(f"/app/admin?updated={quote(status)}", status_code=303)


_ADMIN_LOG_PAGE_SIZE = 50


def _parse_admin_log_date(raw_value: str | None, *, inclusive_end: bool = False) -> float | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    timestamp = parsed.timestamp()
    if inclusive_end and len(value) == 10:
        timestamp += 86399.999
    return timestamp


def _admin_activity_actor_label(actor_user_id: int | None, user_labels: dict[int, str]) -> str:
    if actor_user_id is None:
        return "System"
    return user_labels.get(actor_user_id, f"User #{actor_user_id}")


def _admin_activity_payload_details(payload_json: str | None) -> str:
    if not payload_json:
        return "{}"
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return str(payload_json)
    if not isinstance(payload, dict):
        return str(payload)
    if not payload:
        return "{}"
    return ", ".join(f"{key}={payload[key]}" for key in sorted(payload))


def _build_admin_activity_entries(*, db_path: str, user_labels: dict[int, str], fetch_limit: int) -> list[AdminActivityLogEntry]:
    entries: list[AdminActivityLogEntry] = []

    for row in list_recent_control_plane_audit_logs(db_path, limit=fetch_limit):
        entries.append(
            AdminActivityLogEntry(
                source="control_plane",
                occurred_at=row.created_at,
                event_type=row.event_type,
                workspace_id=row.workspace_id,
                actor_user_id=row.actor_user_id,
                actor_label=_admin_activity_actor_label(row.actor_user_id, user_labels),
                subject_type=row.subject_type,
                subject_id=row.subject_id,
                details=_admin_activity_payload_details(row.payload_json),
                raw_id=f"control_plane:{row.id}",
            )
        )

    for row in list_webhook_event_receipts(db_path, limit=fetch_limit):
        details = [f"provider={row.provider}", f"status={row.status}"]
        if row.error_summary:
            details.append(f"error={row.error_summary}")
        entries.append(
            AdminActivityLogEntry(
                source="webhook",
                occurred_at=row.processed_at or row.created_at,
                event_type=row.event_type,
                workspace_id=None,
                actor_user_id=None,
                actor_label="System",
                subject_type="webhook_event",
                subject_id=row.event_id,
                details=", ".join(details),
                raw_id=f"webhook:{row.id}",
            )
        )

    for row in list_recent_feedback_events(db_path, limit=fetch_limit):
        detail_parts = [f"source={row.source}"]
        if row.comment:
            detail_parts.append(row.comment)
        if row.metadata:
            detail_parts.append(", ".join(f"{key}={row.metadata[key]}" for key in sorted(row.metadata)))
        entries.append(
            AdminActivityLogEntry(
                source="audit_feedback",
                occurred_at=row.created_at,
                event_type=f"audit.feedback.{row.kind}",
                workspace_id=row.workspace_id,
                actor_user_id=None,
                actor_label=row.source,
                subject_type="audit",
                subject_id=str(row.audit_id),
                details=" | ".join(detail_parts),
                raw_id=f"audit_feedback:{row.id}",
            )
        )

    for row in list_recent_triage_events(db_path, limit=fetch_limit):
        entries.append(
            AdminActivityLogEntry(
                source="audit_triage",
                occurred_at=row.created_at,
                event_type=f"audit.triage.{row.state}",
                workspace_id=row.workspace_id,
                actor_user_id=None,
                actor_label="System",
                subject_type="audit",
                subject_id=str(row.audit_id),
                details=row.reason or row.state,
                raw_id=f"audit_triage:{row.id}",
            )
        )

    entries.sort(key=lambda item: (item.occurred_at, item.raw_id), reverse=True)
    return entries


def _admin_log_entry_matches(entry: AdminActivityLogEntry, *, event_type: str, workspace: str, actor: str, from_ts: float | None, to_ts: float | None, query: str) -> bool:
    if event_type and entry.event_type != event_type:
        return False
    if workspace:
        workspace_value = "global" if entry.workspace_id is None else str(entry.workspace_id)
        if workspace_value != workspace:
            return False
    if actor and entry.actor_label != actor:
        return False
    if from_ts is not None and entry.occurred_at < from_ts:
        return False
    if to_ts is not None and entry.occurred_at > to_ts:
        return False
    if query:
        haystack = " ".join(
            filter(
                None,
                [
                    entry.source,
                    entry.event_type,
                    str(entry.workspace_id) if entry.workspace_id is not None else "global",
                    entry.actor_label,
                    entry.subject_type,
                    entry.subject_id,
                    entry.details,
                ],
            )
        ).lower()
        if query not in haystack:
            return False
    return True


def _build_admin_logs_view(request: Request, *, admin_rows: list[dict[str, object]]) -> dict[str, object]:
    event_type = (request.query_params.get("event_type") or "").strip()
    workspace = (request.query_params.get("workspace") or "").strip().lower()
    actor = (request.query_params.get("actor") or "").strip()
    from_date = (request.query_params.get("from_date") or "").strip()
    to_date = (request.query_params.get("to_date") or "").strip()
    query = (request.query_params.get("query") or "").strip()
    try:
        page = max(int(request.query_params.get("page") or "1"), 1)
    except ValueError:
        page = 1

    from_ts = _parse_admin_log_date(from_date)
    to_ts = _parse_admin_log_date(to_date, inclusive_end=True)
    fetch_limit = max(250, page * _ADMIN_LOG_PAGE_SIZE * 4)

    user_labels = {
        int(row.get("user_id") or 0): str(row.get("user_display_name") or f"User #{int(row.get('user_id') or 0)}")
        for row in admin_rows
        if row.get("user_id") is not None
    }
    workspace_labels = {
        int(row.get("workspace_id") or 0): str(row.get("workspace_display_name") or f"Workspace #{int(row.get('workspace_id') or 0)}")
        for row in admin_rows
        if row.get("workspace_id") is not None
    }

    entries = _build_admin_activity_entries(db_path=AUDIT_DB_PATH, user_labels=user_labels, fetch_limit=fetch_limit)
    filtered = [
        entry
        for entry in entries
        if _admin_log_entry_matches(
            entry,
            event_type=event_type,
            workspace=workspace,
            actor=actor,
            from_ts=from_ts,
            to_ts=to_ts,
            query=query.lower(),
        )
    ]

    start = (page - 1) * _ADMIN_LOG_PAGE_SIZE
    end = start + _ADMIN_LOG_PAGE_SIZE
    page_entries = filtered[start:end]

    base_params = {"tab": "logs"}
    if event_type:
        base_params["event_type"] = event_type
    if workspace:
        base_params["workspace"] = workspace
    if actor:
        base_params["actor"] = actor
    if from_date:
        base_params["from_date"] = from_date
    if to_date:
        base_params["to_date"] = to_date
    if query:
        base_params["query"] = query

    def _page_href(target_page: int) -> str:
        return f"/app/admin?{urlencode({**base_params, 'page': target_page})}"

    return {
        "rows": [
            {
                "occurred_at": entry.occurred_at,
                "source": entry.source.replace("_", " ").title(),
                "event_type": entry.event_type,
                "workspace_label": "Global" if entry.workspace_id is None else workspace_labels.get(entry.workspace_id, f"Workspace #{entry.workspace_id}"),
                "actor_label": entry.actor_label,
                "subject": f"{entry.subject_type}:{entry.subject_id}",
                "details": entry.details,
            }
            for entry in page_entries
        ],
        "filters": {
            "event_type": event_type,
            "workspace": workspace,
            "actor": actor,
            "from_date": from_date,
            "to_date": to_date,
            "query": query,
        },
        "event_options": sorted({entry.event_type for entry in entries}),
        "workspace_options": [{"value": "global", "label": "Global"}] + [
            {"value": str(workspace_id), "label": workspace_labels[workspace_id]}
            for workspace_id in sorted(workspace_labels)
        ],
        "actor_options": sorted({entry.actor_label for entry in entries}),
        "result_count": len(filtered),
        "page": page,
        "has_prev": page > 1,
        "has_next": end < len(filtered),
        "prev_href": _page_href(page - 1) if page > 1 else None,
        "next_href": _page_href(page + 1) if end < len(filtered) else None,
    }


def _normalize_nonempty_text(value: str | None, *, field_name: str, max_length: int) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} cannot be empty.")
    if len(normalized) > max_length:
        raise HTTPException(status_code=400, detail=f"{field_name} must be {max_length} characters or fewer.")
    return normalized


def _normalize_optional_email(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _normalize_workspace_slug(value: str | None, display_name: str) -> str:
    base = value if value and value.strip() else display_name
    normalized = re.sub(r"[^a-z0-9]+", "-", base.strip().lower()).strip("-") or "workspace"
    if len(normalized) > 120:
        raise HTTPException(status_code=400, detail="Workspace slug must be 120 characters or fewer.")
    return normalized


def _github_oauth_callback_url(request: Request) -> str:
    if settings.github_oauth_callback_url:
        return settings.github_oauth_callback_url
    return str(request.url_for("github_auth_callback"))


def _current_workspace_context(request: Request, *, allow_local_debug: bool = False) -> dict[str, object]:
    return current_workspace_access_context(
        settings,
        AUDIT_DB_PATH,
        request,
        allow_local_debug=allow_local_debug,
        local_debug_context_factory=_local_debug_workspace_context,
    )


def _current_authenticated_identity_context(request: Request) -> dict[str, object]:
    return current_workspace_authenticated_identity_context(settings, AUDIT_DB_PATH, request)


def _require_dashboard_access(request: Request, *, allow_local_debug: bool = False) -> dict[str, object]:
    return require_workspace_dashboard_access(
        settings,
        AUDIT_DB_PATH,
        request,
        allow_local_debug=allow_local_debug,
        local_debug_context_factory=_local_debug_workspace_context,
    )


def _require_dashboard_read_access(request: Request, *, allow_local_debug: bool = False) -> dict[str, object]:
    return _require_dashboard_access(request, allow_local_debug=allow_local_debug)


def _current_theme_preference(request: Request) -> str:
    if not _control_plane_active():
        return "dark"
    session = _get_session(request)
    if session is None:
        return "dark"
    user = get_user_by_id(AUDIT_DB_PATH, session.user_id)
    return user.theme_preference if user else "dark"


def _sidebar_profile_initial(*, display_name: str | None = None, github_login: str | None = None) -> str:
    for candidate in (display_name, github_login):
        value = (candidate or "").strip()
        if not value:
            continue
        for character in value:
            if character.isalnum():
                return character.upper()
    return "V"


def _workspace_repo_rows(workspace_id: int, *, pr_feedback_allowed: bool = True) -> list[dict[str, object]]:
    workspace = get_workspace_by_id(AUDIT_DB_PATH, workspace_id)
    workspace_mode = workspace.pr_feedback_mode if workspace is not None else "comments"
    connections = list_repo_connections_for_workspace(AUDIT_DB_PATH, workspace_id)
    allocations = {
        allocation.repo_full: allocation
        for allocation in list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace_id)
        if allocation.allocation_status in {"active", "onboarded"}
    }
    rows: list[dict[str, object]] = []
    seen_repo_fulls: set[str] = set()
    for connection in connections:
        allocation = allocations.get(connection.repo_full)
        status = "Available"
        if allocation is not None:
            status = "Onboarded" if allocation.allocation_status == "onboarded" else "Allocated"
        effective_mode_label = {
            "comments": "Comments",
            "reviews": "Reviews",
            "off": "Off",
        }.get(resolve_pr_feedback_mode(workspace_mode, allocation.pr_feedback_mode if allocation is not None else None), "Comments")
        if not pr_feedback_allowed:
            effective_mode_label = "Off (plan gated)"
        rows.append(
            {
                "repo_full": connection.repo_full,
                "status": status,
                "branch": connection.default_branch or "unknown",
                "visibility": "Private" if connection.is_private else "Public",
                "href": f"/dashboard/{quote(connection.repo_full, safe='')}",
                "allocation_id": allocation.id if allocation is not None else None,
                "repo_feedback_mode_override": allocation.pr_feedback_mode if allocation is not None else None,
                "repo_feedback_mode_override_label": (
                    {"comments": "Comments only", "reviews": "Formal reviews", "off": "Off"}.get(allocation.pr_feedback_mode, "Inherit workspace default")
                    if allocation is not None
                    else "Not allocated"
                ),
                "effective_feedback_mode_label": effective_mode_label,
            }
        )
        seen_repo_fulls.add(connection.repo_full)

    for repo_full, allocation in allocations.items():
        if repo_full in seen_repo_fulls:
            continue
        effective_mode_label = {
            "comments": "Comments",
            "reviews": "Reviews",
            "off": "Off",
        }.get(resolve_pr_feedback_mode(workspace_mode, allocation.pr_feedback_mode), "Comments")
        if not pr_feedback_allowed:
            effective_mode_label = "Off (plan gated)"
        rows.append(
            {
                "repo_full": repo_full,
                "status": "Onboarded" if allocation.allocation_status == "onboarded" else "Allocated",
                "branch": "unknown",
                "visibility": "Unknown",
                "href": f"/dashboard/{quote(repo_full, safe='')}",
                "allocation_id": allocation.id,
                "repo_feedback_mode_override": allocation.pr_feedback_mode,
                "repo_feedback_mode_override_label": {"comments": "Comments only", "reviews": "Formal reviews", "off": "Off"}.get(allocation.pr_feedback_mode, "Inherit workspace default"),
                "effective_feedback_mode_label": effective_mode_label,
            }
        )

    return sorted(rows, key=lambda item: str(item["repo_full"]).lower())


def _workspace_member_rows(workspace_id: int) -> list[dict[str, object]]:
    rows = [row for row in list_admin_workspace_users(AUDIT_DB_PATH) if row.workspace_id == workspace_id]
    member_rows = [
        {
            "display_name": row.user_display_name,
            "github_login": row.github_login,
            "role": row.membership_role,
            "state": "Accepted",
        }
        for row in rows
    ]
    for invite in list_workspace_invites_for_workspace(AUDIT_DB_PATH, workspace_id):
        member_rows.append(
            {
                "display_name": "Pending invite",
                "github_login": invite.invited_github_login,
                "role": invite.role,
                "state": "Pending",
            }
        )
    return sorted(member_rows, key=lambda item: (str(item["state"]).lower(), str(item["github_login"]).lower()))


def _github_account_repo_inventory(access_context: dict[str, object]) -> list[dict[str, object]]:
    workspace = access_context.get("workspace")
    installation = access_context.get("installation")
    user = access_context.get("user")
    if workspace is None:
        return []

    if installation is not None and settings.has_github_app_credentials:
        try:
            _installation_payload, repositories = sync_installation_repositories(
                app_id=settings.github_app_id,
                private_key_path=settings.github_private_key_path,
                private_key=settings.resolved_github_private_key,
                installation_id=installation.installation_id,
            )
            replace_repo_connections(
                AUDIT_DB_PATH,
                workspace_id=workspace.id,
                installation_id=installation.installation_id,
                repositories=repositories,
            )
        except (HTTPError, OSError, RuntimeError, ValueError, InvalidKeyError):
            pass

    connections = list_repo_connections_for_workspace(AUDIT_DB_PATH, workspace.id)
    allocations = list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace.id)
    connected_repo_fulls = {connection.repo_full for connection in connections}
    active_allocated_repo_fulls = {
        allocation.repo_full
        for allocation in allocations
        if allocation.allocation_status in {"active", "onboarded"}
    }
    inactive_allocated_repo_fulls = {
        allocation.repo_full
        for allocation in allocations
        if allocation.allocation_status == "inactive"
    }
    repo_scope_by_full = {repo_full: "connected_history" for repo_full in connected_repo_fulls}
    repo_scope_by_full.update({repo_full: "allocated" for repo_full in active_allocated_repo_fulls})
    allocation_status_by_full = {
        allocation.repo_full: allocation.allocation_status
        for allocation in allocations
        if allocation.allocation_status in {"active", "onboarded"}
    }
    visible_repo_fulls = connected_repo_fulls | active_allocated_repo_fulls
    onboarding_summary_by_full = {
        item.repo_full: asdict(item)
        for item in list_repo_dashboard_index(
            AUDIT_DB_PATH,
            allowed_repo_fulls=visible_repo_fulls,
            repo_scope_by_full=repo_scope_by_full,
            allocation_status_by_full=allocation_status_by_full,
        )
    }

    inventory_by_full: dict[str, dict[str, object]] = {}
    for connection in connections:
        repo_full = connection.repo_full.strip()
        if not repo_full:
            continue
        summary = onboarding_summary_by_full.get(repo_full)
        workspace_summary = (
            summary
            if summary is not None and repo_full in active_allocated_repo_fulls and str(summary.get("dashboard_scope") or "allocated") == "allocated"
            else None
        )
        inventory_by_full[repo_full.lower()] = {
            "repo_full": repo_full,
            "is_connected": repo_full in connected_repo_fulls,
            "is_allocated": repo_full in active_allocated_repo_fulls,
            "is_onboarded": workspace_summary is not None,
            "onboarding_status": workspace_summary.get("onboarding_status") if workspace_summary is not None else None,
            "can_restore": repo_full in inactive_allocated_repo_fulls and repo_full in connected_repo_fulls,
        }

    if user is not None:
        identity = get_github_identity_for_user(AUDIT_DB_PATH, user.id)
        access_token_encrypted = identity.access_token_encrypted if identity is not None else None
        if access_token_encrypted:
            try:
                access_token = decrypt_text(access_token_encrypted, settings.app_encryption_key)
                for repository in list_github_user_repositories(access_token):
                    repo_full = repository.full_name.strip()
                    if not repo_full:
                        continue
                    summary = onboarding_summary_by_full.get(repo_full)
                    workspace_summary = (
                        summary
                        if summary is not None and repo_full in active_allocated_repo_fulls and str(summary.get("dashboard_scope") or "allocated") == "allocated"
                        else None
                    )
                    inventory_by_full.setdefault(
                        repo_full.lower(),
                        {
                            "repo_full": repo_full,
                            "is_connected": repo_full in connected_repo_fulls,
                            "is_allocated": repo_full in active_allocated_repo_fulls,
                            "is_onboarded": workspace_summary is not None,
                            "onboarding_status": workspace_summary.get("onboarding_status") if workspace_summary is not None else None,
                            "can_restore": repo_full in inactive_allocated_repo_fulls and repo_full in connected_repo_fulls,
                        },
                    )
            except (HTTPError, OSError, RuntimeError, ValueError):
                pass

    return sorted(inventory_by_full.values(), key=lambda item: str(item.get("repo_full") or "").lower())


def _require_repo_dashboard_read_access(request: Request, repo_full: str) -> dict[str, object]:
    access_context = _require_dashboard_read_access(request)
    workspace = access_context["workspace"]
    allocation = get_repo_allocation_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    if allocation is not None and allocation.allocation_status in {"active", "onboarded"}:
        return {**access_context, "dashboard_repo_scope": "allocated", "dashboard_repo_allocation_status": allocation.allocation_status, "repo_installation_id": allocation.installation_id}
    connection = get_repo_connection_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    onboarding = get_latest_repository_onboarding(AUDIT_DB_PATH, repo_full)
    if connection is not None and connection.status == "available" and onboarding is not None:
        return {**access_context, "dashboard_repo_scope": "connected_history", "dashboard_repo_allocation_status": None, "repo_installation_id": connection.installation_id}
    raise HTTPException(status_code=404, detail="Repository is not visible in this workspace dashboard.")


def _require_repo_dashboard_mutation_access(request: Request, repo_full: str) -> dict[str, object]:
    access_context = _require_dashboard_access(request)
    workspace = access_context["workspace"]
    allocation = get_repo_allocation_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    if allocation is not None and allocation.allocation_status in {"active", "onboarded"}:
        return {**access_context, "dashboard_repo_scope": "allocated", "dashboard_repo_allocation_status": allocation.allocation_status, "repo_installation_id": allocation.installation_id}
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
    payload = {
        "id": job.id,
        "repo_full": job.repo_full,
        "from_ts": job.from_ts,
        "to_ts": job.to_ts,
        "workspace_id": job.workspace_id,
        "requested_by_user_id": job.requested_by_user_id,
        "requested_by_github_login": job.requested_by_github_login,
        "export_mode": job.export_mode,
        "include_artifact_content": job.include_artifact_content,
        "export_version": job.export_version,
        "status": job.status,
        "attempt_count": job.attempt_count,
        "next_attempt_at": job.next_attempt_at,
        "last_error": job.last_error,
        "download_token": job.download_token,
        "result_size_bytes": job.result_size_bytes,
        "result_sha256": job.result_sha256,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
    }
    payload["download_url"] = _export_download_url(job) if job.status == "completed" and job.result_blob else None
    return payload


def _build_export_download_response(_db_path: str, job, token: str | None):
    if not token or not job.download_token or not hmac.compare_digest(token, job.download_token):
        raise HTTPException(status_code=404, detail="Export job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Export job not completed")
    if not job.result_size_bytes or not job.download_token or not job.result_blob:
        raise HTTPException(status_code=400, detail="Export job missing download data")

    filename = (
        f"promptdrift-{job.export_mode.replace('_', '-')}-export-"
        f"{job.repo_full.replace('/', '-')}-"
        f"{datetime.fromtimestamp(job.from_ts).strftime('%Y-%m-%d')}-to-"
        f"{datetime.fromtimestamp(job.to_ts).strftime('%Y-%m-%d')}.zip"
    )
    return StreamingResponse(
        io.BytesIO(job.result_blob),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _render_compliance_repo_rows(repo_rows: list[dict[str, object]]) -> str:
    if not repo_rows:
        return '<div class="control-page-empty">No repositories are connected to this workspace yet.</div>'
    rendered: list[str] = []
    for repo in repo_rows:
        repo_full = str(repo.get("repo_full") or "")
        if not repo_full:
            continue
        status = str(repo.get("status") or "Unknown")
        branch = str(repo.get("branch") or "unknown")
        href = str(repo.get("href") or "#")
        onboarding = get_latest_repository_onboarding(AUDIT_DB_PATH, repo_full)
        artifact_families = _compliance_repo_artifact_families(repo_full) if onboarding is not None else set()
        freshness_label, freshness_chip_class, _freshness_guidance = _evidence_freshness_label(
            onboarding.updated_at if onboarding is not None else None
        )
        is_review_ready = onboarding is not None and str(onboarding.status or "").lower() == "baseline_approved" and "governance" in artifact_families
        is_fresh_review_ready = is_review_ready and freshness_label.startswith("Fresh")

        eligibility_chips: list[str] = []
        if is_review_ready:
            eligibility_chips.append('<span class="drift-chip chip-guardrails">Review-ready preset</span>')
        else:
            eligibility_chips.append('<span class="drift-chip chip-baseline">Not review-ready yet</span>')
        if is_fresh_review_ready:
            eligibility_chips.append('<span class="drift-chip chip-guardrails">Fresh review-ready preset</span>')
        elif freshness_label:
            eligibility_chips.append(f'<span class="drift-chip {freshness_chip_class}">{html.escape(freshness_label)}</span>')

        if onboarding is None:
            preset_reason = "No onboarding record exists yet, so server-side presets will not include this repo."
        elif str(onboarding.status or "").lower() != "baseline_approved":
            preset_reason = "Pending baseline approval keeps this repo out of review-ready presets until human review is complete."
        elif "governance" not in artifact_families:
            preset_reason = "A governance or policy artifact is still missing, so the stricter preset excludes this repo."
        elif not freshness_label.startswith("Fresh"):
            preset_reason = "This repo qualifies for the review-ready preset, but not the fresh review-ready preset yet."
        else:
            preset_reason = "This repo qualifies for both secure review-ready presets based on current workspace evidence."

        rendered.append(
            f'''
            <label class="compliance-repo-row">
                <input type="checkbox" name="repo_fulls" value="{html.escape(repo_full)}" />
                <div class="compliance-repo-main">
                    <div class="compliance-repo-copy">
                        <strong>{html.escape(repo_full)}</strong>
                        <span>{html.escape(status)} · default branch {html.escape(branch)}</span>
                        <div class="tag-row">{"".join(eligibility_chips)}</div>
                        <span>{html.escape(preset_reason)}</span>
                    </div>
                    <a class="subtle-link" href="{html.escape(href)}">Open audit page</a>
                </div>
            </label>
            '''
        )
    return "".join(rendered)


def _render_compliance_export_history(jobs: list[object]) -> str:
    if not jobs:
        return '<div class="control-page-empty">No compliance exports have been generated for this workspace session yet.</div>'
    headers = ["Repository", "Mode", "Date range", "Status", "Download"]
    head_html = "".join(f"<th>{header}</th>" for header in headers)
    rows = []
    for job in jobs:
        range_label = (
            f"{datetime.fromtimestamp(job.from_ts).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(job.to_ts).strftime('%Y-%m-%d')}"
        )
        download_cell = (
            f'<a class="link" href="{html.escape(_export_download_url(job) or "#")}">Download</a>'
            if job.status == "completed" and job.result_blob
            else html.escape(job.status.replace("_", " ").title())
        )
        rows.append(
            "<tr>"
            + "".join(
                (
                    f"<td>{html.escape(job.repo_full)}</td>",
                    f"<td>{html.escape(job.export_mode.replace('_', ' ').title())}</td>",
                    f"<td>{html.escape(range_label)}</td>",
                    f"<td>{html.escape(job.status.replace('_', ' ').title())}</td>",
                    f"<td>{download_cell}</td>",
                )
            )
            + "</tr>"
        )
    return f'<div class="table-shell"><table class="data-table"><thead><tr>{head_html}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _render_compliance_ai_act_assessment(repo_summaries: list[object]) -> str:
    if not repo_summaries:
        return '<div class="control-page-empty">No onboarded repositories are available for AI Act relevance assessment yet.</div>'

    repos_with_ai_surfaces = 0
    repos_with_tool_surfaces = 0
    repos_with_model_surfaces = 0
    repos_with_governance_surfaces = 0
    rendered_cards: list[str] = []

    for summary in sorted(repo_summaries, key=lambda item: str(item.repo_full).lower()):
        onboarding = get_latest_repository_onboarding(AUDIT_DB_PATH, summary.repo_full)
        if onboarding is None:
            continue
        artifact_families = {
            artifact_family(artifact.artifact_type)
            for artifact in list_onboarded_artifacts_for_onboarding(AUDIT_DB_PATH, onboarding.id)
        }
        if not artifact_families:
            continue

        has_ai_surface = bool(artifact_families & {"prompt", "tool", "model", "config"})
        has_tool_surface = "tool" in artifact_families
        has_model_surface = bool(artifact_families & {"model", "config"})
        has_governance_surface = "governance" in artifact_families

        repos_with_ai_surfaces += int(has_ai_surface)
        repos_with_tool_surfaces += int(has_tool_surface)
        repos_with_model_surfaces += int(has_model_surface)
        repos_with_governance_surfaces += int(has_governance_surface)

        chips: list[str] = []
        if "prompt" in artifact_families:
            chips.append('<span class="drift-chip chip-capability">AI control surface</span>')
        if has_tool_surface:
            chips.append('<span class="drift-chip chip-model">AI-assisted tool surface</span>')
        if has_model_surface:
            chips.append('<span class="drift-chip chip-baseline">Model/config surface</span>')
        if has_governance_surface:
            chips.append('<span class="drift-chip chip-governance">Governance surface</span>')

        if str(summary.onboarding_status or "").lower() == "baseline_approved":
            chips.append('<span class="drift-chip chip-guardrails">Human-reviewed baseline</span>')
            oversight_copy = "Reviewed baseline and stored control-surface evidence are present for this repository."
        else:
            chips.append('<span class="drift-chip chip-baseline">Baseline review pending</span>')
            oversight_copy = "Control-surface evidence is present, but baseline review is not yet fully approved."

        rendered_cards.append(
            f'''
            <article class="compliance-assessment-card">
                <div class="compliance-assessment-head">
                    <strong>{html.escape(summary.repo_full)}</strong>
                    <a class="subtle-link" href="/dashboard/{quote(summary.repo_full, safe='')}">Open audit page</a>
                </div>
                <div class="tag-row">{"".join(chips)}</div>
                <p>{html.escape(oversight_copy)}</p>
            </article>
            '''
        )

    summary_cards = [
        ("Repos with AI surfaces", repos_with_ai_surfaces, "Prompt, tool, model, or config surfaces were found in persisted onboarding artifacts."),
        ("Repos with tool surfaces", repos_with_tool_surfaces, "Tool-linked artifacts suggest action-taking or integrated AI tooling surfaces to review."),
        ("Repos with model/config surfaces", repos_with_model_surfaces, "Model selection and behavior-shaping config artifacts are present."),
        ("Repos with governance surfaces", repos_with_governance_surfaces, "Policy, guardrail, or governance artifacts were detected in the stored baseline."),
    ]
    summary_html = "".join(
        f'''
        <article class="control-page-stat-card">
            <span class="control-page-stat-label">{html.escape(label)}</span>
            <strong>{value}</strong>
            <span class="control-page-microcopy">{html.escape(detail)}</span>
        </article>
        '''
        for label, value, detail in summary_cards
    )
    cards_html = "".join(rendered_cards) or '<div class="control-page-empty">Stored onboarding evidence was found, but no artifact families were available for assessment.</div>'
    return f'<div class="control-page-stat-grid">{summary_html}</div><div class="compliance-assessment-grid">{cards_html}</div>'


def _render_compliance_evidence_gaps(repo_summaries: list[object]) -> str:
    if not repo_summaries:
        return '<div class="control-page-empty">No onboarded repositories are available for evidence-gap review yet.</div>'

    repos_needing_baseline_review = 0
    repos_missing_governance = 0
    repos_missing_model_config = 0
    repos_ready_for_review_pack = 0
    rendered_cards: list[str] = []

    for summary in sorted(repo_summaries, key=lambda item: str(item.repo_full).lower()):
        onboarding = get_latest_repository_onboarding(AUDIT_DB_PATH, summary.repo_full)
        if onboarding is None:
            continue
        artifact_families = {
            artifact_family(artifact.artifact_type)
            for artifact in list_onboarded_artifacts_for_onboarding(AUDIT_DB_PATH, onboarding.id)
        }
        if not artifact_families:
            continue

        missing_governance = "governance" not in artifact_families
        missing_model_config = not bool(artifact_families & {"model", "config"})
        needs_baseline_review = str(summary.onboarding_status or "").lower() != "baseline_approved"

        repos_needing_baseline_review += int(needs_baseline_review)
        repos_missing_governance += int(missing_governance)
        repos_missing_model_config += int(missing_model_config)
        repos_ready_for_review_pack += int(not needs_baseline_review and not missing_governance)

        gaps: list[str] = []
        if needs_baseline_review:
            gaps.append("Baseline still needs human approval")
        if missing_governance:
            gaps.append("No governance or policy artifact detected")
        if missing_model_config:
            gaps.append("No model/config artifact detected")
        if not gaps:
            gaps.append("No immediate evidence gaps detected from stored onboarding artifacts")

        if needs_baseline_review:
            recommended_action = "Approve or reject the pending baseline so the repo can become a stable review reference."
        elif missing_governance:
            recommended_action = "Add a policy, guardrail, or governance artifact so oversight evidence is packaged with the repo."
        elif missing_model_config:
            recommended_action = "Capture the model or behavior-shaping config artifact to complete the repo evidence set."
        else:
            recommended_action = "Use this repo in compliance export runs as a stronger evidence candidate."

        rendered_cards.append(
            f'''
            <article class="compliance-assessment-card">
                <div class="compliance-assessment-head">
                    <strong>{html.escape(summary.repo_full)}</strong>
                    <a class="subtle-link" href="/dashboard/{quote(summary.repo_full, safe='')}">Open audit page</a>
                </div>
                <div class="stack compact-stack">
                    <div>
                        <div class="detail-section-label">Evidence gaps</div>
                        <div class="tag-row">{"".join(f'<span class="drift-chip chip-baseline">{html.escape(gap)}</span>' for gap in gaps)}</div>
                    </div>
                    <div>
                        <div class="detail-section-label">Recommended next action</div>
                        <p>{html.escape(recommended_action)}</p>
                    </div>
                </div>
            </article>
            '''
        )

    summary_cards = [
        ("Repos needing baseline approval", repos_needing_baseline_review, "Baseline approval is still pending, so the stored evidence is not yet a stable review reference."),
        ("Repos missing governance artifacts", repos_missing_governance, "No policy, guardrail, or governance artifact was detected in the stored onboarding baseline."),
        ("Repos missing model/config artifacts", repos_missing_model_config, "No explicit model selection or behavior-shaping config artifact was detected."),
        ("Repos ready for review packs", repos_ready_for_review_pack, "Approved baselines with governance evidence are the strongest candidates for compliance export workflows."),
    ]
    summary_html = "".join(
        f'''
        <article class="control-page-stat-card">
            <span class="control-page-stat-label">{html.escape(label)}</span>
            <strong>{value}</strong>
            <span class="control-page-microcopy">{html.escape(detail)}</span>
        </article>
        '''
        for label, value, detail in summary_cards
    )
    cards_html = "".join(rendered_cards) or '<div class="control-page-empty">Stored onboarding evidence was found, but no repo-level gaps could be summarized.</div>'
    return f'<div class="control-page-stat-grid">{summary_html}</div><div class="compliance-assessment-grid">{cards_html}</div>'


def _evidence_freshness_label(last_onboarded_at: float | None) -> tuple[str, str, str]:
    if not isinstance(last_onboarded_at, (int, float)) or last_onboarded_at <= 0:
        return (
            "No freshness signal",
            "chip-baseline",
            "No onboarding timestamp is available, so evidence freshness cannot be assessed.",
        )
    age_days = max(0, int((time.time() - float(last_onboarded_at)) // 86400))
    if age_days >= 30:
        return (
            f"Stale evidence ({age_days}d)",
            "chip-baseline",
            "Re-run onboarding before relying on this repo in a governance review pack.",
        )
    if age_days >= 7:
        return (
            f"Aging evidence ({age_days}d)",
            "chip-model",
            "Evidence is still usable, but a refresh is worth scheduling soon.",
        )
    return (
        f"Fresh evidence ({age_days}d)",
        "chip-guardrails",
        "Stored onboarding evidence is recent enough for current governance follow-up.",
    )


def _render_compliance_evidence_freshness(repo_summaries: list[object]) -> str:
    if not repo_summaries:
        return '<div class="control-page-empty">No onboarded repositories are available for evidence freshness review yet.</div>'

    stale_count = 0
    aging_count = 0
    fresh_count = 0
    rendered_cards: list[str] = []

    for summary in sorted(repo_summaries, key=lambda item: str(item.repo_full).lower()):
        label, chip_class, guidance = _evidence_freshness_label(getattr(summary, "last_onboarded_at", None))
        if label.startswith("Stale"):
            stale_count += 1
        elif label.startswith("Aging"):
            aging_count += 1
        elif label.startswith("Fresh"):
            fresh_count += 1

        last_onboarded_at = getattr(summary, "last_onboarded_at", None)
        if isinstance(last_onboarded_at, (int, float)) and last_onboarded_at > 0:
            last_seen = datetime.fromtimestamp(last_onboarded_at).strftime("%Y-%m-%d")
        else:
            last_seen = "Unavailable"

        rendered_cards.append(
            f'''
            <article class="compliance-assessment-card">
                <div class="compliance-assessment-head">
                    <strong>{html.escape(summary.repo_full)}</strong>
                    <span class="drift-chip {chip_class}">{html.escape(label)}</span>
                </div>
                <div class="stack compact-stack">
                    <div class="detail-section-label">Last onboarded</div>
                    <p>{html.escape(last_seen)}</p>
                    <div class="detail-section-label">Follow-up</div>
                    <p>{html.escape(guidance)}</p>
                </div>
            </article>
            '''
        )

    summary_cards = [
        ("Fresh repos", fresh_count, "Evidence refreshed within the last 7 days."),
        ("Aging repos", aging_count, "Evidence is 7 to 29 days old and may need a scheduled refresh."),
        ("Stale repos", stale_count, "Evidence is 30 or more days old and should be refreshed before formal review."),
    ]
    summary_html = "".join(
        f'''
        <article class="control-page-stat-card">
            <span class="control-page-stat-label">{html.escape(label)}</span>
            <strong>{value}</strong>
            <span class="control-page-microcopy">{html.escape(detail)}</span>
        </article>
        '''
        for label, value, detail in summary_cards
    )
    return f'<div class="control-page-stat-grid">{summary_html}</div><div class="compliance-assessment-grid">{"".join(rendered_cards)}</div>'


def _compliance_repo_artifact_families(repo_full: str) -> set[str]:
    onboarding = get_latest_repository_onboarding(AUDIT_DB_PATH, repo_full)
    if onboarding is None:
        return set()
    return {
        artifact_family(artifact.artifact_type)
        for artifact in list_onboarded_artifacts_for_onboarding(AUDIT_DB_PATH, onboarding.id)
    }


def _compliance_export_preset_repo_fulls(visible_repo_fulls: set[str], export_preset: str) -> list[str]:
    selected_repo_fulls: list[str] = []
    for repo_full in sorted(visible_repo_fulls):
        onboarding = get_latest_repository_onboarding(AUDIT_DB_PATH, repo_full)
        if onboarding is None:
            continue
        artifact_families = _compliance_repo_artifact_families(repo_full)
        if not artifact_families:
            continue

        needs_baseline_review = str(onboarding.status or "").lower() != "baseline_approved"
        missing_governance = "governance" not in artifact_families
        freshness_label, _, _ = _evidence_freshness_label(onboarding.updated_at)

        if export_preset == "review_ready" and not needs_baseline_review and not missing_governance:
            selected_repo_fulls.append(repo_full)
        if export_preset == "fresh_review_ready" and not needs_baseline_review and not missing_governance and freshness_label.startswith("Fresh"):
            selected_repo_fulls.append(repo_full)
    return selected_repo_fulls


def _user_safe_compliance_export_error(exc: Exception) -> str:
    detail = " ".join(str(exc).split()).lower()
    if "no onboarding found" in detail:
        return "No onboarding evidence is available for this repository yet."
    return "Export generation failed. Retry after checking onboarding and evidence coverage."


_AI_SYSTEM_ALLOWED_RISK_LEVELS = {
    "unclassified",
    "minimal-risk",
    "limited-risk",
    "high-risk",
    "prohibited",
}

_AI_SYSTEM_ALLOWED_DOMAINS = {
    None,
    "general_purpose",
    "employment",
    "education",
    "essential_services",
    "biometric",
    "law_enforcement",
    "internal_productivity",
}


def _normalize_ai_system_classification(
    *,
    risk_level: str,
    eu_ai_act_domain: str | None,
    purpose_summary: str | None,
) -> tuple[str, str | None, str | None]:
    normalized_risk = (risk_level or "").strip().lower() or "unclassified"
    if normalized_risk not in _AI_SYSTEM_ALLOWED_RISK_LEVELS:
        raise HTTPException(status_code=400, detail="Choose a valid risk classification.")

    normalized_domain = (eu_ai_act_domain or "").strip().lower() or None
    if normalized_domain not in _AI_SYSTEM_ALLOWED_DOMAINS:
        raise HTTPException(status_code=400, detail="Choose a valid AI system domain.")

    normalized_purpose = (purpose_summary or "").strip() or None
    if normalized_purpose is not None and len(normalized_purpose) > 280:
        raise HTTPException(status_code=400, detail="System purpose must be 280 characters or fewer.")

    return normalized_risk, normalized_domain, normalized_purpose


def _run_compliance_export_job(
    *,
    repo_full: str,
    from_ts: float,
    to_ts: float,
    export_mode: str,
    include_artifact_content: bool,
    workspace_id: int | None,
    requested_by_user_id: int | None,
    requested_by_github_login: str | None,
):
    ai_system = (
        get_ai_system_for_workspace_repo(AUDIT_DB_PATH, workspace_id, repo_full)
        if workspace_id is not None
        else None
    )
    if ai_system is None:
        ai_system_provenance_label = "No registry entry"
        ai_system_review_detail = "Last review: Not yet reviewed"
        ai_system_risk_level = None
        ai_system_eu_ai_act_domain = None
        ai_system_purpose_summary = None
    elif ai_system.last_reviewed_at:
        ai_system_provenance_label = "Reviewer confirmed"
        ai_system_review_detail = f"Last review: {datetime.fromtimestamp(ai_system.last_reviewed_at).strftime('%Y-%m-%d %H:%M UTC')}"
        ai_system_risk_level = ai_system.risk_level
        ai_system_eu_ai_act_domain = ai_system.eu_ai_act_domain
        ai_system_purpose_summary = ai_system.purpose_summary
    else:
        ai_system_provenance_label = "Auto-prefilled from repository evidence"
        ai_system_review_detail = "Last review: Not yet reviewed"
        ai_system_risk_level = ai_system.risk_level
        ai_system_eu_ai_act_domain = ai_system.eu_ai_act_domain
        ai_system_purpose_summary = ai_system.purpose_summary
    job = create_export_job(
        AUDIT_DB_PATH,
        repo_full=repo_full,
        from_ts=from_ts,
        to_ts=to_ts,
        export_mode=export_mode,
        include_artifact_content=include_artifact_content,
        workspace_id=workspace_id,
        requested_by_user_id=requested_by_user_id,
        requested_by_github_login=requested_by_github_login,
        ai_system_provenance_label=ai_system_provenance_label,
        ai_system_review_detail=ai_system_review_detail,
        ai_system_risk_level=ai_system_risk_level,
        ai_system_eu_ai_act_domain=ai_system_eu_ai_act_domain,
        ai_system_purpose_summary=ai_system_purpose_summary,
    )
    try:
        result = build_compliance_export(
            AUDIT_DB_PATH,
            ComplianceExportServiceRequest(
                repo_full=repo_full,
                from_ts=from_ts,
                to_ts=to_ts,
                export_mode=export_mode,
                include_artifact_content=include_artifact_content,
                export_version=job.export_version,
                workspace_id=workspace_id,
            ),
        )
    except Exception as exc:
        logger.exception("Compliance export job failed for %s", repo_full)
        update_export_job_status(AUDIT_DB_PATH, job.id, "failed", last_error=_user_safe_compliance_export_error(exc))
        raise
    update_export_job_status(
        AUDIT_DB_PATH,
        job.id,
        "completed",
        result_size_bytes=result.total_size_bytes,
        result_sha256=hashlib.sha256(result.zip_bytes).hexdigest(),
        result_blob=result.zip_bytes,
    )
    return get_export_job(AUDIT_DB_PATH, job.id) or job


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


def _is_owner_identity(user, identity) -> bool:
    if not settings.has_owner_access_config:
        return False
    checks: list[bool] = []
    if settings.owner_github_user_id.strip():
        checks.append(identity.github_user_id == settings.owner_github_user_id.strip())
    if settings.normalized_owner_github_login:
        checks.append(identity.github_login.lower() == settings.normalized_owner_github_login)
    if settings.normalized_owner_email:
        checks.append(bool(user.primary_email and user.primary_email.lower() == settings.normalized_owner_email))
    return bool(checks) and all(checks)


def _has_local_owner_fallback(user, workspace) -> bool:
    if settings.has_owner_access_config or settings.is_production:
        return False
    if user is None or workspace is None:
        return False
    if not settings.is_local:
        return False
    app_base_host = (urlparse(settings.app_base_url).hostname or "").strip().lower()
    if app_base_host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    return bool(getattr(workspace, "billing_owner_user_id", None) == user.id)


def _has_owner_admin_access(user, identity, workspace=None) -> bool:
    if user is None or identity is None:
        return False
    return _is_owner_identity(user, identity) or _has_local_owner_fallback(user, workspace)


def _identity_has_required_repo_scopes(identity) -> bool:
    granted_scopes = {str(scope).strip().lower() for scope in getattr(identity, "granted_scopes", []) if str(scope).strip()}
    return GITHUB_REQUIRED_REPO_SCOPES.issubset(granted_scopes)


def _require_owner_access(request: Request) -> dict[str, object]:
    context = _current_authenticated_identity_context(request)
    workspace = get_workspace_by_id(AUDIT_DB_PATH, context["session"].workspace_id) if context["session"].workspace_id else None
    if not _has_owner_admin_access(context["user"], context["identity"], workspace):
        raise HTTPException(status_code=403, detail="System owner access is not enabled for this GitHub identity.")
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


def _public_identity_payload(identity) -> dict[str, object] | None:
    if identity is None:
        return None
    return {
        "id": identity.id,
        "user_id": identity.user_id,
        "github_user_id": identity.github_user_id,
        "github_login": identity.github_login,
        "avatar_url": identity.avatar_url,
        "profile_url": identity.profile_url,
        "company": identity.company,
        "blog": identity.blog,
        "location": identity.location,
        "bio": identity.bio,
        "twitter_username": identity.twitter_username,
        "granted_scopes": list(identity.granted_scopes),
        "last_login_at": identity.last_login_at,
        "created_at": identity.created_at,
        "updated_at": identity.updated_at,
    }


def _trusted_workspace_installation_id(access_context: dict[str, object], requested_installation_id: int) -> int:
    installation = access_context.get("installation")
    if installation is None:
        raise HTTPException(status_code=400, detail="GitHub installation is required for this workspace.")
    trusted_installation_id = int(installation.installation_id)
    if trusted_installation_id != requested_installation_id:
        raise HTTPException(status_code=403, detail="Installation mismatch for workspace access.")
    return trusted_installation_id


@app.get("/", response_class=HTMLResponse)
async def marketing_page(request: Request):
    session = _get_session(request)
    destination = "/dashboard" if session is not None else "/login"
    return RedirectResponse(destination, status_code=303)


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
    pending_install = _pending_install_context_from_query(request)
    if existing_session is not None:
        existing_identity = get_github_identity_for_user(AUDIT_DB_PATH, existing_session.user_id)
        if existing_identity is not None and _identity_has_required_repo_scopes(existing_identity):
            response = RedirectResponse(_resume_destination_for_session(existing_session, flow_context), status_code=303)
            if pending_install:
                _set_context_cookie(
                    response,
                    CONTROL_PLANE_PENDING_INSTALL_COOKIE,
                    pending_install,
                    binding=_context_cookie_binding_for_session_id(existing_session.session_id),
                    max_age=1800,
                )
            return response
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
        _set_context_cookie(
            response,
            CONTROL_PLANE_OAUTH_CONTEXT_COOKIE,
            flow_context,
            binding=_context_cookie_binding_for_oauth_state(state),
            max_age=1800,
        )
    if pending_install:
        _set_context_cookie(
            response,
            CONTROL_PLANE_PENDING_INSTALL_COOKIE,
            pending_install,
            binding=_context_cookie_binding_for_oauth_state(state),
            max_age=1800,
        )
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
        profile_url=profile.profile_url,
        company=profile.company,
        blog=profile.blog,
        location=profile.location,
        bio=profile.bio,
        twitter_username=profile.twitter_username,
        granted_scopes=token.granted_scopes,
        access_token_encrypted=encrypted_token,
    )
    accept_workspace_invites_for_github_login(AUDIT_DB_PATH, user_id=user.id, github_login=profile.login)
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
    response.delete_cookie(CONTROL_PLANE_INSTALL_STATE_COOKIE)
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


@app.get("/workspace", response_class=HTMLResponse)
@app.get("/app", response_class=HTMLResponse)
async def control_plane_app_page_route(request: Request, state: str | None = None):
    session = _get_session(request)
    if session is None:
        return RedirectResponse("/login", status_code=303)
    access_context = _build_access_context(session)
    resolution = access_context["resolution"]
    if _has_profile_access(access_context):
        return RedirectResponse("/profile", status_code=303)

    destination_by_state = {
        "authenticated_no_workspace": "/workspaces/new",
        "workspace_no_subscription": "/billing",
        "billing_pending_confirmation": "/billing",
        "payment_failed": "/billing",
        "awaiting_github_install": "/setup/install",
        "awaiting_repo_onboarding": "/repos",
        "active_comments_only": "/repos",
        "canceled_active_until_period_end": "/billing",
        "expired_read_only": "/billing",
        "forbidden": "/dashboard",
    }
    return RedirectResponse(destination_by_state.get(resolution.state, "/login"), status_code=303)


@app.get("/profile", response_class=HTMLResponse)
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
            admin_url="/admin" if _has_owner_admin_access(user, identity, workspace) else None,
            csrf_token=access_context["session"].csrf_secret,
        )
    )


@app.post("/profile")
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
    return RedirectResponse("/profile?updated=1", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
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
    pr_feedback_allowed = _workspace_pr_comments_allowed_by_plan(access_context)
    return HTMLResponse(
        render_control_plane_settings_page(
            workspace_name=workspace.display_name,
            plan_label=get_plan_definition(plan_code).label,
            theme_preference=user.theme_preference if user else "dark",
            status_note=(
                "Invitation queued." if request.query_params.get("invite_added") else "Settings updated." if request.query_params.get("updated") else None
            ),
            resolution=access_context["resolution"],
            csrf_token=access_context["session"].csrf_secret,
            pr_comments_allowed_by_plan=pr_feedback_allowed,
            pr_feedback_mode=workspace.pr_feedback_mode,
            can_manage=bool(membership and membership.role in {"owner", "admin"}),
            workspace_role=membership.role if membership else "viewer",
            workspace_members=_workspace_member_rows(workspace.id),
            repo_rows=_workspace_repo_rows(workspace.id, pr_feedback_allowed=pr_feedback_allowed),
            next_payment_at=subscription.next_payment_at if subscription else None,
            subscription_status=subscription.status if subscription else None,
            setup_state=workspace.setup_state,
            installation_account_login=installation.account_login if installation else None,
            repo_limit=entitlement.repo_limit if entitlement else None,
            seat_limit=entitlement.seat_limit if entitlement else None,
            invite_enabled=bool(membership and membership.role in {"owner", "admin"}),
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=user.display_name if user else None,
                github_login=identity.github_login if identity else None,
            ),
        )
    )


@app.post("/settings")
@app.post("/app/settings")
async def settings_update(
    request: Request,
    pr_feedback_mode: str = Form(...),
    workspace_name: str | None = Form(default=None),
    csrf_token: str | None = Form(None),
):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")

    normalized_feedback_mode = (pr_feedback_mode or "").strip().lower()
    if normalized_feedback_mode not in {"comments", "reviews", "off"}:
        raise HTTPException(status_code=400, detail="PR feedback mode must be comments, reviews, or off.")

    normalized_workspace_name = (workspace_name or "").strip()
    if not normalized_workspace_name:
        raise HTTPException(status_code=400, detail="Workspace name cannot be empty.")
    if len(normalized_workspace_name) > 120:
        raise HTTPException(status_code=400, detail="Workspace name must be 120 characters or fewer.")

    update_workspace_pr_feedback_mode(
        AUDIT_DB_PATH,
        access_context["workspace"].id,
        pr_feedback_mode=normalized_feedback_mode,
    )
    update_workspace_pr_comments_setting(
        AUDIT_DB_PATH,
        access_context["workspace"].id,
        enabled=normalized_feedback_mode != "off",
    )
    update_workspace_display_name(
        AUDIT_DB_PATH,
        access_context["workspace"].id,
        display_name=normalized_workspace_name,
    )
    return RedirectResponse("/settings?updated=1", status_code=303)


@app.post("/settings/repositories/feedback-mode")
@app.post("/app/settings/repositories/feedback-mode")
async def settings_update_repo_feedback_mode(
    request: Request,
    allocation_id: int = Form(...),
    pr_feedback_mode: str | None = Form(default=None),
    csrf_token: str | None = Form(None),
):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")

    allocation = next(
        (candidate for candidate in list_repo_allocations_for_workspace(AUDIT_DB_PATH, access_context["workspace"].id) if candidate.id == allocation_id),
        None,
    )
    if allocation is None:
        raise HTTPException(status_code=404, detail="Repository allocation was not found for this workspace.")

    normalized_feedback_mode = (pr_feedback_mode or "").strip().lower()
    if normalized_feedback_mode in {"", "inherit"}:
        override_mode = None
    elif normalized_feedback_mode in {"comments", "reviews", "off"}:
        override_mode = normalized_feedback_mode
    else:
        raise HTTPException(status_code=400, detail="Repository feedback mode must be inherit, comments, reviews, or off.")

    update_repo_allocation_pr_feedback_mode(
        AUDIT_DB_PATH,
        allocation.id,
        pr_feedback_mode=override_mode,
    )
    return RedirectResponse("/settings?updated=1", status_code=303)


@app.post("/settings/invite")
@app.post("/app/settings/invite")
async def settings_invite_user(
    request: Request,
    github_login: str = Form(...),
    role: str = Form(...),
    csrf_token: str | None = Form(None),
):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")

    normalized_login = github_login.strip().lstrip("@").lower()
    if not normalized_login:
        raise HTTPException(status_code=400, detail="GitHub login is required.")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?", normalized_login):
        raise HTTPException(status_code=400, detail="GitHub login format is invalid.")
    normalized_role = (role or "").strip().lower()
    if normalized_role not in {"admin", "viewer"}:
        raise HTTPException(status_code=400, detail="Invited role must be edit or read.")

    identity = access_context.get("identity")
    if identity is not None and identity.github_login.lower() == normalized_login:
        raise HTTPException(status_code=400, detail="You are already in this workspace.")

    upsert_workspace_invite(
        AUDIT_DB_PATH,
        workspace_id=access_context["workspace"].id,
        invited_github_login=normalized_login,
        role=normalized_role,
        invited_by_user_id=access_context["session"].user_id,
    )
    return RedirectResponse("/settings?invite_added=1", status_code=303)


def _has_cp_api_access(access_context: dict[str, object]) -> bool:
    """Return True if the workspace is entitled to the CP API.

    In local environments always returns True so local development can run without
    feature-flag plumbing.  In production, checks ``cp_api_enabled`` in
    the entitlement's ``feature_flags_json``; absent key means True.
    """
    if settings.is_local:
        return True
    entitlement = access_context.get("entitlement")
    if entitlement is None:
        return True
    try:
        flags = json.loads(entitlement.feature_flags_json) if entitlement.feature_flags_json else {}
    except (ValueError, TypeError):
        flags = {}
    return flags.get("cp_api_enabled", True) is not False


# Scopes that customer self-service is NOT allowed to assign.
_ADMIN_SCOPES = {"admin.read", "admin.write"}
_CUSTOMER_ALLOWED_SCOPES = {"drift.read", "drift.write.low", "drift.write.high"}


@app.get("/settings/api-keys", response_class=HTMLResponse)
@app.get("/app/settings/api-keys", response_class=HTMLResponse)
async def api_keys_page(request: Request):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")
    return RedirectResponse("/integrations/mcp?tab=api-keys", status_code=303)


@app.post("/settings/api-keys")
@app.post("/app/settings/api-keys")
async def create_api_key(
    request: Request,
    display_name: str = Form(...),
    csrf_token: str | None = Form(None),
    scope_drift_read: str | None = Form(None),
    scope_drift_write_low: str | None = Form(None),
    scope_drift_write_high: str | None = Form(None),
):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")

    if not _has_cp_api_access(access_context):
        raise HTTPException(status_code=403, detail="Control plane API is not enabled for this workspace.")

    if not settings.has_encryption_key:
        raise HTTPException(status_code=503, detail="APP_ENCRYPTION_KEY must be configured.")

    workspace = access_context["workspace"]
    session = access_context["session"]

    # Validate name first so the user sees name errors before scope errors
    normalized_name = (display_name or "").strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Display name is required.")
    if len(normalized_name) > 120:
        raise HTTPException(status_code=400, detail="Display name must be 120 characters or fewer.")

    # Build scopes from submitted checkboxes (only allow customer-safe scopes)
    requested_scopes: list[str] = []
    if scope_drift_read:
        requested_scopes.append("drift.read")
    if scope_drift_write_low:
        requested_scopes.append("drift.write.low")
    if scope_drift_write_high:
        requested_scopes.append("drift.write.high")

    if not requested_scopes:
        raise HTTPException(status_code=400, detail="At least one scope must be selected.")

    # Block admin scopes — admin.* may only be assigned by operators
    forbidden = set(requested_scopes) & _ADMIN_SCOPES
    if forbidden:
        raise HTTPException(status_code=400, detail=f"Scopes not allowed in self-service: {sorted(forbidden)}.")

    # Enforce per-workspace principal limit
    count = count_machine_principals_for_workspace(AUDIT_DB_PATH, workspace.id)
    if count >= settings.cp_max_principals_per_workspace:
        raise HTTPException(
            status_code=409,
            detail=f"Workspace has reached the maximum of {settings.cp_max_principals_per_workspace} API keys.",
        )

    import uuid as _uuid
    client_id = str(_uuid.uuid4())
    raw_secret = secrets.token_urlsafe(32)
    encrypted_secret = encrypt_text(raw_secret, settings.app_encryption_key)

    # If the process crashes between the two, the principal is never silently
    # orphaned without the caller receiving the secret.
    principal = create_machine_principal_and_flash_secret(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        display_name=normalized_name,
        principal_kind="service_account",
        client_id=client_id,
        client_secret_encrypted=encrypted_secret,
        scopes=requested_scopes,
        created_by_user_id=session.user_id,
        session_id=session.session_id,
        raw_secret=raw_secret,
    )
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        actor_user_id=session.user_id,
        event_type="principal.created",
        subject_type="machine_principal",
        subject_id=principal.client_id,
        payload={"scopes": requested_scopes, "source": "self_service"},
    )

    return RedirectResponse("/integrations/mcp?tab=api-keys", status_code=303)


@app.post("/settings/api-keys/{client_id}/revoke")
@app.post("/app/settings/api-keys/{client_id}/revoke")
async def revoke_api_key(
    client_id: str,
    request: Request,
    csrf_token: str | None = Form(None),
):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")

    workspace = access_context["workspace"]
    session = access_context["session"]

    principal = get_machine_principal_by_client_id(AUDIT_DB_PATH, client_id)
    if principal is None or principal.workspace_id != workspace.id:
        raise HTTPException(status_code=403, detail="Not authorized.")

    revoke_machine_principal(AUDIT_DB_PATH, client_id)
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        actor_user_id=session.user_id,
        event_type="principal.revoked",
        subject_type="machine_principal",
        subject_id=client_id,
        payload={"source": "self_service"},
    )
    return RedirectResponse("/integrations/mcp?tab=api-keys", status_code=303)


@app.get("/integrations/mcp", response_class=HTMLResponse)
@app.get("/app/integrations/mcp", response_class=HTMLResponse)
async def mcp_integrations_page(request: Request):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")

    workspace = access_context["workspace"]
    user = access_context["user"]
    identity = access_context["identity"]
    session = access_context["session"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    membership = access_context["membership"]
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    can_manage = bool(membership and membership.role in {"owner", "admin"})
    active_tab = (request.query_params.get("tab") or "overview").strip().lower()
    if active_tab not in {"overview", "tools", "api-keys", "activity"}:
        active_tab = "overview"
    if not can_manage and active_tab in {"api-keys", "activity"}:
        active_tab = "overview"

    principals = list_machine_principals_for_workspace(AUDIT_DB_PATH, workspace.id) if can_manage else []
    audit_logs = list_control_plane_audit_logs_for_workspace(AUDIT_DB_PATH, workspace.id, limit=50) if can_manage else []
    flash = pop_all_session_flash(AUDIT_DB_PATH, session.session_id)
    one_time_secret = flash.get("new_api_key_secret")
    new_client_id = flash.get("new_api_key_client_id")
    if not can_manage:
        one_time_secret = None
        new_client_id = None
    entitlement_allows = _has_cp_api_access(access_context)
    broker_url = settings.app_base_url.rstrip("/") + "/api/agent-integrations/mcp"
    config_snippet = (
        f"VIPARI_MCP_BROKER_URL={broker_url}\n"
        "VIPARI_CLIENT_ID=replace-with-your-client-id\n"
        "VIPARI_CLIENT_SECRET=replace-with-your-client-secret"
    )

    return HTMLResponse(
        render_control_plane_mcp_page(
            workspace_name=workspace.display_name,
            audit_href="/dashboard",
            plan_label=get_plan_definition(plan_code).label,
            theme_preference=user.theme_preference if user else "dark",
            admin_url="/admin" if _has_owner_admin_access(user, identity, workspace) else None,
            active_tab=active_tab,
            download_url="/integrations/mcp/download",
            broker_host=broker_url,
            config_snippet=config_snippet,
            principals=principals,
            audit_logs=audit_logs,
            csrf_token=session.csrf_secret,
            can_manage=can_manage,
            entitlement_allows=entitlement_allows,
            one_time_secret=one_time_secret,
            max_principals=settings.cp_max_principals_per_workspace,
            new_client_id=new_client_id,
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=user.display_name if user else None,
                github_login=identity.github_login if identity else None,
            ),
        )
    )


@app.get("/integrations/mcp/download")
@app.get("/app/integrations/mcp/download")
async def mcp_integrations_download(request: Request):
    access_context = _current_workspace_context(request)
    if not _has_settings_access(access_context):
        raise HTTPException(status_code=403, detail="Settings are available only for accepted workspace members.")

    bundle_bytes = build_customer_mcp_bundle(app_base_url=settings.app_base_url)
    return Response(
        content=bundle_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="vipari-mcp-connector.zip"'},
    )


@app.post("/api/agent-integrations/mcp/token")
async def mcp_broker_token(request: Request, payload: McpBrokerTokenRequest):
    client_ip = request.client.host if request.client else "unknown"
    result = issue_mcp_broker_token_via_client_credentials(
        payload.client_id,
        payload.client_secret,
        settings=settings,
        db_path=AUDIT_DB_PATH,
        client_ip=client_ip,
    )
    return JSONResponse(result)


@app.get("/api/agent-integrations/mcp/tools")
async def mcp_broker_tools(request: Request):
    context = authenticate_mcp_broker_request(
        request.headers.get("Authorization"),
        settings=settings,
        db_path=AUDIT_DB_PATH,
    )
    return JSONResponse(
        {
            "workspace_id": context.workspace_id,
            "tools": list_mcp_tools_for_scopes(context.scopes),
        }
    )


@app.post("/api/agent-integrations/mcp/invoke")
async def mcp_broker_invoke(request: Request, payload: McpBrokerInvokeRequest):
    context = authenticate_mcp_broker_request(
        request.headers.get("Authorization"),
        settings=settings,
        db_path=AUDIT_DB_PATH,
    )

    result = invoke_mcp_broker_tool(
        payload.tool_name,
        payload.arguments,
        context=context,
        db_path=AUDIT_DB_PATH,
    )
    record_mcp_broker_invocation(
        db_path=AUDIT_DB_PATH,
        context=context,
        tool_name=payload.tool_name,
    )
    return JSONResponse(
        {
            "tool_name": payload.tool_name,
            "workspace_id": context.workspace_id,
            "result": result,
        }
    )


@app.get("/policies", response_class=HTMLResponse)
@app.get("/app/policies", response_class=HTMLResponse)
async def policies_page(request: Request):
    access_context = _current_workspace_context(request)
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    user = access_context["user"]
    identity = access_context["identity"]
    membership = access_context["membership"]
    session = access_context["session"]
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    can_manage = membership is not None and membership.role in {"owner", "admin"}

    for allocation in list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace.id):
        if allocation.allocation_status in {"active", "onboarded"}:
            sync_ai_system_for_repo(
                AUDIT_DB_PATH,
                workspace_id=workspace.id,
                repo_full=allocation.repo_full,
                created_by_user_id=session.user_id,
            )

    def _labelize(value: str | None) -> str:
        if not value:
            return "Not set"
        return value.replace("_", " ").replace("-", " ").title()

    systems = list_ai_systems_for_workspace(AUDIT_DB_PATH, workspace.id)
    summary_cards = [
        {
            "label": "Registered systems",
            "value": str(len(systems)),
            "detail": "Workspace-backed AI systems derived from repo allocations and onboarding evidence.",
        },
        {
            "label": "Reviewer-confirmed",
            "value": str(sum(1 for system in systems if system.last_reviewed_at is not None)),
            "detail": "Systems whose registry classification and policy context were explicitly confirmed by a reviewer.",
        },
        {
            "label": "Auto-prefilled",
            "value": str(sum(1 for system in systems if system.last_reviewed_at is None)),
            "detail": "Systems still relying on deterministic repository evidence until a reviewer confirms the registry entry.",
        },
        {
            "label": "High-risk flagged",
            "value": str(sum(1 for system in systems if system.risk_level == "high-risk")),
            "detail": "Systems currently marked for the higher-control operating path.",
        },
    ]
    system_rows = []
    for system in systems:
        try:
            artifact_families = json.loads(system.artifact_families_json or "[]")
        except json.JSONDecodeError:
            artifact_families = []
        evidence_summary = ", ".join(str(item).replace("_", " ") for item in artifact_families) if artifact_families else "Static repository evidence"
        system_rows.append(
            {
                "id": system.id,
                "display_name": system.display_name,
                "repo_full": system.repo_full,
                "evidence_summary": evidence_summary,
                "onboarding_status": _labelize(system.latest_onboarding_status),
                "risk_level": system.risk_level,
                "risk_level_label": _labelize(system.risk_level),
                "eu_ai_act_domain": system.eu_ai_act_domain or "",
                "eu_ai_act_domain_label": _labelize(system.eu_ai_act_domain),
                "purpose_summary": system.purpose_summary or "",
                "last_reviewed_at": datetime.fromtimestamp(system.last_reviewed_at).strftime("%Y-%m-%d %H:%M UTC") if system.last_reviewed_at else "Not reviewed",
            }
        )

    status_note = None
    if request.query_params.get("classification_saved") == "1":
        status_note = "AI system classification saved. The registry remains deterministic; human review controls the final policy state."
    return HTMLResponse(
        render_control_plane_policies_page(
            workspace_name=workspace.display_name,
            audit_href="/dashboard",
            plan_label=get_plan_definition(plan_code).label,
            theme_preference=user.theme_preference if user else "dark",
            admin_url="/admin" if _has_owner_admin_access(user, identity, workspace) else None,
            summary_cards=summary_cards,
            system_rows=system_rows,
            status_note=status_note,
            can_manage=can_manage,
            csrf_token=session.csrf_secret,
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=user.display_name if user else None,
                github_login=identity.github_login if identity else None,
            ),
        )
    )


@app.post("/policies/systems/{ai_system_id}")
@app.post("/app/policies/systems/{ai_system_id}")
async def classify_policy_system(
    request: Request,
    ai_system_id: int,
    risk_level: str = Form(...),
    eu_ai_act_domain: str | None = Form(default=None),
    purpose_summary: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
):
    access_context = _current_workspace_context(request)
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")
    workspace = access_context["workspace"]
    ai_system = get_ai_system_by_id(AUDIT_DB_PATH, ai_system_id)
    if ai_system is None or ai_system.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="AI system not found for this workspace.")

    normalized_risk, normalized_domain, normalized_purpose = _normalize_ai_system_classification(
        risk_level=risk_level,
        eu_ai_act_domain=eu_ai_act_domain,
        purpose_summary=purpose_summary,
    )
    updated = update_ai_system_classification(
        AUDIT_DB_PATH,
        ai_system_id=ai_system_id,
        risk_level=normalized_risk,
        eu_ai_act_domain=normalized_domain,
        purpose_summary=normalized_purpose,
        reviewed_by_user_id=access_context["session"].user_id,
    )
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        actor_user_id=access_context["session"].user_id,
        event_type="ai_system_classification_updated",
        subject_type="ai_system",
        subject_id=str(updated.id),
        payload={
            "repo_full": updated.repo_full,
            "risk_level": updated.risk_level,
            "eu_ai_act_domain": updated.eu_ai_act_domain,
        },
    )
    return RedirectResponse("/policies?classification_saved=1", status_code=303)


@app.get("/compliance", response_class=HTMLResponse)
@app.get("/app/compliance", response_class=HTMLResponse)
async def compliance_page(request: Request):
    return _render_compliance_tab_page(
        request,
        active_tab="readiness",
        page_title="Workspace readiness",
        page_description="Track workspace readiness, the blockers holding exports back, and which repositories need action next.",
        page_note="The main page stays focused on the immediate readiness answer. Framework detail, export execution, and evidence inspection live in their own tabs.",
    )


@app.get("/compliance/frameworks", response_class=HTMLResponse)
@app.get("/app/compliance/frameworks", response_class=HTMLResponse)
async def compliance_frameworks_page(request: Request):
    return _render_compliance_tab_page(
        request,
        active_tab="frameworks",
        page_title="Framework mapping",
        page_description="Review how the monitored repositories map to EU AI Act, SOC 2, and ISO 27001 expectations without the operational export controls competing for attention.",
        page_note="These cards summarize the framework story for the current workspace evidence set.",
    )


@app.get("/compliance/exports", response_class=HTMLResponse)
@app.get("/app/compliance/exports", response_class=HTMLResponse)
async def compliance_exports_page(request: Request):
    return _render_compliance_tab_page(
        request,
        active_tab="exports",
        page_title="Export operations",
        page_description="Generate evidence bundles and review recent export activity for the repositories already in scope.",
        page_note="Server-side presets still reuse baseline approval, governance evidence, and freshness checks from the readiness model.",
    )


@app.get("/compliance/evidence", response_class=HTMLResponse)
@app.get("/app/compliance/evidence", response_class=HTMLResponse)
async def compliance_evidence_page(request: Request):
    return _render_compliance_tab_page(
        request,
        active_tab="evidence",
        page_title="Evidence posture",
        page_description="Inspect stale evidence, missing governance artifacts, and pending baseline approvals repository by repository.",
        page_note="Use this view when you are clearing blockers rather than making the overall readiness call.",
    )


def _render_compliance_tab_page(
    request: Request,
    *,
    active_tab: str,
    page_title: str,
    page_description: str,
    page_note: str,
) -> HTMLResponse:
    access_context = _current_workspace_context(request)
    view, export_jobs = _build_compliance_workspace_api_context(access_context)
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    user = access_context["user"]
    identity = access_context["identity"]
    session = access_context["session"]
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    blocked_free_tier = _is_active_comments_only_workspace(access_context)
    shell_state = "active_comments_only" if blocked_free_tier else "active"
    shell_title = ""
    shell_body = ""
    shell_cta_href = None
    shell_cta_label = None
    if blocked_free_tier:
        shell_title, shell_body, shell_cta_href, shell_cta_label = _free_tier_upgrade_shell_copy("the Compliance workspace")
    status_note = request.query_params.get("status") or ""
    evidence_filter = request.query_params.get("gap") or ""
    evidence_repo = request.query_params.get("repo") or ""
    return HTMLResponse(
        render_control_plane_compliance_page(
            workspace_name=workspace.display_name,
            audit_href="/dashboard",
            plan_label=get_plan_definition(plan_code).label,
            theme_preference=user.theme_preference if user else "dark",
            status_note=status_note,
            active_tab=active_tab,
            page_title=page_title,
            page_description=page_description,
            page_note=page_note,
            view=view,
            export_jobs=tuple(export_jobs),
            csrf_token=session.csrf_secret if session is not None else "",
            evidence_filter=evidence_filter,
            evidence_repo=evidence_repo,
            shell_state=shell_state,
            shell_title=shell_title,
            shell_body=shell_body,
            shell_cta_href=shell_cta_href,
            shell_cta_label=shell_cta_label,
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=user.display_name if user else None,
                github_login=identity.github_login if identity else None,
            ),
        )
    )


def _build_compliance_workspace_api_context(access_context: dict[str, object]) -> tuple[object, tuple[object, ...]]:
    workspace = access_context.get("workspace") if access_context else None
    session = access_context.get("session") if access_context else None
    if workspace is None:
        return build_compliance_workspace_view(AUDIT_DB_PATH, [], (), (), workspace_id=None), tuple()

    visibility = _dashboard_repo_visibility(access_context)
    allowed_repo_fulls = visibility.get("allowed_repo_fulls")
    repo_rows = _workspace_repo_rows(workspace.id)
    if allowed_repo_fulls is not None:
        repo_rows = [row for row in repo_rows if str(row.get("repo_full") or "") in allowed_repo_fulls]

    repo_summaries = list_repo_dashboard_index(
        AUDIT_DB_PATH,
        allowed_repo_fulls=allowed_repo_fulls,
        repo_scope_by_full=visibility.get("repo_scope_by_full"),
        allocation_status_by_full=visibility.get("allocation_status_by_full"),
    )
    export_jobs = (
        tuple(list_export_jobs_for_workspace_requester(AUDIT_DB_PATH, workspace.id, session.user_id))
        if session is not None
        else tuple()
    )
    view = build_compliance_workspace_view(AUDIT_DB_PATH, repo_rows, repo_summaries, export_jobs, workspace_id=workspace.id)
    return view, export_jobs


def _filtered_compliance_evidence_payload(view, gap_filter: str | None):
    return filter_compliance_evidence_view(view, gap_filter)


@app.get("/help", response_class=HTMLResponse)
@app.get("/app/help", response_class=HTMLResponse)
async def help_page(request: Request):
    access_context = _current_workspace_context(request)
    session = access_context.get("session")
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    user = access_context["user"]
    identity = access_context["identity"]
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    repo_rows = _workspace_repo_rows(workspace.id)
    allocation_status_by_full = {
        allocation.repo_full: allocation.allocation_status
        for allocation in list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace.id)
    }
    repo_summaries = list_repo_dashboard_index(
        AUDIT_DB_PATH,
        allowed_repo_fulls={str(item["repo_full"]) for item in repo_rows},
        allocation_status_by_full=allocation_status_by_full,
    )
    export_jobs = (
        list_export_jobs_for_workspace_requester(AUDIT_DB_PATH, workspace.id, session.user_id)
        if session is not None
        else []
    )
    return HTMLResponse(
        render_control_plane_help_page(
            workspace_name=workspace.display_name,
            plan_label=get_plan_definition(plan_code).label,
            theme_preference=user.theme_preference if user else "dark",
            admin_url="/admin" if _has_owner_admin_access(user, identity, workspace) else None,
            resolution=access_context["resolution"],
            repo_rows=repo_rows,
            repo_summaries=repo_summaries,
            export_ready_count=sum(1 for job in export_jobs if job.status == "completed"),
            export_pending_count=sum(1 for job in export_jobs if job.status != "completed"),
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=user.display_name if user else None,
                github_login=identity.github_login if identity else None,
            ),
        )
    )


@app.post("/compliance/export")
@app.post("/app/compliance/export")
async def compliance_export_page_submit(
    request: Request,
    export_scope: str = Form(default="all"),
    export_preset: str = Form(default="none"),
    repo_fulls: list[str] = Form(default=[]),
    from_date: str = Form(default=""),
    to_date: str = Form(default=""),
    export_mode: str = Form(default="compliance"),
    include_artifact_content: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
):
    access_context = _current_workspace_context(request)
    if _is_active_comments_only_workspace(access_context):
        return RedirectResponse("/compliance/exports?status=Upgrade+to+Starter+to+generate+compliance+exports.", status_code=303)
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    workspace = access_context["workspace"]
    session = access_context["session"]
    if not from_date or not to_date:
        return RedirectResponse("/compliance/exports?status=Choose+an+export+date+range+before+running+Compliance+exports.", status_code=303)
    from_ts = datetime.fromisoformat(from_date).timestamp()
    to_ts = datetime.fromisoformat(to_date).timestamp()
    if from_ts > to_ts:
        return RedirectResponse("/compliance/exports?status=The+export+start+date+must+be+earlier+than+the+end+date.", status_code=303)
    if export_mode not in {"compliance", "compliance_plus_drift"}:
        return RedirectResponse("/compliance/exports?status=Choose+a+valid+export+mode.", status_code=303)
    if export_preset not in {"none", "review_ready", "fresh_review_ready"}:
        return RedirectResponse("/compliance/exports?status=Choose+a+valid+export+preset.", status_code=303)

    view, _export_jobs = _build_compliance_workspace_api_context(access_context)
    visible_repo_fulls = {str(row.repo_full) for row in view.repo_rows}
    exportable_repo_fulls = {str(row.repo_full) for row in view.repo_rows if row.last_onboarded_at is not None}
    if export_preset != "none":
        selected_repo_fulls = _compliance_export_preset_repo_fulls(exportable_repo_fulls, export_preset)
    else:
        selected_repo_fulls = (
            sorted(exportable_repo_fulls)
            if export_scope == "all_visible"
            else sorted({repo for repo in repo_fulls if repo in visible_repo_fulls})
        )
    if not selected_repo_fulls:
        return RedirectResponse("/compliance/exports?status=Select+at+least+one+repository+or+choose+all+repos.", status_code=303)

    completed = 0
    failed = 0
    failure_details: list[str] = []
    for repo_full in selected_repo_fulls:
        try:
            _run_compliance_export_job(
                repo_full=repo_full,
                from_ts=from_ts,
                to_ts=to_ts,
                export_mode=export_mode,
                include_artifact_content=include_artifact_content is not None,
                workspace_id=workspace.id,
                requested_by_user_id=session.user_id,
                requested_by_github_login=_dashboard_actor_login(request),
            )
            completed += 1
        except Exception as exc:
            failed += 1
            if not failure_details:
                error_detail = _user_safe_compliance_export_error(exc)
                failure_details.append(f"{repo_full}: {error_detail}")
    status_message = f"Completed exports for {completed} repo(s)."
    if failed:
        status_message += f" {failed} repo(s) failed and can be retried."
    if failure_details:
        status_message += f" First failure: {failure_details[0]}"
    return RedirectResponse(f"/compliance/exports?status={quote(status_message)}", status_code=303)


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
@app.get("/app/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_page(request: Request):
    admin_context = _require_owner_access(request)
    active_tab = (request.query_params.get("tab") or "overview").strip().lower()
    if active_tab not in {"overview", "logs"}:
        active_tab = "overview"
    admin_rows = [asdict(row) for row in list_admin_workspace_users(AUDIT_DB_PATH)]
    return HTMLResponse(
        render_control_plane_admin_page(
            actor_github_login=admin_context["identity"].github_login,
            admin_rows=admin_rows,
            unclaimed_installations=[asdict(row) for row in list_unclaimed_installations(AUDIT_DB_PATH)],
            billing_claims=[asdict(row) for row in list_billing_handoff_claims(AUDIT_DB_PATH)],
            audit_logs=[asdict(row) for row in list_recent_control_plane_audit_logs(AUDIT_DB_PATH)],
            csrf_token=admin_context["session"].csrf_secret,
            active_tab=active_tab,
            logs_view=_build_admin_logs_view(request, admin_rows=admin_rows) if active_tab == "logs" else None,
            status_note=(request.query_params.get("updated") or "").replace("_", " ").strip().capitalize() or None,
        )
    )


@app.post("/admin/users/create", include_in_schema=False)
@app.post("/app/admin/users/create", include_in_schema=False)
async def admin_create_user(request: Request, display_name: str = Form(...), primary_email: str | None = Form(default=None), csrf_token: str | None = Form(None)):
    admin_context = _require_owner_access(request)
    _validate_csrf_secret(admin_context["session"].csrf_secret, csrf_token)
    normalized_name = _normalize_nonempty_text(display_name, field_name="Display name", max_length=120)
    user = create_user(AUDIT_DB_PATH, display_name=normalized_name, primary_email=_normalize_optional_email(primary_email), active=True)
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=None,
        actor_user_id=admin_context["session"].user_id,
        event_type="admin_user_created",
        subject_type="user",
        subject_id=str(user.id),
        payload={"display_name": user.display_name, "primary_email": user.primary_email},
    )
    return _admin_redirect("user_created")


@app.post("/admin/users/{user_id}/update", include_in_schema=False)
@app.post("/app/admin/users/{user_id}/update", include_in_schema=False)
async def admin_update_user(
    request: Request,
    user_id: int,
    display_name: str = Form(...),
    primary_email: str | None = Form(default=None),
    active: str | None = Form(default=None),
    csrf_token: str | None = Form(None),
):
    admin_context = _require_owner_access(request)
    _validate_csrf_secret(admin_context["session"].csrf_secret, csrf_token)
    normalized_name = _normalize_nonempty_text(display_name, field_name="Display name", max_length=120)
    user = update_user_admin_fields(
        AUDIT_DB_PATH,
        user_id,
        display_name=normalized_name,
        primary_email=_normalize_optional_email(primary_email),
        active=bool(active),
    )
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=None,
        actor_user_id=admin_context["session"].user_id,
        event_type="admin_user_updated",
        subject_type="user",
        subject_id=str(user.id),
        payload={"display_name": user.display_name, "primary_email": user.primary_email, "active": user.active},
    )
    return _admin_redirect("user_updated")


@app.post("/admin/users/{user_id}/delete", include_in_schema=False)
@app.post("/app/admin/users/{user_id}/delete", include_in_schema=False)
async def admin_delete_user(request: Request, user_id: int, csrf_token: str | None = Form(None)):
    admin_context = _require_owner_access(request)
    _validate_csrf_secret(admin_context["session"].csrf_secret, csrf_token)
    user = get_user_by_id(AUDIT_DB_PATH, user_id)
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=None,
        actor_user_id=admin_context["session"].user_id,
        event_type="admin_user_deleted",
        subject_type="user",
        subject_id=str(user_id),
        payload={"display_name": user.display_name if user else None, "primary_email": user.primary_email if user else None},
    )
    delete_user(AUDIT_DB_PATH, user_id)
    return _admin_redirect("user_deleted")


@app.post("/admin/workspaces/create", include_in_schema=False)
@app.post("/app/admin/workspaces/create", include_in_schema=False)
async def admin_create_workspace(
    request: Request,
    display_name: str = Form(...),
    slug: str | None = Form(default=None),
    billing_owner_user_id: int = Form(...),
    csrf_token: str | None = Form(None),
):
    admin_context = _require_owner_access(request)
    _validate_csrf_secret(admin_context["session"].csrf_secret, csrf_token)
    normalized_name = _normalize_nonempty_text(display_name, field_name="Workspace name", max_length=120)
    normalized_slug = _normalize_workspace_slug(slug, normalized_name)
    try:
        workspace = create_workspace(
            AUDIT_DB_PATH,
            slug=normalized_slug,
            display_name=normalized_name,
            billing_owner_user_id=billing_owner_user_id,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Workspace slug must be unique.") from exc
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        actor_user_id=admin_context["session"].user_id,
        event_type="admin_workspace_created",
        subject_type="workspace",
        subject_id=str(workspace.id),
        payload={"slug": workspace.slug, "display_name": workspace.display_name, "billing_owner_user_id": billing_owner_user_id},
    )
    return _admin_redirect("workspace_created")


@app.post("/admin/workspaces/{workspace_id}/update", include_in_schema=False)
@app.post("/app/admin/workspaces/{workspace_id}/update", include_in_schema=False)
async def admin_update_workspace(
    request: Request,
    workspace_id: int,
    display_name: str = Form(...),
    slug: str | None = Form(default=None),
    plan_code: str | None = Form(default=None),
    csrf_token: str | None = Form(None),
):
    admin_context = _require_owner_access(request)
    _validate_csrf_secret(admin_context["session"].csrf_secret, csrf_token)
    normalized_name = _normalize_nonempty_text(display_name, field_name="Workspace name", max_length=120)
    normalized_slug = _normalize_workspace_slug(slug, normalized_name)
    normalized_plan = get_plan_definition(plan_code or "starter").code
    try:
        workspace = update_workspace_admin_fields(AUDIT_DB_PATH, workspace_id, slug=normalized_slug, display_name=normalized_name)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Workspace slug must be unique.") from exc
    _subscription, entitlement = _admin_apply_workspace_plan(workspace_id, normalized_plan)
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        actor_user_id=admin_context["session"].user_id,
        event_type="admin_workspace_updated",
        subject_type="workspace",
        subject_id=str(workspace.id),
        payload={"slug": workspace.slug, "display_name": workspace.display_name, "plan_code": entitlement.plan_code},
    )
    return _admin_redirect("workspace_updated")


@app.post("/admin/workspaces/{workspace_id}/delete", include_in_schema=False)
@app.post("/app/admin/workspaces/{workspace_id}/delete", include_in_schema=False)
async def admin_delete_workspace(request: Request, workspace_id: int, csrf_token: str | None = Form(None)):
    admin_context = _require_owner_access(request)
    _validate_csrf_secret(admin_context["session"].csrf_secret, csrf_token)
    workspace = get_workspace_by_id(AUDIT_DB_PATH, workspace_id)
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=workspace_id,
        actor_user_id=admin_context["session"].user_id,
        event_type="admin_workspace_deleted",
        subject_type="workspace",
        subject_id=str(workspace_id),
        payload={"slug": workspace.slug if workspace else None, "display_name": workspace.display_name if workspace else None},
    )
    delete_workspace(AUDIT_DB_PATH, workspace_id)
    return _admin_redirect("workspace_deleted")


@app.post("/admin/memberships/upsert", include_in_schema=False)
@app.post("/app/admin/memberships/upsert", include_in_schema=False)
async def admin_upsert_membership(
    request: Request,
    workspace_id: int = Form(...),
    user_id: int = Form(...),
    role: str = Form(...),
    csrf_token: str | None = Form(None),
):
    admin_context = _require_owner_access(request)
    _validate_csrf_secret(admin_context["session"].csrf_secret, csrf_token)
    normalized_role = (role or "").strip().lower()
    if normalized_role not in {"owner", "admin", "viewer"}:
        raise HTTPException(status_code=400, detail="Membership role must be owner, edit, or read.")
    membership = upsert_workspace_membership(
        AUDIT_DB_PATH,
        workspace_id=workspace_id,
        user_id=user_id,
        role=normalized_role,
        invitation_state="accepted",
        invited_by_user_id=admin_context["session"].user_id,
    )
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=workspace_id,
        actor_user_id=admin_context["session"].user_id,
        event_type="admin_membership_saved",
        subject_type="workspace_membership",
        subject_id=f"{workspace_id}:{user_id}",
        payload={"role": membership.role, "invitation_state": membership.invitation_state},
    )
    return _admin_redirect("membership_saved")


@app.post("/admin/memberships/{workspace_id}/{user_id}/delete", include_in_schema=False)
@app.post("/app/admin/memberships/{workspace_id}/{user_id}/delete", include_in_schema=False)
async def admin_delete_membership(request: Request, workspace_id: int, user_id: int, csrf_token: str | None = Form(None)):
    admin_context = _require_owner_access(request)
    _validate_csrf_secret(admin_context["session"].csrf_secret, csrf_token)
    membership = get_workspace_membership(AUDIT_DB_PATH, workspace_id, user_id)
    create_control_plane_audit_log(
        AUDIT_DB_PATH,
        workspace_id=workspace_id,
        actor_user_id=admin_context["session"].user_id,
        event_type="admin_membership_deleted",
        subject_type="workspace_membership",
        subject_id=f"{workspace_id}:{user_id}",
        payload={"role": membership.role if membership else None},
    )
    delete_workspace_membership(AUDIT_DB_PATH, workspace_id=workspace_id, user_id=user_id)
    return _admin_redirect("membership_deleted")


@app.get("/workspaces/new", response_class=HTMLResponse)
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


@app.post("/workspaces/bootstrap")
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
            response = RedirectResponse(_path_with_flow_context("/repos?installation_linked=1", flow_context), status_code=303)
            response.delete_cookie(CONTROL_PLANE_PENDING_INSTALL_COOKIE)
            return response
        except Exception:
            return RedirectResponse(_path_with_flow_context("/setup/install?install_error=callback_link_failed", flow_context), status_code=303)
    return RedirectResponse(_resume_destination_for_session(get_user_session(AUDIT_DB_PATH, session.session_id), flow_context), status_code=303)


@app.get("/billing", response_class=HTMLResponse)
@app.get("/app/billing", response_class=HTMLResponse)
async def billing_page(request: Request):
    access_context = _current_workspace_context(request)
    flow_context = _flow_context_from_request(request)
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    resolution = access_context["resolution"]
    customer = get_billing_customer_for_workspace(AUDIT_DB_PATH, workspace.id)
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    current_plan_label = get_plan_definition(plan_code).label if plan_code else "No plan"
    selected_plan_code = _normalize_plan_hint(request.query_params.get("plan")) or plan_code
    portal_url = "/billing/portal" if customer else None
    checkout_status_note = None
    if request.query_params.get("checkout_session_id"):
        checkout_status_note = "Checkout returned to Vipari. Access remains pending until Stripe webhook confirmation arrives."
    elif request.query_params.get("claim_activated"):
        checkout_status_note = "Billing activation was accepted. GitHub installation is the next required step."
    elif request.query_params.get("free_activated"):
        checkout_status_note = "Free tier activated. Link the GitHub App and allocate one repository to start PR comments."
    elif request.query_params.get("external_checkout_required"):
        checkout_status_note = "Paid plan checkout is handled by the external billing provider before Vipari grants access."
    elif request.query_params.get("canceled"):
        checkout_status_note = "Checkout was canceled before payment confirmation."
    elif resolution.state in {"active", "active_comments_only", "canceled_active_until_period_end", "expired_read_only"}:
        checkout_status_note = "GitHub installation and repository onboarding are already in place. Choosing a plan here will return you to the dashboard instead of restarting setup."
    elif resolution.state == "awaiting_repo_onboarding":
        checkout_status_note = "GitHub installation is already linked. After plan activation, the next step is selecting repositories and finishing onboarding."
    elif resolution.state == "awaiting_github_install":
        checkout_status_note = "Choose a plan to continue. GitHub App installation is still required before repositories can be allocated."
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
            theme_preference=access_context["user"].theme_preference if access_context.get("user") else "dark",
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=access_context["user"].display_name if access_context.get("user") else None,
                github_login=access_context["identity"].github_login if access_context.get("identity") else None,
            ),
        )
    )


@app.post("/billing/checkout")
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
        return _post_billing_activation_redirect(access_context, flow_context, activation_flag="free_activated")

    if settings.base44_checkout_url:
        checkout_params = {
            "plan": normalized_plan,
            "workspace_id": workspace.id,
            "workspace_slug": workspace.slug,
            "workspace_name": workspace.display_name,
            "billing_email": (access_context["user"].primary_email if access_context["user"] else "") or "",
            "source": flow_context.get("source") or "driftguard",
            "return_url": f"{settings.app_base_url}/claim",
        }
        checkout_url = f"{settings.base44_checkout_url}?{urlencode(checkout_params)}"
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
        destination = _path_with_flow_context("/billing/claim", flow_context)
    response = RedirectResponse(destination, status_code=303)
    _set_context_cookie(
        response,
        CONTROL_PLANE_OAUTH_CONTEXT_COOKIE,
        flow_context,
        binding=_context_cookie_binding_for_session_id(session.session_id) if session is not None else None,
        max_age=1800,
    )
    return response


@app.get("/billing/claim")
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
    return RedirectResponse(_path_with_flow_context("/setup/install?claim_activated=1", next_flow_context), status_code=303)


@app.get("/billing/portal")
@app.get("/app/billing/portal")
async def billing_portal(request: Request):
    access_context = _current_workspace_context(request)
    _require_workspace_role(access_context, "owner", "admin")
    workspace = access_context["workspace"]
    customer = get_billing_customer_for_workspace(AUDIT_DB_PATH, workspace.id)
    if customer is None:
        return RedirectResponse("/billing", status_code=303)
    portal_url = create_billing_portal_session(
        settings=settings,
        stripe_customer_id=customer.stripe_customer_id,
        return_url=f"{settings.app_base_url}/billing",
    )
    return RedirectResponse(portal_url, status_code=303)


@app.get("/setup/install", response_class=HTMLResponse)
@app.get("/app/setup/install", response_class=HTMLResponse)
async def install_page(request: Request):
    access_context = _current_workspace_context(request)
    flow_context = _flow_context_from_request(request)
    workspace = access_context["workspace"]
    installation = access_context["installation"]
    latest_installation = get_latest_workspace_installation(AUDIT_DB_PATH, workspace.id)
    install_url = _path_with_flow_context("/setup/install/start", flow_context) if settings.has_github_app_credentials else None
    installation_summary = (
        f"Connected installation {installation.account_login} ({installation.account_type})." if installation else "No GitHub App installation is linked yet."
    )
    install_hint = "Billing is active. The next gate is granting GitHub App installation authority."
    install_action_label = "Start GitHub App install"
    if request.query_params.get("installation_linked"):
        install_hint = "GitHub installation linked successfully. Review the synced repositories below."
    elif request.query_params.get("install_error"):
        install_hint = "GitHub installation completed, but DriftGuard could not finish linking it automatically. Use the manual fallback form below."
    elif installation is None and latest_installation is not None and latest_installation.status != "active":
        install_hint = "Vipari was previously installed for this workspace, but GitHub App access is no longer active. Reconnect the app to restore dashboard access and automation."
        installation_summary = (
            f"Last linked installation {latest_installation.account_login} ({latest_installation.account_type}) is currently {latest_installation.status}."
        )
        install_action_label = "Reconnect GitHub App"
    return HTMLResponse(
        render_control_plane_install_page(
            workspace_name=workspace.display_name,
            install_hint=install_hint,
            installation_summary=installation_summary,
            install_url=install_url,
            install_callback_url=_path_with_flow_context("/setup/install/callback", flow_context),
            csrf_token=access_context["session"].csrf_secret,
            install_action_label=install_action_label,
        )
    )


@app.get("/setup/install/start")
@app.get("/app/setup/install/start")
async def install_start(request: Request):
    access_context = _current_workspace_context(request)
    flow_context = _flow_context_from_request(request)
    if not settings.has_github_app_credentials:
        return RedirectResponse(_path_with_flow_context("/setup/install?install_error=install_url_unavailable", flow_context), status_code=303)
    try:
        install_state_nonce = secrets.token_urlsafe(24)
        install_url = get_live_github_install_url(
            settings.github_app_id,
            settings.github_private_key_path,
            settings.resolved_github_private_key,
            state=install_state_nonce,
        )
    except Exception:
        return RedirectResponse(_path_with_flow_context("/setup/install?install_error=install_url_unavailable", flow_context), status_code=303)

    response = RedirectResponse(install_url, status_code=303)
    _set_context_cookie(
        response,
        CONTROL_PLANE_INSTALL_STATE_COOKIE,
        {"nonce": install_state_nonce, "workspace_id": access_context["workspace"].id},
        binding=_context_cookie_binding_for_session_id(access_context["session"].session_id),
        max_age=1800,
    )
    return response


@app.get("/setup/install/callback")
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
    session = _get_session(request)
    install_state = _install_callback_context_from_request(request) if session is not None else {}
    validated_workspace_id = None
    if session is not None and install_state:
        nonce = str(install_state.get("nonce") or "")
        if not state or not hmac.compare_digest(nonce, state):
            raise HTTPException(status_code=400, detail="Install callback state validation failed.")
        validated_workspace_id = _coerce_workspace_hint(str(install_state.get("workspace_id") or ""))
        session = _switch_session_workspace_if_allowed(session, validated_workspace_id)
    if session is None:
        try:
            _link_installation_to_workspace(workspace_id=None, installation_id=installation_id_int)
        except Exception:
            pass
        return _redirect_with_pending_install(
            request,
            installation_id=installation_id_int,
            workspace_id=None,
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
            {"installation_id": installation_id_int, "workspace_id": validated_workspace_id, "setup_action": setup_action or "install"},
            binding=_context_cookie_binding_for_session_id(session.session_id),
            max_age=1800,
        )
        response.delete_cookie(CONTROL_PLANE_INSTALL_STATE_COOKIE)
        return response
    if not install_state:
        raise HTTPException(status_code=400, detail="Install callback state validation failed.")
    _require_workspace_role(access_context, "owner", "admin")
    _link_installation_to_workspace(workspace_id=access_context["workspace"].id, installation_id=installation_id_int)
    response = RedirectResponse(
        _path_with_flow_context(
            f"/repos?installation_linked=1&setup_action={setup_action or 'install'}",
            _flow_context_from_request(request),
        ),
        status_code=303,
    )
    response.delete_cookie(CONTROL_PLANE_PENDING_INSTALL_COOKIE)
    response.delete_cookie(CONTROL_PLANE_INSTALL_STATE_COOKIE)
    return response


@app.post("/setup/install/link")
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
    return RedirectResponse("/repos", status_code=303)


@app.get("/repos", response_class=HTMLResponse)
@app.get("/app/repos", response_class=HTMLResponse)
async def repo_setup_page(request: Request):
    access_context = _current_workspace_context(request)
    flow_context = _flow_context_from_request(request)
    workspace = access_context["workspace"]
    user = access_context["user"]
    identity = access_context["identity"]
    installation = access_context.get("installation")
    latest_installation = get_latest_workspace_installation(AUDIT_DB_PATH, workspace.id)
    entitlement = access_context["entitlement"]
    subscription = access_context["subscription"]
    repo_inventory = _github_account_repo_inventory(access_context)
    connections = [asdict(item) for item in list_repo_connections_for_workspace(AUDIT_DB_PATH, workspace.id)]
    allocations = [asdict(item) for item in list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace.id)]
    active_allocations = [item for item in allocations if str(item.get("allocation_status") or "") in {"active", "onboarded"}]
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    repo_limit = entitlement.repo_limit if entitlement else get_plan_definition(plan_code).repo_limit
    allocation_status_by_full = {
        str(item["repo_full"]): str(item["allocation_status"])
        for item in active_allocations
    }
    repo_scope_by_full = {str(item["repo_full"]): "connected_history" for item in connections}
    repo_scope_by_full.update({str(item["repo_full"]): "allocated" for item in active_allocations})
    visible_repo_fulls = {str(item["repo_full"]) for item in connections} | {str(item["repo_full"]) for item in active_allocations}
    onboarded_summaries = [
        asdict(item)
        for item in list_repo_dashboard_index(
            AUDIT_DB_PATH,
            allowed_repo_fulls=visible_repo_fulls,
            repo_scope_by_full=repo_scope_by_full,
            allocation_status_by_full=allocation_status_by_full,
        )
        if str(item.dashboard_scope or "allocated") == "allocated"
    ]
    consumed_repo_slots = len(
        {str(item["repo_full"]) for item in active_allocations}
        | {str(item["repo_full"]) for item in onboarded_summaries}
    )
    remaining_repo_slots = max(repo_limit - consumed_repo_slots, 0)
    install_disabled = remaining_repo_slots <= 0
    inventory_summary = f"{remaining_repo_slots} of {repo_limit} repository slots available on this plan."
    install_action_label = "Install app"
    if installation is None and latest_installation is not None and latest_installation.status != "active":
        inventory_summary = (
            f"GitHub App access for {latest_installation.account_login} is currently {latest_installation.status}. "
            f"Reconnect Vipari to restore repository visibility. {remaining_repo_slots} of {repo_limit} repository slots remain available."
        )
        install_action_label = "Reconnect GitHub App"
    audit_repo_full = (
        (active_allocations[0]["repo_full"] if active_allocations else None)
        or (connections[0]["repo_full"] if connections else None)
    )
    audit_href = f"/dashboard/{quote(audit_repo_full, safe='')}" if audit_repo_full else "/dashboard"
    return HTMLResponse(
        render_control_plane_repo_setup_page(
            workspace_name=workspace.display_name,
            inventory_summary=inventory_summary,
            inventory_cards=render_repo_inventory_cards(
                repo_inventory,
                csrf_token=access_context["session"].csrf_secret,
                install_start_href=_path_with_flow_context("/setup/install/start", flow_context),
                install_disabled=install_disabled,
                install_disabled_href="/billing?plan=starter",
                install_action_label=install_action_label,
            ),
            onboarding_metrics=render_repo_onboarding_metrics(onboarded_summaries),
            onboarding_summary_cards=render_repo_onboarded_summary_cards(onboarded_summaries),
            audit_href=audit_href,
            theme_preference=user.theme_preference if user else "dark",
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=user.display_name if user else None,
                github_login=identity.github_login if identity else None,
            ),
        )
    )


@app.post("/repos/disconnect")
@app.post("/app/repos/disconnect")
async def repo_disconnect(request: Request, repo_full: str, csrf_token: str | None = Form(default=None)):
    access_context = _current_workspace_context(request)
    _validate_csrf_secret(access_context["session"].csrf_secret, csrf_token)
    _require_workspace_role(access_context, "owner", "admin")
    workspace = access_context["workspace"]
    allocation = get_repo_allocation_for_workspace(AUDIT_DB_PATH, workspace.id, repo_full)
    if allocation is None or allocation.allocation_status not in {"active", "onboarded"}:
        raise HTTPException(status_code=404, detail="Repository is not currently attached to this workspace.")
    update_repo_allocation_status(AUDIT_DB_PATH, allocation.id, "inactive")
    return RedirectResponse("/repos?repo_removed=1", status_code=303)


@app.post("/repos/allocate")
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
    existing_allocation_consumes_slot = bool(existing_allocation and existing_allocation.allocation_status in {"active", "onboarded"})
    if entitlement is not None and allocated_count >= entitlement.repo_limit and not existing_allocation_consumes_slot:
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
    sync_ai_system_for_repo(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        repo_full=allocation.repo_full,
        created_by_user_id=access_context["session"].user_id,
    )
    return RedirectResponse("/workspace", status_code=303)


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
        "identity": _public_identity_payload(access_context["identity"]),
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


async def dashboard_index_page(request: Request, range: str = "7d", filter: str = "all", artifact: str | None = None, pr: str | None = None, head_sha: str | None = None):
    request_started = time.perf_counter()
    timing_metrics: list[tuple[str, float]] = []
    access_started = time.perf_counter()
    redirect, _session, access_context, shell_mode = _normalize_dashboard_redirect_result(_dashboard_redirect_for_request(request))
    _record_server_timing_metric(timing_metrics, "access", access_started)
    if redirect is not None:
        timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
        return _attach_server_timing(redirect, timing_metrics)
    render_started = time.perf_counter()
    active_range = range.strip().lower() if range else "7d"
    if active_range not in {"24h", "7d", "30d"}:
        active_range = "7d"
    active_filter = filter.strip().lower() if filter else "all"
    if active_filter not in {"all", "critical", "mine"}:
        active_filter = "all"
    shell_state = "active"
    shell_title = ""
    shell_body = ""
    shell_cta_href = None
    shell_cta_label = None
    if shell_mode:
        shell_state = str(access_context["resolution"].state)
        shell_title, shell_body, shell_cta_href, shell_cta_label = _dashboard_shell_copy(access_context)
    user = access_context.get("user") if access_context else None
    identity = access_context.get("identity") if access_context else None
    response = HTMLResponse(
        render_dashboard_index_page(
            _current_theme_preference(request),
            active_range=active_range,
            active_filter=active_filter,
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=user.display_name if user else None,
                github_login=identity.github_login if identity else None,
            ),
            shell_state=shell_state,
            shell_title=shell_title,
            shell_body=shell_body,
            shell_cta_href=shell_cta_href,
            shell_cta_label=shell_cta_label,
            deep_link_artifact=(artifact or "").strip(),
            deep_link_pr=(pr or "").strip(),
            deep_link_head_sha=(head_sha or "").strip(),
        )
    )
    _record_server_timing_metric(timing_metrics, "render", render_started)
    timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
    return _attach_server_timing(response, timing_metrics)


async def dashboard_repo_page(request: Request, repo_full: str, tab: str = "audit", artifact: str | None = None, pr: str | None = None, head_sha: str | None = None):
    return await _render_dashboard_repo_page(request, repo_full, requested_tab=tab, artifact=artifact, pr=pr, head_sha=head_sha)


async def dashboard_repo_audit_page(request: Request, repo_full: str, artifact: str | None = None, pr: str | None = None, head_sha: str | None = None):
    return await _render_dashboard_repo_page(request, repo_full, requested_tab="audit", artifact=artifact, pr=pr, head_sha=head_sha)


async def _render_dashboard_repo_page(request: Request, repo_full: str, *, requested_tab: str, artifact: str | None = None, pr: str | None = None, head_sha: str | None = None):
    request_started = time.perf_counter()
    timing_metrics: list[tuple[str, float]] = []
    access_started = time.perf_counter()
    redirect, _session, access_context, shell_mode = _normalize_dashboard_redirect_result(_dashboard_redirect_for_request(request))
    _record_server_timing_metric(timing_metrics, "access", access_started)
    if redirect is not None:
        timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
        return _attach_server_timing(redirect, timing_metrics)
    if _control_plane_active() and access_context is not None and not shell_mode:
        try:
            access_context = _require_repo_dashboard_read_access(request, repo_full)
        except HTTPException as exc:
            if exc.status_code == 404:
                response = _render_missing_repo_dashboard_page(
                    request,
                    repo_full,
                    requested_tab=requested_tab,
                    access_context=access_context,
                    artifact=artifact,
                    pr=pr,
                    head_sha=head_sha,
                    status_code=404,
                )
                timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
                return _attach_server_timing(response, timing_metrics)
            raise
    if shell_mode and not _repo_visible_for_dashboard_shell(access_context, repo_full):
        response = _render_missing_repo_dashboard_page(
            request,
            repo_full,
            requested_tab=requested_tab,
            access_context=access_context,
            artifact=artifact,
            pr=pr,
            head_sha=head_sha,
            status_code=404,
        )
        timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
        return _attach_server_timing(response, timing_metrics)
    render_started = time.perf_counter()
    active_tab = requested_tab.strip().lower() if requested_tab else "audit"
    if active_tab not in {"audit", "drift", "version-control", "baseline", "compliance", "reports"}:
        active_tab = "audit"
    blocked_free_tier_tab = _is_active_comments_only_workspace(access_context) and active_tab in {"compliance", "reports"}
    shell_state = "active"
    shell_title = ""
    shell_body = ""
    shell_cta_href = None
    shell_cta_label = None
    if blocked_free_tier_tab:
        shell_state = "active_comments_only"
        shell_title, shell_body, shell_cta_href, shell_cta_label = _free_tier_upgrade_shell_copy(f"the {active_tab} tab")
    elif shell_mode:
        shell_state = str(access_context["resolution"].state)
        shell_title, shell_body, shell_cta_href, shell_cta_label = _dashboard_shell_copy(access_context, repo_full=repo_full)
    user = access_context.get("user") if access_context else None
    identity = access_context.get("identity") if access_context else None
    response = HTMLResponse(
        render_repo_dashboard_page(
            repo_full,
            theme_preference=_current_theme_preference(request),
            active_tab=active_tab,
            sidebar_profile_initial=_sidebar_profile_initial(
                display_name=user.display_name if user else None,
                github_login=identity.github_login if identity else None,
            ),
            shell_state=shell_state,
            shell_title=shell_title,
            shell_body=shell_body,
            shell_cta_href=shell_cta_href,
            shell_cta_label=shell_cta_label,
            deep_link_artifact=(artifact or "").strip(),
            deep_link_pr=(pr or "").strip(),
            deep_link_head_sha=(head_sha or "").strip(),
        )
    )
    _record_server_timing_metric(timing_metrics, "render", render_started)
    timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
    return _attach_server_timing(response, timing_metrics)


async def list_repos(request: Request):
    request_started = time.perf_counter()
    timing_metrics: list[tuple[str, float]] = []
    access_started = time.perf_counter()
    access_context = _require_dashboard_read_access(request)
    _record_server_timing_metric(timing_metrics, "access", access_started)
    visibility_started = time.perf_counter()
    visibility = _dashboard_repo_visibility(access_context)
    _record_server_timing_metric(timing_metrics, "visibility", visibility_started)
    list_started = time.perf_counter()
    response = JSONResponse(
        build_repo_index_payload(
            AUDIT_DB_PATH,
            allowed_repo_fulls=visibility["allowed_repo_fulls"],
            repo_scope_by_full=visibility["repo_scope_by_full"],
            allocation_status_by_full=visibility["allocation_status_by_full"],
            list_repo_dashboard_index_fn=list_repo_dashboard_index,
        )
    )
    _record_server_timing_metric(timing_metrics, "list", list_started)
    timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
    return _attach_server_timing(response, timing_metrics)


def dashboard_overview(request: Request, range: str = "7d", filter: str = "all"):
    request_started = time.perf_counter()
    timing_metrics: list[tuple[str, float]] = []
    access_started = time.perf_counter()
    access_context = _require_dashboard_read_access(request)
    _record_server_timing_metric(timing_metrics, "access", access_started)
    visibility_started = time.perf_counter()
    visibility = _dashboard_repo_visibility(access_context)
    _record_server_timing_metric(timing_metrics, "visibility", visibility_started)
    build_started = time.perf_counter()
    _record_server_timing_metric(timing_metrics, "build", build_started)
    json_started = time.perf_counter()
    response = JSONResponse(
        build_dashboard_overview_payload(
            AUDIT_DB_PATH,
            allowed_repo_fulls=visibility["allowed_repo_fulls"],
            repo_scope_by_full=visibility["repo_scope_by_full"],
            allocation_status_by_full=visibility["allocation_status_by_full"],
            active_filter=filter,
            active_range=range,
            access_context=access_context,
            build_dashboard_overview_view_fn=build_dashboard_overview_view,
        )
    )
    _record_server_timing_metric(timing_metrics, "json", json_started)
    timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
    return _attach_server_timing(response, timing_metrics)


def persistence_status(request: Request):
    _require_dashboard_access(request)
    return JSONResponse(persistence_status_payload(get_persistence_status(AUDIT_DB_PATH)))


def dashboard_escalation_queue(request: Request, include_watch: bool = False):
    access_context = _require_dashboard_read_access(request)
    visibility = _dashboard_repo_visibility(access_context)
    result = build_dashboard_escalation_queue_payload(
        AUDIT_DB_PATH,
        allowed_repo_fulls=visibility["allowed_repo_fulls"],
        include_watch=include_watch,
        build_workspace_escalation_queue_fn=build_workspace_escalation_queue,
    )
    return JSONResponse(result)


app.include_router(
    create_dashboard_page_router(
        dashboard_index_handler=dashboard_index_page,
        dashboard_repo_handler=dashboard_repo_page,
        dashboard_repo_audit_handler=dashboard_repo_audit_page,
    )
)

app.include_router(
    create_dashboard_read_router(
        list_repos_handler=list_repos,
        dashboard_overview_handler=dashboard_overview,
        dashboard_escalation_queue_handler=dashboard_escalation_queue,
        persistence_status_handler=persistence_status,
    )
)

app.include_router(
    create_compliance_api_router(
        current_workspace_context_fn=_current_workspace_context,
        build_compliance_workspace_api_context_fn=_build_compliance_workspace_api_context,
        filter_compliance_evidence_view_fn=filter_compliance_evidence_view,
        export_job_payload_fn=_export_job_payload,
    )
)


def list_pending_proposals_for_repo(request: Request, repo_full: str):
    access_context = _require_repo_dashboard_read_access(request, repo_full)
    from services.internal_auth import PRINCIPAL_KIND_SERVICE_ACCOUNT
    from services.proposals_records import list_pending_baseline_proposals_for_repo_in_workspace
    from services.onboarding_records import list_onboarded_artifacts_for_onboarding
    workspace = access_context.get("workspace")
    return JSONResponse(
        build_pending_proposals_payload(
            AUDIT_DB_PATH,
            repo_full,
            workspace_id=workspace.id if workspace is not None else None,
            list_pending_proposals_fn=list_pending_baseline_proposals_for_repo_in_workspace,
            get_latest_repository_onboarding_fn=get_latest_repository_onboarding,
            list_onboarded_artifacts_for_onboarding_fn=list_onboarded_artifacts_for_onboarding,
            get_machine_principal_by_id_fn=get_machine_principal_by_id,
            service_account_principal_kind=PRINCIPAL_KIND_SERVICE_ACCOUNT,
        )
    )


def list_pre_audit_relevance_for_repo(request: Request, repo_full: str):
    _require_repo_dashboard_read_access(request, repo_full)
    raw_pr_number = (request.query_params.get("pr_number") or "").strip()
    raw_head_sha = (request.query_params.get("head_sha") or "").strip()
    if not raw_pr_number.isdigit() or not raw_head_sha:
        raise HTTPException(status_code=400, detail="pr_number and head_sha are required.")
    return JSONResponse(
        build_pre_audit_relevance_payload(
            AUDIT_DB_PATH,
            repo_full,
            pr_number=int(raw_pr_number),
            head_sha=raw_head_sha,
            list_pre_audit_relevance_decisions_fn=list_pre_audit_relevance_decisions,
        )
    )


app.include_router(
    create_repo_read_router(
        pending_proposals_handler=list_pending_proposals_for_repo,
        pre_audit_relevance_handler=list_pre_audit_relevance_for_repo,
    )
)

app.include_router(
    create_repo_dashboard_router(
        authorize_repo_read_fn=lambda request, repo_full: _require_repo_dashboard_read_access(request, repo_full),
        resolve_db_path_fn=lambda: AUDIT_DB_PATH,
        build_repo_dashboard_view_with_timings_fn=build_repo_dashboard_view_with_timings,
        build_pre_audit_relevance_payload_fn=build_pre_audit_relevance_payload,
        list_pre_audit_relevance_decisions_fn=list_pre_audit_relevance_decisions,
        list_export_jobs_for_requester_fn=list_export_jobs_for_requester,
        export_job_payload_fn=_export_job_payload,
        record_server_timing_metric_fn=_record_server_timing_metric,
        attach_server_timing_fn=_attach_server_timing,
    )
)

app.include_router(
    create_repo_history_router(
        authorize_repo_read_fn=lambda request, repo_full: _require_repo_dashboard_read_access(request, repo_full),
        resolve_db_path_fn=lambda: AUDIT_DB_PATH,
        build_artifact_storyline_payload_fn=build_artifact_storyline_payload,
        build_repo_journey_payload_fn=build_repo_journey_payload,
        build_repo_snapshot_detail_payload_fn=build_repo_snapshot_detail_payload,
        build_repo_snapshot_compare_payload_fn=build_repo_snapshot_compare_payload,
        build_repo_artifact_storyline_fn=build_repo_artifact_storyline,
        build_repo_journey_fn=build_repo_journey,
        get_repo_snapshot_detail_fn=get_repo_snapshot_detail,
        snapshot_to_public_payload_fn=snapshot_to_public_payload,
        compare_repo_snapshots_fn=compare_repo_snapshots,
    )
)


app.include_router(
    create_repo_onboarding_router(
        authorize_repo_read_fn=lambda request, repo_full: _require_repo_dashboard_read_access(request, repo_full),
        authorize_repo_mutation_fn=lambda request, repo_full: _require_repo_dashboard_mutation_access(request, repo_full),
        resolve_installation_id_fn=lambda access_context, installation_id: _trusted_workspace_installation_id(access_context, installation_id),
        resolve_db_path_fn=lambda: AUDIT_DB_PATH,
        github_app_id=GITHUB_APP_ID,
        github_private_key_path=GITHUB_PRIVATE_KEY_PATH,
        generate_jwt_fn=lambda app_id, private_key_path: generate_jwt(app_id, private_key_path, settings.resolved_github_private_key),
        get_installation_token_fn=lambda jwt_token, installation_id: get_installation_token(jwt_token, installation_id),
        list_repository_files_fn=lambda repo_full, token, ref=None: list_repository_files(repo_full, token, ref=ref),
        onboard_repository_fn=lambda active_db_path, **kwargs: onboard_repository(active_db_path, **kwargs),
        add_repo_artifact_to_onboarding_fn=lambda active_db_path, **kwargs: add_repo_artifact_to_onboarding(active_db_path, **kwargs),
        remove_repo_artifact_from_onboarding_fn=lambda active_db_path, **kwargs: remove_repo_artifact_from_onboarding(active_db_path, **kwargs),
        update_repo_artifact_type_fn=lambda active_db_path, **kwargs: update_repo_artifact_type(active_db_path, **kwargs),
        infer_artifact_type_from_path_fn=infer_artifact_type_from_path,
        tracked_artifact_type_options_fn=tracked_artifact_type_options,
        plan_repository_history_backfill_fn=lambda active_db_path, **kwargs: plan_repository_history_backfill(active_db_path, **kwargs),
        execute_repository_history_backfill_fn=lambda active_db_path, **kwargs: execute_repository_history_backfill(active_db_path, **kwargs),
        build_repo_dashboard_view_fn=lambda active_db_path, repo_full: build_repo_dashboard_view(active_db_path, repo_full),
    )
)


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


def pending_repo_baselines(request: Request, repo_full: str):
    _require_repo_dashboard_read_access(request, repo_full)
    panel = build_repo_baseline_review_panel(AUDIT_DB_PATH, repo_full)
    if panel is None:
        raise HTTPException(status_code=404, detail="Repository onboarding was not found.")
    return JSONResponse(asdict(panel))


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


app.include_router(
    create_repo_baseline_router(
        authorize_repo_read_fn=lambda request, repo_full: _require_repo_dashboard_read_access(request, repo_full),
        authorize_repo_mutation_fn=lambda request, repo_full: _require_repo_dashboard_mutation_access(request, repo_full),
        resolve_db_path_fn=lambda: AUDIT_DB_PATH,
        build_repo_dashboard_view_fn=lambda active_db_path, repo_full: build_repo_dashboard_view(active_db_path, repo_full),
        build_repo_journey_fn=lambda active_db_path, repo_full: build_repo_journey(active_db_path, repo_full),
        promote_latest_source_to_onboarding_baseline_fn=lambda active_db_path, repo_full, artifact_path: promote_latest_source_to_onboarding_baseline(active_db_path, repo_full, artifact_path),
        build_repo_baseline_review_panel_fn=lambda active_db_path, repo_full: build_repo_baseline_review_panel(active_db_path, repo_full),
        approve_repo_baseline_artifact_fn=lambda active_db_path, **kwargs: approve_repo_baseline_artifact(active_db_path, **kwargs),
        reject_repo_baseline_artifact_fn=lambda active_db_path, **kwargs: reject_repo_baseline_artifact(active_db_path, **kwargs),
        approve_repo_baseline_fn=lambda active_db_path, **kwargs: approve_repo_baseline(active_db_path, **kwargs),
        reject_repo_baseline_fn=lambda active_db_path, **kwargs: reject_repo_baseline(active_db_path, **kwargs),
        rebaseline_repo_from_snapshot_fn=lambda active_db_path, **kwargs: rebaseline_repo_from_snapshot(active_db_path, **kwargs),
        resolve_actor_login_fn=lambda request, _payload: _dashboard_actor_login(request),
        github_app_id=GITHUB_APP_ID,
        github_private_key_path=GITHUB_PRIVATE_KEY_PATH,
        generate_jwt_fn=lambda app_id, private_key_path: generate_jwt(
            app_id,
            private_key_path,
            settings.resolved_github_private_key,
        ),
        get_installation_token_fn=lambda jwt_token, installation_id: get_installation_token(jwt_token, installation_id),
        fetch_file_content_fn=lambda repo, path, token, ref: fetch_file_content(repo, path, token, ref),
    )
)


async def create_compliance_export(repo_full: str, payload: ComplianceExportRequest, request: Request):
    access_context = _require_repo_dashboard_mutation_access(request, repo_full)
    if _is_active_comments_only_workspace(access_context):
        raise HTTPException(status_code=403, detail="Upgrade to Starter to generate compliance exports.")
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
        job = _run_compliance_export_job(
            repo_full=repo_full,
            from_ts=from_ts,
            to_ts=to_ts,
            export_mode=payload.export_mode,
            include_artifact_content=payload.include_artifact_content,
            workspace_id=workspace.id if workspace is not None else None,
            requested_by_user_id=session.user_id if session is not None else None,
            requested_by_github_login=_dashboard_actor_login(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="An identical export request is already in progress. Change the date range or wait for it to finish.")
    except Exception as exc:
        safe_error = _user_safe_compliance_export_error(exc)
        if "job" in locals():
            update_export_job_status(AUDIT_DB_PATH, job.id, "failed", last_error=safe_error)
        raise HTTPException(status_code=500, detail=safe_error) from exc
    return JSONResponse({
        "job_id": job.id,
        "status": job.status,
        "download_url": _export_download_url(job),
    })


app.include_router(
    create_export_create_router(
        create_export_handler=create_compliance_export,
    )
)


app.include_router(
    create_export_job_router(
        resolve_db_path_fn=lambda: AUDIT_DB_PATH,
        get_export_job_fn=lambda active_db_path, job_id: get_export_job(active_db_path, job_id),
        authorize_export_job_access_fn=lambda request, job: _require_export_job_owner_access(request, job),
        export_job_payload_fn=_export_job_payload,
        build_export_download_response_fn=_build_export_download_response,
    )
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


@app.get("/feedback/pr/{owner}/{repo}/{pr_number}", response_class=HTMLResponse)
async def pr_feedback_form(owner: str, repo: str, pr_number: int, head_sha: str | None = None, audit_id: int | None = None):
    resolved_audit = _resolve_feedback_target_audit(owner, repo, pr_number, audit_id=audit_id, head_sha=head_sha)
    if resolved_audit is None:
        raise HTTPException(status_code=404, detail="Audit feedback target was not found.")

    escaped_repo_full = html.escape(resolved_audit.repo_full)
    escaped_head_sha = html.escape(resolved_audit.head_sha)
    return HTMLResponse(
        f"""
        <!DOCTYPE html>
        <html lang=\"en\">
        <head><meta charset=\"utf-8\"><title>Vipari Feedback</title></head>
        <body>
        <main>
        <h1>Vipari review feedback</h1>
        <p>PR #{resolved_audit.pr_number} for {escaped_repo_full}</p>
        <form method=\"post\" action=\"/feedback/pr/{html.escape(owner)}/{html.escape(repo)}/{resolved_audit.pr_number}\">
            <input type=\"hidden\" name=\"audit_id\" value=\"{resolved_audit.id}\">
            <input type=\"hidden\" name=\"head_sha\" value=\"{escaped_head_sha}\">
            <label><input type=\"radio\" name=\"sentiment\" value=\"helpful\" checked> This was helpful</label><br>
            <label><input type=\"radio\" name=\"sentiment\" value=\"noisy\"> This was noisy</label><br>
            <label><input type=\"radio\" name=\"sentiment\" value=\"strongly_disagree\"> We strongly disagree</label><br>
            <label for=\"notes\">Notes</label><br>
            <textarea id=\"notes\" name=\"notes\" rows=\"5\" cols=\"60\"></textarea><br>
            <button type=\"submit\">Send feedback</button>
        </form>
        </main>
        </body>
        </html>
        """
    )


@app.post("/feedback/pr/{owner}/{repo}/{pr_number}")
async def pr_feedback_submit(
    owner: str,
    repo: str,
    pr_number: int,
    audit_id: int = Form(...),
    sentiment: str = Form(...),
    notes: str | None = Form(default=None),
    head_sha: str | None = Form(default=None),
):
    bounded_notes = (notes or "").strip()
    if sentiment not in {"helpful", "noisy", "strongly_disagree"}:
        raise HTTPException(status_code=400, detail="Invalid feedback sentiment.")
    if len(bounded_notes) > 2000:
        raise HTTPException(status_code=400, detail="Feedback notes must be 2000 characters or fewer.")

    audit = _resolve_feedback_target_audit(owner, repo, pr_number, audit_id=audit_id, head_sha=head_sha)
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit feedback target was not found.")

    record_audit_feedback_event(
        AUDIT_DB_PATH,
        audit_id=audit.id,
        kind="explicit_feedback",
        source="feedback_link",
        payload_json=json.dumps(
            {
                "sentiment": sentiment,
                "notes": bounded_notes,
                "repo_full": audit.repo_full,
                "pr_number": audit.pr_number,
            }
        ),
    )
    return HTMLResponse("<html><body><p>Thanks. Your feedback was recorded.</p></body></html>")


def _resolve_feedback_target_audit(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    audit_id: int | None,
    head_sha: str | None,
):
    repo_full = f"{owner}/{repo}"
    resolved_audit = None
    if audit_id is not None:
        candidate_audit = get_pull_request_audit_by_id(AUDIT_DB_PATH, audit_id)
        if candidate_audit is not None and candidate_audit.repo_full == repo_full and candidate_audit.pr_number == pr_number:
            if not head_sha or candidate_audit.head_sha == head_sha:
                resolved_audit = candidate_audit

    if resolved_audit is None and head_sha:
        episode = get_audit_comment_episode_for_pr_head_sha(AUDIT_DB_PATH, repo_full, pr_number, head_sha)
        if episode is not None:
            resolved_audit = get_pull_request_audit_by_id(AUDIT_DB_PATH, episode.audit_comment.audit_id)

    if resolved_audit is None:
        latest_comment = get_latest_audit_comment_for_pr(AUDIT_DB_PATH, repo_full, pr_number)
        if latest_comment is not None:
            candidate_audit = get_pull_request_audit_by_id(AUDIT_DB_PATH, latest_comment.audit_id)
            if candidate_audit is not None and (not head_sha or candidate_audit.head_sha == head_sha):
                resolved_audit = candidate_audit

    return resolved_audit


@app.post("/webhook")
async def webhook(request: Request):
    if settings.service_role == "api":
        raise HTTPException(status_code=404, detail="Webhook ingress is not enabled on the API service.")
    if not await verify_signature(request):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event not in {"pull_request", "push", "installation", "installation_repositories"}:
        return JSONResponse({"message": "ignored"})

    payload = await request.json()
    if event == "installation":
        action = str(payload.get("action") or "").strip().lower()
        installation = payload.get("installation") or {}
        installation_id = installation.get("id")
        if not installation_id:
            raise HTTPException(status_code=400, detail="Missing installation id")
        account = installation.get("account") if isinstance(installation, dict) else {}
        updated_installation = apply_github_installation_lifecycle_event(
            AUDIT_DB_PATH,
            installation_id=int(installation_id),
            action=action,
            account_id=str(account.get("id") or "") if isinstance(account, dict) else "",
            account_login=str(account.get("login") or "") if isinstance(account, dict) else "",
            account_type=str(account.get("type") or "Organization") if isinstance(account, dict) else "Organization",
            target_type=str(payload.get("target_type") or "Organization"),
        )
        if updated_installation is None:
            return JSONResponse({"message": "ignored"})
        return JSONResponse({"message": "installation status updated", "status": updated_installation.status})
    if event == "installation_repositories":
        installation = payload.get("installation") or {}
        installation_id = installation.get("id")
        if not installation_id:
            raise HTTPException(status_code=400, detail="Missing installation id")
        result = apply_github_installation_repository_event(
            AUDIT_DB_PATH,
            installation_id=int(installation_id),
            repositories_added=payload.get("repositories_added") if isinstance(payload.get("repositories_added"), list) else [],
            repositories_removed=payload.get("repositories_removed") if isinstance(payload.get("repositories_removed"), list) else [],
        )
        if result is None:
            return JSONResponse({"message": "ignored"})
        return JSONResponse({"message": "installation repositories updated", **result})

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
    base_info = pull_request.get("base", {})
    base_sha = base_info.get("sha")
    base_ref = base_info.get("ref")
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
        record_pr_outcome_feedback_events(
            AUDIT_DB_PATH,
            repo_full=repo_full,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_state=pr_state,
            pr_merged=pr_merged,
        )
        try:
            jwt_token = generate_jwt(GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH, settings.resolved_github_private_key)
            token = get_installation_token(jwt_token, installation_id)
            refresh_audit_reaction_feedback_for_pr(
                AUDIT_DB_PATH,
                repo_full=repo_full,
                pr_number=pr_number,
                head_sha=head_sha,
                token=token,
            )
        except Exception:
            pass
        branch_scan_job_id = None
        if action == "closed" and pr_merged and pr_merge_commit_sha:
            onboarding = get_latest_repository_onboarding(AUDIT_DB_PATH, str(repo_full))
            if onboarding is not None:
                branch_ref = f"refs/heads/{base_ref}" if base_ref else f"refs/heads/{onboarding.default_branch}"
                branch_scan_job = create_branch_scan_job(
                    AUDIT_DB_PATH,
                    repo_full=str(repo_full),
                    installation_id=int(installation_id),
                    commit_sha=str(pr_merge_commit_sha),
                    branch_ref=branch_ref,
                    triggered_by="pr_merged_webhook",
                )
                branch_scan_job_id = branch_scan_job.id
        payload = {"message": "pr state updated"}
        if branch_scan_job_id is not None:
            payload["branch_scan_job_id"] = branch_scan_job_id
        return JSONResponse(payload)

    if not head_sha:
        raise HTTPException(status_code=400, detail="Missing payload data")

    jwt_token = generate_jwt(GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH, settings.resolved_github_private_key)
    token = get_installation_token(jwt_token, installation_id)
    diff_text = await fetch_diff_with_retry(
        repo_full,
        pr_number,
        token,
        use_commit_pair=action == "synchronize",
        base_sha=base_sha,
        head_sha=head_sha,
    )

    audit_decision = evaluate_and_persist_audit_decision(
        AUDIT_DB_PATH,
        repo_full=repo_full,
        pr_number=pr_number,
        head_sha=head_sha,
        diff_text=diff_text,
        llm_client=client,
        model=(AI_MODEL if client is not None else None),
        timeout_seconds=RELEVANCE_MICRO_CLASSIFIER_TIMEOUT_SECONDS,
        provider=AI_PROVIDER,
    )

    if not audit_decision.should_audit:
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
