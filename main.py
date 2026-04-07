import asyncio
import hashlib
import hmac
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from urllib.error import HTTPError

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from github.GithubException import GithubException
from openai import OpenAI
from pydantic import BaseModel

from config import get_settings
from engine.relevance import needs_audit as engine_needs_audit
from services.access_state import WorkspaceAccessSnapshot, resolve_workspace_access_state
from services.audit_jobs import create_audit_job, init_db, update_job_pr_state
from services.audit_records import update_pull_request_audit_state
from services.audit_worker import AuditWorker, WorkerSettings
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
    render_control_plane_app_page,
    render_control_plane_billing_page,
    render_control_plane_install_page,
    render_control_plane_login_page,
    render_control_plane_marketing_page,
    render_control_plane_pricing_page,
    render_control_plane_repo_setup_page,
    render_control_plane_workspace_new_page,
    render_repo_allocation_cards,
    render_repo_connection_cards,
)
from services.control_plane_records import (
    allocate_repo_to_workspace,
    count_workspace_repo_allocations,
    count_workspaces,
    create_user_session,
    create_workspace,
    get_billing_customer_for_workspace,
    get_github_identity_for_user,
    get_repo_connection_for_workspace,
    get_user_by_id,
    get_user_session,
    get_workspace_by_id,
    get_workspace_entitlement,
    get_workspace_installation,
    get_workspace_membership,
    get_workspace_subscription,
    has_processed_webhook_event,
    list_repo_allocations_for_workspace,
    list_repo_connections_for_workspace,
    list_workspace_memberships_for_user,
    record_webhook_event,
    replace_repo_connections,
    revoke_user_session,
    update_repo_allocation_status,
    update_session_workspace,
    upsert_billing_customer,
    upsert_entitlement,
    upsert_github_identity,
    upsert_github_installation,
    upsert_subscription,
)
from services.dashboard_frontend import DASHBOARD_STATIC_DIR, render_dashboard_index_page, render_repo_dashboard_page
from services.dashboard_views import build_dashboard_overview_view, build_repo_artifact_storyline, build_repo_dashboard_view, list_repo_dashboard_index
from services.entitlements import get_plan_definition
from services.github_integration import fetch_commit_pair_diff, fetch_pr_diff, generate_jwt, get_installation_token
from services.github_provisioning import get_live_github_install_url, sync_installation_repositories
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from services.onboarding_records import promote_latest_source_to_onboarding_baseline
from services.persistence import get_persistence_status
from services.secure_store import encrypt_text

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

client = OpenAI(api_key=AI_API_KEY, base_url=AZURE_OPENAI_ENDPOINT or None) if AI_API_KEY else None
worker: AuditWorker | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker
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
    try:
        yield
    finally:
        if worker is not None:
            worker.stop()
            worker = None


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(DASHBOARD_STATIC_DIR)), name="static")


class RepositoryOnboardingRequest(BaseModel):
    installation_id: int
    commit_limit_per_artifact: int = 10
    plan_backfill: bool = True
    execute_backfill: bool = False


class RepositoryBackfillRequest(BaseModel):
    installation_id: int


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
        dashboard_enabled=bool(entitlement.dashboard_enabled) if entitlement else subscription_status in {"active", "trialing", "canceled"},
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


def _require_workspace_role(access_context: dict[str, object], *allowed_roles: str) -> None:
    membership = access_context.get("membership")
    if membership is None or membership.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="This action requires a workspace owner or admin role.")


@app.get("/", response_class=HTMLResponse)
async def marketing_page():
    return HTMLResponse(render_control_plane_marketing_page())


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    return HTMLResponse(render_control_plane_pricing_page())


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(render_control_plane_login_page())


