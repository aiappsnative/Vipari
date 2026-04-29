from __future__ import annotations

import hmac
import io
import secrets
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
from .cp_auth import require_cp_principal, require_cp_scope, require_cp_workspace_match
from .control_plane_records import (
    create_control_plane_audit_log,
    create_machine_principal,
    count_machine_principals_for_workspace,
    get_machine_principal_by_client_id,
    get_repo_allocation_for_workspace,
    get_workspace_by_id,
    get_workspace_entitlement,
    list_control_plane_audit_logs_for_workspace,
    list_machine_principals_for_workspace,
    revoke_machine_principal,
)
from .dashboard_frontend import DASHBOARD_STATIC_DIR, render_dashboard_index_page, render_repo_dashboard_page
from .dashboard_views import build_dashboard_overview_view, build_repo_artifact_storyline, build_repo_dashboard_view, list_repo_dashboard_index
from .github_integration import fetch_file_content, generate_jwt, get_installation_token
from .internal_auth import (
    ALL_SCOPES,
    SCOPE_ADMIN_READ,
    SCOPE_ADMIN_WRITE,
    SCOPE_DRIFT_READ,
    SCOPE_DRIFT_WRITE_HIGH,
    SCOPE_DRIFT_WRITE_LOW,
    issue_cp_token,
    validate_cp_token,
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
from .export_jobs import create_export_job, get_export_job, list_export_jobs_for_repo
from .onboarding_records import promote_latest_source_to_onboarding_baseline
from .persistence import get_persistence_status, persistence_status_payload
from .repo_journey import build_repo_journey, compare_repo_snapshots, get_repo_snapshot_detail, snapshot_to_public_payload
from .secure_store import decrypt_text, encrypt_text
from .audit_jobs import init_db
from .runtime_guardrails import build_runtime_readiness, readiness_json_response, validate_runtime_configuration
from .static_assets import FingerprintedStaticFiles


class RepositoryOnboardingRequest(BaseModel):
    installation_id: int
    commit_limit_per_artifact: int = 10
    plan_backfill: bool = True
    execute_backfill: bool = False


class RepositoryBackfillRequest(BaseModel):
    installation_id: int


class BaselineDecisionRequest(BaseModel):
    note: str | None = None
    actor_login: str | None = None


class RepoRebaselineRequest(BaseModel):
    snapshot_id: int
    rationale: str | None = None
    actor_login: str | None = None


class ComplianceExportRequest(BaseModel):
    from_date: str  # YYYY-MM-DD
    to_date: str    # YYYY-MM-DD
    export_mode: str  # "compliance" | "compliance_plus_drift"
    include_artifact_content: bool = False


class CreatePrincipalRequest(BaseModel):
    workspace_id: int
    display_name: str = Field(..., min_length=1, max_length=120)
    principal_kind: Literal["service_account"] = "service_account"
    scopes: list[str]


class IssuePrincipalTokenRequest(BaseModel):
    workspace_id: int


class ClientCredentialsRequest(BaseModel):
    client_id: str
    client_secret: str


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

    @app.get("/health")
    async def health():
        return {"status": "ok", "service_role": settings.service_role}

    @app.get("/health/ready")
    async def ready():
        return readiness_json_response(await build_runtime_readiness(settings))

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_index_page(request: Request):
        _require_admin_token(request, settings)
        return HTMLResponse(render_dashboard_index_page())

    @app.get("/dashboard/{repo_full:path}", response_class=HTMLResponse)
    async def dashboard_repo_page(repo_full: str, request: Request):
        _require_admin_token(request, settings)
        return HTMLResponse(render_repo_dashboard_page(repo_full))

    @app.get("/api/repos")
    async def list_repos(request: Request):
        _require_admin_token(request, settings)
        return JSONResponse({"repos": [asdict(item) for item in list_repo_dashboard_index(db_path)]})

    @app.get("/api/dashboard/overview")
    def dashboard_overview(request: Request):
        _require_admin_token(request, settings)
        return JSONResponse(asdict(build_dashboard_overview_view(db_path)))

    @app.get("/api/persistence")
    def persistence_status(request: Request):
        _require_admin_token(request, settings)
        return JSONResponse(persistence_status_payload(get_persistence_status(db_path)))

    @app.get("/api/repos/{repo_full:path}/dashboard")
    def repo_dashboard(repo_full: str, request: Request):
        _require_admin_token(request, settings)
        return JSONResponse(asdict(build_repo_dashboard_view(db_path, repo_full)))

    @app.get("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/episodes")
    def artifact_storyline(repo_full: str, artifact_path: str, request: Request):
        _require_admin_token(request, settings)
        storyline = build_repo_artifact_storyline(db_path, repo_full, artifact_path)
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
    def repo_journey(repo_full: str, request: Request):
        _require_admin_token(request, settings)
        return JSONResponse(
            {
                "repo_full": repo_full,
                "snapshots": [snapshot_to_public_payload(item) for item in build_repo_journey(db_path, repo_full)],
            }
        )

    @app.get("/api/repos/{repo_full:path}/snapshots/{snapshot_id}")
    def repo_snapshot_detail(repo_full: str, snapshot_id: int, request: Request):
        _require_admin_token(request, settings)
        snapshot = get_repo_snapshot_detail(db_path, repo_full, snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Repo posture snapshot was not found.")
        return JSONResponse({"repo_full": repo_full, "snapshot": snapshot_to_public_payload(snapshot)})

    @app.get("/api/repos/{repo_full:path}/compare")
    def repo_snapshot_compare(repo_full: str, left: int, right: int, request: Request):
        _require_admin_token(request, settings)
        try:
            comparison = compare_repo_snapshots(db_path, repo_full, left, right)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(asdict(comparison))

    @app.post("/api/repos/{repo_full:path}/onboard")
    async def run_repo_onboarding(repo_full: str, payload: RepositoryOnboardingRequest, request: Request):
        _require_admin_token(request, settings)
        jwt_token = generate_jwt(
            settings.github_app_id,
            settings.github_private_key_path,
            settings.resolved_github_private_key,
        )
        token = get_installation_token(jwt_token, payload.installation_id)
        onboarding_result = onboard_repository(
            db_path,
            repo_full=repo_full,
            installation_id=payload.installation_id,
            token=token,
        )
        planned_jobs = []
        if payload.plan_backfill:
            planned_jobs = plan_repository_history_backfill(
                db_path,
                repo_full=repo_full,
                token=token,
                commit_limit_per_artifact=payload.commit_limit_per_artifact,
            )
        executed_jobs = []
        if payload.execute_backfill:
            executed_jobs = execute_repository_history_backfill(db_path, repo_full=repo_full, token=token)
        logger.info("Processed onboarding request", extra={"repo": repo_full})
        return JSONResponse(
            {
                "repo_full": repo_full,
                "onboarding_id": onboarding_result.onboarding.id,
                "discovered_artifact_count": len(onboarding_result.artifacts),
                "baseline_version_count": len(onboarding_result.baseline_versions),
                "planned_backfill_job_count": len(planned_jobs),
                "executed_backfill_job_count": len(executed_jobs),
                "dashboard": asdict(build_repo_dashboard_view(db_path, repo_full)),
            }
        )

    @app.post("/api/repos/{repo_full:path}/backfill")
    async def run_repo_backfill(repo_full: str, payload: RepositoryBackfillRequest, request: Request):
        _require_admin_token(request, settings)
        jwt_token = generate_jwt(
            settings.github_app_id,
            settings.github_private_key_path,
            settings.resolved_github_private_key,
        )
        token = get_installation_token(jwt_token, payload.installation_id)
        executed_jobs = execute_repository_history_backfill(db_path, repo_full=repo_full, token=token)
        return JSONResponse(
            {
                "repo_full": repo_full,
                "executed_backfill_job_count": len(executed_jobs),
                "completed_backfill_job_count": sum(1 for result in executed_jobs if result.job.status == "completed"),
                "failed_backfill_job_count": sum(1 for result in executed_jobs if result.job.status == "failed"),
                "dashboard": asdict(build_repo_dashboard_view(db_path, repo_full)),
            }
        )

    @app.post("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/baseline")
    async def promote_artifact_baseline(repo_full: str, artifact_path: str, request: Request):
        _require_admin_token(request, settings)
        baseline = promote_latest_source_to_onboarding_baseline(db_path, repo_full, artifact_path)
        if baseline is None:
            raise HTTPException(status_code=404, detail="No stored source version is available to promote as baseline.")
        build_repo_journey(db_path, repo_full)
        return JSONResponse(
            {
                "repo_full": repo_full,
                "artifact_path": artifact_path,
                "baseline": asdict(baseline),
                "dashboard": asdict(build_repo_dashboard_view(db_path, repo_full)),
            }
        )

    @app.get("/api/repos/{repo_full:path}/baseline/pending")
    def pending_repo_baselines(repo_full: str, request: Request):
        _require_admin_token(request, settings)
        panel = build_repo_baseline_review_panel(db_path, repo_full)
        if panel is None:
            raise HTTPException(status_code=404, detail="Repository onboarding was not found.")
        return JSONResponse(asdict(panel))

    @app.post("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/baseline/approve")
    async def approve_artifact_baseline(repo_full: str, artifact_path: str, payload: BaselineDecisionRequest, request: Request):
        _require_admin_token(request, settings)
        try:
            baseline = approve_repo_baseline_artifact(
                db_path,
                repo_full=repo_full,
                artifact_path=artifact_path,
                actor_login=payload.actor_login,
                approval_note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse({"repo_full": repo_full, "artifact_path": artifact_path, "baseline": asdict(baseline), "dashboard": asdict(build_repo_dashboard_view(db_path, repo_full))})

    @app.post("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/baseline/reject")
    async def reject_artifact_baseline(repo_full: str, artifact_path: str, payload: BaselineDecisionRequest, request: Request):
        _require_admin_token(request, settings)
        try:
            baseline = reject_repo_baseline_artifact(
                db_path,
                repo_full=repo_full,
                artifact_path=artifact_path,
                actor_login=payload.actor_login,
                approval_note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse({"repo_full": repo_full, "artifact_path": artifact_path, "baseline": asdict(baseline), "dashboard": asdict(build_repo_dashboard_view(db_path, repo_full))})

    @app.post("/api/repos/{repo_full:path}/baseline/approve")
    async def approve_repo_baseline_candidate(repo_full: str, payload: BaselineDecisionRequest, request: Request):
        _require_admin_token(request, settings)
        try:
            baselines = approve_repo_baseline(
                db_path,
                repo_full=repo_full,
                actor_login=payload.actor_login,
                approval_note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse({"repo_full": repo_full, "approved_baseline_count": len(baselines), "dashboard": asdict(build_repo_dashboard_view(db_path, repo_full))})

    @app.post("/api/repos/{repo_full:path}/baseline/reject")
    async def reject_repo_baseline_candidate(repo_full: str, payload: BaselineDecisionRequest, request: Request):
        _require_admin_token(request, settings)
        try:
            baselines = reject_repo_baseline(
                db_path,
                repo_full=repo_full,
                actor_login=payload.actor_login,
                approval_note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse({"repo_full": repo_full, "rejected_baseline_count": len(baselines), "dashboard": asdict(build_repo_dashboard_view(db_path, repo_full))})

    @app.post("/api/repos/{repo_full:path}/baseline/rebaseline")
    async def rebaseline_repo(repo_full: str, payload: RepoRebaselineRequest, request: Request):
        _require_admin_token(request, settings)
        try:
            baselines = rebaseline_repo_from_snapshot(
                db_path,
                repo_full=repo_full,
                snapshot_id=payload.snapshot_id,
                rationale=payload.rationale,
                actor_login=payload.actor_login,
                github_app_id=settings.github_app_id,
                github_private_key_path=settings.github_private_key_path,
                generate_jwt_fn=lambda app_id, private_key_path: generate_jwt(app_id, private_key_path, settings.resolved_github_private_key),
                get_installation_token_fn=get_installation_token,
                fetch_file_content_fn=fetch_file_content,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"repo_full": repo_full, "snapshot_id": payload.snapshot_id, "created_baseline_count": len(baselines), "dashboard": asdict(build_repo_dashboard_view(db_path, repo_full))})

    @app.post("/api/repos/{repo_full:path}/export/compliance")
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

    @app.get("/api/export/{job_id}/status")
    async def get_export_status(job_id: int, request: Request):
        _require_admin_token(request, settings)
        job = get_export_job(db_path, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Export job not found")
        return JSONResponse({
            "job_id": job.id,
            "status": job.status,
            "export_mode": job.export_mode,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "result_size_bytes": job.result_size_bytes,
            "last_error": job.last_error,
        })

    @app.get("/api/export/{job_id}/download")
    async def download_export(job_id: int, request: Request):
        _require_admin_token(request, settings)
        job = get_export_job(db_path, job_id)
        if not job or job.status != "completed" or not job.download_token:
            raise HTTPException(status_code=404, detail="Export not available")
        # For now, generate on the fly. In production, store the ZIP.
        try:
            result = build_compliance_export(
                db_path,
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
        filename = f"promptdrift-{job.export_mode.replace('_', '-')}-export-{job.repo_full.replace('/', '-')}-{datetime.fromtimestamp(job.from_ts).strftime('%Y-%m-%d')}-to-{datetime.fromtimestamp(job.to_ts).strftime('%Y-%m-%d')}.zip"
        return StreamingResponse(
            io.BytesIO(result.zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
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
    async def cp_auth_token(payload: ClientCredentialsRequest):
        """Exchange client credentials for a short-lived CP JWT."""
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

    return app
