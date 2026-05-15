import os
import sys
import base64
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services import github_integration
from services.github_integration import _resolve_private_key_path, create_pr_review, ensure_pr_label, generate_jwt, list_pr_comment_reactions, list_pr_review_reactions, remove_pr_label, sync_pr_label


def test_list_repository_files_reuses_cached_tree_for_same_repo_and_ref(monkeypatch):
    github_integration._REPOSITORY_FILE_LIST_CACHE.clear()
    repo_calls = []
    tree_calls = []

    class FakeRepo:
        default_branch = "main"

        def get_git_tree(self, sha, recursive=True):
            tree_calls.append((sha, recursive))
            return SimpleNamespace(
                tree=[
                    SimpleNamespace(path="src/app.py", type="blob"),
                    SimpleNamespace(path="README.md", type="blob"),
                    SimpleNamespace(path="docs", type="tree"),
                ]
            )

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            repo_calls.append(repo_full)
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    first = github_integration.list_repository_files("doria90/dummyAI", "installation-token", ref="main")
    second = github_integration.list_repository_files("doria90/dummyAI", "different-token", ref="main")

    assert first == ["README.md", "src/app.py"]
    assert second == ["README.md", "src/app.py"]
    assert repo_calls == ["doria90/dummyAI"]
    assert tree_calls == [("main", True)]


def test_list_repository_files_cache_is_keyed_by_ref(monkeypatch):
    github_integration._REPOSITORY_FILE_LIST_CACHE.clear()
    tree_calls = []

    class FakeRepo:
        default_branch = "main"

        def get_git_tree(self, sha, recursive=True):
            tree_calls.append((sha, recursive))
            return SimpleNamespace(tree=[SimpleNamespace(path=f"{sha}.md", type="blob")])

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    main_files = github_integration.list_repository_files("doria90/dummyAI", "installation-token", ref="main")
    release_files = github_integration.list_repository_files("doria90/dummyAI", "installation-token", ref="release")

    assert main_files == ["main.md"]
    assert release_files == ["release.md"]
    assert tree_calls == [("main", True), ("release", True)]


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


def test_generate_jwt_supports_inline_private_key(monkeypatch):
    captured = {}

    def fake_encode(payload, private_key, algorithm):
        captured['payload'] = payload
        captured['private_key'] = private_key
        captured['algorithm'] = algorithm
        return 'encoded-inline-token'

    monkeypatch.setattr(github_integration.time, 'time', lambda: 1_700_000_100)
    monkeypatch.setattr(github_integration.jwt, 'encode', fake_encode)

    token = generate_jwt('2963335', '', 'line-one\\nline-two')

    assert token == 'encoded-inline-token'
    assert captured['private_key'] == 'line-one\nline-two'
    assert captured['algorithm'] == 'RS256'


def test_get_installation_token_reuses_cached_token_until_expiry(monkeypatch):
    github_integration._INSTALLATION_TOKEN_CACHE.clear()
    requests = []

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"token":"installation-token","expires_at":"2099-01-01T00:00:00Z"}'

    def fake_urlopen(request: Request):
        requests.append(request.full_url)
        return DummyResponse()

    monkeypatch.setattr(github_integration.urllib.request, "urlopen", fake_urlopen)

    first = github_integration.get_installation_token("jwt-one", 123)
    second = github_integration.get_installation_token("jwt-two", 123)

    assert first == "installation-token"
    assert second == "installation-token"
    assert requests == ["https://api.github.com/app/installations/123/access_tokens"]


