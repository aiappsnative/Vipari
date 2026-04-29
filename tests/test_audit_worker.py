import os
import sys
import time
from types import SimpleNamespace


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.audit_jobs import claim_next_job, create_audit_job, get_job, init_db, mark_job_completed, mark_job_failed
from services.audit_jobs import (
    claim_next_job,
    create_audit_job,
    get_job,
    init_db,
    mark_job_completed,
    mark_job_failed,
    update_job_pr_state,
)
from services.audit_records import (
    AuditCommentRecord,
    PrCommentEpisodeRecord,
    get_audit_comment_for_audit,
    get_latest_artifact_version_for_repo_artifact,
    get_pull_request_audit_for_job,
    list_artifact_versions_for_repo_artifact,
    list_changed_artifacts_for_audit,
    list_findings_for_audit,
    record_audit_result,
    update_pull_request_audit_state,
)
from services.onboarding import onboard_repository
from services.audit_worker import WorkerSettings, build_fallback_comment, process_next_job_once
from services.dashboard_views import ArtifactAttributeProfile, AttributeProfileDimension


class FakeRateLimitError(Exception):
    pass


def test_build_signal_fusion_assessment_escalates_on_medium_medium_agreement():
    from services.audit_worker import _build_signal_fusion_assessment

    deterministic_analysis = SimpleNamespace(
        suggested_risk_level=SimpleNamespace(value="Medium"),
        findings=[],
    )

    assessment = _build_signal_fusion_assessment(
        "Risk Level: Medium\nConfidence: High\nRecommendation: Review the changed AI control surface closely before merge.",
        deterministic_analysis,
    )

    assert assessment.risk_level == "High"
    assert assessment.confidence == "High"
    assert assessment.escalation_recommendation.decision == "normal_review"


def test_build_signal_fusion_assessment_bounds_semantic_only_high_without_merge_blocking_recommendation():
    from services.audit_worker import _build_signal_fusion_assessment

    deterministic_analysis = SimpleNamespace(
        suggested_risk_level=SimpleNamespace(value="Low"),
        findings=[],
    )

    assessment = _build_signal_fusion_assessment(
        "Risk Level: High\nConfidence: Medium\nRecommendation: Review the changed AI control surface closely before merge.",
        deterministic_analysis,
    )

    assert assessment.risk_level == "Medium"
    assert assessment.confidence == "Medium"
    assert assessment.escalation_recommendation.decision == "normal_review"


def test_build_signal_fusion_assessment_treats_low_confidence_semantic_high_as_advisory():
    from services.audit_worker import _build_signal_fusion_assessment

    deterministic_analysis = SimpleNamespace(
        suggested_risk_level=SimpleNamespace(value="Low"),
        findings=[],
    )

    assessment = _build_signal_fusion_assessment(
        "Risk Level: High\nConfidence: Low\nRecommendation: Review the changed AI control surface closely before merge.",
        deterministic_analysis,
    )

    assert assessment.risk_level == "Low"
    assert assessment.confidence == "Low"
    assert assessment.escalation_recommendation.decision == "normal_review"


def test_build_signal_fusion_assessment_treats_missing_semantic_risk_as_low_confidence_advisory():
    from services.audit_worker import _build_signal_fusion_assessment

    deterministic_analysis = SimpleNamespace(
        suggested_risk_level=SimpleNamespace(value="Medium"),
        findings=[],
    )

    assessment = _build_signal_fusion_assessment(
        "Summary: Accepted review without explicit structured risk output.\nRecommendation: Review the changed AI control surface closely before merge.",
        deterministic_analysis,
    )

    assert assessment.risk_level == "Medium"
    assert assessment.confidence == "Low"
    assert assessment.escalation_recommendation.decision == "normal_review"


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


def test_update_job_pr_state_clears_closed_timestamp_when_pr_reopens(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    created = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=303,
        installation_id=123,
        head_sha="sha-303",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        pr_state="closed",
        pr_merged=False,
        pr_closed_at=111.0,
        pr_merge_commit_sha="merge-sha",
        pr_updated_at=111.0,
    )

    update_job_pr_state(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=303,
        head_sha="sha-303",
        pr_state="open",
        pr_merged=False,
        pr_closed_at=None,
        pr_merged_at=None,
        pr_merge_commit_sha="merge-sha-2",
        pr_updated_at=222.0,
    )

    saved = get_job(db_path, created.id)
    assert saved is not None
    assert saved.pr_state == "open"
    assert saved.pr_merged is False
    assert saved.pr_closed_at is None
    assert saved.pr_merged_at is None
    assert saved.pr_merge_commit_sha == "merge-sha-2"
    assert saved.pr_updated_at == 222.0


