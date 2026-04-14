from __future__ import annotations

import json
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass
from urllib.error import HTTPError


GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_USER_EMAILS_URL = "https://api.github.com/user/emails"
GITHUB_USER_REPOS_URL = "https://api.github.com/user/repos"
GITHUB_OAUTH_SCOPE = "read:user user:email repo"


@dataclass(frozen=True)
class GithubOAuthToken:
    access_token: str
    granted_scopes: list[str]


@dataclass(frozen=True)
class GithubUserProfile:
    github_user_id: str
    login: str
    display_name: str
    email: str | None
    avatar_url: str | None
    profile_url: str | None = None
    company: str | None = None
    blog: str | None = None
    location: str | None = None
    bio: str | None = None
    twitter_username: str | None = None


@dataclass(frozen=True)
class GithubUserRepository:
    github_repo_id: str
    full_name: str
    default_branch: str | None
    is_private: bool
    html_url: str | None


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(24)


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def generate_csrf_secret() -> str:
    return secrets.token_urlsafe(24)


def build_github_oauth_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": GITHUB_OAUTH_SCOPE,
            "state": state,
        }
    )
    return f"{GITHUB_AUTHORIZE_URL}?{query}"


def exchange_code_for_access_token(client_id: str, client_secret: str, code: str, redirect_uri: str) -> GithubOAuthToken:
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = urllib.request.Request(GITHUB_ACCESS_TOKEN_URL, data=payload, method="POST")
    request.add_header("Accept", "application/json")
    with urllib.request.urlopen(request) as response:
        data = json.load(response)

    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("GitHub OAuth token exchange did not return an access token.")

    scopes = [scope for scope in str(data.get("scope") or "").split(",") if scope]
    return GithubOAuthToken(access_token=access_token, granted_scopes=scopes)


def _load_github_json(url: str, access_token: str) -> object:
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {access_token}")
    request.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def _resolve_primary_email(access_token: str, profile_data: dict[str, object]) -> str | None:
    public_email = profile_data.get("email")
    if public_email:
        return str(public_email)
    try:
        email_payload = _load_github_json(GITHUB_USER_EMAILS_URL, access_token)
    except HTTPError:
        return None

    if not isinstance(email_payload, list):
        return None

    fallback_email: str | None = None
    for entry in email_payload:
        if not isinstance(entry, dict):
            continue
        email = str(entry.get("email") or "").strip()
        if not email:
            continue
        if fallback_email is None:
            fallback_email = email
        if entry.get("primary"):
            return email
    return fallback_email


def fetch_github_user_profile(access_token: str) -> GithubUserProfile:
    data = _load_github_json(GITHUB_USER_URL, access_token)
    if not isinstance(data, dict):
        raise RuntimeError("GitHub user profile response was not an object.")

    github_user_id = data.get("id")
    login = data.get("login")
    if not github_user_id or not login:
        raise RuntimeError("GitHub user profile did not include the required immutable id and login.")

    display_name = data.get("name") or login
    email = _resolve_primary_email(access_token, data)
    avatar_url = data.get("avatar_url")
    return GithubUserProfile(
        github_user_id=str(github_user_id),
        login=str(login),
        display_name=str(display_name),
        email=str(email) if email else None,
        avatar_url=str(avatar_url) if avatar_url else None,
        profile_url=str(data.get("html_url")) if data.get("html_url") else None,
        company=str(data.get("company")) if data.get("company") else None,
        blog=str(data.get("blog")) if data.get("blog") else None,
        location=str(data.get("location")) if data.get("location") else None,
        bio=str(data.get("bio")) if data.get("bio") else None,
        twitter_username=str(data.get("twitter_username")) if data.get("twitter_username") else None,
    )


def list_github_user_repositories(access_token: str) -> list[GithubUserRepository]:
    repositories: list[GithubUserRepository] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {
                "per_page": 100,
                "page": page,
                "affiliation": "owner,collaborator,organization_member",
                "sort": "full_name",
            }
        )
        request = urllib.request.Request(f"{GITHUB_USER_REPOS_URL}?{query}")
        request.add_header("Authorization", f"Bearer {access_token}")
        request.add_header("Accept", "application/vnd.github+json")
        with urllib.request.urlopen(request) as response:
            data = json.load(response)

        if not isinstance(data, list) or not data:
            break

        for repo in data:
            if not isinstance(repo, dict):
                continue
            full_name = str(repo.get("full_name") or "").strip()
            if not full_name:
                continue
            repositories.append(
                GithubUserRepository(
                    github_repo_id=str(repo.get("id") or full_name),
                    full_name=full_name,
                    default_branch=str(repo.get("default_branch")) if repo.get("default_branch") else None,
                    is_private=bool(repo.get("private", True)),
                    html_url=str(repo.get("html_url")) if repo.get("html_url") else None,
                )
            )

        if len(data) < 100:
            break
        page += 1

    return repositories