@app.get("/auth/github/start")
async def github_auth_start(request: Request):
    if not settings.has_github_oauth_credentials:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured.")
    state = generate_oauth_state()
    authorize_url = build_github_oauth_authorize_url(
        settings.github_oauth_client_id,
        _github_oauth_callback_url(request),
        state,
    )
    response = RedirectResponse(authorize_url, status_code=302)
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
async def github_auth_callback(request: Request, code: str, state: str):
    expected_state = request.cookies.get(CONTROL_PLANE_OAUTH_STATE_COOKIE)
    if not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="OAuth state validation failed.")

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
    destination = "/app" if workspace_id else "/app/workspaces/new"
    response = RedirectResponse(destination, status_code=303)
    _set_session_cookie(response, session.session_id)
    response.delete_cookie(CONTROL_PLANE_OAUTH_STATE_COOKIE)
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
    if session is None and state:
        return HTMLResponse(render_control_plane_app_page(state=state))
    if session is None:
        return RedirectResponse("/login", status_code=303)
    access_context = _build_access_context(session)
    return HTMLResponse(render_control_plane_app_page(resolution=access_context["resolution"]))


@app.get("/app/workspaces/new", response_class=HTMLResponse)
async def workspace_new_page(request: Request):
    if _get_session(request) is None:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(render_control_plane_workspace_new_page())


@app.post("/app/workspaces/bootstrap")
async def workspace_bootstrap(request: Request, name: str | None = Form(default=None)):
    session = _get_session(request)
    if session is None:
        return RedirectResponse("/login", status_code=303)
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
    return RedirectResponse("/app", status_code=303)


@app.get("/app/billing", response_class=HTMLResponse)
async def billing_page(request: Request):
    access_context = _current_workspace_context(request)
    workspace = access_context["workspace"]
    subscription = access_context["subscription"]
    entitlement = access_context["entitlement"]
    customer = get_billing_customer_for_workspace(AUDIT_DB_PATH, workspace.id)
    plan_code = entitlement.plan_code if entitlement else subscription.plan_code if subscription else "starter"
    current_plan_label = get_plan_definition(plan_code).label if plan_code else "No plan"
    portal_url = "/app/billing/portal" if customer else None
    checkout_url = request.url.path
    if request.url.query:
        checkout_url = f"{checkout_url}?{request.url.query}"
    return HTMLResponse(
        render_control_plane_billing_page(
            workspace_name=workspace.display_name,
            current_plan_label=current_plan_label,
            subscription_status=subscription.status if subscription else "not_started",
            checkout_url=checkout_url,
            portal_url=portal_url,
        )
    )


@app.post("/app/billing/checkout")
async def billing_checkout(request: Request, plan: str):
    access_context = _current_workspace_context(request)
    _require_workspace_role(access_context, "owner", "admin")
    workspace = access_context["workspace"]
    existing_customer = get_billing_customer_for_workspace(AUDIT_DB_PATH, workspace.id)
    checkout = create_checkout_session(
        settings=settings,
        workspace_id=workspace.id,
        workspace_slug=workspace.slug,
        plan_code=plan,
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
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    return RedirectResponse(checkout.checkout_url, status_code=303)


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
    return HTMLResponse(
        render_control_plane_install_page(
            workspace_name=workspace.display_name,
            install_hint="Billing is active. The next gate is granting GitHub App installation authority.",
            installation_summary=installation_summary,
            install_url=install_url,
        )
    )


@app.post("/app/setup/install/link")
async def install_link(
    request: Request,
    installation_id: str = Form(default=""),
    account_login: str = Form(default=""),
    account_type: str = Form(default="Organization"),
    repo_fulls: str = Form(default=""),
):
    access_context = _current_workspace_context(request)
    _require_workspace_role(access_context, "owner", "admin")
    workspace = access_context["workspace"]
    installation_id_int = int(installation_id)
    repositories: list[dict[str, object]] = []
    account_id = account_login or str(installation_id_int)
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
            installation_id=installation_id_int,
        )
        account = installation_payload.get("account") if isinstance(installation_payload, dict) else {}
        if isinstance(account, dict):
            account_login = str(account.get("login") or account_login)
            account_id = str(account.get("id") or account_id)
            account_type = str(account.get("type") or account_type)
        target_type = str(installation_payload.get("target_type") or target_type)

    upsert_github_installation(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation_id_int,
        account_id=account_id,
        account_login=account_login or str(installation_id_int),
        account_type=account_type or "Organization",
        target_type=target_type,
    )
    replace_repo_connections(
        AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation_id_int,
        repositories=repositories,
    )
    return RedirectResponse("/app/setup/repos", status_code=303)


