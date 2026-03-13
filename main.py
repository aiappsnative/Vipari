import os
import hmac
import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from openai import OpenAI

from engine.relevance import needs_audit as engine_needs_audit
from services.audit_jobs import create_audit_job, init_db
from services.audit_worker import AuditWorker, WorkerSettings
from services.github_integration import fetch_pr_diff, generate_jwt, get_installation_token

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
    head_sha = payload.get("pull_request", {}).get("head", {}).get("sha")

    if not all([installation_id, repo_full, pr_number, head_sha]):
        raise HTTPException(status_code=400, detail="Missing payload data")

    jwt_token = generate_jwt(GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH)
    token = get_installation_token(jwt_token, installation_id)
    diff_text = fetch_pr_diff(repo_full, pr_number, token)

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
