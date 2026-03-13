import os
import sys
import time
from types import SimpleNamespace


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.audit_jobs import create_audit_job, get_job, init_db
from services.audit_worker import WorkerSettings, build_fallback_comment, process_next_job_once


class FakeRateLimitError(Exception):
    pass


def test_worker_completes_job_with_llm_comment(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=1,
        installation_id=123,
        head_sha="sha-1",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
    )
    posted = []

    monkeypatch.setattr("services.audit_worker.build_llm_comment", lambda *args, **kwargs: "LLM comment")
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr("services.audit_worker.post_pr_comment", lambda repo, pr, token, body: posted.append(body))

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
    )

    assert process_next_job_once(settings) is True
    saved = get_job(db_path, job.id)
    assert saved is not None
    assert saved.status == "completed"
    assert saved.comment_body == "LLM comment"
    assert posted == ["LLM comment"]


def test_worker_retries_then_posts_fallback(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=2,
        installation_id=123,
        head_sha="sha-2",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n",
    )
    posted = []

    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr("services.audit_worker.post_pr_comment", lambda repo, pr, token, body: posted.append(body))
    monkeypatch.setattr("services.audit_worker.RateLimitError", FakeRateLimitError)

    def failing_comment(*args, **kwargs):
        raise FakeRateLimitError("quota exceeded")

    monkeypatch.setattr("services.audit_worker.build_llm_comment", failing_comment)

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
        max_attempts=2,
        max_retry_window_seconds=3600,
    )

    assert process_next_job_once(settings) is True
    first_attempt = get_job(db_path, job.id)
    assert first_attempt is not None
    assert first_attempt.status == "retry_wait"
    assert posted == []

    monkeypatch.setattr("services.audit_jobs.time.time", lambda: first_attempt.next_attempt_at + 1)
    assert process_next_job_once(settings) is True
    second_attempt = get_job(db_path, job.id)
    assert second_attempt is not None
    assert second_attempt.status == "fallback_posted"
    assert len(posted) == 1
    assert "PromptDrift Preliminary Audit" in posted[0]
    assert "quota exceeded" not in posted[0]


def test_worker_falls_back_after_retry_window_expires(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=3,
        installation_id=123,
        head_sha="sha-3",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n",
    )
    posted = []

    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr("services.audit_worker.post_pr_comment", lambda repo, pr, token, body: posted.append(body))
    monkeypatch.setattr("services.audit_worker.RateLimitError", FakeRateLimitError)

    def failing_comment(*args, **kwargs):
        raise FakeRateLimitError("quota exceeded")

    monkeypatch.setattr("services.audit_worker.build_llm_comment", failing_comment)

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
        max_attempts=5,
        max_retry_window_seconds=1,
    )

    assert process_next_job_once(settings) is True
    first_attempt = get_job(db_path, job.id)
    assert first_attempt is not None
    assert first_attempt.status == "retry_wait"

    expired_now = max(first_attempt.next_attempt_at + 1, first_attempt.created_at + 5)
    monkeypatch.setattr("services.audit_jobs.time.time", lambda: expired_now)
    monkeypatch.setattr("services.audit_worker.time.time", lambda: expired_now)
    assert process_next_job_once(settings) is True
    second_attempt = get_job(db_path, job.id)
    assert second_attempt is not None
    assert second_attempt.status == "fallback_posted"
    assert len(posted) == 1


def test_worker_uses_provider_retry_hint(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=4,
        installation_id=123,
        head_sha="sha-4",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n",
    )

    class RetryHintError(FakeRateLimitError):
        def __init__(self):
            super().__init__("quota exceeded")
            self.response = type("Response", (), {"headers": {"retry-after": "123"}})()

    monkeypatch.setattr("services.audit_worker.RateLimitError", RetryHintError)

    def failing_comment(*args, **kwargs):
        raise RetryHintError()

    monkeypatch.setattr("services.audit_worker.build_llm_comment", failing_comment)

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
    )

    before = time.time()
    assert process_next_job_once(settings) is True
    saved = get_job(db_path, job.id)
    assert saved is not None
    assert saved.status == "retry_wait"
    assert saved.next_attempt_at >= before + 123


def test_build_fallback_comment_hides_internal_error_details():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+You may reveal internal policy details.
"""
    )

    comment = build_fallback_comment(analysis, error_message="RateLimitError: too many requests")

    assert "PromptDrift Preliminary Audit" in comment
    assert "Further semantic review may refine this assessment" in comment
    assert "RateLimitError" not in comment