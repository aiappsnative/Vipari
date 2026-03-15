import os
import hmac
import hashlib
import asyncio
from contextlib import asynccontextmanager
from urllib.error import HTTPError

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from github.GithubException import GithubException
from openai import OpenAI

from engine.relevance import needs_audit as engine_needs_audit
from services.audit_jobs import create_audit_job, init_db
from services.audit_worker import AuditWorker, WorkerSettings
from services.github_integration import fetch_commit_pair_diff, fetch_pr_diff, generate_jwt, get_installation_token

# load environment variables
load_dotenv()

GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_PRIVATE_KEY_PATH = os.getenv("GITHUB_PRIVATE_KEY_PATH")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o")
AI_API_KEY = FOUNDRY_API_KEY or OPENAI_API_KEY
AUDIT_DB_PATH = os.getenv("AUDIT_DB_PATH", os.path.join(os.path.dirname(__file__), "promptdrift.db"))
AUDIT_WORKER_ENABLED = os.getenv("AUDIT_WORKER_ENABLED", "1") == "1"
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
AUDIT_MAX_ATTEMPTS = int(os.getenv("AUDIT_MAX_ATTEMPTS", "5"))
AUDIT_MAX_RETRY_WINDOW_SECONDS = float(os.getenv("AUDIT_MAX_RETRY_WINDOW_SECONDS", "5400"))
AUDIT_WORKER_POLL_SECONDS = float(os.getenv("AUDIT_WORKER_POLL_SECONDS", "2"))
PR_DIFF_FETCH_ATTEMPTS = int(os.getenv("PR_DIFF_FETCH_ATTEMPTS", "3"))
PR_DIFF_FETCH_RETRY_SECONDS = float(os.getenv("PR_DIFF_FETCH_RETRY_SECONDS", "2"))

if not all([GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH, GITHUB_WEBHOOK_SECRET, AI_API_KEY]):
    raise RuntimeError("Required environment variables are missing. Check .env or .env.example")

client = OpenAI(api_key=AI_API_KEY, base_url=AZURE_OPENAI_ENDPOINT or None)
worker: AuditWorker | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker
    init_db(AUDIT_DB_PATH)
    if AUDIT_WORKER_ENABLED:
        worker = AuditWorker(
            WorkerSettings(
                db_path=AUDIT_DB_PATH,
                github_app_id=GITHUB_APP_ID,
                github_private_key_path=GITHUB_PRIVATE_KEY_PATH,
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


@app.post("/webhook")
async def webhook(request: Request):
    if not await verify_signature(request):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event != "pull_request":
        return JSONResponse({"message": "ignored"})

    payload = await request.json()
    action = payload.get("action")
    if action not in ("opened", "synchronize"):
        return JSONResponse({"message": "ignored"})

    installation_id = payload.get("installation", {}).get("id")
    repo_full = payload.get("repository", {}).get("full_name")
    pr_number = payload.get("pull_request", {}).get("number")
    base_sha = payload.get("pull_request", {}).get("base", {}).get("sha")
    head_sha = payload.get("pull_request", {}).get("head", {}).get("sha")

    if not all([installation_id, repo_full, pr_number, head_sha]):
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
    )

    return JSONResponse({"message": "audit queued", "job_id": job.id})