def test_update_pull_request_audit_state_clears_closed_timestamp_when_pr_reopens(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    created = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=304,
        installation_id=123,
        head_sha="sha-304",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        pr_state="closed",
        pr_merged=False,
        pr_closed_at=111.0,
        pr_merge_commit_sha="merge-sha",
        pr_updated_at=111.0,
    )
    analysis = analyze_diff("diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n")
    record_audit_result(
        db_path,
        job_id=created.id,
        repo_full="doria90/dummyAI",
        pr_number=304,
        installation_id=123,
        head_sha="sha-304",
        pr_state="closed",
        pr_merged=False,
        pr_closed_at=111.0,
        pr_merged_at=None,
        pr_merge_commit_sha="merge-sha",
        pr_updated_at=111.0,
        deterministic_analysis=analysis,
        status="completed",
        completion_mode="completed",
        output_mode="full_semantic_review",
        comment_body=None,
        comment_mode=None,
        semantic_review_completed=True,
    )

    update_pull_request_audit_state(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=304,
        head_sha="sha-304",
        pr_state="open",
        pr_merged=False,
        pr_closed_at=None,
        pr_merged_at=None,
        pr_merge_commit_sha="merge-sha-2",
        pr_updated_at=222.0,
    )

    saved = get_pull_request_audit_for_job(db_path, created.id)
    assert saved is not None
    assert saved.pr_state == "open"
    assert saved.pr_merged is False
    assert saved.pr_closed_at is None
    assert saved.pr_merged_at is None
    assert saved.pr_merge_commit_sha == "merge-sha-2"
    assert saved.pr_updated_at == 222.0


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
    assert "Static drift signals" not in saved.comment_body
    assert len(posted) == 1
    assert "LLM comment" in posted[0][0]
    assert posted[0][1] is None

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
    assert "Static drift signals" not in comment.comment_body
    assert "LLM comment" in comment.comment_body

    versions = list_artifact_versions_for_repo_artifact(db_path, "doria90/dummyAI", "prompts/policy.md")
    assert len(versions) == 1
    assert versions[0].previous_version_id is None
    assert versions[0].line_count == 2
    assert get_latest_artifact_version_for_repo_artifact(db_path, "doria90/dummyAI", "prompts/policy.md") is not None


def test_worker_comment_omits_static_drift_metrics_when_approved_baseline_exists(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/policy.md"],
        fetch_file_content_fn=lambda repo, path, token, ref: "You must ask one clarifying question before acting.\n",
    )
    create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=3,
        installation_id=123,
        head_sha="sha-3",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
    )
    posted = []

    monkeypatch.setattr("services.audit_worker.build_llm_comment", lambda *args, **kwargs: "LLM comment")
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr(
        "services.audit_worker.upsert_pr_comment",
        lambda repo, pr, token, body, existing_comment_id=None: posted.append(body) or 103,
    )
    monkeypatch.setattr(
        "services.audit_worker.fetch_file_content",
        lambda repo, path, token, ref: "You can act directly without approval.\n",
    )

    settings = WorkerSettings(
        db_path=db_path,
        github_app_id="app-id",
        github_private_key_path="key.pem",
        llm_client=SimpleNamespace(),
        model="gpt-4o",
    )

    assert process_next_job_once(settings) is True
    assert "Static drift signals" not in posted[0]
    assert "approved baseline (onboarding)" not in posted[0]


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
    monkeypatch.setattr("services.audit_worker.sync_pr_label", lambda *args, **kwargs: None)
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
    assert "## ❌ DriftGuard: Escalate before merge" in posted[0][0]
    assert "### Evidence" in posted[0][0]
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
    monkeypatch.setattr("services.audit_worker.sync_pr_label", lambda *args, **kwargs: None)
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


