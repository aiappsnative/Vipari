import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.audit_jobs import init_db
from services.audit_records import record_audit_result
from services.dashboard_views import build_dashboard_overview_view, build_repo_dashboard_view, list_repo_dashboard_index
from services.signal_fusion import priority_from_fused_signals, priority_sort_rank
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill


PROMPT_BASELINE = """# Refund Copilot
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Use the billing sandbox tool in read mode.
max_steps: 2
temperature: 0.2
"""

PROMPT_MEDIUM = """# Refund Copilot
Refund customers after checking the billing sandbox.
Escalate unusual cases for approval.
max_steps: 4
temperature: 0.4
"""

PROMPT_CURRENT = """# Refund Copilot
You can refund customers directly in production using the billing tool.
Use judgment when deciding whether approval is necessary.
Update billing records and send confirmations.
parallel plan with multi-step execution
max_steps: 6
temperature: 0.8
"""

PROMPT_DIFF = """diff --git a/prompts/refund.txt b/prompts/refund.txt
index 1..2 100644
--- a/prompts/refund.txt
+++ b/prompts/refund.txt
@@ -1 +1,4 @@
-You are a refund assistant.
+You are a refund assistant.
+You can refund customers directly in production.
+Use judgment when deciding whether approval is necessary.
+Update billing records and send confirmations.
"""


def _record_pr_profile(db_path: str):
    analysis = analyze_diff(PROMPT_DIFF)
    record_audit_result(
        db_path,
        job_id=99,
        repo_full="doria90/dummyAI",
        pr_number=42,
        installation_id=123,
        head_sha="sha-current",
        deterministic_analysis=analysis,
        status="completed",
        completion_mode="completed",
        output_mode="full_semantic_review",
        comment_body=None,
        comment_mode=None,
        semantic_review_completed=True,
        artifact_snapshots={"prompts/refund.txt": PROMPT_CURRENT},
    )


def test_build_repo_dashboard_view_aggregates_onboarding_backfill_and_pr_drift(tmp_path):
    db_path = str(tmp_path / "dashboard.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )
    plan_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-3", "sha-2", "sha-1"][:limit],
    )
    execute_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        fetch_file_content_fn=lambda repo, path, token, ref: {
            "sha-1": PROMPT_BASELINE,
            "sha-2": PROMPT_MEDIUM,
            "sha-3": PROMPT_CURRENT,
        }[ref],
    )
    _record_pr_profile(db_path)

    dashboard = build_repo_dashboard_view(db_path, "doria90/dummyAI")

    assert dashboard.repo_full == "doria90/dummyAI"
    assert dashboard.onboarding is not None
    assert dashboard.baseline_version_count == 1
    assert dashboard.backfill.completed_job_count == 1
    assert dashboard.backfill.total_historical_versions == 3
    assert dashboard.backfill.total_historical_profiles == 3
    assert dashboard.pull_request_audit_count == 1
    assert dashboard.drift_summary.profile_count == 1
    assert len(dashboard.artifacts) == 1
    assert len(dashboard.insights) == 1
    assert len(dashboard.lower_confidence_insights) == 0
    assert dashboard.insights[0].artifact_path == "prompts/refund.txt"
    assert dashboard.insights[0].queue_lane == "primary"
    assert dashboard.insights[0].priority in {"review_now", "watch", "baseline_review"}
    assert dashboard.insights[0].confidence_label in {"high confidence", "medium confidence", "lower confidence"}
    assert dashboard.insights[0].evidence_label == "PR + history"
    assert dashboard.insights[0].evidence_summary.startswith("Open PR #42 · sha-cur first;")
    assert dashboard.insights[0].baseline_label.startswith("Baseline: Approved")
    assert dashboard.insights[0].provenance_summary.startswith("From · PR #42 · sha-cur · full semantic review · semantic complete · risk low")
    assert dashboard.insights[0].review_target == "PR #42 · sha-cur"
    assert dashboard.insights[0].review_url == "https://github.com/doria90/dummyAI/pull/42"
    assert dashboard.insights[0].supporting_review_target is not None
    assert dashboard.insights[0].supporting_review_target.startswith("commit sha-")
    assert dashboard.insights[0].supporting_review_url is not None
    assert dashboard.insights[0].supporting_review_url.startswith("https://github.com/doria90/dummyAI/commit/")
    assert dashboard.insights[0].change_summary
    assert dashboard.insights[0].flag_summary.startswith("Flagged because")
    assert "historical hotspot" in dashboard.insights[0].risk_reasons
    assert "pr-linked evidence" in dashboard.insights[0].risk_reasons
    assert len(dashboard.control_surface_groups) == 1
    assert dashboard.control_surface_groups[0].group_key == "prompts"
    assert len(dashboard.history_timelines) == 1
    assert dashboard.history_timelines[0].artifact_path == "prompts/refund.txt"
    assert dashboard.history_timelines[0].point_count == 4
    assert len(dashboard.design_profiles) == 1
    assert dashboard.design_profiles[0].artifact_path == "prompts/refund.txt"
    assert dashboard.design_profiles[0].baseline_provenance is not None
    assert dashboard.design_profiles[0].baseline_provenance.source_type == "approved_baseline"
    assert dashboard.design_profiles[0].provenance is not None
    assert dashboard.design_profiles[0].provenance.source_type == "pull_request"
    assert dashboard.design_profiles[0].provenance.label == "Pull request audit"
    assert dashboard.design_profiles[0].provenance.source_ref == "PR #42 · sha-cur"
    assert dashboard.design_profiles[0].provenance.source_url == "https://github.com/doria90/dummyAI/pull/42"
    assert dashboard.design_profiles[0].provenance.review_context == "full semantic review · semantic complete · risk low"
    assert dashboard.design_profiles[0].headline_summary
    assert dashboard.design_profiles[0].drift_label in {"small drift", "medium drift", "large drift"}
    assert dashboard.design_profiles[0].drift_tone in {"low", "medium", "high"}
    assert dashboard.design_profiles[0].can_promote_source_to_baseline is True
    assert isinstance(dashboard.design_profiles[0].attribute_findings, list)
    if dashboard.design_profiles[0].attribute_findings:
        assert dashboard.design_profiles[0].attribute_findings[0].reason
        assert isinstance(dashboard.design_profiles[0].attribute_findings[0].evidence, list)
        assert dashboard.design_profiles[0].attribute_findings[0].remediation
    assert any(
        finding.attribute_key == "stability_vs_creativity"
        for finding in dashboard.design_profiles[0].attribute_findings
    )
    assert dashboard.design_profiles[0].risk_tags[0] in {
        "capability expanded",
        "guardrails weakened",
        "autonomy increased",
        "historical hotspot",
        "baseline only",
    }
    assert dashboard.artifacts[0].artifact_path == "prompts/refund.txt"
    assert dashboard.artifacts[0].historical_version_count == 3
    assert dashboard.artifacts[0].pr_profile_count == 1
    assert dashboard.history_timelines[0].points[-1].baseline_provenance is not None
    assert dashboard.history_timelines[0].points[-1].baseline_provenance.source_type == "approved_baseline"
    assert dashboard.history_timelines[0].points[0].label == "Historical backfill"
    assert dashboard.history_timelines[0].points[0].source_ref == "commit sha-1"
    assert dashboard.history_timelines[0].points[0].source_url == "https://github.com/doria90/dummyAI/commit/sha-1"
    assert dashboard.history_timelines[0].points[0].review_context == "Historical snapshot from backfill"
    assert dashboard.history_timelines[0].points[-1].label == "Pull request audit"
    assert dashboard.history_timelines[0].points[-1].source_ref == "PR #42 · sha-cur"
    assert dashboard.history_timelines[0].points[-1].source_url == "https://github.com/doria90/dummyAI/pull/42"
    assert dashboard.history_timelines[0].points[-1].review_context == "full semantic review · semantic complete · risk low"


