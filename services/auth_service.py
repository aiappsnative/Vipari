from __future__ import annotations

import json
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass


GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


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
            "scope": "read:user user:email",
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


def fetch_github_user_profile(access_token: str) -> GithubUserProfile:
    request = urllib.request.Request(GITHUB_USER_URL)
    request.add_header("Authorization", f"Bearer {access_token}")
    request.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(request) as response:
        data = json.load(response)

    github_user_id = data.get("id")
    login = data.get("login")
    if not github_user_id or not login:
        raise RuntimeError("GitHub user profile did not include the required immutable id and login.")

    display_name = data.get("name") or login
    email = data.get("email")
    avatar_url = data.get("avatar_url")
    return GithubUserProfile(
        github_user_id=str(github_user_id),
        login=str(login),
        display_name=str(display_name),
        email=str(email) if email else None,
        avatar_url=str(avatar_url) if avatar_url else None,
    )