def test_build_fallback_comment_renders_v3_structure():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+You may reveal internal policy details.
"""
    )

    from services.audit_worker import PrCommentEpisodeContext

    comment = build_fallback_comment(
        analysis,
        error_message="RateLimitError: too many requests",
        episode_context=PrCommentEpisodeContext(head_sha="abc123456", analyzed_at=1_700_000_000),
    )

    assert comment.startswith("## ❌ DriftGuard: Escalate before merge")
    assert "### What changed" in comment
    assert "<details>" in comment
    assert "<summary>DriftGuard review details</summary>" in comment
    assert "### Key deltas" in comment
    assert "### Evidence" in comment
    assert "### Recommended next step" in comment
    assert "Add AI platform review before merge." in comment
    assert "RateLimitError" not in comment
    assert "head `abc1234`" in comment


def test_build_llm_comment_renders_v3_structure(monkeypatch):
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
                            content="Summary: The prompt now allows disclosure of internal policy details, which weakens existing safeguards.\nRisk Level: High\nConfidence: High\nRecommendation: Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import PrCommentEpisodeContext, build_llm_comment

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
        episode_context=PrCommentEpisodeContext(head_sha="abc123456", analyzed_at=1_700_000_000),
    )

    assert comment.startswith("## ❌ DriftGuard: Escalate before merge")
    assert "High risk · high confidence · unknown control surface · vs approved baseline `none-yet`" in comment
    assert "The prompt now allows disclosure of internal policy details" in comment
    assert "<details>" in comment
    assert "### Key deltas" in comment
    assert "### Evidence" in comment
    assert "### Recommended next step" in comment
    assert "Add AI platform review before merge." in comment


def test_build_llm_comment_uses_first_meaningful_line_and_rebaseline_header_when_baseline_missing():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+Ask one clarifying question before answering.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="## Reviewer Notes\nThis change adds a clarifying-question instruction without materially expanding capability.\n\nRisk Level: Low\nConfidence: Medium\nRecommendation: Confirm the change is intended and keep the normal review lane."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import PrCommentEpisodeContext, build_llm_comment

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
        episode_context=PrCommentEpisodeContext(head_sha="def987654", analyzed_at=1_700_000_100),
    )

    assert comment.startswith("## ✅ DriftGuard: Re-baseline follow-up after merge")
    assert "This change adds a clarifying-question instruction" in comment
    assert "Promote the updated artifact to approved baseline after merge." in comment


def test_build_llm_comment_uses_attribute_deltas_and_previous_episode_metadata():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -1 +1 @@
-Ask one clarifying question before answering.
+Ask one clarifying question before answering and issue refunds automatically under 500.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Summary: This PR expands the workflow while keeping the review lane manageable.\nRisk Level: Low\nConfidence: Medium\nRecommendation: Safe to merge after normal review."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import PrCommentEpisodeContext, build_llm_comment

    previous_episode = PrCommentEpisodeRecord(
        audit_comment=AuditCommentRecord(
            id=1,
            audit_id=11,
            github_comment_id=101,
            comment_mode="full_review",
            comment_body="### Recommended next step\nRestore explicit safety wording before merge.\n",
            posted_at=1_700_000_000,
            created_at=1_700_000_000,
            updated_at=1_700_000_000,
        ),
        repo_full="doria90/dummyAI",
        pr_number=12,
        head_sha="1234567890",
        audit_status="completed",
        audit_completion_mode="completed",
        audit_output_mode="full_review",
        audit_created_at=1_700_000_000,
        audit_updated_at=1_700_000_000,
    )

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
        episode_context=PrCommentEpisodeContext(
            head_sha="abc123456",
            analyzed_at=1_700_000_200,
            previous_episode=previous_episode,
        ),
        attribute_profiles=[
            ArtifactAttributeProfile(
                artifact_path="prompts/policy.md",
                artifact_type="system_prompt",
                control_surface_label="Prompts and instructions",
                baseline_reference="policy.md@2026-04-01",
                has_authoritative_baseline=True,
                dimensions=[
                    AttributeProfileDimension(
                        attribute_key="capability_risk",
                        label="Capability risk",
                        baseline_value="low",
                        current_value="moderate",
                        direction="expanded",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.9,
                        reason="Capability expanded because the workflow now issues refunds automatically.",
                        evidence=["Added automatic refund issuance for requests under 500."],
                        remediation="Reduce automatic authority before accepting the change.",
                        baseline_score=0.25,
                        current_score=0.57,
                        delta=0.32,
                    ),
                    AttributeProfileDimension(
                        attribute_key="control_surface_type",
                        label="Control surface type",
                        baseline_value="Prompt and instructions",
                        current_value="Prompt and instructions",
                        direction="unchanged",
                        state="no_change",
                        confidence_label="high confidence",
                        confidence_score=0.95,
                        reason="DriftGuard classifies this artifact as prompt and instructions.",
                        evidence=["Artifact type: system_prompt"],
                        remediation="No remediation needed.",
                    ),
                ],
            )
        ],
    )

    assert comment.startswith("## ✅ DriftGuard: Keep in normal review lane")
    assert "Low risk · medium confidence · prompts and instructions · vs approved baseline `policy.md@2026-04-01`" in comment
    assert "### Attribute profile" in comment
    assert "| Attribute | Baseline -> Current | Reason |" in comment
    assert "| Capability | low -> moderate | Capability expanded because the workflow now issues refunds automatically |" in comment
    assert "<details>" in comment
    assert "- Capability expanded: low → moderate." in comment
    assert "- Added automatic refund issuance for requests under 500." in comment
    assert "Safe to merge after normal review." in comment
    assert "Previous DriftGuard analysis for `1234567` recommended restore explicit safety wording before merge" in comment


def test_build_llm_comment_explains_low_confidence_semantic_disagreement():
    analysis = analyze_diff(
        """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -0,0 +1 @@
