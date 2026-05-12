from __future__ import annotations

import collections
import hmac
import io
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal

from config import get_settings
from .api_models import BaselineDecisionRequest, RepoRebaselineRequest, RepositoryBackfillRequest, RepositoryOnboardingRequest
from .cp_auth import require_cp_principal, require_cp_principal_kind, require_cp_scope, require_cp_workspace_match
from .control_plane_records import (
    create_control_plane_audit_log,
    create_machine_principal,
    count_machine_principals_for_workspace,
    get_machine_principal_by_client_id,
    get_machine_principal_by_id,
    get_repo_allocation_for_workspace,
    get_workspace_by_id,
    get_workspace_entitlement,
    list_control_plane_audit_logs_for_workspace,
    list_machine_principals_for_workspace,
    revoke_machine_principal,
)
from .dashboard_api_payloads import build_artifact_storyline_payload, build_dashboard_escalation_queue_payload, build_dashboard_overview_payload, build_pending_proposals_payload, build_repo_index_payload, build_repo_journey_payload, build_repo_snapshot_compare_payload, build_repo_snapshot_detail_payload
from .dashboard_frontend import DASHBOARD_STATIC_DIR, render_dashboard_index_page, render_repo_dashboard_page
from .dashboard_views import build_dashboard_overview_view, build_repo_artifact_storyline, build_repo_dashboard_view, build_workspace_escalation_queue, list_repo_dashboard_index
from .github_integration import fetch_file_content, generate_jwt, get_installation_token
from .internal_auth import (
    ALL_SCOPES,
    PRINCIPAL_KIND_HUMAN_OPERATOR,
    SCOPE_ADMIN_READ,
    SCOPE_ADMIN_WRITE,
    SCOPE_DRIFT_READ,
    SCOPE_DRIFT_WRITE_HIGH,
    SCOPE_DRIFT_WRITE_LOW,
    issue_cp_token,
    validate_cp_token,
    validate_scope_kind_compatibility,
)
from .proposals_records import (
    approve_baseline_proposal,
    approve_onboarding_proposal,
    create_baseline_proposal,
    create_onboarding_proposal,
    get_baseline_proposal,
    get_onboarding_proposal,
    list_baseline_proposals,
    list_onboarding_proposals,
    reject_baseline_proposal,
    reject_onboarding_proposal,
)
from .observability import configure_logging, instrument_fastapi
from .onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from .baseline_approval_service import (
    approve_repo_baseline,
    approve_repo_baseline_artifact,
    build_repo_baseline_review_panel,
    reject_repo_baseline,
    rebaseline_repo_from_snapshot,
    reject_repo_baseline_artifact,
)
from .compliance_export_service import ComplianceExportRequest as ComplianceExportServiceRequest, build_compliance_export
from .export_jobs import create_export_job, get_export_job, list_export_jobs_for_repo, list_export_jobs_for_requester
from .onboarding_records import get_onboarded_artifact_by_id, promote_latest_source_to_onboarding_baseline
from .persistence import get_persistence_status, persistence_status_payload
from .repo_journey import build_repo_journey, compare_repo_snapshots, get_repo_snapshot_detail, snapshot_to_public_payload
from .secure_store import decrypt_text, encrypt_text
from .audit_jobs import init_db
from .runtime_guardrails import build_runtime_readiness, readiness_json_response, validate_runtime_configuration
from .static_assets import FingerprintedStaticFiles
from routers.dashboard import create_dashboard_page_router, create_dashboard_read_router, create_export_create_router, create_export_job_router, create_repo_baseline_router, create_repo_dashboard_router, create_repo_history_router, create_repo_onboarding_router, create_repo_read_router
from routers.health import create_health_router
from .audit_feedback_records import (
    VALID_FEEDBACK_KINDS,
    VALID_TRIAGE_STATES,
    add_audit_feedback,
    add_audit_triage,
)
from .audit_records import get_pull_request_audit_by_id

# Module-level constant to avoid allocating a new frozenset on every approve request.
_HUMAN_ONLY_KINDS: frozenset[str] = frozenset({PRINCIPAL_KIND_HUMAN_OPERATOR})


class ComplianceExportRequest(BaseModel):
    from_date: str  # YYYY-MM-DD
    to_date: str    # YYYY-MM-DD
    export_mode: str  # "compliance" | "compliance_plus_drift"
    include_artifact_content: bool = False


class CreatePrincipalRequest(BaseModel):
    workspace_id: int
    display_name: str = Field(..., min_length=1, max_length=120)
    principal_kind: Literal["service_account", "human_operator"] = "service_account"
    scopes: list[str]


class BaselineProposalRequest(BaseModel):
    snapshot_id: int | None = None
    rationale: str = Field(default="", max_length=2000)
    linked_audit_ids: list[int] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class ProposalDecisionRequest(BaseModel):
    decision_note: str | None = Field(default=None, max_length=2000)


class OnboardingProposalRequest(BaseModel):
    repo_full: str = Field(..., min_length=1, max_length=300, pattern=r'^[^/]+/[^/]+$')
    installation_id: int | None = None
    proposed_category: str | None = Field(default=None, max_length=80)
    rationale: str = Field(default="", max_length=2000)
    metadata: dict[str, str] = Field(default_factory=dict)


class IssuePrincipalTokenRequest(BaseModel):
    workspace_id: int


class ClientCredentialsRequest(BaseModel):
    client_id: str
    client_secret: str


class AuditFeedbackRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=80)
    kind: str
    comment: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, str] = Field(default_factory=dict)

    def model_post_init(self, __context: object) -> None:
        if len(self.metadata) > 20:
            raise ValueError("metadata may contain at most 20 keys")
        for k, v in self.metadata.items():
            if len(k) > 80:
                raise ValueError("metadata key must be ≤ 80 characters")
            if len(v) > 500:
                raise ValueError("metadata value must be ≤ 500 characters")


class AuditTriageRequest(BaseModel):
    state: str
    reason: str | None = Field(default=None, max_length=2000)


class _SlidingWindowRateLimiter:
    """Thread-safe in-process sliding-window rate limiter keyed by string.

    Tracks request timestamps per key inside a fixed-size deque.  Any call
    that would exceed ``limit`` requests within ``window_seconds`` returns
    False; callers should respond with HTTP 429.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._buckets: dict[str, collections.deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = collections.deque()
            bucket = self._buckets[key]
            # Evict timestamps outside the window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
            return True


# 20 token-exchange attempts per IP per minute
_token_endpoint_limiter = _SlidingWindowRateLimiter(limit=20, window_seconds=60.0)


def _require_admin_token(request: Request, settings) -> None:
    configured_token = settings.api_admin_token
    if not configured_token:
        raise HTTPException(status_code=503, detail="API admin token is not configured.")

    authorization = request.headers.get("Authorization", "")
    bearer_prefix = "Bearer "
    provided_token = request.headers.get("X-Admin-Token", "")
    if authorization.startswith(bearer_prefix):
        provided_token = authorization[len(bearer_prefix):].strip()

    if not provided_token or not hmac.compare_digest(provided_token, configured_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


def create_api_app() -> FastAPI:
    settings = get_settings()
    db_path = settings.resolved_db_path
    logger = configure_logging("api")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        validate_runtime_configuration(settings)
        init_db(db_path)
        yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", FingerprintedStaticFiles(directory=str(DASHBOARD_STATIC_DIR)), name="static")
    instrument_fastapi(app, enabled=settings.enable_metrics)
    app.include_router(create_health_router(settings))

    async def dashboard_index_page(request: Request):
        _require_admin_token(request, settings)
        return HTMLResponse(render_dashboard_index_page())

    async def dashboard_repo_page(repo_full: str, request: Request):
        _require_admin_token(request, settings)
        return HTMLResponse(render_repo_dashboard_page(repo_full))

    async def dashboard_repo_audit_page(request: Request, repo_full: str, artifact: str | None = None, pr: str | None = None, head_sha: str | None = None):
        _require_admin_token(request, settings)
        return HTMLResponse(
            render_repo_dashboard_page(
                repo_full,
                active_tab="audit",
                deep_link_artifact=artifact or "",
                deep_link_pr=pr or "",
                deep_link_head_sha=head_sha or "",
            )
        )

    async def list_repos(request: Request):
        _require_admin_token(request, settings)
        return JSONResponse(build_repo_index_payload(db_path, list_repo_dashboard_index_fn=list_repo_dashboard_index))

    def dashboard_overview(request: Request):
        _require_admin_token(request, settings)
        return JSONResponse(build_dashboard_overview_payload(db_path, build_dashboard_overview_view_fn=build_dashboard_overview_view))

    def dashboard_escalation_queue(request: Request, include_watch: bool = False):
        _require_admin_token(request, settings)
        result = build_dashboard_escalation_queue_payload(
            db_path,
            include_watch=include_watch,
            build_workspace_escalation_queue_fn=build_workspace_escalation_queue,
        )
        return JSONResponse(result)

    def list_pending_proposals_for_repo(repo_full: str, request: Request):
        _require_admin_token(request, settings)
        from .proposals_records import list_pending_baseline_proposals_for_repo
        from .onboarding_records import list_onboarded_artifacts_for_onboarding, get_latest_repository_onboarding
        from .internal_auth import PRINCIPAL_KIND_SERVICE_ACCOUNT
        return JSONResponse(
            build_pending_proposals_payload(
                db_path,
                repo_full,
                workspace_id=0,
                list_pending_proposals_fn=lambda active_db_path, active_repo_full, _workspace_id: list_pending_baseline_proposals_for_repo(active_db_path, active_repo_full),
                get_latest_repository_onboarding_fn=get_latest_repository_onboarding,
                list_onboarded_artifacts_for_onboarding_fn=list_onboarded_artifacts_for_onboarding,
                get_machine_principal_by_id_fn=get_machine_principal_by_id,
                service_account_principal_kind=PRINCIPAL_KIND_SERVICE_ACCOUNT,
            )
        )

    def persistence_status(request: Request):
        _require_admin_token(request, settings)
        return JSONResponse(persistence_status_payload(get_persistence_status(db_path)))

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
        create_repo_read_router(
            pending_proposals_handler=list_pending_proposals_for_repo,
        )
    )

    app.include_router(
        create_repo_dashboard_router(
            authorize_repo_read_fn=lambda request, _repo_full: _require_admin_token(request, settings) or {},
            resolve_db_path_fn=lambda: db_path,
            build_repo_dashboard_view_with_timings_fn=lambda active_db_path, repo_full: (build_repo_dashboard_view(active_db_path, repo_full), []),
            list_export_jobs_for_requester_fn=list_export_jobs_for_requester,
            export_job_payload_fn=lambda job: job,
            record_server_timing_metric_fn=lambda metrics, name, started: None,
            attach_server_timing_fn=lambda response, _metrics: response,
        )
    )

    app.include_router(
        create_repo_history_router(
            authorize_repo_read_fn=lambda request, _repo_full: _require_admin_token(request, settings),
            resolve_db_path_fn=lambda: db_path,
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
            authorize_repo_mutation_fn=lambda request, _repo_full: _require_admin_token(request, settings),
            resolve_installation_id_fn=lambda _auth_context, installation_id: installation_id,
            resolve_db_path_fn=lambda: db_path,
            github_app_id=settings.github_app_id,
            github_private_key_path=settings.github_private_key_path,
            generate_jwt_fn=lambda app_id, private_key_path: generate_jwt(app_id, private_key_path, settings.resolved_github_private_key),
            get_installation_token_fn=lambda jwt_token, installation_id: get_installation_token(jwt_token, installation_id),
            onboard_repository_fn=lambda active_db_path, **kwargs: onboard_repository(active_db_path, **kwargs),
            plan_repository_history_backfill_fn=lambda active_db_path, **kwargs: plan_repository_history_backfill(active_db_path, **kwargs),
            execute_repository_history_backfill_fn=lambda active_db_path, **kwargs: execute_repository_history_backfill(active_db_path, **kwargs),
            build_repo_dashboard_view_fn=lambda active_db_path, repo_full: build_repo_dashboard_view(active_db_path, repo_full),
        )
    )

    app.include_router(
        create_repo_baseline_router(
            authorize_repo_read_fn=lambda request, _repo_full: _require_admin_token(request, settings),
            authorize_repo_mutation_fn=lambda request, _repo_full: _require_admin_token(request, settings),
            resolve_db_path_fn=lambda: db_path,
            build_repo_dashboard_view_fn=build_repo_dashboard_view,
            build_repo_journey_fn=build_repo_journey,
            promote_latest_source_to_onboarding_baseline_fn=promote_latest_source_to_onboarding_baseline,
            build_repo_baseline_review_panel_fn=build_repo_baseline_review_panel,
            approve_repo_baseline_artifact_fn=approve_repo_baseline_artifact,
            reject_repo_baseline_artifact_fn=reject_repo_baseline_artifact,
            approve_repo_baseline_fn=approve_repo_baseline,
            reject_repo_baseline_fn=reject_repo_baseline,
            rebaseline_repo_from_snapshot_fn=rebaseline_repo_from_snapshot,
            resolve_actor_login_fn=lambda _request, payload: payload.actor_login,
            github_app_id=settings.github_app_id,
            github_private_key_path=settings.github_private_key_path,
            generate_jwt_fn=lambda app_id, private_key_path: generate_jwt(app_id, private_key_path, settings.resolved_github_private_key),
            get_installation_token_fn=get_installation_token,
            fetch_file_content_fn=fetch_file_content,
        )
    )

    async def create_compliance_export(repo_full: str, payload: ComplianceExportRequest, request: Request):
        _require_admin_token(request, settings)
        try:
            from_ts = datetime.fromisoformat(payload.from_date).timestamp()
            to_ts = datetime.fromisoformat(payload.to_date).timestamp()
            if payload.export_mode not in ["compliance", "compliance_plus_drift"]:
                raise HTTPException(status_code=400, detail="Invalid export_mode")
            job = create_export_job(
                db_path,
                repo_full=repo_full,
                from_ts=from_ts,
                to_ts=to_ts,
                export_mode=payload.export_mode,
                include_artifact_content=payload.include_artifact_content,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"job_id": job.id})

    app.include_router(
        create_export_create_router(
            create_export_handler=create_compliance_export,
        )
    )

    def export_status_payload(job):
        return {
            "job_id": job.id,
            "status": job.status,
            "export_mode": job.export_mode,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "result_size_bytes": job.result_size_bytes,
            "last_error": job.last_error,
        }

    def build_export_download_response(active_db_path: str, job, _token: str | None):
        if job.status != "completed" or not job.download_token:
            raise HTTPException(status_code=404, detail="Export not available")
        try:
            result = build_compliance_export(
                active_db_path,
                ComplianceExportServiceRequest(
                    repo_full=job.repo_full,
                    from_ts=job.from_ts,
                    to_ts=job.to_ts,
                    export_mode=job.export_mode,
                    include_artifact_content=job.include_artifact_content,
                    export_version=job.export_version,
                    workspace_id=job.workspace_id,
                    ai_system_provenance_label=job.ai_system_provenance_label,
                    ai_system_review_detail=job.ai_system_review_detail,
                    ai_system_risk_level=job.ai_system_risk_level,
                    ai_system_eu_ai_act_domain=job.ai_system_eu_ai_act_domain,
                    ai_system_purpose_summary=job.ai_system_purpose_summary,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        filename = f"promptdrift-{job.export_mode.replace('_', '-')}-export-{job.repo_full.replace('/', '-')}-{datetime.fromtimestamp(job.from_ts).strftime('%Y-%m-%d')}-to-{datetime.fromtimestamp(job.to_ts).strftime('%Y-%m-%d')}.zip"
        return StreamingResponse(
            io.BytesIO(result.zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    app.include_router(
        create_export_job_router(
            resolve_db_path_fn=lambda: db_path,
            get_export_job_fn=lambda active_db_path, job_id: get_export_job(active_db_path, job_id),
            authorize_export_job_access_fn=lambda request, _job: _require_admin_token(request, settings),
            export_job_payload_fn=export_status_payload,
            build_export_download_response_fn=build_export_download_response,
        )
    )

    # -----------------------------------------------------------------------
    # /cp/* — internal control-plane surface (machine-principal JWT auth)
    # -----------------------------------------------------------------------
    # Operator bootstrap routes — use the shared admin token so operators can
    # seed the first principal without needing an existing machine token.
    # These are the *only* /cp/* routes that accept the legacy admin token.

    @app.post("/cp/principals")
    async def cp_create_principal(payload: CreatePrincipalRequest, request: Request):
        """Create a workspace-bound machine principal (operator-only).

        Returns the ``client_id`` and a plaintext ``client_secret``.  The
        secret is returned exactly once; store it securely.
        """
        _require_admin_token(request, settings)
        unknown_scopes = set(payload.scopes) - ALL_SCOPES
        if unknown_scopes:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scopes: {sorted(unknown_scopes)}. Valid scopes: {sorted(ALL_SCOPES)}",
            )
        try:
            validate_scope_kind_compatibility(payload.principal_kind, payload.scopes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not settings.has_encryption_key:
            raise HTTPException(
                status_code=503,
                detail="APP_ENCRYPTION_KEY must be configured to create machine principals.",
            )
        count = count_machine_principals_for_workspace(db_path, payload.workspace_id)
        if count >= settings.cp_max_principals_per_workspace:
            raise HTTPException(
                status_code=409,
                detail=f"Workspace has reached the maximum of {settings.cp_max_principals_per_workspace} machine principals.",
            )
        client_id = str(uuid.uuid4())
        raw_secret = secrets.token_urlsafe(32)
        encrypted_secret = encrypt_text(raw_secret, settings.app_encryption_key)
        principal = create_machine_principal(
            db_path,
            workspace_id=payload.workspace_id,
            display_name=payload.display_name,
            principal_kind=payload.principal_kind,
            client_id=client_id,
            client_secret_encrypted=encrypted_secret,
            scopes=payload.scopes,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=principal.workspace_id,
            actor_user_id=None,
            event_type="principal.created",
            subject_type="machine_principal",
            subject_id=principal.client_id,
            payload={"scopes": payload.scopes, "workspace_id": principal.workspace_id},
        )
        return JSONResponse(
            {
                "client_id": principal.client_id,
                "client_secret": raw_secret,
                "workspace_id": principal.workspace_id,
                "display_name": principal.display_name,
                "principal_kind": principal.principal_kind,
                "scopes": payload.scopes,
                "status": principal.status,
                "note": "Store client_secret securely — it will not be returned again.",
            }
        )

    @app.post("/cp/principals/{client_id}/token")
    async def cp_issue_principal_token(client_id: str, payload: IssuePrincipalTokenRequest, request: Request):
        """Issue a short-lived JWT for an existing machine principal (operator-only)."""
        _require_admin_token(request, settings)
        if not settings.has_internal_jwt_config:
            raise HTTPException(
                status_code=503,
                detail="Internal JWT auth (INTERNAL_JWT_SECRET) is not configured.",
            )
        principal = get_machine_principal_by_client_id(db_path, client_id)
        if principal is None:
            raise HTTPException(status_code=404, detail="Machine principal not found.")
        if principal.status != "active":
            raise HTTPException(status_code=400, detail="Machine principal is not active.")
        if principal.workspace_id != payload.workspace_id:
            raise HTTPException(status_code=400, detail="workspace_id does not match principal's workspace.")
        scopes = json.loads(principal.scopes_json)
        unknown_scopes = set(scopes) - ALL_SCOPES
        if unknown_scopes:
            raise HTTPException(
                status_code=400,
                detail=f"Principal has unrecognised scopes: {sorted(unknown_scopes)}. Revoke and recreate this principal.",
            )
        token = issue_cp_token(
            client_id=principal.client_id,
            workspace_id=principal.workspace_id,
            scopes=scopes,
            secret=settings.internal_jwt_secret,
            issuer=settings.internal_jwt_issuer,
            audience=settings.internal_jwt_audience,
            ttl_seconds=settings.internal_jwt_ttl_seconds,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=principal.workspace_id,
            actor_user_id=None,
            event_type="token.issued",
            subject_type="machine_principal",
            subject_id=principal.client_id,
        )
        return JSONResponse(
            {
                "token": token,
                "client_id": client_id,
                "workspace_id": principal.workspace_id,
                "ttl_seconds": settings.internal_jwt_ttl_seconds,
            }
        )

    @app.delete("/cp/principals/{client_id}")
    async def cp_revoke_principal(client_id: str, request: Request):
        """Revoke a machine principal (operator-only)."""
        _require_admin_token(request, settings)
        principal = revoke_machine_principal(db_path, client_id)
        if principal is None:
            raise HTTPException(status_code=404, detail="Machine principal not found.")
        create_control_plane_audit_log(
            db_path,
            workspace_id=principal.workspace_id,
            actor_user_id=None,
            event_type="principal.revoked",
            subject_type="machine_principal",
            subject_id=principal.client_id,
        )
        return JSONResponse(
            {
                "client_id": principal.client_id,
                "workspace_id": principal.workspace_id,
                "status": principal.status,
                "revoked_at": principal.revoked_at,
            }
        )

    # Machine-auth routes — all require a valid control-plane JWT bearer token.

    @app.get("/cp/workspaces/{workspace_id}/repos/{repo_full:path}/dashboard")
    def cp_repo_dashboard(workspace_id: int, repo_full: str, request: Request):
        """Return repo drift dashboard for the given workspace-allocated repo.

        Requires scope: ``drift.read``.
        """
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_READ)
        require_cp_workspace_match(claims, workspace_id)
        allocation = get_repo_allocation_for_workspace(db_path, workspace_id, repo_full)
        if allocation is None:
            raise HTTPException(status_code=404, detail="Repository is not allocated to this workspace.")
        return JSONResponse(asdict(build_repo_dashboard_view(db_path, repo_full)))

    @app.post("/cp/workspaces/{workspace_id}/repos/{repo_full:path}/export")
    async def cp_create_export(workspace_id: int, repo_full: str, payload: ComplianceExportRequest, request: Request):
        """Initiate a compliance export for the given workspace-allocated repo.

        Requires scope: ``drift.write.low``.
        """
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_LOW)
        require_cp_workspace_match(claims, workspace_id)
        allocation = get_repo_allocation_for_workspace(db_path, workspace_id, repo_full)
        if allocation is None:
            raise HTTPException(status_code=404, detail="Repository is not allocated to this workspace.")
        try:
            from_ts = datetime.fromisoformat(payload.from_date).timestamp()
            to_ts = datetime.fromisoformat(payload.to_date).timestamp()
            if payload.export_mode not in ["compliance", "compliance_plus_drift"]:
                raise HTTPException(status_code=400, detail="Invalid export_mode")
            job = create_export_job(
                db_path,
                repo_full=repo_full,
                from_ts=from_ts,
                to_ts=to_ts,
                export_mode=payload.export_mode,
                include_artifact_content=payload.include_artifact_content,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"job_id": job.id, "workspace_id": workspace_id, "repo_full": repo_full})

    @app.post("/cp/workspaces/{workspace_id}/repos/{repo_full:path}/baseline/approve")
    async def cp_approve_baseline(workspace_id: int, repo_full: str, payload: BaselineDecisionRequest, request: Request):
        """Approve the pending baseline candidate for the given workspace-allocated repo.

        Requires scope: ``drift.write.high`` because baseline approval changes
        the accepted safety posture — it is an irreversible governance action.
        """
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_HIGH)
        require_cp_workspace_match(claims, workspace_id)
        allocation = get_repo_allocation_for_workspace(db_path, workspace_id, repo_full)
        if allocation is None:
            raise HTTPException(status_code=404, detail="Repository is not allocated to this workspace.")
        try:
            baselines = approve_repo_baseline(
                db_path,
                repo_full=repo_full,
                actor_login=payload.actor_login,
                approval_note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        create_control_plane_audit_log(
            db_path,
            workspace_id=workspace_id,
            actor_user_id=None,
            event_type="baseline.approved",
            subject_type="repo",
            subject_id=repo_full,
            payload={"approved_count": len(baselines)},
        )
        return JSONResponse(
            {
                "repo_full": repo_full,
                "workspace_id": workspace_id,
                "approved_baseline_count": len(baselines),
                "dashboard": asdict(build_repo_dashboard_view(db_path, repo_full)),
            }
        )

    # -----------------------------------------------------------------------
    # /cp/auth/token — machine client credentials token exchange
    # -----------------------------------------------------------------------
    # Machine clients present client_id + client_secret to obtain a short-
    # lived JWT.  This route lives in api_service only; never dual-mounted on
    # the cookie-session monolith.

    @app.post("/cp/auth/token")
    async def cp_auth_token(payload: ClientCredentialsRequest, request: Request):
        """Exchange client credentials for a short-lived CP JWT."""
        # Rate limit: 20 attempts per client IP per minute
        client_ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        if not _token_endpoint_limiter.allow(client_ip):
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please retry after 60 seconds.",
                headers={"Retry-After": "60"},
            )

        if not settings.has_internal_jwt_config:
            raise HTTPException(status_code=503, detail="Internal JWT auth is not configured.")
        if not settings.has_encryption_key:
            raise HTTPException(status_code=503, detail="APP_ENCRYPTION_KEY must be configured.")

        _GENERIC_401 = "Invalid client credentials."

        principal = get_machine_principal_by_client_id(db_path, payload.client_id)
        if principal is None:
            # timing-safe dummy compare — prevents client_id enumeration via timing
            hmac.compare_digest(secrets.token_urlsafe(32).encode(), payload.client_secret.encode())
            raise HTTPException(status_code=401, detail=_GENERIC_401)

        decrypted = decrypt_text(principal.client_secret_encrypted, settings.app_encryption_key)
        if not hmac.compare_digest(decrypted.encode(), payload.client_secret.encode()):
            raise HTTPException(status_code=401, detail=_GENERIC_401)

        if principal.status != "active":
            raise HTTPException(status_code=401, detail=_GENERIC_401)

        if settings.is_production:
            entitlement = get_workspace_entitlement(db_path, principal.workspace_id)
            flags = json.loads(entitlement.feature_flags_json) if entitlement and entitlement.feature_flags_json else {}
            if flags.get("cp_api_enabled", True) is False:
                raise HTTPException(status_code=403, detail="Control plane API is not enabled for this workspace.")

        scopes = json.loads(principal.scopes_json)
        token = issue_cp_token(
            client_id=principal.client_id,
            workspace_id=principal.workspace_id,
            scopes=scopes,
            secret=settings.internal_jwt_secret,
            issuer=settings.internal_jwt_issuer,
            audience=settings.internal_jwt_audience,
            ttl_seconds=settings.internal_jwt_ttl_seconds,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=principal.workspace_id,
            actor_user_id=None,
            event_type="token.issued_via_client_credentials",
            subject_type="machine_principal",
            subject_id=principal.client_id,
        )
        return JSONResponse(
            {
                "token": token,
                "client_id": principal.client_id,
                "workspace_id": principal.workspace_id,
                "ttl_seconds": settings.internal_jwt_ttl_seconds,
            }
        )

    # -----------------------------------------------------------------------
    # Extended /cp/* read routes — all require a valid CP JWT bearer token.
    # -----------------------------------------------------------------------

    @app.get("/cp/workspaces/{workspace_id}")
    def cp_get_workspace(workspace_id: int, request: Request):
        """Return workspace summary (no billing fields).

        Requires scope: ``drift.read``.
        """
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_READ)
        require_cp_workspace_match(claims, workspace_id)
        workspace = get_workspace_by_id(db_path, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found.")
        return JSONResponse(
            {
                "id": workspace.id,
                "slug": workspace.slug,
                "display_name": workspace.display_name,
                "status": workspace.status,
                "setup_state": workspace.setup_state,
            }
        )

    @app.get("/cp/workspaces/{workspace_id}/repos")
    def cp_list_workspace_repos(workspace_id: int, request: Request):
        """Return repos allocated to the workspace.

        Requires scope: ``drift.read``.  Billing fields are excluded.
        """
        from .control_plane_records import list_repo_allocations_for_workspace

        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_READ)
        require_cp_workspace_match(claims, workspace_id)
        allocations = list_repo_allocations_for_workspace(db_path, workspace_id)
        return JSONResponse(
            {
                "workspace_id": workspace_id,
                "repos": [
                    {
                        "repo_full": alloc.repo_full,
                        "allocation_status": alloc.allocation_status,
                    }
                    for alloc in allocations
                ],
            }
        )

    @app.get("/cp/workspaces/{workspace_id}/principals")
    def cp_list_workspace_principals(workspace_id: int, request: Request):
        """Return machine principals for the workspace.

        ``client_secret_encrypted`` is never included in the response.
        Requires scope: ``drift.read``.
        """
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_READ)
        require_cp_workspace_match(claims, workspace_id)
        principals = list_machine_principals_for_workspace(db_path, workspace_id)
        return JSONResponse(
            {
                "workspace_id": workspace_id,
                "principals": [
                    {
                        "client_id": p.client_id,
                        "display_name": p.display_name,
                        "principal_kind": p.principal_kind,
                        "scopes": json.loads(p.scopes_json),
                        "status": p.status,
                        "created_at": p.created_at,
                        "revoked_at": p.revoked_at,
                    }
                    for p in principals
                ],
            }
        )

    @app.get("/cp/workspaces/{workspace_id}/audit-log")
    def cp_workspace_audit_log(workspace_id: int, request: Request):
        """Return recent audit log entries for the workspace.

        Requires scope: ``admin.read`` — only operator-provisioned principals
        may carry this scope.
        """
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_ADMIN_READ)
        require_cp_workspace_match(claims, workspace_id)
        entries = list_control_plane_audit_logs_for_workspace(db_path, workspace_id)
        return JSONResponse(
            {
                "workspace_id": workspace_id,
                "entries": [
                    {
                        "id": e.id,
                        "event_type": e.event_type,
                        "subject_type": e.subject_type,
                        "subject_id": e.subject_id,
                        "created_at": e.created_at,
                    }
                    for e in entries
                ],
            }
        )

    @app.post("/cp/audits/{audit_id}/feedback")
    def cp_add_audit_feedback(audit_id: int, payload: AuditFeedbackRequest, request: Request):
        """Append structured feedback to an audit record. Requires drift.write.low.

        Workspace isolation: derived from audit ownership (audit → repo_full →
        allocation). Returns 404 for unknown audits **and** for audits that
        belong to a different workspace (avoids leaking audit existence).
        """
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_LOW)
        if payload.kind not in VALID_FEEDBACK_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid kind. Valid values: {sorted(VALID_FEEDBACK_KINDS)}",
            )
        audit = get_pull_request_audit_by_id(db_path, audit_id)
        if audit is None:
            raise HTTPException(status_code=404, detail="Audit not found.")
        allocation = get_repo_allocation_for_workspace(db_path, claims.workspace_id, audit.repo_full)
        if allocation is None:
            raise HTTPException(status_code=404, detail="Audit not found.")
        event = add_audit_feedback(
            db_path,
            audit_id=audit_id,
            workspace_id=claims.workspace_id,
            source=payload.source,
            kind=payload.kind,
            comment=payload.comment,
            metadata=payload.metadata,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=claims.workspace_id,
            actor_user_id=None,
            event_type="audit.feedback_added",
            subject_type="audit",
            subject_id=str(audit_id),
        )
        return JSONResponse(
            {
                "id": event.id,
                "audit_id": event.audit_id,
                "kind": event.kind,
                "source": event.source,
                "comment": event.comment,
                "created_at": event.created_at,
            }
        )

    @app.post("/cp/audits/{audit_id}/triage")
    def cp_triage_audit(audit_id: int, payload: AuditTriageRequest, request: Request):
        """Record a triage state transition for an audit. Requires drift.write.low.

        Writes only to audit_triage_events — does NOT modify pull_request_audits.
        Workspace isolation same as the feedback endpoint.
        """
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_LOW)
        if payload.state not in VALID_TRIAGE_STATES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid state. Valid values: {sorted(VALID_TRIAGE_STATES)}",
            )
        audit = get_pull_request_audit_by_id(db_path, audit_id)
        if audit is None:
            raise HTTPException(status_code=404, detail="Audit not found.")
        allocation = get_repo_allocation_for_workspace(db_path, claims.workspace_id, audit.repo_full)
        if allocation is None:
            raise HTTPException(status_code=404, detail="Audit not found.")
        event = add_audit_triage(
            db_path,
            audit_id=audit_id,
            workspace_id=claims.workspace_id,
            state=payload.state,
            reason=payload.reason,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=claims.workspace_id,
            actor_user_id=None,
            event_type="audit.triage_state_changed",
            subject_type="audit",
            subject_id=str(audit_id),
            payload={"state": payload.state},
        )
        return JSONResponse(
            {
                "id": event.id,
                "audit_id": event.audit_id,
                "state": event.state,
                "reason": event.reason,
                "created_at": event.created_at,
            }
        )

    @app.get("/cp/workspaces/{workspace_id}/exports/{export_id}")
    def cp_get_export(workspace_id: int, export_id: int, request: Request):
        """Return export job status. Requires drift.read. Never returns result_blob."""
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_READ)
        require_cp_workspace_match(claims, workspace_id)
        job = get_export_job(db_path, export_id)
        if job is None or job.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="Export not found.")
        return JSONResponse(
            {
                "id": job.id,
                "repo_full": job.repo_full,
                "workspace_id": workspace_id,
                "status": job.status,
                "export_mode": job.export_mode,
                "from_ts": job.from_ts,
                "to_ts": job.to_ts,
                "attempt_count": job.attempt_count,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "completed_at": job.completed_at,
            }
        )

    # -----------------------------------------------------------------------
    # /cp/* — high-risk change proposals (issue #61)
    # -----------------------------------------------------------------------
    # Baseline proposals — artifact-scoped
    # -----------------------------------------------------------------------

    def _baseline_proposal_json(p):
        return {
            "id": p.id,
            "artifact_id": p.artifact_id,
            "repo_full": p.repo_full,
            "workspace_id": p.workspace_id,
            "proposal_kind": p.proposal_kind,
            "snapshot_id": p.snapshot_id,
            "rationale": p.rationale,
            "linked_audit_ids": p.linked_audit_ids,
            "metadata": p.metadata,
            "status": p.status,
            "proposer_principal_id": p.proposer_principal_id,
            "decision_principal_id": p.decision_principal_id,
            "decision_note": p.decision_note,
            "expires_at": p.expires_at,
            "decided_at": p.decided_at,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }

    def _onboarding_proposal_json(p):
        return {
            "id": p.id,
            "workspace_id": p.workspace_id,
            "repo_full": p.repo_full,
            "proposal_kind": p.proposal_kind,
            "installation_id": p.installation_id,
            "proposed_category": p.proposed_category,
            "rationale": p.rationale,
            "metadata": p.metadata,
            "status": p.status,
            "proposer_principal_id": p.proposer_principal_id,
            "decision_principal_id": p.decision_principal_id,
            "decision_note": p.decision_note,
            "expires_at": p.expires_at,
            "decided_at": p.decided_at,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }

    @app.post("/cp/artifacts/{artifact_id}/baseline/proposals")
    def cp_create_baseline_proposal(artifact_id: int, payload: BaselineProposalRequest, request: Request):
        """Submit a baseline promotion proposal for an artifact. Requires drift.write.low."""
        claims, principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_LOW)
        artifact = get_onboarded_artifact_by_id(db_path, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        # Resolve workspace isolation via repo allocation (same pattern as existing CP routes)
        allocation = get_repo_allocation_for_workspace(db_path, claims.workspace_id, artifact.repo_full)
        if allocation is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        proposal = create_baseline_proposal(
            db_path,
            artifact_id=artifact_id,
            repo_full=artifact.repo_full,
            workspace_id=claims.workspace_id,
            snapshot_id=payload.snapshot_id,
            rationale=payload.rationale,
            linked_audit_ids=payload.linked_audit_ids,
            metadata=payload.metadata,
            proposer_principal_id=principal.id,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=claims.workspace_id,
            actor_user_id=None,
            event_type="proposal.created",
            subject_type="baseline_proposal",
            subject_id=str(proposal.id),
            payload={"artifact_id": artifact_id, "proposer_principal_id": principal.id},
        )
        return JSONResponse(_baseline_proposal_json(proposal), status_code=201)

    @app.get("/cp/artifacts/{artifact_id}/baseline/proposals")
    def cp_list_baseline_proposals(artifact_id: int, request: Request):
        """List baseline proposals for an artifact. Requires drift.read."""
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_READ)
        artifact = get_onboarded_artifact_by_id(db_path, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        allocation = get_repo_allocation_for_workspace(db_path, claims.workspace_id, artifact.repo_full)
        if allocation is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        proposals = list_baseline_proposals(db_path, artifact_id=artifact_id, workspace_id=claims.workspace_id)
        return JSONResponse({"proposals": [_baseline_proposal_json(p) for p in proposals]})

    @app.post("/cp/artifacts/{artifact_id}/baseline/proposals/{proposal_id}/approve")
    def cp_approve_baseline_proposal(artifact_id: int, proposal_id: int, payload: ProposalDecisionRequest, request: Request):
        """Approve a baseline proposal. Requires drift.write.high and human_operator kind."""
        claims, principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_HIGH)
        require_cp_principal_kind(principal, _HUMAN_ONLY_KINDS)
        artifact = get_onboarded_artifact_by_id(db_path, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        allocation = get_repo_allocation_for_workspace(db_path, claims.workspace_id, artifact.repo_full)
        if allocation is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        proposal = approve_baseline_proposal(
            db_path,
            proposal_id=proposal_id,
            artifact_id=artifact_id,
            workspace_id=claims.workspace_id,
            decision_principal_id=principal.id,
            decision_note=payload.decision_note,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=claims.workspace_id,
            actor_user_id=None,
            event_type="proposal.approved",
            subject_type="baseline_proposal",
            subject_id=str(proposal_id),
            payload={"artifact_id": artifact_id, "decision_principal_id": principal.id},
        )
        return JSONResponse(_baseline_proposal_json(proposal))

    @app.post("/cp/artifacts/{artifact_id}/baseline/proposals/{proposal_id}/reject")
    def cp_reject_baseline_proposal(artifact_id: int, proposal_id: int, payload: ProposalDecisionRequest, request: Request):
        """Reject a baseline proposal. Requires drift.write.high."""
        claims, principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_HIGH)
        artifact = get_onboarded_artifact_by_id(db_path, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        allocation = get_repo_allocation_for_workspace(db_path, claims.workspace_id, artifact.repo_full)
        if allocation is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        proposal = reject_baseline_proposal(
            db_path,
            proposal_id=proposal_id,
            artifact_id=artifact_id,
            workspace_id=claims.workspace_id,
            decision_principal_id=principal.id,
            decision_note=payload.decision_note,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=claims.workspace_id,
            actor_user_id=None,
            event_type="proposal.rejected",
            subject_type="baseline_proposal",
            subject_id=str(proposal_id),
            payload={"artifact_id": artifact_id, "decision_principal_id": principal.id},
        )
        return JSONResponse(_baseline_proposal_json(proposal))

    # -----------------------------------------------------------------------
    # Repo onboarding proposals — workspace-scoped
    # -----------------------------------------------------------------------

    @app.post("/cp/workspaces/{workspace_id}/repos/onboarding-proposals")
    def cp_create_onboarding_proposal(workspace_id: int, payload: OnboardingProposalRequest, request: Request):
        """Submit a repository onboarding proposal. Requires drift.write.low."""
        claims, principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_LOW)
        require_cp_workspace_match(claims, workspace_id)
        proposal = create_onboarding_proposal(
            db_path,
            workspace_id=workspace_id,
            repo_full=payload.repo_full,
            installation_id=payload.installation_id,
            proposed_category=payload.proposed_category,
            rationale=payload.rationale,
            metadata=payload.metadata,
            proposer_principal_id=principal.id,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=workspace_id,
            actor_user_id=None,
            event_type="proposal.created",
            subject_type="onboarding_proposal",
            subject_id=str(proposal.id),
            payload={"repo_full": payload.repo_full, "proposer_principal_id": principal.id},
        )
        return JSONResponse(_onboarding_proposal_json(proposal), status_code=201)

    @app.get("/cp/workspaces/{workspace_id}/repos/onboarding-proposals")
    def cp_list_onboarding_proposals(workspace_id: int, request: Request):
        """List onboarding proposals for a workspace. Requires drift.read."""
        claims, _principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_READ)
        require_cp_workspace_match(claims, workspace_id)
        proposals = list_onboarding_proposals(db_path, workspace_id=workspace_id)
        return JSONResponse({"proposals": [_onboarding_proposal_json(p) for p in proposals]})

    @app.post("/cp/workspaces/{workspace_id}/repos/onboarding-proposals/{proposal_id}/approve")
    def cp_approve_onboarding_proposal(workspace_id: int, proposal_id: int, payload: ProposalDecisionRequest, request: Request):
        """Approve a repo onboarding proposal. Requires drift.write.high and human_operator kind."""
        claims, principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_HIGH)
        require_cp_principal_kind(principal, _HUMAN_ONLY_KINDS)
        require_cp_workspace_match(claims, workspace_id)
        proposal = approve_onboarding_proposal(
            db_path,
            proposal_id=proposal_id,
            workspace_id=workspace_id,
            decision_principal_id=principal.id,
            decision_note=payload.decision_note,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=workspace_id,
            actor_user_id=None,
            event_type="proposal.approved",
            subject_type="onboarding_proposal",
            subject_id=str(proposal_id),
            payload={"repo_full": proposal.repo_full, "decision_principal_id": principal.id},
        )
        return JSONResponse(_onboarding_proposal_json(proposal))

    @app.post("/cp/workspaces/{workspace_id}/repos/onboarding-proposals/{proposal_id}/reject")
    def cp_reject_onboarding_proposal(workspace_id: int, proposal_id: int, payload: ProposalDecisionRequest, request: Request):
        """Reject a repo onboarding proposal. Requires drift.write.high."""
        claims, principal = require_cp_principal(request, settings, db_path)
        require_cp_scope(claims, SCOPE_DRIFT_WRITE_HIGH)
        require_cp_workspace_match(claims, workspace_id)
        proposal = reject_onboarding_proposal(
            db_path,
            proposal_id=proposal_id,
            workspace_id=workspace_id,
            decision_principal_id=principal.id,
            decision_note=payload.decision_note,
        )
        create_control_plane_audit_log(
            db_path,
            workspace_id=workspace_id,
            actor_user_id=None,
            event_type="proposal.rejected",
            subject_type="onboarding_proposal",
            subject_id=str(proposal_id),
            payload={"repo_full": proposal.repo_full, "decision_principal_id": principal.id},
        )
        return JSONResponse(_onboarding_proposal_json(proposal))

    return app