def test_get_installation_token_refreshes_after_cached_expiry(monkeypatch):
    github_integration._INSTALLATION_TOKEN_CACHE.clear()
    requests = []
    time_values = iter([1_000.0, 1_005.0])
    payloads = iter([
        b'{"token":"installation-token-1","expires_at":"1970-01-01T00:17:40Z"}',
        b'{"token":"installation-token-2","expires_at":"1970-01-01T00:22:20Z"}',
    ])

    class DummyResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    def fake_urlopen(request: Request):
        requests.append(request.full_url)
        return DummyResponse(next(payloads))

    monkeypatch.setattr(github_integration.time, "time", lambda: next(time_values))
    monkeypatch.setattr(github_integration.urllib.request, "urlopen", fake_urlopen)

    first = github_integration.get_installation_token("jwt-one", 123)
    second = github_integration.get_installation_token("jwt-two", 123)

    assert first == "installation-token-1"
    assert second == "installation-token-2"
    assert requests == [
        "https://api.github.com/app/installations/123/access_tokens",
        "https://api.github.com/app/installations/123/access_tokens",
    ]


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


def test_upsert_pr_comment_updates_existing_episode_comment(monkeypatch):
    edited = []

    class FakeComment:
        def __init__(self, comment_id, body):
            self.id = comment_id
            self.body = body

        def edit(self, body):
            edited.append((self.id, body))
            self.body = body

    class FakePullRequest:
        def __init__(self):
            self.comments = [
                FakeComment(101, "<!-- driftguard:managed-comment -->\nOld audit"),
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

    assert comment_id == 101
    assert edited == [(101, "<!-- driftguard:managed-comment -->\nNew audit")]


def test_upsert_pr_comment_creates_new_episode_comment_without_touching_older_ones(monkeypatch):
    class FakeComment:
        def __init__(self, comment_id, body):
            self.id = comment_id
            self.body = body

        def edit(self, body):
            raise AssertionError("edit should not be called")

    class FakePullRequest:
        def __init__(self):
            self.comments = [
                FakeComment(101, "<!-- driftguard:managed-comment -->\nOld audit"),
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


def test_upsert_pr_comment_normalizes_legacy_promptdrift_marker(monkeypatch):
    edited = []

    class FakeComment:
        def __init__(self, comment_id, body):
            self.id = comment_id
            self.body = body

        def edit(self, body):
            edited.append((self.id, body))
            self.body = body

    class FakePullRequest:
        def __init__(self):
            self.comments = [
                FakeComment(101, "<!-- promptdrift:managed-comment -->\nOld audit"),
            ]

        def get_issue_comments(self):
            return self.comments

        def create_issue_comment(self, body):
            raise AssertionError("create_issue_comment should not be called")

    class FakeRepo:
        def __init__(self):
            self.pull = FakePullRequest()

        def get_pull(self, pr_number):
            assert pr_number == 17
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
        17,
        "installation-token",
        "New audit",
        existing_comment_id=101,
    )

    assert comment_id == 101
    assert edited == [(101, "<!-- driftguard:managed-comment -->\nNew audit")]


def test_create_pr_review_wraps_body_with_managed_marker(monkeypatch):
    created_reviews = []

    class FakeReview:
        def __init__(self, review_id):
            self.id = review_id

    class FakePullRequest:
        def create_review(self, *, body, event):
            created_reviews.append((body, event))
            return FakeReview(404)

    class FakeRepo:
        def __init__(self):
            self.pull = FakePullRequest()

        def get_pull(self, pr_number):
            assert pr_number == 21
            return self.pull

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    review_id = create_pr_review(
        "doria90/dummyAI",
        21,
        "installation-token",
        "Review body",
        event="REQUEST_CHANGES",
    )

    assert review_id == 404
    assert created_reviews == [("<!-- driftguard:managed-comment -->\nReview body", "REQUEST_CHANGES")]


def test_list_pr_comment_reactions_returns_serialized_reactions(monkeypatch):
    class FakeReaction:
        def __init__(self):
            self.id = 41
            self.content = "+1"
            self.user = SimpleNamespace(id=7, login="doria90")
            self.created_at = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)

    class FakeComment:
        def __init__(self, comment_id):
            self.id = comment_id

        def get_reactions(self):
            return [FakeReaction()]

    class FakePullRequest:
        def get_issue_comments(self):
            return [FakeComment(301)]

    class FakeRepo:
        def get_pull(self, pr_number):
            assert pr_number == 55
            return FakePullRequest()

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    reactions = list_pr_comment_reactions("doria90/dummyAI", 55, "installation-token", comment_id=301)

    assert len(reactions) == 1
    assert reactions[0].reaction_id == "41"
    assert reactions[0].content == "+1"
    assert reactions[0].user_login == "doria90"
    assert reactions[0].target_kind == "issue_comment"
    assert reactions[0].target_id == 301


