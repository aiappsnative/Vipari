from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import datetime
from urllib.error import HTTPError, URLError

from github.GithubException import GithubException

from engine.relevance import needs_audit as engine_needs_audit
from .github_integration import fetch_commit_pair_diff, fetch_pr_diff


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, signature)


def needs_audit(diff_text: str) -> bool:
    return engine_needs_audit(diff_text)


def get_diff_fetch_error_status_code(exc: Exception) -> int | None:
    if isinstance(exc, HTTPError):
        return exc.code
    return getattr(exc, "status", None)


async def fetch_diff_with_retry(
    repo_full: str,
    pr_number: int,
    token: str,
    *,
    use_commit_pair: bool,
    base_sha: str | None,
    head_sha: str | None,
    attempts: int,
    retry_seconds: float,
) -> str:
    last_error: Exception | None = None
    fetcher = fetch_pr_diff
    fetch_args: tuple[object, ...] = (repo_full, pr_number, token)
    if use_commit_pair and base_sha and head_sha:
        fetcher = fetch_commit_pair_diff
        fetch_args = (repo_full, base_sha, head_sha, token)

    for attempt in range(1, attempts + 1):
        try:
            return fetcher(*fetch_args)
        except (HTTPError, GithubException) as exc:
            if get_diff_fetch_error_status_code(exc) != 404 or attempt == attempts:
                raise
            last_error = exc
            await asyncio.sleep(retry_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to fetch PR diff after retry attempts.")


def parse_github_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def build_webhook_envelope(payload: dict, *, delivery_id: str | None) -> dict | None:
    action = payload.get("action")
    if action not in ("opened", "synchronize", "closed", "reopened"):
        return None

    installation_id = payload.get("installation", {}).get("id")
    repo_full = payload.get("repository", {}).get("full_name")
    pr_number = payload.get("pull_request", {}).get("number")
    pull_request = payload.get("pull_request", {})
    head_sha = pull_request.get("head", {}).get("sha")

    if not all([installation_id, repo_full, pr_number]):
        return None

    return {
        "delivery_id": delivery_id,
        "action": action,
        "installation_id": installation_id,
        "repo_full": repo_full,
        "pr_number": pr_number,
        "base_sha": pull_request.get("base", {}).get("sha"),
        "head_sha": head_sha,
        "pr_state": pull_request.get("state"),
        "pr_merged": pull_request.get("merged"),
        "pr_closed_at": parse_github_timestamp(pull_request.get("closed_at")),
        "pr_merged_at": parse_github_timestamp(pull_request.get("merged_at")),
        "pr_merge_commit_sha": pull_request.get("merge_commit_sha"),
        "pr_updated_at": parse_github_timestamp(pull_request.get("updated_at")),
    }


def is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, URLError):
        return True
    if isinstance(exc, HTTPError):
        return exc.code in {429, 500, 502, 503, 504}
    status = getattr(exc, "status", None)
    return status in {429, 500, 502, 503, 504}
