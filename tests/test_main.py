import os
import sys
import hmac
import hashlib
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

# make sure the package root is on sys.path so `import main` works
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient
from engine.analysis import analyze_diff
from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile

import main
from services.audit_jobs import create_audit_job
from services.audit_records import list_audit_feedback_events_for_audit, list_pull_request_audits_for_repo, record_audit_result
from services.control_plane_records import allocate_repo_to_workspace, create_workspace, get_repo_allocation_for_workspace, get_repo_connection_for_workspace, upsert_github_identity, upsert_github_installation
from services.onboarding_records import DiscoveredArtifactInput, record_repository_onboarding

client = TestClient(main.app)


def sign_payload(payload: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), payload, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _fake_llm_client(payload: dict[str, object]):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
                )
            )
        )
    )


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


def test_feedback_form_persists_explicit_feedback_event(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "feedback-form.db")
    main.init_db(main.AUDIT_DB_PATH)

    try:
        job = create_audit_job(
            main.AUDIT_DB_PATH,
            repo_full="doria90/dummyAI",
            pr_number=77,
            installation_id=123,
            head_sha="sha-feedback",
            diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
        )
        audit = record_audit_result(
            main.AUDIT_DB_PATH,
            job_id=job.id,
            repo_full="doria90/dummyAI",
            pr_number=77,
            installation_id=123,
            head_sha="sha-feedback",
            deterministic_analysis=analyze_diff(job.diff_text),
            status="completed",
            completion_mode="completed",
            output_mode="formal_review",
            comment_body="Vipari review body",
            comment_mode="review_request_changes",
            semantic_review_completed=True,
        )

        get_response = client.get("/feedback/pr/doria90/dummyAI/77?head_sha=sha-feedback")
        assert get_response.status_code == 200
        assert "Vipari review feedback" in get_response.text

        post_response = client.post(
            "/feedback/pr/doria90/dummyAI/77",
            data={
                "audit_id": str(audit.id),
                "head_sha": "sha-feedback",
                "sentiment": "helpful",
                "notes": "useful callout",
            },
        )
        assert post_response.status_code == 200
        assert "Thanks. Your feedback was recorded." in post_response.text

        feedback_events = list_audit_feedback_events_for_audit(main.AUDIT_DB_PATH, audit.id)
        assert len(feedback_events) == 1
        payload = json.loads(feedback_events[0].payload_json)
        assert feedback_events[0].kind == "explicit_feedback"
        assert feedback_events[0].source == "feedback_link"
        assert payload["sentiment"] == "helpful"
        assert payload["notes"] == "useful callout"
    finally:
        main.AUDIT_DB_PATH = original_db_path


def test_feedback_form_persists_explicit_feedback_event_without_head_sha(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "feedback-form-no-head-sha.db")
    main.init_db(main.AUDIT_DB_PATH)

    try:
        job = create_audit_job(
            main.AUDIT_DB_PATH,
            repo_full="doria90/dummyAI",
            pr_number=177,
            installation_id=123,
            head_sha="sha-feedback-legacy",
            diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
        )
        audit = record_audit_result(
            main.AUDIT_DB_PATH,
            job_id=job.id,
            repo_full="doria90/dummyAI",
            pr_number=177,
            installation_id=123,
            head_sha="sha-feedback-legacy",
            deterministic_analysis=analyze_diff(job.diff_text),
            status="completed",
            completion_mode="completed",
            output_mode="formal_review",
            comment_body="Vipari review body",
            comment_mode="review_request_changes",
            semantic_review_completed=True,
        )

        get_response = client.get("/feedback/pr/doria90/dummyAI/177")
        assert get_response.status_code == 200
        assert "Vipari review feedback" in get_response.text

        post_response = client.post(
            "/feedback/pr/doria90/dummyAI/177",
            data={
                "audit_id": str(audit.id),
                "sentiment": "helpful",
                "notes": "legacy link still works",
            },
        )
        assert post_response.status_code == 200
        assert "Thanks. Your feedback was recorded." in post_response.text

        feedback_events = list_audit_feedback_events_for_audit(main.AUDIT_DB_PATH, audit.id)
        assert len(feedback_events) == 1
        payload = json.loads(feedback_events[0].payload_json)
        assert payload["notes"] == "legacy link still works"
    finally:
        main.AUDIT_DB_PATH = original_db_path


