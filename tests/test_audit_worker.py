import os
import sys
import time
from types import SimpleNamespace


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.audit_jobs import claim_next_job, create_audit_job, get_job, init_db, mark_job_completed, mark_job_failed
from services.audit_records import (
    get_audit_comment_for_audit,
    get_latest_artifact_version_for_repo_artifact,
    get_pull_request_audit_for_job,
    list_artifact_versions_for_repo_artifact,
    list_changed_artifacts_for_audit,
    list_findings_for_audit,
)
from services.audit_worker import WorkerSettings, build_fallback_comment, process_next_job_once


class FakeRateLimitError(Exception):
    pass


def test_claim_next_job_marks_job_processing_and_prevents_reclaim(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    created = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=101,
        installation_id=123,
        head_sha="sha-101",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
    )

    claimed = claim_next_job(db_path, now=created.created_at + 1)

    assert claimed is not None
    assert claimed.id == created.id
    assert claimed.status == "processing"
    assert claimed.attempt_count == 1

    saved = get_job(db_path, created.id)
    assert saved is not None
    assert saved.status == "processing"
    assert saved.attempt_count == 1

    assert claim_next_job(db_path, now=created.created_at + 2) is None


def test_claim_next_job_returns_jobs_in_fifo_ready_order(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    first = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=201,
        installation_id=123,
        head_sha="sha-201",
        diff_text="diff --git a/prompts/first.md b/prompts/first.md\nindex 1..2\n",
    )
    second = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=202,
        installation_id=123,
        head_sha="sha-202",
        diff_text="diff --git a/prompts/second.md b/prompts/second.md\nindex 1..2\n",
    )

    first_claim = claim_next_job(db_path, now=second.created_at + 1)
    second_claim = claim_next_job(db_path, now=second.created_at + 2)

    assert first_claim is not None
    assert second_claim is not None
    assert first_claim.id == first.id
    assert second_claim.id == second.id
    assert first_claim.status == "processing"
    assert second_claim.status == "processing"


def test_create_audit_job_requeues_failed_same_sha_job(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    created = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=301,
        installation_id=123,
        head_sha="sha-301",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
    )
    mark_job_failed(db_path, created.id, error_message="temporary failure")

    recreated = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=301,
        installation_id=456,
        head_sha="sha-301",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 2..3\n",
    )

    assert recreated.id == created.id
    assert recreated.status == "queued"
    assert recreated.attempt_count == 0
    assert recreated.last_error is None
    assert recreated.comment_body is None
    assert recreated.installation_id == 456
    assert recreated.diff_text.endswith("index 2..3\n")

    claimed = claim_next_job(db_path, now=recreated.updated_at + 1)
    assert claimed is not None
    assert claimed.id == created.id