@app.get("/app/setup/repos", response_class=HTMLResponse)
async def repo_setup_page(request: Request):
    access_context = _current_workspace_context(request)
    workspace = access_context["workspace"]
    connections = [asdict(item) for item in list_repo_connections_for_workspace(AUDIT_DB_PATH, workspace.id)]
    allocations = [asdict(item) for item in list_repo_allocations_for_workspace(AUDIT_DB_PATH, workspace.id)]
    return HTMLResponse(
        render_control_plane_repo_setup_page(
            workspace_name=workspace.display_name,
            repo_cards=render_repo_connection_cards(connections),
            allocation_cards=render_repo_allocation_cards(allocations),
        )
    )


@app.post("/app/setup/repos/allocate")
async def repo_allocate(request: Request, repo_full: str):
    access_context = _current_workspace_context(request)
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
        "session": asdict(access_context["session"]),
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


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_index_page(request: Request):
    redirect, _session = _dashboard_redirect_for_request(request)
    if redirect is not None:
        return redirect
    return HTMLResponse(render_dashboard_index_page())


@app.get("/dashboard/{repo_full:path}", response_class=HTMLResponse)
async def dashboard_repo_page(request: Request, repo_full: str):
    redirect, _session = _dashboard_redirect_for_request(request)
    if redirect is not None:
        return redirect
    return HTMLResponse(render_repo_dashboard_page(repo_full))


@app.get("/api/repos")
async def list_repos():
    return JSONResponse({"repos": [asdict(item) for item in list_repo_dashboard_index(AUDIT_DB_PATH)]})


@app.get("/api/dashboard/overview")
async def dashboard_overview():
    return JSONResponse(asdict(build_dashboard_overview_view(AUDIT_DB_PATH)))


@app.get("/api/persistence")
async def persistence_status():
    payload = asdict(get_persistence_status(AUDIT_DB_PATH))
    payload.pop("database_path", None)
    return JSONResponse(payload)


@app.get("/api/repos/{repo_full:path}/dashboard")
async def repo_dashboard(repo_full: str):
    return JSONResponse(asdict(build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)))


@app.get("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/episodes")
async def artifact_storyline(repo_full: str, artifact_path: str):
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


@app.post("/api/repos/{repo_full:path}/onboard")
async def run_repo_onboarding(repo_full: str, payload: RepositoryOnboardingRequest):
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
async def run_repo_backfill(repo_full: str, payload: RepositoryBackfillRequest):
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
async def promote_artifact_baseline(repo_full: str, artifact_path: str):
    baseline = promote_latest_source_to_onboarding_baseline(AUDIT_DB_PATH, repo_full, artifact_path)
    if baseline is None:
        raise HTTPException(status_code=404, detail="No stored source version is available to promote as baseline.")
    dashboard = build_repo_dashboard_view(AUDIT_DB_PATH, repo_full)
    return JSONResponse(
        {
            "repo_full": repo_full,
            "artifact_path": artifact_path,
            "baseline": asdict(baseline),
            "dashboard": asdict(dashboard),
        }
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

        if projection["stripe_customer_id"]:
            upsert_billing_customer(
                AUDIT_DB_PATH,
                workspace_id=projection["workspace_id"],
                stripe_customer_id=projection["stripe_customer_id"],
                billing_email=projection["billing_email"],
            )
        upsert_subscription(
            AUDIT_DB_PATH,
            workspace_id=projection["workspace_id"],
            stripe_subscription_id=str(projection["stripe_subscription_id"] or event_id or "stripe-event"),
            stripe_price_id=str(projection["stripe_price_id"] or ""),
            plan_code=str(projection["plan_code"]),
            status=str(projection["status"]),
            cancel_at_period_end=bool(projection["cancel_at_period_end"]),
            current_period_start_at=projection["current_period_start_at"],
            current_period_end_at=projection["current_period_end_at"],
            trial_ends_at=projection["trial_ends_at"],
            last_webhook_event_id=event_id or None,
        )
        upsert_entitlement(
            AUDIT_DB_PATH,
            workspace_id=projection["workspace_id"],
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
    if event != "pull_request":
        return JSONResponse({"message": "ignored"})

    payload = await request.json()
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