def test_list_pr_review_reactions_returns_serialized_reactions(monkeypatch):
    class FakeReaction:
        def __init__(self):
            self.id = 51
            self.content = "heart"
            self.user = SimpleNamespace(id=8, login="octocat")
            self.created_at = datetime(2026, 5, 14, 12, 30, 0, tzinfo=timezone.utc)

    class FakeReview:
        def __init__(self, review_id):
            self.id = review_id

        def get_reactions(self):
            return [FakeReaction()]

    class FakePullRequest:
        def get_reviews(self):
            return [FakeReview(401)]

    class FakeRepo:
        def get_pull(self, pr_number):
            assert pr_number == 56
            return FakePullRequest()

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    reactions = list_pr_review_reactions("doria90/dummyAI", 56, "installation-token", review_id=401)

    assert len(reactions) == 1
    assert reactions[0].reaction_id == "51"
    assert reactions[0].content == "heart"
    assert reactions[0].user_login == "octocat"
    assert reactions[0].target_kind == "review"
    assert reactions[0].target_id == 401


def test_ensure_pr_label_creates_missing_repo_label_and_applies_it(monkeypatch):
    created_labels = []
    issue_added_labels = []

    class FakeLabel:
        def __init__(self, name):
            self.name = name

    class FakeIssue:
        def __init__(self):
            self.labels = []

        def get_labels(self):
            return self.labels

        def add_to_labels(self, label_name):
            issue_added_labels.append(label_name)
            self.labels.append(FakeLabel(label_name))

    class FakeRepo:
        def __init__(self):
            self.labels = [FakeLabel("bug")]
            self.issue = FakeIssue()

        def get_labels(self):
            return self.labels

        def create_label(self, name, color, description):
            created_labels.append((name, color, description))
            self.labels.append(FakeLabel(name))

        def get_issue(self, number):
            assert number == 9
            return self.issue

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    applied = ensure_pr_label("doria90/dummyAI", 9, "installation-token")

    assert applied is True
    assert created_labels == [
        (
            github_integration.DRIFTGUARD_ESCALATION_LABEL,
            github_integration.DRIFTGUARD_ESCALATION_LABEL_COLOR,
            github_integration.DRIFTGUARD_ESCALATION_LABEL_DESCRIPTION,
        )
    ]
    assert issue_added_labels == [github_integration.DRIFTGUARD_ESCALATION_LABEL]


def test_ensure_pr_label_is_idempotent_when_label_already_exists(monkeypatch):
    created_labels = []
    issue_added_labels = []

    class FakeLabel:
        def __init__(self, name):
            self.name = name

    class FakeIssue:
        def __init__(self):
            self.labels = [FakeLabel(github_integration.DRIFTGUARD_ESCALATION_LABEL)]

        def get_labels(self):
            return self.labels

        def add_to_labels(self, label_name):
            issue_added_labels.append(label_name)

    class FakeRepo:
        def __init__(self):
            self.labels = [FakeLabel(github_integration.DRIFTGUARD_ESCALATION_LABEL)]
            self.issue = FakeIssue()

        def get_labels(self):
            return self.labels

        def create_label(self, name, color, description):
            created_labels.append((name, color, description))

        def get_issue(self, number):
            assert number == 10
            return self.issue

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    applied = ensure_pr_label("doria90/dummyAI", 10, "installation-token")

    assert applied is False
    assert created_labels == []
    assert issue_added_labels == []