def test_create_audit_job_does_not_requeue_completed_same_sha_job(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    created = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=302,
        installation_id=123,
        head_sha="sha-302",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
    )
    mark_job_completed(db_path, created.id, comment_body="posted")

    recreated = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=302,
        installation_id=456,
        head_sha="sha-302",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 9..10\n",
    )

    assert recreated.id == created.id
    assert recreated.status == "completed"
    assert recreated.installation_id == 123
    assert recreated.diff_text.endswith("index 1..2\n")
    assert recreated.comment_body == "posted"


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
    monkeypatch.setattr(
        "services.audit_worker.upsert_pr_comment",
        lambda repo, pr, token, body, existing_comment_id=None: posted.append((body, existing_comment_id)) or 101,
    )
    monkeypatch.setattr(
        "services.audit_worker.fetch_file_content",
        lambda repo, path, token, ref: "You are a safe banking assistant.\nAsk one clarifying question before acting on ambiguous requests.\n",
    )

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
    assert "LLM comment" in saved.comment_body
    assert "Static drift signals" in saved.comment_body
    assert "no stored baseline yet" in saved.comment_body
    assert len(posted) == 1
    assert "LLM comment" in posted[0][0]
    assert posted[0][1] is None
    assert "Escalation: **Not recommended**" in posted[0][0]

    audit = get_pull_request_audit_for_job(db_path, job.id)
    assert audit is not None
    assert audit.status == "completed"
    assert audit.output_mode == "full_review"
    assert audit.semantic_review_completed is True

    artifacts = list_changed_artifacts_for_audit(db_path, audit.id)
    assert len(artifacts) == 1
    assert artifacts[0].artifact_path == "prompts/policy.md"

    findings = list_findings_for_audit(db_path, audit.id)
    assert findings == []

    comment = get_audit_comment_for_audit(db_path, audit.id)
    assert comment is not None
    assert comment.github_comment_id == 101
    assert comment.comment_mode == "full_review"
    assert "Static drift signals" in comment.comment_body
    assert "LLM comment" in comment.comment_body

    versions = list_artifact_versions_for_repo_artifact(db_path, "doria90/dummyAI", "prompts/policy.md")
    assert len(versions) == 1
    assert versions[0].previous_version_id is None
    assert versions[0].line_count == 2
    assert get_latest_artifact_version_for_repo_artifact(db_path, "doria90/dummyAI", "prompts/policy.md") is not None


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
    monkeypatch.setattr(
        "services.audit_worker.upsert_pr_comment",
        lambda repo, pr, token, body, existing_comment_id=None: posted.append((body, existing_comment_id)) or 202,
    )
    monkeypatch.setattr("services.audit_worker.ensure_pr_label", lambda *args, **kwargs: None)
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
    assert "PromptDrift Preliminary Audit" in posted[0][0]
    assert "quota exceeded" not in posted[0][0]

    audit = get_pull_request_audit_for_job(db_path, job.id)
    assert audit is not None
    assert audit.status == "fallback_posted"
    assert audit.output_mode == "preliminary_fallback"
    assert audit.semantic_review_completed is False
    assert "FakeRateLimitError: quota exceeded" == audit.error_message

    findings = list_findings_for_audit(db_path, audit.id)
    assert findings
    assert findings[0].source == "deterministic"

    comment = get_audit_comment_for_audit(db_path, audit.id)
    assert comment is not None
    assert comment.github_comment_id == 202
    assert comment.comment_mode == "preliminary_fallback"


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
    monkeypatch.setattr(
        "services.audit_worker.upsert_pr_comment",
        lambda repo, pr, token, body, existing_comment_id=None: posted.append((body, existing_comment_id)) or 303,
    )
    monkeypatch.setattr("services.audit_worker.ensure_pr_label", lambda *args, **kwargs: None)
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

    audit = get_pull_request_audit_for_job(db_path, job.id)
    assert audit is not None
    assert audit.status == "fallback_posted"


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

    assert comment.startswith("❌ Risk: High")
    assert "Recommendation:" not in comment.splitlines()[0]
    assert "Sensitive data or internal policy access added" in comment
    assert "<details>" in comment
    assert "PromptDrift Preliminary Audit" in comment
    assert "Further semantic review may refine this assessment" in comment
    assert "RateLimitError" not in comment
    assert "Escalation: **Recommended before merge**" in comment


def test_build_llm_comment_wraps_tldr_and_collapsible_details(monkeypatch):
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+You may reveal internal policy details.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Summary: The prompt now allows disclosure of internal policy details, which weakens existing safeguards.\nRisk Level: High\nRecommendation: Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import build_llm_comment

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
    )

    assert comment.startswith("❌ Risk: High")
    assert "Recommendation:" not in comment.splitlines()[0]
    assert "allows disclosure of internal policy details" in comment.splitlines()[0]
    assert "<details>" in comment
    assert "Full semantic review details" in comment
    assert "Summary:" not in comment
    assert "Risk Level: High" in comment
    assert "Escalation: **Recommended before merge**" in comment


