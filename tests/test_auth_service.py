import json
import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services import auth_service


def test_build_github_oauth_authorize_url_contains_expected_query():
    url = auth_service.build_github_oauth_authorize_url(
        "client-123",
        "https://app.driftguard.ai/auth/github/callback",
        "opaque-state",
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"
    assert query["client_id"] == ["client-123"]
    assert query["redirect_uri"] == ["https://app.driftguard.ai/auth/github/callback"]
    assert query["state"] == ["opaque-state"]
    assert query["scope"] == ["read:user user:email"]


def test_generated_auth_tokens_are_non_empty_and_distinct():
    oauth_state = auth_service.generate_oauth_state()
    session_id = auth_service.generate_session_id()
    csrf_secret = auth_service.generate_csrf_secret()

    assert oauth_state
    assert session_id
    assert csrf_secret
    assert oauth_state != session_id
    assert oauth_state != csrf_secret
    assert session_id != csrf_secret


def test_exchange_code_for_access_token_parses_github_response(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"access_token": "token-123", "scope": "read:user,user:email,repo"}).encode("utf-8")

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data.decode("utf-8")
        return FakeResponse()

    monkeypatch.setattr(auth_service.urllib.request, "urlopen", fake_urlopen)

    token = auth_service.exchange_code_for_access_token(
        "client-123",
        "secret-456",
        "code-789",
        "https://app.driftguard.ai/auth/github/callback",
    )

    assert captured["url"] == auth_service.GITHUB_ACCESS_TOKEN_URL
    assert captured["headers"]["Accept"] == "application/json"
    assert "client_id=client-123" in captured["body"]
    assert token.access_token == "token-123"
    assert token.granted_scopes == ["read:user", "user:email", "repo"]


def test_fetch_github_user_profile_parses_required_fields(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "id": 42,
                    "login": "doria90",
                    "name": "Doria",
                    "email": "doria@example.com",
                    "avatar_url": "https://avatars.example.com/doria90",
                }
            ).encode("utf-8")

    monkeypatch.setattr(auth_service.urllib.request, "urlopen", lambda request: FakeResponse())

    profile = auth_service.fetch_github_user_profile("oauth-token")

    assert profile.github_user_id == "42"
    assert profile.login == "doria90"
    assert profile.display_name == "Doria"
    assert profile.email == "doria@example.com"
    assert profile.avatar_url == "https://avatars.example.com/doria90"


def test_list_github_user_repositories_parses_repository_inventory(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                [
                    {
                        "id": 1,
                        "full_name": "doria90/PromptDrift",
                        "default_branch": "main",
                        "private": True,
                        "html_url": "https://github.com/doria90/PromptDrift",
                    },
                    {
                        "id": 2,
                        "full_name": "doria90/dummyAI",
                        "default_branch": "main",
                        "private": False,
                        "html_url": "https://github.com/doria90/dummyAI",
                    },
                ]
            ).encode("utf-8")

    monkeypatch.setattr(auth_service.urllib.request, "urlopen", lambda request: FakeResponse())

    repositories = auth_service.list_github_user_repositories("oauth-token")

    assert [repository.full_name for repository in repositories] == ["doria90/PromptDrift", "doria90/dummyAI"]
    assert repositories[0].is_private is True
    assert repositories[1].html_url == "https://github.com/doria90/dummyAI"