def test_ensure_pr_label_replaces_legacy_promptdrift_issue_label(monkeypatch):
    created_labels = []
    issue_added_labels = []
    removed_labels = []

    class FakeLabel:
        def __init__(self, name):
            self.name = name

    class FakeIssue:
        def __init__(self):
            self.labels = [FakeLabel(github_integration.PROMPTDRIFT_ESCALATION_LABEL)]

        def get_labels(self):
            return self.labels

        def add_to_labels(self, label_name):
            issue_added_labels.append(label_name)
            self.labels.append(FakeLabel(label_name))

        def remove_from_labels(self, *label_names):
            removed_labels.extend(label_names)
            self.labels = [label for label in self.labels if label.name not in label_names]

    class FakeRepo:
        def __init__(self):
            self.labels = [FakeLabel(github_integration.PROMPTDRIFT_ESCALATION_LABEL)]
            self.issue = FakeIssue()

        def get_labels(self):
            return self.labels

        def create_label(self, name, color, description):
            created_labels.append((name, color, description))
            self.labels.append(FakeLabel(name))

        def get_issue(self, number):
            assert number == 18
            return self.issue

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    applied = ensure_pr_label("doria90/dummyAI", 18, "installation-token")

    assert applied is True
    assert created_labels == [
        (
            github_integration.DRIFTGUARD_ESCALATION_LABEL,
            github_integration.DRIFTGUARD_ESCALATION_LABEL_COLOR,
            github_integration.DRIFTGUARD_ESCALATION_LABEL_DESCRIPTION,
        )
    ]
    assert removed_labels == [github_integration.PROMPTDRIFT_ESCALATION_LABEL]
    assert issue_added_labels == [github_integration.DRIFTGUARD_ESCALATION_LABEL]


def test_ensure_pr_label_replaces_legacy_driftguard_issue_label(monkeypatch):
    created_labels = []
    issue_added_labels = []
    removed_labels = []

    class FakeLabel:
        def __init__(self, name):
            self.name = name

    class FakeIssue:
        def __init__(self):
            self.labels = [FakeLabel(github_integration.LEGACY_DRIFTGUARD_ESCALATION_LABEL)]

        def get_labels(self):
            return self.labels

        def add_to_labels(self, label_name):
            issue_added_labels.append(label_name)
            self.labels.append(FakeLabel(label_name))

        def remove_from_labels(self, *label_names):
            removed_labels.extend(label_names)
            self.labels = [label for label in self.labels if label.name not in label_names]

    class FakeRepo:
        def __init__(self):
            self.labels = [FakeLabel(github_integration.LEGACY_DRIFTGUARD_ESCALATION_LABEL)]
            self.issue = FakeIssue()

        def get_labels(self):
            return self.labels

        def create_label(self, name, color, description):
            created_labels.append((name, color, description))
            self.labels.append(FakeLabel(name))

        def get_issue(self, number):
            assert number == 28
            return self.issue

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    applied = ensure_pr_label("doria90/dummyAI", 28, "installation-token")

    assert applied is True
    assert created_labels == [
        (
            github_integration.DRIFTGUARD_ESCALATION_LABEL,
            github_integration.DRIFTGUARD_ESCALATION_LABEL_COLOR,
            github_integration.DRIFTGUARD_ESCALATION_LABEL_DESCRIPTION,
        )
    ]
    assert removed_labels == [github_integration.LEGACY_DRIFTGUARD_ESCALATION_LABEL]
    assert issue_added_labels == [github_integration.DRIFTGUARD_ESCALATION_LABEL]


