import os
import sys
import hmac
import hashlib
import asyncio
import json
from unittest.mock import patch
from urllib.error import HTTPError

# make sure the package root is on sys.path so `import main` works
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient
from engine.drift_profile import build_attribute_profile

import main

client = TestClient(main.app)


def sign_payload(payload: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), payload, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def test_verify_signature_valid():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    body = b"payload"
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    class Dummy:
        def __init__(self):
            self.headers = {"X-Hub-Signature-256": sig}
            self._body = body

        async def body(self):
            return self._body

    req = Dummy()
    assert asyncio.run(main.verify_signature(req))


def test_verify_signature_invalid():
    main.GITHUB_WEBHOOK_SECRET = "secret"

    class Dummy:
        def __init__(self):
            self.headers = {"X-Hub-Signature-256": "sha256=wrong"}
            self._body = b"foo"

        async def body(self):
            return self._body

    req = Dummy()
    assert not asyncio.run(main.verify_signature(req))


def test_needs_audit_false():
    diff = """diff --git a/README.md b/README.md
index 123..456
"""
    assert not main.needs_audit(diff)


def test_needs_audit_true():
    diff = """diff --git a/prompts/test.txt b/prompts/test.txt
index 123..456
"""
    assert main.needs_audit(diff)


def test_webhook_invalid_signature():
    payload = {"action": "opened"}
    response = client.post("/webhook", json=payload, headers={"X-Hub-Signature-256": "bad"})
    assert response.status_code == 400


def test_webhook_queues_relevant_audit_job():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 7,
            "base": {"sha": "base-opened"},
            "head": {"sha": "abc123"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
    }

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_pr_diff", return_value="diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n") as fetch_pr_diff, patch(
        "main.fetch_commit_pair_diff"
    ) as fetch_commit_pair_diff, patch("main.create_audit_job") as create_job:
        fetch_commit_pair_diff.return_value = "diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n"
        create_job.return_value = type("Job", (), {"id": 42})()
        response = client.post("/webhook", content=body, headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"message": "audit queued", "job_id": 42}
    fetch_pr_diff.assert_called_once_with("doria90/dummyAI", 7, "installation-token")
    fetch_commit_pair_diff.assert_not_called()
    create_job.assert_called_once()


def test_webhook_prefers_commit_pair_diff_only_for_synchronize():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "synchronize",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 9,
            "base": {"sha": "base123"},
            "head": {"sha": "head456"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
    }

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch(
        "main.fetch_commit_pair_diff", return_value="diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n"
    ) as fetch_commit_pair_diff, patch("main.fetch_pr_diff") as fetch_pr_diff, patch("main.create_audit_job") as create_job:
        create_job.return_value = type("Job", (), {"id": 44})()
        response = client.post("/webhook", content=body, headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"message": "audit queued", "job_id": 44}
    fetch_commit_pair_diff.assert_called_once_with("doria90/dummyAI", "base123", "head456", "installation-token")
    fetch_pr_diff.assert_not_called()
    create_job.assert_called_once()


def test_webhook_ignores_pr_when_workspace_settings_disable_comments(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "webhook-comments-disabled.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_workspace,
        get_workspace_by_id,
        update_workspace_pr_comments_setting,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1400",
        github_login="settings-webhook-owner",
        display_name="Settings Webhook Owner",
        primary_email="settings-webhook@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="settings-webhook-workspace",
        display_name="Settings Webhook Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "standard",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=123,
        account_id="123",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=123,
        repo_github_id="dummyAI",
        repo_full="doria90/dummyAI",
        baseline_mode="default_branch",
        activated_by_user_id=user.id,
    )
    update_workspace_pr_comments_setting(main.AUDIT_DB_PATH, workspace.id, enabled=False)
    assert get_workspace_by_id(main.AUDIT_DB_PATH, workspace.id).pr_comments_setting_enabled is False

    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 7,
            "base": {"sha": "base-opened"},
            "head": {"sha": "abc123"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }

    response = client.post("/webhook", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json()["message"] == "ignored: PR comments disabled in settings"

    main.AUDIT_DB_PATH = original_db_path


def test_webhook_retries_diff_fetch_after_transient_404():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {"number": 8, "head": {"sha": "def456"}},
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
    }

    transient_404 = HTTPError(
        url="https://api.github.com/repos/doria90/dummyAI/pulls/8",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,
    )

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch(
        "main.fetch_pr_diff", side_effect=[transient_404, "diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n"]
    ) as fetch_diff, patch("main.create_audit_job") as create_job, patch("main.PR_DIFF_FETCH_RETRY_SECONDS", 0):
        create_job.return_value = type("Job", (), {"id": 43})()
        response = client.post("/webhook", content=body, headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"message": "audit queued", "job_id": 43}
    assert fetch_diff.call_count == 2
    create_job.assert_called_once()