def test_feedback_form_rejects_audit_id_for_different_repo(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "feedback-form-cross-repo.db")
    main.init_db(main.AUDIT_DB_PATH)

    try:
        job = create_audit_job(
            main.AUDIT_DB_PATH,
            repo_full="other-owner/other-repo",
            pr_number=78,
            installation_id=123,
            head_sha="sha-other",
            diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
        )
        audit = record_audit_result(
            main.AUDIT_DB_PATH,
            job_id=job.id,
            repo_full="other-owner/other-repo",
            pr_number=78,
            installation_id=123,
            head_sha="sha-other",
            deterministic_analysis=analyze_diff(job.diff_text),
            status="completed",
            completion_mode="completed",
            output_mode="formal_review",
            comment_body="Vipari review body",
            comment_mode="review_request_changes",
            semantic_review_completed=True,
        )

        get_response = client.get(f"/feedback/pr/doria90/dummyAI/77?audit_id={audit.id}")
        assert get_response.status_code == 404
    finally:
        main.AUDIT_DB_PATH = original_db_path


def test_feedback_form_rejects_notes_above_bound(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "feedback-form-notes-bound.db")
    main.init_db(main.AUDIT_DB_PATH)

    try:
        job = create_audit_job(
            main.AUDIT_DB_PATH,
            repo_full="doria90/dummyAI",
            pr_number=79,
            installation_id=123,
            head_sha="sha-feedback-bound",
            diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
        )
        audit = record_audit_result(
            main.AUDIT_DB_PATH,
            job_id=job.id,
            repo_full="doria90/dummyAI",
            pr_number=79,
            installation_id=123,
            head_sha="sha-feedback-bound",
            deterministic_analysis=analyze_diff(job.diff_text),
            status="completed",
            completion_mode="completed",
            output_mode="formal_review",
            comment_body="Vipari review body",
            comment_mode="review_request_changes",
            semantic_review_completed=True,
        )

        post_response = client.post(
            "/feedback/pr/doria90/dummyAI/79",
            data={
                "audit_id": str(audit.id),
                "head_sha": "sha-feedback-bound",
                "sentiment": "helpful",
                "notes": "x" * 2001,
            },
        )
        assert post_response.status_code == 400
        assert post_response.json()["detail"] == "Feedback notes must be 2000 characters or fewer."
    finally:
        main.AUDIT_DB_PATH = original_db_path


def test_webhook_marks_installation_inactive_on_delete(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "webhook-install-delete.db")
    main.init_db(main.AUDIT_DB_PATH)
    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="321",
        github_login="install-delete-owner",
        display_name="Install Delete Owner",
        primary_email="install-delete-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="install-delete-workspace",
        display_name="Install Delete Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=321,
        account_id="321",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "deleted",
        "installation": {
            "id": 321,
            "account": {"id": 321, "login": "doria90", "type": "Organization"},
        },
        "target_type": "Organization",
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "installation",
        "Content-Type": "application/json",
    }

    try:
        response = client.post("/webhook", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"message": "installation status updated", "status": "inactive"}
        installation = main.get_github_installation_by_installation_id(main.AUDIT_DB_PATH, 321)
        assert installation is not None
        assert installation.status == "inactive"
    finally:
        main.AUDIT_DB_PATH = original_db_path