def test_remove_pr_label_removes_existing_issue_label(monkeypatch):
    removed_labels = []

    class FakeLabel:
        def __init__(self, name):
            self.name = name

    class FakeIssue:
        def __init__(self):
            self.labels = [FakeLabel(github_integration.DRIFTGUARD_ESCALATION_LABEL), FakeLabel("bug")]

        def get_labels(self):
            return self.labels

        def remove_from_labels(self, *label_names):
            removed_labels.extend(label_names)
            self.labels = [label for label in self.labels if label.name not in label_names]

    class FakeRepo:
        def __init__(self):
            self.issue = FakeIssue()

        def get_issue(self, number):
            assert number == 11
            return self.issue

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    removed = remove_pr_label("doria90/dummyAI", 11, "installation-token")

    assert removed is True
    assert removed_labels == [github_integration.DRIFTGUARD_ESCALATION_LABEL]


def test_remove_pr_label_removes_legacy_promptdrift_issue_label(monkeypatch):
    removed_labels = []

    class FakeLabel:
        def __init__(self, name):
            self.name = name

    class FakeIssue:
        def __init__(self):
            self.labels = [FakeLabel(github_integration.PROMPTDRIFT_ESCALATION_LABEL), FakeLabel("bug")]

        def get_labels(self):
            return self.labels

        def remove_from_labels(self, *label_names):
            removed_labels.extend(label_names)
            self.labels = [label for label in self.labels if label.name not in label_names]

    class FakeRepo:
        def __init__(self):
            self.issue = FakeIssue()

        def get_issue(self, number):
            assert number == 19
            return self.issue

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    removed = remove_pr_label("doria90/dummyAI", 19, "installation-token")

    assert removed is True
    assert removed_labels == [github_integration.PROMPTDRIFT_ESCALATION_LABEL]


def test_remove_pr_label_removes_legacy_driftguard_issue_label(monkeypatch):
    removed_labels = []

    class FakeLabel:
        def __init__(self, name):
            self.name = name

    class FakeIssue:
        def __init__(self):
            self.labels = [FakeLabel(github_integration.LEGACY_DRIFTGUARD_ESCALATION_LABEL), FakeLabel("bug")]

        def get_labels(self):
            return self.labels

        def remove_from_labels(self, *label_names):
            removed_labels.extend(label_names)
            self.labels = [label for label in self.labels if label.name not in label_names]

    class FakeRepo:
        def __init__(self):
            self.issue = FakeIssue()

        def get_issue(self, number):
            assert number == 29
            return self.issue

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    removed = remove_pr_label("doria90/dummyAI", 29, "installation-token")

    assert removed is True
    assert removed_labels == [github_integration.LEGACY_DRIFTGUARD_ESCALATION_LABEL]


def test_remove_pr_label_is_noop_when_label_absent(monkeypatch):
    removed_labels = []

    class FakeLabel:
        def __init__(self, name):
            self.name = name

    class FakeIssue:
        def __init__(self):
            self.labels = [FakeLabel("bug")]

        def get_labels(self):
            return self.labels

        def remove_from_labels(self, label_name):
            removed_labels.append(label_name)

    class FakeRepo:
        def __init__(self):
            self.issue = FakeIssue()

        def get_issue(self, number):
            assert number == 12
            return self.issue

    class FakeGithub:
        def __init__(self, auth):
            self.auth = auth

        def get_repo(self, repo_full):
            assert repo_full == "doria90/dummyAI"
            return FakeRepo()

    monkeypatch.setattr(github_integration, "Github", FakeGithub)

    removed = remove_pr_label("doria90/dummyAI", 12, "installation-token")

    assert removed is False
    assert removed_labels == []


def test_sync_pr_label_removes_label_when_not_required(monkeypatch):
    captured = []

    monkeypatch.setattr(github_integration, "remove_pr_label", lambda repo, pr, token, label_name=None: captured.append((repo, pr, token, label_name)) or True)

    changed = sync_pr_label(
        "doria90/dummyAI",
        13,
        "installation-token",
        should_have_label=False,
    )

    assert changed is True
    assert captured == [("doria90/dummyAI", 13, "installation-token", github_integration.DRIFTGUARD_ESCALATION_LABEL)]