def test_webhook_retries_commit_pair_diff_after_transient_github_404():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "synchronize",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 10,
            "base": {"sha": "base789"},
            "head": {"sha": "head789"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
    }

    class FakeGithubException(Exception):
        def __init__(self, status: int, message: str):
            super().__init__(message)
            self.status = status

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch(
        "main.fetch_commit_pair_diff",
        side_effect=[FakeGithubException(404, "Not Found"), "diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n"],
    ) as fetch_commit_pair_diff, patch("main.GithubException", FakeGithubException), patch("main.create_audit_job") as create_job, patch("main.PR_DIFF_FETCH_RETRY_SECONDS", 0):
        create_job.return_value = type("Job", (), {"id": 45})()
        response = client.post("/webhook", content=body, headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"message": "audit queued", "job_id": 45}
    assert fetch_commit_pair_diff.call_count == 2
    create_job.assert_called_once()


def test_webhook_push_to_default_branch_queues_branch_scan_job():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "ref": "refs/heads/main",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI", "default_branch": "main"},
        "head_commit": {"id": "pushsha123"},
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "push",
    }

    with patch("main.get_latest_repository_onboarding", return_value=type("Onboarding", (), {"id": 1})()), patch(
        "main.create_branch_scan_job"
    ) as create_job:
        create_job.return_value = type("BranchScanJob", (), {"id": 77})()
        response = client.post("/webhook", content=body, headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"message": "branch scan queued", "job_id": 77}
    create_job.assert_called_once_with(
        main.AUDIT_DB_PATH,
        repo_full="doria90/dummyAI",
        installation_id=123,
        commit_sha="pushsha123",
        branch_ref="refs/heads/main",
        triggered_by="push_webhook",
    )


def test_webhook_push_ignores_non_default_branch():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "ref": "refs/heads/feature/demo",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI", "default_branch": "main"},
        "head_commit": {"id": "pushsha123"},
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "push",
    }

    with patch("main.create_branch_scan_job") as create_job:
        response = client.post("/webhook", content=body, headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"message": "ignored"}
    create_job.assert_not_called()


def test_webhook_push_is_idempotent_for_same_commit(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "push-idempotent.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.onboarding_records import record_repository_onboarding, DiscoveredArtifactInput

    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="doria90/dummyAI",
        installation_id=123,
        default_branch="main",
        status="completed",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/refund.txt",
                artifact_type="prompt",
                discovery_reason="seeded for push idempotency test",
                confidence=1.0,
                baseline_content="You are a safe assistant.",
            )
        ],
        extract_signal_terms_fn=lambda text: [],
        build_profile_fn=build_attribute_profile,
    )

    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "ref": "refs/heads/main",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI", "default_branch": "main"},
        "head_commit": {"id": "pushsha123"},
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
    }

    try:
        first = client.post("/webhook", content=body, headers=headers)
        second = client.post("/webhook", content=body, headers=headers)
    finally:
        main.AUDIT_DB_PATH = original_db_path

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["message"] == "branch scan queued"
    assert second.json()["message"] == "branch scan queued"
    assert first.json()["job_id"] == second.json()["job_id"]


def test_webhook_ignores_unallocated_repo_for_managed_installation(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "managed-installation-webhook.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_workspace, upsert_entitlement, upsert_github_identity, upsert_github_installation

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1300",
        github_login="managed-webhook-owner",
        display_name="Managed Webhook Owner",
        primary_email="managed-webhook@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="managed-webhook-workspace",
        display_name="Managed Webhook Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "standard",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=123,
        account_id="123",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )

    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 7,
            "base": {"sha": "base-opened"},
            "head": {"sha": "abc123"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
    }

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_pr_diff") as fetch_pr_diff, patch(
        "main.fetch_commit_pair_diff"
    ) as fetch_commit_pair_diff, patch("main.create_audit_job") as create_job:
        response = client.post("/webhook", content=body, headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"message": "ignored: repo not allocated"}
    fetch_pr_diff.assert_not_called()
    fetch_commit_pair_diff.assert_not_called()
    create_job.assert_not_called()

    main.AUDIT_DB_PATH = original_db_path


# additional tests could mock github/openai but for MVP keep simple
