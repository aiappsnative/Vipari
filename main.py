import hmac
import hashlib
import asyncio
from datetime import datetime
from dataclasses import asdict
from contextlib import asynccontextmanager
from urllib.error import HTTPError

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from github.GithubException import GithubException
from openai import OpenAI
from pydantic import BaseModel

from config import get_settings
from engine.relevance import needs_audit as engine_needs_audit
from services.audit_jobs import create_audit_job, init_db, update_job_pr_state
from services.dashboard_views import build_dashboard_overview_view, build_repo_dashboard_view, list_repo_dashboard_index
from services.dashboard_frontend import DASHBOARD_STATIC_DIR, render_dashboard_index_page, render_repo_dashboard_page
from services.audit_worker import AuditWorker, WorkerSettings
from services.github_integration import fetch_commit_pair_diff, fetch_pr_diff, generate_jwt, get_installation_token
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from services.onboarding_records import promote_latest_source_to_onboarding_baseline
from services.persistence import get_persistence_status
from services.audit_records import update_pull_request_audit_state

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


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_index_page():
    return HTMLResponse(render_dashboard_index_page())


@app.get("/dashboard/{repo_full:path}", response_class=HTMLResponse)
async def dashboard_repo_page(repo_full: str):
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