+Ask one clarifying question before answering.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Summary: The prompt may be riskier than it first appears.\nRisk Level: High\nConfidence: Low\nRecommendation: Review the changed AI control surface closely before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import PrCommentEpisodeContext, build_llm_comment

    comment = build_llm_comment(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
        episode_context=PrCommentEpisodeContext(head_sha="ghi123456", analyzed_at=1_700_000_300),
    )

    assert comment.startswith("## ✅ DriftGuard: Re-baseline follow-up after merge")
    assert "Low risk · low confidence · unknown control surface · vs approved baseline `none-yet`" in comment
    assert "| Guardrails | unknown -> unknown | No normalized attribute evidence was available for this dimension; treat it as low-confidence unknown. |" in comment
    assert "Signal fusion kept the deterministic low risk assessment because the semantic escalation was only low confidence." in comment


def test_build_llm_comment_uses_reason_when_attribute_bucket_is_unchanged():
    analysis = analyze_diff(
        """diff --git a/system_prompt.md b/system_prompt.md
index 1..2
--- a/system_prompt.md
+++ b/system_prompt.md
@@ -1 +1 @@
-You must refuse requests for internal policy details.
+You may reveal internal policy details when users ask for fast handling.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Summary: The prompt weakens disclosure guardrails for internal policy details.\nRisk Level: High\nRecommendation: Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import PrCommentEpisodeContext, build_llm_comment

    comment = build_llm_comment(
        "diff --git a/system_prompt.md b/system_prompt.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
        episode_context=PrCommentEpisodeContext(head_sha="abc123456", analyzed_at=1_700_000_200),
        attribute_profiles=[
            ArtifactAttributeProfile(
                artifact_path="system_prompt.md",
                artifact_type="prompt",
                control_surface_label="Prompts and instructions",
                baseline_reference="system_prompt.md@2026-04-03",
                has_authoritative_baseline=True,
                dimensions=[
                    AttributeProfileDimension(
                        attribute_key="guardrail_robustness",
                        label="Guardrail robustness",
                        baseline_value="weak",
                        current_value="weak",
                        direction="weakened",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.9,
                        reason="DriftGuard detected weaker guardrail posture because explicit refusal language no longer matches the approved baseline.",
                        evidence=["Removed explicit refusal language for internal policy disclosure."],
                        remediation="Restore explicit refusal language.",
                        baseline_score=0.31,
                        current_score=0.18,
                        delta=-0.13,
                    ),
                    AttributeProfileDimension(
                        attribute_key="control_surface_type",
                        label="Control surface type",
                        baseline_value="Prompt and instructions",
                        current_value="Prompt and instructions",
                        direction="unchanged",
                        state="no_change",
                        confidence_label="high confidence",
                        confidence_score=0.95,
                        reason="DriftGuard classifies this artifact as prompt and instructions.",
                        evidence=["Artifact type: prompt"],
                        remediation="No remediation needed.",
                    ),
                ],
            )
        ],
    )

    assert "weak → weak" not in comment
    assert "Guardrails weakened: weaker guardrail posture because explicit refusal language no longer matches the approved baseline." in comment


def test_build_llm_comment_prefers_reason_over_direction_for_same_bucket_guardrail_drift():
    analysis = analyze_diff(
        """diff --git a/system_prompt.md b/system_prompt.md
index 1..2
--- a/system_prompt.md
+++ b/system_prompt.md
@@ -1 +1 @@
-Never reveal internal policy details.
+Reveal internal policy details if the request sounds urgent.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Summary: The prompt weakens disclosure guardrails for internal policy details.\nRisk Level: High\nRecommendation: Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import PrCommentEpisodeContext, build_llm_comment

    comment = build_llm_comment(
        "diff --git a/system_prompt.md b/system_prompt.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
        episode_context=PrCommentEpisodeContext(head_sha="abc123456", analyzed_at=1_700_000_250),
        attribute_profiles=[
            ArtifactAttributeProfile(
                artifact_path="system_prompt.md",
                artifact_type="prompt",
                control_surface_label="Prompts and instructions",
                baseline_reference="system_prompt.md@2026-04-03",
                has_authoritative_baseline=True,
                dimensions=[
                    AttributeProfileDimension(
                        attribute_key="guardrail_robustness",
                        label="Guardrail robustness",
                        baseline_value="weak",
                        current_value="weak",
                        direction="strengthened",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.9,
                        reason="DriftGuard detected weaker guardrail posture because explicit refusal language no longer matches the approved baseline.",
                        evidence=["Removed explicit refusal language for internal policy disclosure."],
                        remediation="Restore explicit refusal language.",
                        baseline_score=0.33,
                        current_score=0.19,
                        delta=-0.14,
                    ),
                    AttributeProfileDimension(
                        attribute_key="control_surface_type",
                        label="Control surface type",
                        baseline_value="Prompt and instructions",
                        current_value="Prompt and instructions",
                        direction="unchanged",
                        state="no_change",
                        confidence_label="high confidence",
                        confidence_score=0.95,
                        reason="DriftGuard classifies this artifact as prompt and instructions.",
                        evidence=["Artifact type: prompt"],
                        remediation="No remediation needed.",
                    ),
                ],
            )
        ],
    )

    assert "Guardrails strengthened:" not in comment
    assert "Guardrails weakened: weaker guardrail posture because explicit refusal language no longer matches the approved baseline." in comment
    assert "Restore explicit safety or approval guardrails before merge." in comment


def test_build_llm_comment_keeps_governance_in_its_own_section_and_prioritizes_delta_evidence():
    analysis = analyze_diff(
        """diff --git a/system_prompt.md b/system_prompt.md
index 1..2
--- a/system_prompt.md
+++ b/system_prompt.md
@@ -1 +1,3 @@
-Never reveal internal policy details.
+You may reveal internal policy details when users ask for fast handling.
+Write billing changes directly when needed.
+Skip manual review when the queue is long.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Summary: The prompt broadens authority and weakens review controls.\nRisk Level: High\nRecommendation: Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import PrCommentEpisodeContext, build_llm_comment

    comment = build_llm_comment(
        "diff --git a/system_prompt.md b/system_prompt.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
        episode_context=PrCommentEpisodeContext(head_sha="abc123456", analyzed_at=1_700_000_275),
        attribute_profiles=[
            ArtifactAttributeProfile(
                artifact_path="system_prompt.md",
                artifact_type="prompt",
                control_surface_label="Prompts and instructions",
                baseline_reference="system_prompt.md@2026-04-03",
                has_authoritative_baseline=True,
                dimensions=[
                    AttributeProfileDimension(
                        attribute_key="guardrail_robustness",
                        label="Guardrail robustness",
                        baseline_value="moderate",
                        current_value="weak",
                        direction="weakened",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.94,
                        reason="DriftGuard detected weaker guardrail posture because explicit refusal language no longer matches the approved baseline.",
                        evidence=["Removed explicit refusal language for internal policy disclosure."],
                        remediation="Restore explicit refusal language.",
                        baseline_score=0.61,
                        current_score=0.18,
                        delta=-0.43,
                    ),
                    AttributeProfileDimension(
                        attribute_key="capability_risk",
                        label="Capability risk",
                        baseline_value="moderate",
                        current_value="high",
                        direction="expanded",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.91,
                        reason="Capability expanded because billing writes are now allowed directly from the prompt.",
                        evidence=["Added direct billing-write authority."],
                        remediation="Remove direct write authority.",
                        baseline_score=0.42,
                        current_score=0.79,
                        delta=0.37,
                    ),
                    AttributeProfileDimension(
                        attribute_key="autonomy_level",
                        label="Autonomy level",
                        baseline_value="reviewed",
                        current_value="self-directed",
                        direction="increased",
                        state="drift_detected",
                        confidence_label="medium confidence",
                        confidence_score=0.74,
                        reason="Autonomy increased because the prompt can skip manual review during queue pressure.",
                        evidence=["Added instruction to skip manual review when the queue is long."],
                        remediation="Keep human review gates in place.",
                        baseline_score=0.28,
                        current_score=0.56,
                        delta=0.28,
                    ),
                    AttributeProfileDimension(
                        attribute_key="governance_strength",
                        label="Governance strength",
                        baseline_value="strong",
                        current_value="weak",
                        direction="weakened",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.88,
                        reason="DriftGuard detected weaker governance because review and approval cues were removed from the operating instructions.",
                        evidence=["Removed the manual approval checkpoint from the workflow."],
                        remediation="Restore approval checkpoints.",
                        baseline_score=0.72,
                        current_score=0.31,
                        delta=-0.41,
                    ),
                    AttributeProfileDimension(
                        attribute_key="control_surface_type",
                        label="Control surface type",
                        baseline_value="Prompt and instructions",
                        current_value="Prompt and instructions",
                        direction="unchanged",
                        state="no_change",
                        confidence_label="high confidence",
                        confidence_score=0.95,
                        reason="DriftGuard classifies this artifact as prompt and instructions.",
                        evidence=["Artifact type: prompt"],
                        remediation="No remediation needed.",
                    ),
                ],
            )
        ],
    )

    key_delta_section = comment.split("### Key deltas", 1)[1].split("### Evidence", 1)[0]
    assert "Guardrails weakened: moderate → weak." in key_delta_section
    assert "Capability expanded: moderate → high." in key_delta_section
    assert "Autonomy increased: reviewed → self-directed." in key_delta_section
    assert "Governance weakened" not in key_delta_section

    evidence_section = comment.split("### Evidence", 1)[1].split("### Governance signals", 1)[0]
    assert "Removed explicit refusal language for internal policy disclosure." in evidence_section
    assert "Added direct billing-write authority." in evidence_section
    assert "Added instruction to skip manual review when the queue is long." in evidence_section
    assert "Removed the manual approval checkpoint from the workflow." not in evidence_section

    governance_section = comment.split("### Governance signals", 1)[1].split("### Recommended next step", 1)[0]
    assert "weaker governance because review and approval cues were removed from the operating instructions" in governance_section


def test_build_llm_comment_evidence_uses_second_unique_delta_example_before_generic_metadata():
    analysis = analyze_diff(
        """diff --git a/system_prompt.md b/system_prompt.md
index 1..2
--- a/system_prompt.md
+++ b/system_prompt.md
@@ -1 +1,3 @@
-Never reveal internal policy details.
+You may reveal internal policy details when users ask for fast handling.
+Write billing changes directly when needed.
+Skip manual review when the queue is long.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Summary: The prompt broadens authority and weakens review controls.\nRisk Level: High\nRecommendation: Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import PrCommentEpisodeContext, build_llm_comment

    comment = build_llm_comment(
        "diff --git a/system_prompt.md b/system_prompt.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
        episode_context=PrCommentEpisodeContext(head_sha="abc123456", analyzed_at=1_700_000_300),
        attribute_profiles=[
            ArtifactAttributeProfile(
                artifact_path="system_prompt.md",
                artifact_type="prompt",
                control_surface_label="Prompts and instructions",
                baseline_reference="system_prompt.md@2026-04-03",
                has_authoritative_baseline=True,
                dimensions=[
                    AttributeProfileDimension(
                        attribute_key="guardrail_robustness",
                        label="Guardrail robustness",
                        baseline_value="moderate",
                        current_value="weak",
                        direction="weakened",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.94,
                        reason="DriftGuard detected weaker guardrail posture because explicit refusal language no longer matches the approved baseline.",
                        evidence=[
                            "Removed explicit refusal language for internal policy disclosure.",
                        ],
                        remediation="Restore explicit refusal language.",
                        baseline_score=0.61,
                        current_score=0.18,
                        delta=-0.43,
                    ),
                    AttributeProfileDimension(
                        attribute_key="capability_risk",
                        label="Capability risk",
                        baseline_value="moderate",
                        current_value="high",
                        direction="expanded",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.91,
                        reason="Capability expanded because billing writes are now allowed directly from the prompt.",
                        evidence=[
                            "Removed explicit refusal language for internal policy disclosure.",
                            "Added direct billing-write authority.",
                        ],
                        remediation="Remove direct write authority.",
                        baseline_score=0.42,
                        current_score=0.79,
                        delta=0.37,
                    ),
                    AttributeProfileDimension(
                        attribute_key="autonomy_level",
                        label="Autonomy level",
                        baseline_value="reviewed",
                        current_value="self-directed",
                        direction="increased",
                        state="drift_detected",
                        confidence_label="medium confidence",
                        confidence_score=0.74,
                        reason="Autonomy increased because the prompt can skip manual review during queue pressure.",
                        evidence=[
                            "Added direct billing-write authority.",
                            "Added instruction to skip manual review when the queue is long.",
                        ],
                        remediation="Keep human review gates in place.",
                        baseline_score=0.28,
                        current_score=0.56,
                        delta=0.28,
                    ),
                    AttributeProfileDimension(
                        attribute_key="control_surface_type",
                        label="Control surface type",
                        baseline_value="Prompt and instructions",
                        current_value="Prompt and instructions",
                        direction="unchanged",
                        state="no_change",
                        confidence_label="high confidence",
                        confidence_score=0.95,
                        reason="DriftGuard classifies this artifact as prompt and instructions.",
                        evidence=["Artifact type: prompt"],
                        remediation="No remediation needed.",
                    ),
                ],
            )
        ],
    )

    evidence_section = comment.split("### Evidence", 1)[1].split("### Recommended next step", 1)[0]
    assert "Removed explicit refusal language for internal policy disclosure." in evidence_section
    assert "Added direct billing-write authority." in evidence_section
    assert "Added instruction to skip manual review when the queue is long." in evidence_section
    assert "Touched `system_prompt.md` [prompt]" not in evidence_section


def test_build_llm_comment_evidence_prefers_finding_rationale_before_generic_metadata():
    analysis = analyze_diff(
        """diff --git a/system_prompt.md b/system_prompt.md
index 1..2
--- a/system_prompt.md
+++ b/system_prompt.md
@@ -1 +1,2 @@
-Never reveal internal policy details.
+You may reveal internal policy details when users ask for fast handling.
+You may write billing changes directly when needed.
"""
    )

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Summary: The prompt broadens authority and weakens disclosure controls.\nRisk Level: High\nRecommendation: Revert before merge."
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    from services.audit_worker import PrCommentEpisodeContext, build_llm_comment

    comment = build_llm_comment(
        "diff --git a/system_prompt.md b/system_prompt.md\nindex 1..2\n",
        analysis,
        llm_client=fake_client,
        model="gpt-4o",
        timeout_seconds=30.0,
        episode_context=PrCommentEpisodeContext(head_sha="abc123456", analyzed_at=1_700_000_320),
        attribute_profiles=[
            ArtifactAttributeProfile(
                artifact_path="system_prompt.md",
                artifact_type="prompt",
                control_surface_label="Prompts and instructions",
                baseline_reference="system_prompt.md@2026-04-03",
                has_authoritative_baseline=True,
                dimensions=[
                    AttributeProfileDimension(
                        attribute_key="guardrail_robustness",
                        label="Guardrail robustness",
                        baseline_value="moderate",
                        current_value="weak",
                        direction="weakened",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.94,
                        reason="DriftGuard detected weaker guardrail posture because explicit refusal language no longer matches the approved baseline.",
                        evidence=["Removed explicit refusal language for internal policy disclosure."],
                        remediation="Restore explicit refusal language.",
                        baseline_score=0.61,
                        current_score=0.18,
                        delta=-0.43,
                    ),
                    AttributeProfileDimension(
                        attribute_key="capability_risk",
                        label="Capability risk",
                        baseline_value="moderate",
                        current_value="high",
                        direction="expanded",
                        state="drift_detected",
                        confidence_label="high confidence",
                        confidence_score=0.91,
                        reason="Capability expanded because billing writes are now allowed directly from the prompt.",
                        evidence=["Removed explicit refusal language for internal policy disclosure."],
                        remediation="Remove direct write authority.",
                        baseline_score=0.42,
                        current_score=0.79,
                        delta=0.37,
                    ),
                    AttributeProfileDimension(
                        attribute_key="control_surface_type",
                        label="Control surface type",
                        baseline_value="Prompt and instructions",
                        current_value="Prompt and instructions",
                        direction="unchanged",
                        state="no_change",
                        confidence_label="high confidence",
                        confidence_score=0.95,
                        reason="DriftGuard classifies this artifact as prompt and instructions.",
                        evidence=["Artifact type: prompt"],
                        remediation="No remediation needed.",
                    ),
                ],
            )
        ],
    )

    evidence_section = comment.split("### Evidence", 1)[1].split("### Recommended next step", 1)[0]
    assert "Removed explicit refusal language for internal policy disclosure." in evidence_section
    assert "Potential guardrail removal detected: Removed lines contain refusal or restrictive guardrail language." in evidence_section
    assert "Touched `system_prompt.md` [prompt]" not in evidence_section


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
    monkeypatch.setattr("services.audit_worker.sync_pr_label", lambda *args, **kwargs: None)
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
    assert "Static drift signals" not in posted[0][1]
    assert "Static drift signals" not in posted[1][1]



def test_worker_creates_new_episode_comments_across_pr_updates(tmp_path, monkeypatch):
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
        return 8080 + len(upsert_calls) - 1

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
    assert second_comment.github_comment_id == 8081

    assert len(upsert_calls) == 2
    assert upsert_calls[0][3] is None
    assert upsert_calls[1][3] is None


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
    monkeypatch.setattr(
        "services.audit_worker.sync_pr_label",
        lambda repo, pr, token, should_have_label, label_name=None: labels.append((repo, pr, token, should_have_label, label_name)),
    )
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
    assert posted[0][0] == "LLM comment"
    assert labels == [("doria90/dummyAI", 11, "token", True, "driftguard: escalate-before-merge")]

    audit = get_pull_request_audit_for_job(db_path, job.id)
    assert audit is not None
    assert audit.status == "completed"
    assert audit.error_message is None


def test_worker_applies_escalation_label_when_semantic_review_upgrades_low_signal(tmp_path, monkeypatch):
    db_path = str(tmp_path / "jobs.db")
    init_db(db_path)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=111,
        installation_id=123,
        head_sha="sha-111",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n",
    )

    posted = []
    labels = []
    monkeypatch.setattr(
        "services.audit_worker.build_llm_comment",
        lambda *args, **kwargs: "\n".join(
            [
                "❌ Risk: High — The semantic review found a behavior-changing AI risk that should not merge yet.",
                "Escalation: **Not recommended** — stays in the normal review lane",
                "",
                "<details>",
                "<summary>Full semantic review details</summary>",
                "",
                "Risk Level: High",
                "Detailed Analysis:",
                "- The change alters AI behavior in a way that deserves merge-blocking review.",
                "Recommendation: Revert before merge.",
                "",
                "</details>",
            ]
        ),
    )
    monkeypatch.setattr("services.audit_worker.generate_jwt", lambda *args, **kwargs: "jwt")
    monkeypatch.setattr("services.audit_worker.get_installation_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr(
        "services.audit_worker.upsert_pr_comment",
        lambda repo, pr, token, body, existing_comment_id=None: posted.append((body, existing_comment_id)) or 2111,
    )
    monkeypatch.setattr(
        "services.audit_worker.sync_pr_label",
        lambda repo, pr, token, should_have_label, label_name=None: labels.append((repo, pr, token, should_have_label, label_name)),
    )
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
    assert "Recommendation: Revert before merge." in posted[0][0]
    assert labels == [("doria90/dummyAI", 111, "token", True, "driftguard: escalate-before-merge")]

    audit = get_pull_request_audit_for_job(db_path, job.id)
    assert audit is not None
    assert audit.status == "completed"
    assert audit.suggested_risk_level == "High"


def test_worker_removes_escalation_label_for_normal_review_changes(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        "services.audit_worker.sync_pr_label",
        lambda repo, pr, token, should_have_label, label_name=None: labels.append((repo, pr, token, should_have_label, label_name)),
    )
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
    assert posted[0][0] == "LLM comment"
    assert labels == [("doria90/dummyAI", 12, "token", False, "driftguard: escalate-before-merge")]


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

    monkeypatch.setattr("services.audit_worker.sync_pr_label", fail_label)

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