def test_webhook_marks_removed_installation_repository_inactive(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "webhook-installation-repositories.db")
    main.init_db(main.AUDIT_DB_PATH)
    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="654",
        github_login="repo-grant-owner",
        display_name="Repo Grant Owner",
        primary_email="repo-grant-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="repo-grant-workspace",
        display_name="Repo Grant Workspace",
        billing_owner_user_id=user.id,
    )
    installation = upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=654,
        account_id="654",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    main.replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repositories=[
            {
                "repo_github_id": "1",
                "repo_full": "doria90/removed-repo",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
            {
                "repo_github_id": "2",
                "repo_full": "doria90/kept-repo",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repo_github_id="1",
        repo_full="doria90/removed-repo",
        baseline_mode="default_branch",
        activated_by_user_id=user.id,
    )
    main.update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "removed",
        "installation": {"id": 654},
        "repositories_added": [],
        "repositories_removed": [
            {
                "id": 1,
                "full_name": "doria90/removed-repo",
                "default_branch": "main",
                "private": True,
            }
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "installation_repositories",
        "Content-Type": "application/json",
    }

    try:
        response = client.post("/webhook", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == {
            "message": "installation repositories updated",
            "connected_repo_count": 1,
            "deactivated_allocation_count": 1,
        }
        assert get_repo_connection_for_workspace(main.AUDIT_DB_PATH, workspace.id, "doria90/removed-repo") is None
        assert get_repo_connection_for_workspace(main.AUDIT_DB_PATH, workspace.id, "doria90/kept-repo") is not None
        removed_allocation = get_repo_allocation_for_workspace(main.AUDIT_DB_PATH, workspace.id, "doria90/removed-repo")
        assert removed_allocation is not None
        assert removed_allocation.allocation_status == "inactive"
    finally:
        main.AUDIT_DB_PATH = original_db_path


def test_webhook_queues_relevant_audit_job():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 7,
            "title": "Tighten refund approval flow",
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
    assert create_job.call_args.kwargs["pr_title"] == "Tighten refund approval flow"


def test_webhook_prefers_commit_pair_diff_only_for_synchronize():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "synchronize",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 9,
            "title": "Refine policy guardrails",
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
    assert create_job.call_args.kwargs["pr_title"] == "Refine policy guardrails"


def test_webhook_runs_micro_classifier_for_uncertain_diff_and_persists_decision(tmp_path):
    from services.audit_records import list_pre_audit_relevance_decisions

    original_db_path = main.AUDIT_DB_PATH
    original_client = main.client
    main.AUDIT_DB_PATH = str(tmp_path / "webhook-uncertain.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.client = _fake_llm_client({"is_relevant": True, "reason": "Routes assistant behavior for AI requests."})
    main.GITHUB_WEBHOOK_SECRET = "secret"

    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 17,
            "base": {"sha": "base-opened"},
            "head": {"sha": "uncertain-head"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
    }

    try:
        with patch("main.generate_jwt", return_value="jwt-token"), patch(
            "main.get_installation_token", return_value="installation-token"
        ), patch(
            "main.fetch_pr_diff",
            return_value="diff --git a/src/assistant_router.py b/src/assistant_router.py\nindex 1..2\n+route update\n",
        ), patch("main.fetch_commit_pair_diff") as fetch_commit_pair_diff, patch("main.create_audit_job") as create_job:
            fetch_commit_pair_diff.return_value = ""
            create_job.return_value = type("Job", (), {"id": 77})()
            response = client.post("/webhook", content=body, headers={**headers, "Content-Type": "application/json"})

        assert response.status_code == 200
        assert response.json() == {"message": "audit queued", "job_id": 77}
        create_job.assert_called_once()
        decisions = list_pre_audit_relevance_decisions(
            main.AUDIT_DB_PATH,
            repo_full="doria90/dummyAI",
            pr_number=17,
            head_sha="uncertain-head",
        )
        assert len(decisions) == 1
        assert decisions[0].artifact_path == "src/assistant_router.py"
        assert decisions[0].confidence_tier == "uncertain"
        assert decisions[0].classifier_is_relevant is True
    finally:
        main.AUDIT_DB_PATH = original_db_path
        main.client = original_client


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


def test_webhook_merged_pull_request_queues_branch_scan_job():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "closed",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 11,
            "title": "Merge refund authority updates",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-05-14T12:00:00Z",
            "merge_commit_sha": "mergesha123",
            "base": {"sha": "base123", "ref": "main"},
            "head": {"sha": "head123"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }

    with patch("main.update_job_pr_state") as update_job_pr_state, patch(
        "main.update_pull_request_audit_state"
    ) as update_pull_request_audit_state, patch(
        "main.get_latest_repository_onboarding",
        return_value=type("Onboarding", (), {"id": 1, "default_branch": "main"})(),
    ), patch("main.create_branch_scan_job") as create_branch_scan_job:
        create_branch_scan_job.return_value = type("BranchScanJob", (), {"id": 91})()
        response = client.post("/webhook", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"message": "pr state updated", "branch_scan_job_id": 91}
    update_job_pr_state.assert_called_once()
    update_pull_request_audit_state.assert_called_once()
    assert update_job_pr_state.call_args.kwargs["pr_title"] == "Merge refund authority updates"
    assert update_pull_request_audit_state.call_args.kwargs["pr_title"] == "Merge refund authority updates"
    create_branch_scan_job.assert_called_once_with(
        main.AUDIT_DB_PATH,
        repo_full="doria90/dummyAI",
        installation_id=123,
        commit_sha="mergesha123",
        branch_ref="refs/heads/main",
        triggered_by="pr_merged_webhook",
    )


def test_webhook_closed_pull_request_records_pr_outcome_feedback_event(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "webhook-pr-outcome.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.GITHUB_WEBHOOK_SECRET = "secret"

    try:
        job = create_audit_job(
            main.AUDIT_DB_PATH,
            repo_full="doria90/dummyAI",
            pr_number=78,
            installation_id=123,
            head_sha="head-outcome",
            diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
        )
        audit = record_audit_result(
            main.AUDIT_DB_PATH,
            job_id=job.id,
            repo_full="doria90/dummyAI",
            pr_number=78,
            installation_id=123,
            head_sha="head-outcome",
            deterministic_analysis=analyze_diff(job.diff_text),
            status="completed",
            completion_mode="completed",
            output_mode="formal_review",
            comment_body="Vipari review body",
            comment_mode="review_request_changes",
            semantic_review_completed=True,
        )

        payload = {
            "action": "closed",
            "installation": {"id": 123},
            "repository": {"full_name": "doria90/dummyAI"},
            "pull_request": {
                "number": 78,
                "state": "closed",
                "merged": True,
                "merged_at": "2026-05-14T12:00:00Z",
                "merge_commit_sha": "mergesha789",
                "base": {"sha": "base789", "ref": "main"},
                "head": {"sha": "head-outcome"},
            },
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "X-Hub-Signature-256": sign_payload(body, "secret"),
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        }

        with patch(
            "main.get_latest_repository_onboarding",
            return_value=type("Onboarding", (), {"id": 1, "default_branch": "main"})(),
        ), patch("main.create_branch_scan_job") as create_branch_scan_job, patch(
            "main.generate_jwt",
            return_value="jwt",
        ), patch(
            "main.get_installation_token",
            return_value="token",
        ), patch(
            "main.refresh_audit_reaction_feedback_for_pr"
        ) as refresh_audit_reaction_feedback_for_pr:
            create_branch_scan_job.return_value = type("BranchScanJob", (), {"id": 92})()
            response = client.post("/webhook", content=body, headers=headers)

        assert response.status_code == 200
        refresh_audit_reaction_feedback_for_pr.assert_called_once()
        feedback_events = list_audit_feedback_events_for_audit(main.AUDIT_DB_PATH, audit.id)
        assert len(feedback_events) == 1
        payload_json = json.loads(feedback_events[0].payload_json)
        assert payload_json["outcome"] == "recommendation_ignored"
    finally:
        main.AUDIT_DB_PATH = original_db_path


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


def test_webhook_queues_branch_scan_for_onboarded_unallocated_push_repo(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "managed-push-onboarded.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_workspace, upsert_entitlement, upsert_github_identity, upsert_github_installation

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1400",
        github_login="managed-push-owner",
        display_name="Managed Push Owner",
        primary_email="managed-push@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="managed-push-workspace",
        display_name="Managed Push Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": False,
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
        installation_id=321,
        account_id="321",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="doria90/dummyAI",
        installation_id=321,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/policy.md",
                artifact_type="prompt",
                discovery_reason="seed",
                confidence=0.9,
                baseline_content="baseline policy",
            )
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )

    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "installation": {"id": 321},
        "repository": {"full_name": "doria90/dummyAI", "default_branch": "main"},
        "ref": "refs/heads/main",
        "head_commit": {"id": "pushsha321"},
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
    }

    try:
        response = client.post("/webhook", content=body, headers=headers)
    finally:
        main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert response.json()["message"] == "branch scan queued"


def test_webhook_queues_branch_scan_for_onboarded_unallocated_merged_pr(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "managed-merged-onboarded.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_workspace, upsert_entitlement, upsert_github_identity, upsert_github_installation

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1500",
        github_login="managed-merge-owner",
        display_name="Managed Merge Owner",
        primary_email="managed-merge@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="managed-merge-workspace",
        display_name="Managed Merge Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": False,
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
        installation_id=654,
        account_id="654",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="doria90/dummyAI",
        installation_id=654,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/policy.md",
                artifact_type="prompt",
                discovery_reason="seed",
                confidence=0.9,
                baseline_content="baseline policy",
            )
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )

    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "closed",
        "installation": {"id": 654},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {
            "number": 9,
            "title": "Merge tracked repo update",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-05-17T10:00:00Z",
            "merge_commit_sha": "merged654sha",
            "base": {"sha": "base654", "ref": "main"},
            "head": {"sha": "head654"},
            "updated_at": "2026-05-17T10:00:00Z",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }

    try:
        with patch("main.generate_jwt", return_value="jwt"), patch(
            "main.get_installation_token", return_value="token"
        ), patch("main.refresh_audit_reaction_feedback_for_pr"):
            response = client.post("/webhook", content=body, headers=headers)
        audits = list_pull_request_audits_for_repo(main.AUDIT_DB_PATH, "doria90/dummyAI")
    finally:
        main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert response.json()["message"] == "pr state updated"
    assert response.json().get("branch_scan_job_id")
    assert len(audits) == 1
    assert audits[0].output_mode == "lifecycle_tracking"
    assert audits[0].pr_merged is True


# additional tests could mock github/openai but for MVP keep simple
