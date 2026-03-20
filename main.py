import os
import hmac
import hashlib
import asyncio
from dataclasses import asdict
from contextlib import asynccontextmanager
from html import escape as html_escape
from urllib.error import HTTPError

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
from github.GithubException import GithubException
from openai import OpenAI
from pydantic import BaseModel

from engine.relevance import needs_audit as engine_needs_audit
from services.audit_jobs import create_audit_job, init_db
from services.dashboard_views import build_repo_dashboard_view, list_repo_dashboard_index
from services.audit_worker import AuditWorker, WorkerSettings
from services.github_integration import fetch_commit_pair_diff, fetch_pr_diff, generate_jwt, get_installation_token
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill

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


class RepositoryOnboardingRequest(BaseModel):
    installation_id: int
    commit_limit_per_artifact: int = 10
    plan_backfill: bool = True
    execute_backfill: bool = False


class RepositoryBackfillRequest(BaseModel):
    installation_id: int


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_index_page():
    return HTMLResponse(_render_dashboard_index_page())


@app.get("/dashboard/{repo_full:path}", response_class=HTMLResponse)
async def dashboard_repo_page(repo_full: str):
    return HTMLResponse(_render_repo_dashboard_page(repo_full))


@app.get("/api/repos")
async def list_repos():
    return JSONResponse({"repos": [asdict(item) for item in list_repo_dashboard_index(AUDIT_DB_PATH)]})


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


def _render_dashboard_index_page() -> str:
        return """
<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>PromptDrift Dashboard</title>
    <style>
        :root {
            color-scheme: dark;
            --bg: #0b1020;
            --panel: #121a31;
            --panel-border: #263252;
            --text: #edf2ff;
            --muted: #9db0d0;
            --accent: #78a6ff;
            --success: #4fd1a5;
        }
        body { margin: 0; font-family: Segoe UI, Arial, sans-serif; background: linear-gradient(180deg, #0b1020, #111a33); color: var(--text); }
        .wrap { max-width: 1120px; margin: 0 auto; padding: 40px 24px 64px; }
        h1 { margin: 0 0 12px; font-size: 2rem; }
        p { color: var(--muted); }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-top: 24px; }
        .card { background: rgba(18, 26, 49, 0.9); border: 1px solid var(--panel-border); border-radius: 18px; padding: 18px; box-shadow: 0 18px 40px rgba(0,0,0,0.25); }
        .repo-link { color: var(--text); font-weight: 600; text-decoration: none; }
        .repo-link:hover { color: var(--accent); }
        .meta { margin-top: 8px; font-size: 0.95rem; color: var(--muted); }
        .pill { display: inline-block; margin-top: 10px; padding: 6px 10px; border-radius: 999px; background: rgba(79, 209, 165, 0.12); color: var(--success); font-size: 0.85rem; }
    </style>
</head>
<body>
    <div class=\"wrap\">
        <h1>PromptDrift Dashboard</h1>
        <p>Inspect onboarded repositories, historical backfill coverage, and drift posture from the local PromptDrift store.</p>
        <div id=\"repos\" class=\"grid\"></div>
    </div>
    <script>
        async function loadRepos() {
            const container = document.getElementById('repos');
            container.innerHTML = '<div class="card">Loading repositories...</div>';
            const response = await fetch('/api/repos');
            const payload = await response.json();
            if (!payload.repos.length) {
                container.innerHTML = '<div class="card">No onboarded repositories yet. Use the onboarding API first.</div>';
                return;
            }
            container.innerHTML = payload.repos.map((repo) => `
                <div class="card">
                    <a class="repo-link" href="/dashboard/${encodeURIComponent(repo.repo_full)}">${repo.repo_full}</a>
                    <div class="meta">Default branch: ${repo.default_branch}</div>
                    <div class="meta">Discovered artifacts: ${repo.discovered_artifact_count}</div>
                    <div class="pill">${repo.onboarding_status}</div>
                </div>
            `).join('');
        }
        loadRepos();
    </script>
</body>
</html>
"""