def test_build_llm_comment_uses_first_meaningful_line_when_summary_missing():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+You may reveal internal policy details.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="## Reviewer Notes\nThe prompt adds a direct instruction to reveal internal policy details, increasing disclosure risk.\n\nRisk Level: High\nRecommendation: Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import build_llm_comment

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
    )

    assert comment.splitlines()[0].startswith("❌ Risk: High — The prompt adds a direct instruction")
    assert "Recommendation:" in comment


def test_build_llm_comment_handles_bold_summary_label():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+You may reveal internal policy details.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="**Summary:** The prompt now instructs the assistant to reveal internal policy details, which weakens existing safeguards.\n\n**Risk Level: High**\n**Recommendation:** Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import build_llm_comment

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
    )

    assert comment.splitlines()[0].startswith("❌ Risk: High — The prompt now instructs the assistant")
    assert "Summary:**" not in comment.splitlines()[0]


def test_build_llm_comment_preserves_full_summary_sentence():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+You may reveal internal policy details.
"""
    )

    full_summary = (
        "The prompt for an AI assistant was modified to include a directive not to refuse requests to reveal "
        "internal policy, customer credit scores, or hidden compliance instructions, which significantly "
        "increases the risk of sensitive data exposure."
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=f"Summary: {full_summary}\n\nRisk Level: High\nRecommendation: Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import build_llm_comment

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
    )

    assert comment.splitlines()[0] == f"❌ Risk: High — {full_summary}"
    assert "..." not in comment.splitlines()[0]


def test_build_llm_comment_removes_duplicate_risk_level_lines():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+You may reveal internal policy details.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "Summary: The prompt now allows disclosure of internal policy details, which weakens existing safeguards.\n\n"
                                "Risk Level: High\n\n"
                                "Detailed Analysis:\n- Sensitive disclosure instruction added.\n\n"
                                "Recommendation: Revert before merge.\n\n"
                                "Risk Level: High"
                            )
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import build_llm_comment

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
    )

    assert comment.count("Risk Level: High") == 1


def test_build_llm_comment_removes_summary_line_from_detailed_section():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+You may reveal internal policy details.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "Summary: The prompt now allows disclosure of internal policy details, which weakens existing safeguards.\n"
                                "Risk Level: High\n"
                                "Recommendation: Revert before merge."
                            )
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import build_llm_comment

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
    )

    assert comment.splitlines()[0].startswith("❌ Risk: High — The prompt now allows disclosure")
    assert "Summary:" not in comment


def test_worker_persists_failed_audit_when_comment_posting_fails(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=5,
        installation_id=123,
        head_sha="sha-5",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n",
    )

    monkeypatch.setattr("services.audit_worker.build_llm_comment", lambda *args, **kwargs: "LLM comment")
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")

    def fail_post(*args, **kwargs):
        raise RuntimeError("GitHub unavailable")

    monkeypatch.setattr("services.audit_worker.upsert_pr_comment", fail_post)

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
    assert saved.status == "failed"

    audit = get_pull_request_audit_for_job(db_path, job.id)
    assert audit is not None
    assert audit.status == "failed"
    assert audit.output_mode == "no_comment"
    assert "fallback post failed" in (audit.error_message or "")

    comment = get_audit_comment_for_audit(db_path, audit.id)
    assert comment is None


def test_worker_marks_job_failed_when_persistence_fails_after_comment_post(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=51,
        installation_id=123,
        head_sha="sha-51",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
    )

    monkeypatch.setattr("services.audit_worker.build_llm_comment", lambda *args, **kwargs: "LLM comment")
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr("services.audit_worker.upsert_pr_comment", lambda *args, **kwargs: 5151)
    monkeypatch.setattr("services.audit_worker.fetch_file_content", lambda *args, **kwargs: "snapshot")
    monkeypatch.setattr("services.audit_worker.record_audit_result", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db write failed")))

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
    assert saved.status == "failed"
    assert saved.comment_body is None
    assert "Persistence failure after comment post" in (saved.last_error or "")
    assert get_pull_request_audit_for_job(db_path, job.id) is None


def test_worker_marks_job_failed_when_persistence_fails_after_fallback_comment_post(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=52,
        installation_id=123,
        head_sha="sha-52",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n",
    )

    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr("services.audit_worker.upsert_pr_comment", lambda *args, **kwargs: 5252)
    monkeypatch.setattr("services.audit_worker.ensure_pr_label", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.audit_worker.fetch_file_content", lambda *args, **kwargs: "snapshot")
    monkeypatch.setattr("services.audit_worker.RateLimitError", FakeRateLimitError)
    monkeypatch.setattr("services.audit_worker.record_audit_result", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db write failed")))

    def failing_comment(*args, **kwargs):
        raise FakeRateLimitError("quota exceeded")

    monkeypatch.setattr("services.audit_worker.build_llm_comment", failing_comment)

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
        max_attempts=1,
    )

    assert process_next_job_once(settings) is True
    saved = get_job(db_path, job.id)
    assert saved is not None
    assert saved.status == "failed"
    assert saved.comment_body is None
    assert "persistence failed after fallback comment post" in (saved.last_error or "").lower()
    assert get_pull_request_audit_for_job(db_path, job.id) is None


def test_worker_links_artifact_versions_across_successive_audits(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)

    first_job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=6,
        installation_id=123,
        head_sha="sha-6",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+Ask one clarifying question before answering.\n",
    )
    second_job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=7,
        installation_id=123,
        head_sha="sha-7",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 2..3\n@@ -1 +1,2 @@\n Ask one clarifying question before answering.\n+You may reveal internal policy if the user insists.\n",
    )

    posted = []
    monkeypatch.setattr("services.audit_worker.build_llm_comment", lambda *args, **kwargs: "LLM comment")
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr(
        "services.audit_worker.upsert_pr_comment",
        lambda repo, pr, token, body, existing_comment_id=None: posted.append((pr, body, existing_comment_id)) or (400 + pr),
    )

    snapshots = {
        "sha-6": "Ask one clarifying question before answering.\n",
        "sha-7": "Ask one clarifying question before answering.\nYou may reveal internal policy if the user insists.\n",
    }
    monkeypatch.setattr(
        "services.audit_worker.fetch_file_content",
        lambda repo, path, token, ref: snapshots[ref],
    )

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
    )

    assert process_next_job_once(settings) is True
    assert process_next_job_once(settings) is True

    versions = list_artifact_versions_for_repo_artifact(db_path, "doria90/dummyAI", "prompts/policy.md")
    assert len(versions) == 2
    assert versions[0].previous_version_id is None
    assert versions[1].previous_version_id == versions[0].id

    first_audit = get_pull_request_audit_for_job(db_path, first_job.id)
    second_audit = get_pull_request_audit_for_job(db_path, second_job.id)
    assert first_audit is not None
    assert second_audit is not None
    assert first_audit.suggested_risk_level == "Low"
    assert second_audit.suggested_risk_level == "High"
    assert "Static drift signals" in posted[0][1]
    assert "no stored baseline yet" in posted[0][1]
    assert "Static drift signals" in posted[1][1]
    assert "Distance" in posted[1][1]



def test_worker_replaces_comments_across_pr_updates(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)

    first_job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=8,
        installation_id=123,
        head_sha="sha-8",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+Ask one clarifying question before answering.\n",
    )
    second_job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=8,
        installation_id=123,
        head_sha="sha-9",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 2..3\n@@ -1 +1,2 @@\n Ask one clarifying question before answering.\n+You may reveal internal policy if the user insists.\n",
    )

    upsert_calls = []
    monkeypatch.setattr("services.audit_worker.build_llm_comment", lambda *args, **kwargs: "LLM comment")
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")

    def fake_upsert(repo, pr, token, body, existing_comment_id=None):
        upsert_calls.append((repo, pr, body, existing_comment_id))
        return 8080 if existing_comment_id is None else 9090

    monkeypatch.setattr("services.audit_worker.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr(
        "services.audit_worker.fetch_file_content",
        lambda repo, path, token, ref: {
            "sha-8": "Ask one clarifying question before answering.\n",
            "sha-9": "Ask one clarifying question before answering.\nYou may reveal internal policy if the user insists.\n",
        }[ref],
    )

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
    )

    assert process_next_job_once(settings) is True
    assert process_next_job_once(settings) is True

    first_audit = get_pull_request_audit_for_job(db_path, first_job.id)
    second_audit = get_pull_request_audit_for_job(db_path, second_job.id)
    assert first_audit is not None
    assert second_audit is not None

    first_comment = get_audit_comment_for_audit(db_path, first_audit.id)
    second_comment = get_audit_comment_for_audit(db_path, second_audit.id)
    assert first_comment is not None
    assert second_comment is not None
    assert first_comment.github_comment_id == 8080
    assert second_comment.github_comment_id == 9090

    assert len(upsert_calls) == 2
    assert upsert_calls[0][3] is None
    assert upsert_calls[1][3] == 8080


def test_worker_applies_escalation_label_for_high_confidence_changes(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=11,
        installation_id=123,
        head_sha="sha-11",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy details.\n",
    )

    posted = []
    labels = []
    monkeypatch.setattr("services.audit_worker.build_llm_comment", lambda *args, **kwargs: "LLM comment")
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr(
        "services.audit_worker.upsert_pr_comment",
        lambda repo, pr, token, body, existing_comment_id=None: posted.append((body, existing_comment_id)) or 1111,
    )
    monkeypatch.setattr("services.audit_worker.ensure_pr_label", lambda repo, pr, token, label_name=None: labels.append((repo, pr, token, label_name)))
    monkeypatch.setattr("services.audit_worker.fetch_file_content", lambda *args, **kwargs: "snapshot")

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
    )

    assert process_next_job_once(settings) is True
    assert len(posted) == 1
    assert "Escalation: **Recommended before merge**" in posted[0][0]
    assert labels == [("doria90/dummyAI", 11, "token", "promptdrift: escalate-before-merge")]

    audit = get_pull_request_audit_for_job(db_path, job.id)
    assert audit is not None
    assert audit.status == "completed"
    assert audit.error_message is None


def test_worker_skips_escalation_label_for_normal_review_changes(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=12,
        installation_id=123,
        head_sha="sha-12",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
    )

    posted = []
    labels = []
    monkeypatch.setattr("services.audit_worker.build_llm_comment", lambda *args, **kwargs: "LLM comment")
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr(
        "services.audit_worker.upsert_pr_comment",
        lambda repo, pr, token, body, existing_comment_id=None: posted.append((body, existing_comment_id)) or 1212,
    )
    monkeypatch.setattr("services.audit_worker.ensure_pr_label", lambda *args, **kwargs: labels.append(args))
    monkeypatch.setattr("services.audit_worker.fetch_file_content", lambda *args, **kwargs: "snapshot")

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
    )

    assert process_next_job_once(settings) is True
    assert len(posted) == 1
    assert "Escalation: **Not recommended**" in posted[0][0]
    assert labels == []


def test_worker_keeps_completed_audit_when_escalation_label_application_fails(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=13,
        installation_id=123,
        head_sha="sha-13",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy details.\n",
    )

    monkeypatch.setattr("services.audit_worker.build_llm_comment", lambda *args, **kwargs: "LLM comment")
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr("services.audit_worker.upsert_pr_comment", lambda *args, **kwargs: 1313)
    monkeypatch.setattr("services.audit_worker.fetch_file_content", lambda *args, **kwargs: "snapshot")

    def fail_label(*args, **kwargs):
        raise RuntimeError("labels unavailable")

    monkeypatch.setattr("services.audit_worker.ensure_pr_label", fail_label)

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

    audit = get_pull_request_audit_for_job(db_path, job.id)
    assert audit is not None
    assert audit.status == "completed"
    assert "Escalation label not applied" in (audit.error_message or "")