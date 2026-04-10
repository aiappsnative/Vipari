from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from config import get_settings
from .dashboard_frontend import DASHBOARD_STATIC_DIR, render_dashboard_index_page, render_repo_dashboard_page
from .dashboard_views import build_dashboard_overview_view, build_repo_artifact_storyline, build_repo_dashboard_view, list_repo_dashboard_index
from .github_integration import generate_jwt, get_installation_token
from .observability import configure_logging, instrument_fastapi
from .onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from .onboarding_records import promote_latest_source_to_onboarding_baseline
from .persistence import get_persistence_status
from .repo_journey import build_repo_journey, compare_repo_snapshots, get_repo_snapshot_detail, snapshot_to_public_payload
from .audit_jobs import init_db
from .static_assets import FingerprintedStaticFiles


class RepositoryOnboardingRequest(BaseModel):
    installation_id: int
    commit_limit_per_artifact: int = 10
    plan_backfill: bool = True
    execute_backfill: bool = False


class RepositoryBackfillRequest(BaseModel):
    installation_id: int


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
        init_db(db_path)
        yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", FingerprintedStaticFiles(directory=str(DASHBOARD_STATIC_DIR)), name="static")
    instrument_fastapi(app, enabled=settings.enable_metrics)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

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
        payload = asdict(get_persistence_status(db_path))
        payload.pop("database_path", None)
        return JSONResponse(payload)

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

    return app