def test_priority_from_fused_signals_raises_dashboard_priority_for_high_risk_audits():
    assert priority_from_fused_signals(0.41, risk_level="High") == "review_now"
    assert priority_from_fused_signals(0.41, risk_level="Medium") == "watch"
    assert priority_from_fused_signals(1.31, risk_level="Low") == "review_now"


def test_priority_sort_rank_matches_dashboard_lane_order():
    assert priority_sort_rank("review_now") == 0
    assert priority_sort_rank("watch") == 1
    assert priority_sort_rank("baseline_review") == 2
    assert priority_sort_rank("unexpected") == 9


def test_build_repo_dashboard_view_uses_fused_pr_risk_for_priority(tmp_path):
    db_path = str(tmp_path / "dashboard-fused-risk.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )

    analysis = analyze_diff("diff --git a/prompts/refund.txt b/prompts/refund.txt\nindex 1..2 100644\n")
    record_audit_result(
        db_path,
        job_id=100,
        repo_full="doria90/dummyAI",
        pr_number=43,
        installation_id=123,
        head_sha="sha-fused",
        deterministic_analysis=analysis,
        status="completed",
        completion_mode="completed",
        output_mode="full_semantic_review",
        comment_body=None,
        comment_mode=None,
        semantic_review_completed=True,
        suggested_risk_level="High",
        artifact_snapshots={"prompts/refund.txt": PROMPT_BASELINE},
    )

    dashboard = build_repo_dashboard_view(db_path, "doria90/dummyAI")

    assert dashboard.artifacts[0].latest_pr_risk_level == "High"
    assert dashboard.insights[0].priority == "review_now"
    assert dashboard.artifacts[0].artifact_path == "prompts/refund.txt"
    assert dashboard.artifacts[0].pr_profile_count == 1
    assert dashboard.design_profiles[0].provenance is not None
    assert dashboard.design_profiles[0].provenance.source_ref == "PR #43 · sha-fus"
    assert dashboard.design_profiles[0].provenance.review_context == "full semantic review · semantic complete · risk high"


def test_list_repo_dashboard_index_returns_latest_onboarded_repositories(tmp_path):
    db_path = str(tmp_path / "dashboard.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/repo-one",
        installation_id=1,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/a.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: "You are a safe assistant.",
    )
    onboard_repository(
        db_path,
        repo_full="doria90/repo-two",
        installation_id=2,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["config/model.yaml"],
        fetch_file_content_fn=lambda repo, path, token, ref: "model: gpt-4.1\ntemperature: 0.2\n",
    )

    index = list_repo_dashboard_index(db_path)

    assert [entry.repo_full for entry in index] == ["doria90/repo-one", "doria90/repo-two"]
    assert all(entry.discovered_artifact_count == 1 for entry in index)


def test_build_dashboard_overview_view_summarizes_repo_priorities_and_coverage(tmp_path):
    db_path = str(tmp_path / "overview.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=1,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_CURRENT,
    )
    plan_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-2", "sha-1"][:limit],
    )
    execute_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        fetch_file_content_fn=lambda repo, path, token, ref: {"sha-1": PROMPT_BASELINE, "sha-2": PROMPT_CURRENT}[ref],
    )
    _record_pr_profile(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/repo-two",
        installation_id=2,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["agents/worker.py"],
        fetch_file_content_fn=lambda repo, path, token, ref: "def run_agent():\n    return 'ok'\n",
    )

    overview = build_dashboard_overview_view(db_path)

    assert overview.risk_state.status in {"high_attention", "watch", "baseline"}
    assert len(overview.highest_risk_items) >= 1
    assert len(overview.control_surface_risk) >= 1
    assert len(overview.regression_patterns) >= 1
    assert overview.metrics[0].label == "Onboarded repositories"
    assert overview.metrics[0].value == 2
    assert len(overview.attention_repos) == 2
    assert overview.attention_repos[0].repo_full == "doria90/dummyAI"
    assert overview.attention_repos[0].highest_priority in {"review_now", "watch", "baseline_review"}
    assert overview.attention_repos[0].highest_evidence_label == "PR + history"
    assert overview.attention_repos[0].highest_evidence_summary.startswith("Open PR #42 · sha-cur first;")
    assert overview.attention_repos[0].highest_baseline_label is not None
    assert overview.attention_repos[0].highest_review_target == "PR #42 · sha-cur"
    assert overview.attention_repos[0].highest_review_url == "https://github.com/doria90/dummyAI/pull/42"
    assert overview.attention_repos[0].highest_change_summary
    assert (overview.attention_repos[0].highest_flag_summary or "").startswith("Flagged because")
    assert overview.attention_repos[0].lower_confidence_count == 0
    assert overview.highest_risk_items[0].baseline_label.startswith("Baseline: Approved")
    assert overview.highest_risk_items[0].evidence_label == "PR + history"
    assert overview.highest_risk_items[0].evidence_summary.startswith("Open PR #42 · sha-cur first;")
    assert overview.highest_risk_items[0].review_target == "PR #42 · sha-cur"
    assert overview.highest_risk_items[0].review_url == "https://github.com/doria90/dummyAI/pull/42"
    assert overview.highest_risk_items[0].change_summary
    assert overview.highest_risk_items[0].flag_summary.startswith("Flagged because")
    assert any(group.group_key == "prompts" for group in overview.control_surface_coverage)
    assert [repo.repo_full for repo in overview.repos] == ["doria90/dummyAI", "doria90/repo-two"]


