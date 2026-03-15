import os
import sys
import base64
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services import github_integration
from services.github_integration import _resolve_private_key_path, generate_jwt


def test_resolve_private_key_path_prefers_cwd_when_relative_file_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    key_path = tmp_path / "test-key.pem"
    key_path.write_text("dummy", encoding="utf-8")

    resolved = _resolve_private_key_path("test-key.pem")

    assert resolved == key_path.resolve()


def test_resolve_private_key_path_falls_back_to_project_root_relative_path(tmp_path, monkeypatch):
    project_root = Path(__file__).resolve().parent.parent
    workspace_key_path = project_root.parent / "test-relative-key.pem"
    workspace_key_path.write_text("dummy", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    try:
        resolved = _resolve_private_key_path("../test-relative-key.pem")
        assert resolved == workspace_key_path.resolve()
    finally:
        workspace_key_path.unlink(missing_ok=True)


def test_generate_jwt_uses_safe_expiration_window(tmp_path, monkeypatch):
    key_path = tmp_path / "test-key.pem"
    key_path.write_text("dummy-private-key", encoding="utf-8")

    captured = {}

    def fake_encode(payload, private_key, algorithm):
        captured['payload'] = payload
        captured['private_key'] = private_key
        captured['algorithm'] = algorithm
        return 'encoded-token'

    monkeypatch.setattr(github_integration.time, 'time', lambda: 1_700_000_000)
    monkeypatch.setattr(github_integration.jwt, 'encode', fake_encode)

    token = generate_jwt('2963335', str(key_path))

    assert token == 'encoded-token'
    assert captured['private_key'] == 'dummy-private-key'
    assert captured['algorithm'] == 'RS256'
    assert captured['payload']['iss'] == '2963335'
    assert captured['payload']['iat'] == 1_700_000_000 - github_integration.JWT_ISSUED_AT_SKEW_SECONDS
    assert captured['payload']['exp'] == 1_700_000_000 + github_integration.JWT_LIFETIME_SECONDS
    assert github_integration.JWT_LIFETIME_SECONDS < 10 * 60


def test_fetch_compare_diff_uses_compare_endpoint(monkeypatch):
    captured = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"diff --git a/prompts/test.txt b/prompts/test.txt\n"

    def fake_urlopen(request: Request):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["accept"] = request.get_header("Accept")
        return DummyResponse()

    monkeypatch.setattr(github_integration.urllib.request, "urlopen", fake_urlopen)

    diff = github_integration.fetch_compare_diff("doria90/dummyAI", "base123", "head456", "installation-token")

    assert diff == "diff --git a/prompts/test.txt b/prompts/test.txt\n"
    assert captured["url"] == "https://api.github.com/repos/doria90/dummyAI/compare/base123...head456"
    assert captured["authorization"] == "Bearer installation-token"
    assert captured["accept"] == "application/vnd.github.v3.diff"


def test_fetch_commit_pair_diff_reconstructs_diff_from_git_trees(monkeypatch):
    blobs = {
        "sha-system-old": "You are a safe banking assistant.\n",
        "sha-system-new": "You are a safe banking assistant.\nKeep explanations concise.\n",
        "sha-model-new": "model: gpt-4o\n",
    }

    class FakeRepo:
        def get_git_tree(self, sha, recursive=True):
            if sha == "base123":
                return SimpleNamespace(
                    tree=[
                        SimpleNamespace(path="system_prompt.md", sha="sha-system-old", type="blob"),
                    ]
                )
            if sha == "head456":
                return SimpleNamespace(
                    tree=[
                        SimpleNamespace(path="config/model.yaml", sha="sha-model-new", type="blob"),
                        SimpleNamespace(path="system_prompt.md", sha="sha-system-new", type="blob"),
                    ]
                )
            raise AssertionError(f"unexpected tree sha: {sha}")

        def get_git_blob(self, sha):
            return SimpleNamespace(content=base64.b64encode(blobs[sha].encode("utf-8")).decode("ascii"))

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    diff = github_integration.fetch_commit_pair_diff("doria90/dummyAI", "base123", "head456", "installation-token")

    assert "diff --git a/config/model.yaml b/config/model.yaml" in diff
    assert "+model: gpt-4o" in diff
    assert "diff --git a/system_prompt.md b/system_prompt.md" in diff
    assert "+Keep explanations concise." in diff


def test_upsert_pr_comment_replaces_previous_managed_comment(monkeypatch):
    deleted = []

    class FakeComment:
        def __init__(self, comment_id, body):
            self.id = comment_id
            self.body = body

        def delete(self):
            deleted.append(self.id)

    class FakePullRequest:
        def __init__(self):
            self.comments = [
                FakeComment(101, "<!-- promptdrift:managed-comment -->\nOld audit"),
                FakeComment(202, "A regular reviewer comment"),
            ]

        def get_issue_comments(self):
            return self.comments

        def create_issue_comment(self, body):
            created = FakeComment(303, body)
            self.comments.append(created)
            return created

    class FakeRepo:
        def __init__(self):
            self.pull = FakePullRequest()

        def get_pull(self, pr_number):
            assert pr_number == 7
            return self.pull

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    comment_id = github_integration.upsert_pr_comment(
        "doria90/dummyAI",
        7,
        "installation-token",
        "New audit",
        existing_comment_id=101,
    )

    assert comment_id == 303
    assert deleted == [101]


def test_upsert_pr_comment_creates_first_managed_comment_when_none_exists(monkeypatch):
    class FakeComment:
        def __init__(self, comment_id, body):
            self.id = comment_id
            self.body = body

        def delete(self):
            raise AssertionError("delete should not be called")

    class FakePullRequest:
        def __init__(self):
            self.comments = [FakeComment(202, "A regular reviewer comment")]

        def get_issue_comments(self):
            return self.comments

        def create_issue_comment(self, body):
            created = FakeComment(303, body)
            self.comments.append(created)
            return created

    class FakeRepo:
        def __init__(self):
            self.pull = FakePullRequest()

        def get_pull(self, pr_number):
            assert pr_number == 8
            return self.pull

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    comment_id = github_integration.upsert_pr_comment("doria90/dummyAI", 8, "installation-token", "New audit")

    assert comment_id == 303