def _render_repo_dashboard_page(repo_full: str) -> str:
        escaped_repo_full = html_escape(repo_full)
        return f"""
<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>{escaped_repo_full} · PromptDrift</title>
    <style>
        :root {{
            color-scheme: dark;
            --bg: #0b1020;
            --panel: #121a31;
            --panel-border: #263252;
            --text: #edf2ff;
            --muted: #9db0d0;
            --accent: #78a6ff;
            --warning: #f6ad55;
        }}
        body {{ margin: 0; font-family: Segoe UI, Arial, sans-serif; background: linear-gradient(180deg, #0b1020, #111a33); color: var(--text); }}
        .wrap {{ max-width: 1200px; margin: 0 auto; padding: 32px 24px 64px; }}
        .topbar {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }}
        .link {{ color: var(--accent); text-decoration: none; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-top: 24px; }}
        .card {{ background: rgba(18, 26, 49, 0.9); border: 1px solid var(--panel-border); border-radius: 18px; padding: 18px; box-shadow: 0 18px 40px rgba(0,0,0,0.25); }}
        .metric {{ font-size: 1.8rem; font-weight: 700; margin-top: 10px; }}
        .muted {{ color: var(--muted); }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
        th, td {{ text-align: left; padding: 12px 10px; border-bottom: 1px solid rgba(157, 176, 208, 0.15); font-size: 0.95rem; vertical-align: top; }}
        th {{ color: var(--muted); font-weight: 600; }}
        .section {{ margin-top: 28px; }}
        .callout {{ color: var(--warning); font-size: 0.92rem; }}
    </style>
</head>
<body>
    <div class=\"wrap\">
        <div class=\"topbar\">
            <div>
                <a class=\"link\" href=\"/dashboard\">← All repositories</a>
                <h1>{escaped_repo_full}</h1>
                <p class=\"muted\">Unified view of onboarding, backfill lineage, and pull-request drift history.</p>
            </div>
            <div class=\"callout\">Live from the local PromptDrift SQLite store</div>
        </div>

        <div id=\"summary\" class=\"grid\"></div>

        <div class=\"section card\">
            <h2>Top drifting pull-request artifacts</h2>
            <div id=\"leaderboard\" class=\"muted\">Loading drift leaderboard...</div>
        </div>

        <div class=\"section card\">
            <h2>Artifact inventory</h2>
            <div id=\"artifacts\" class=\"muted\">Loading artifact table...</div>
        </div>
    </div>

    <script>
        const repoFull = {repo_full!r};

        function metricCard(label, value, detail) {{
            return `<div class="card"><div class="muted">${{label}}</div><div class="metric">${{value}}</div><div class="muted">${{detail}}</div></div>`;
        }}

        function renderLeaderboard(items) {{
            if (!items.length) {{
                return '<div class="muted">No pull-request drift samples have been recorded yet.</div>';
            }}
            return `<table><thead><tr><th>Artifact</th><th>Type</th><th>Drift magnitude</th><th>Capability shift</th><th>Autonomy shift</th></tr></thead><tbody>${{items.map((item) => `
                <tr>
                    <td>${{item.artifact_path}}</td>
                    <td>${{item.artifact_type}}</td>
                    <td>${{item.drift_magnitude.toFixed(3)}}</td>
                    <td>${{item.capability_shift.toFixed(3)}}</td>
                    <td>${{item.autonomy_shift.toFixed(3)}}</td>
                </tr>`).join('')}}</tbody></table>`;
        }}

        function renderArtifacts(items) {{
            if (!items.length) {{
                return '<div class="muted">No onboarded artifacts were found for this repository yet.</div>';
            }}
            return `<table><thead><tr><th>Artifact</th><th>Baseline lines</th><th>Historical versions</th><th>Historical drift</th><th>PR profiles</th><th>Latest PR semantic distance</th></tr></thead><tbody>${{items.map((item) => `
                <tr>
                    <td><strong>${{item.artifact_path}}</strong><br><span class="muted">${{item.artifact_type}}</span></td>
                    <td>${{item.baseline_line_count}}</td>
                    <td>${{item.historical_version_count}}</td>
                    <td>${{item.latest_historical_drift_magnitude.toFixed(3)}}</td>
                    <td>${{item.pr_profile_count}}</td>
                    <td>${{item.latest_pr_semantic_distance.toFixed(3)}}</td>
                </tr>`).join('')}}</tbody></table>`;
        }}

        async function loadDashboard() {{
            const response = await fetch(`/api/repos/${{encodeURIComponent(repoFull)}}/dashboard`);
            const payload = await response.json();

            document.getElementById('summary').innerHTML = [
                metricCard('Onboarded artifacts', payload.onboarding ? payload.onboarding.discovered_artifact_count : 0, payload.onboarding ? `Default branch: ${{payload.onboarding.default_branch}}` : 'No onboarding yet'),
                metricCard('Baseline versions', payload.baseline_version_count, `Repo: ${{payload.repo_full}}`),
                metricCard('Backfill jobs', payload.backfill.job_count, `Completed: ${{payload.backfill.completed_job_count}} · Failed: ${{payload.backfill.failed_job_count}}`),
                metricCard('Historical versions', payload.backfill.total_historical_versions, `Historical profiles: ${{payload.backfill.total_historical_profiles}}`),
                metricCard('PR audits', payload.pull_request_audit_count, `PR profiles: ${{payload.drift_summary.profile_count}}`),
                metricCard('Avg semantic distance', payload.drift_summary.avg_semantic_distance.toFixed(3), `Highest capability artifact: ${{payload.drift_summary.highest_capability_artifact_path || 'n/a'}}`),
            ].join('');

            document.getElementById('leaderboard').innerHTML = renderLeaderboard(payload.top_drifting_artifacts);
            document.getElementById('artifacts').innerHTML = renderArtifacts(payload.artifacts);
        }}

        loadDashboard();
    </script>
</body>
</html>
"""
