import os
import time
import hmac
import hashlib
import json
import urllib.request

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import jwt
from github import Github
from openai import OpenAI

from engine.analysis import analyze_diff as analyze_diff_signals
from engine.relevance import needs_audit as engine_needs_audit

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

if not all([GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH, GITHUB_WEBHOOK_SECRET, AI_API_KEY]):
    raise RuntimeError("Required environment variables are missing. Check .env or .env.example")

client = OpenAI(api_key=AI_API_KEY, base_url=AZURE_OPENAI_ENDPOINT or None)

app = FastAPI()


async def verify_signature(request: Request) -> bool:
    signature = request.headers.get("X-Hub-Signature-256")
    if signature is None:
        return False
    raw = await request.body()
    mac = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), raw, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, signature)


def generate_jwt() -> str:
    # load private key from file
    with open(GITHUB_PRIVATE_KEY_PATH, "r") as f:
        private_key = f.read()
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),  # max 10 minutes
        # GitHub requires `iss` to be a string
        "iss": str(GITHUB_APP_ID),
    }
    token = jwt.encode(payload, private_key, algorithm="RS256")
    # PyJWT returns str in newest versions; ensure str
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def get_installation_token(jwt_token: str, installation_id: int) -> str:
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {jwt_token}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
    except Exception as e:
        raise RuntimeError(f"Failed to obtain installation token: {e}")
    return data.get("token")


def fetch_pr_diff(repo_full: str, pr_number: int, token: str) -> str:
    diff_url = f"https://api.github.com/repos/{repo_full}/pulls/{pr_number}"
    req = urllib.request.Request(diff_url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.v3.diff")
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8")


def needs_audit(diff: str) -> bool:
    return engine_needs_audit(diff)


def analyze_diff(diff: str) -> str:
    deterministic_analysis = analyze_diff_signals(diff)
    system = (
        "You are an AI Security Auditor. Analyze this code diff. "
        "You will receive both the raw diff and deterministic pre-analysis findings. "
        "Use the deterministic findings as grounding evidence, then write a 2-sentence summary and assign a Risk Level (Low/Medium/High). Format as Markdown."
    )
    response = client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"{deterministic_analysis.format_for_prompt()}\n\nRaw diff:\n{diff}"},
        ],
        temperature=0.0,
    )
    return response.choices[0].message.content or "Audit failed: empty response from AI model."


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

    if not all([installation_id, repo_full, pr_number]):
        raise HTTPException(status_code=400, detail="Missing payload data")

    jwt_token = generate_jwt()
    token = get_installation_token(jwt_token, installation_id)
    diff_text = fetch_pr_diff(repo_full, pr_number, token)

    if not needs_audit(diff_text):
        return JSONResponse({"message": "no relevant changes"})

    analysis = analyze_diff(diff_text)
    gh = Github(token)
    repo = gh.get_repo(repo_full)
    pr = repo.get_pull(pr_number)
    pr.create_issue_comment(analysis)

    return JSONResponse({"message": "comment posted"})
