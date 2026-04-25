from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .github_integration import generate_jwt, get_installation_token


def _github_api_json(url: str, token: str) -> dict[str, object]:
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def get_github_app_metadata(app_id: str, private_key_path: str, private_key: str | None = None) -> dict[str, object]:
    jwt_token = generate_jwt(app_id, private_key_path, private_key)
    return _github_api_json("https://api.github.com/app", jwt_token)


def build_github_app_install_url(*, app_slug: str, state: str | None = None) -> str:
    query = urllib.parse.urlencode({"state": state} if state else {})
    base = f"https://github.com/apps/{app_slug}/installations/new"
    return f"{base}?{query}" if query else base


def get_live_github_install_url(app_id: str, private_key_path: str, private_key: str | None = None, *, state: str | None = None) -> str:
    metadata = get_github_app_metadata(app_id, private_key_path, private_key)
    app_slug = str(metadata.get("slug") or "")
    if not app_slug:
        raise RuntimeError("Unable to resolve the GitHub App slug for installation flow.")
    return build_github_app_install_url(app_slug=app_slug, state=state)


def sync_installation_repositories(
    *,
    app_id: str,
    private_key_path: str,
    private_key: str | None,
    installation_id: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    jwt_token = generate_jwt(app_id, private_key_path, private_key)
    installation = _github_api_json(f"https://api.github.com/app/installations/{installation_id}", jwt_token)
    installation_token = get_installation_token(jwt_token, installation_id)
    parsed_repositories: list[dict[str, object]] = []
    page = 1
    while True:
        repositories = _github_api_json(
            f"https://api.github.com/installation/repositories?per_page=100&page={page}",
            installation_token,
        )
        repo_payloads = repositories.get("repositories") if isinstance(repositories, dict) else []
        repo_items = repo_payloads if isinstance(repo_payloads, list) else []
        if not repo_items:
            break
        for repo in repo_items:
            if not isinstance(repo, dict):
                continue
            parsed_repositories.append(
                {
                    "repo_github_id": str(repo.get("id") or repo.get("full_name") or ""),
                    "repo_full": str(repo.get("full_name") or ""),
                    "default_branch": str(repo.get("default_branch") or "main"),
                    "is_private": bool(repo.get("private", True)),
                    "status": "available",
                }
            )
        if len(repo_items) < 100:
            break
        page += 1
    return installation, parsed_repositories