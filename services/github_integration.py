from __future__ import annotations

import json
import time
import urllib.request

import jwt
from github import Auth, Github


def generate_jwt(app_id: str, private_key_path: str) -> str:
    with open(private_key_path, "r") as file_handle:
        private_key = file_handle.read()
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": str(app_id),
    }
    token = jwt.encode(payload, private_key, algorithm="RS256")
    return token.decode("utf-8") if isinstance(token, bytes) else token


def get_installation_token(jwt_token: str, installation_id: int) -> str:
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {jwt_token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req) as response:
        data = json.load(response)
    token = data.get("token")
    if not token:
        raise RuntimeError("GitHub installation token response did not include a token.")
    return token


def fetch_pr_diff(repo_full: str, pr_number: int, token: str) -> str:
    diff_url = f"https://api.github.com/repos/{repo_full}/pulls/{pr_number}"
    req = urllib.request.Request(diff_url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.v3.diff")
    with urllib.request.urlopen(req) as response:
        return response.read().decode("utf-8")


def fetch_file_content(repo_full: str, file_path: str, token: str, *, ref: str) -> str:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    content = repo.get_contents(file_path, ref=ref)
    if isinstance(content, list):
        raise RuntimeError(f"Expected file content for {file_path}, but received a directory listing.")
    return content.decoded_content.decode("utf-8")


def post_pr_comment(repo_full: str, pr_number: int, token: str, body: str) -> None:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    pr = repo.get_pull(pr_number)
    pr.create_issue_comment(body)