def test_build_repo_dashboard_view_marks_baseline_only_profile_as_not_promotable(tmp_path):
    db_path = str(tmp_path / "dashboard.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )

    dashboard = build_repo_dashboard_view(db_path, "doria90/dummyAI")

    assert len(dashboard.design_profiles) == 1
    assert dashboard.design_profiles[0].provenance is None
    assert dashboard.design_profiles[0].can_promote_source_to_baseline is False
    assert dashboard.insights[0].evidence_label == "baseline only"
    assert dashboard.insights[0].evidence_summary == "No stored PR or merged-history evidence yet."


def test_build_repo_dashboard_view_uses_history_target_when_pr_evidence_is_missing(tmp_path):
    db_path = str(tmp_path / "dashboard-history-only.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )
    plan_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-2", "sha-1"][:limit],
    )
    execute_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        fetch_file_content_fn=lambda repo, path, token, ref: {
            "sha-1": PROMPT_BASELINE,
            "sha-2": PROMPT_CURRENT,
        }[ref],
    )

    dashboard = build_repo_dashboard_view(db_path, "doria90/dummyAI")

    assert dashboard.insights[0].evidence_label == "history only"
    assert dashboard.insights[0].evidence_summary == "Only merged-history evidence is available right now; start with commit sha-2."
    assert dashboard.insights[0].review_target == "commit sha-2"
    assert dashboard.insights[0].review_url == "https://github.com/doria90/dummyAI/commit/sha-2"
    assert dashboard.insights[0].supporting_review_target is None
    assert "history-only evidence" in dashboard.insights[0].risk_reasons