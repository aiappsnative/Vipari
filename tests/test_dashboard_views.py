import os
import sys
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from engine.drift_profile import AgentAttributeProfile, StaticSignals
from services.audit_jobs import init_db
from services.audit_records import RepoStaticDriftSummary, record_audit_result
from services.dashboard_views import DashboardOverviewRiskState, DashboardOverviewView, DriftEpisode, RepoDashboardArtifactEntry, RepoDashboardBackfillSummary, RepoDashboardView, _RepoArtifactEvidenceBundle, _RepoArtifactProfileContext, _build_repo_history_cues, _collapse_storyline_episodes, _insight_title, build_artifact_attribute_profile, build_dashboard_overview_view, build_repo_dashboard_view, invalidate_dashboard_caches, list_repo_dashboard_index
from services.governance_signals import build_repo_governance_posture
from services.signal_fusion import priority_from_fused_signals, priority_sort_rank, priority_weighted_risk
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from services.branch_scan_jobs import create_branch_scan_job
from services.branch_scan_worker import BranchScanWorkerSettings, process_branch_scan_job
from services.repo_journey import build_repo_journey


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


def _profile() -> AgentAttributeProfile:
    return AgentAttributeProfile(
        guardrail_robustness=0.7,
        capability_risk=0.2,
        autonomy_level=0.3,
        stability_vs_creativity=0.8,
        governance_strength=0.6,
        change_frequency=0.1,
        semantic_density=0.4,
        signals=StaticSignals(
            token_count=10,
            char_count=40,
            section_count=1,
            example_count=0,
            instruction_density=0.2,
            constraint_count=2,
            explicit_limit_count=1,
            ambiguity_count=0,
        ),
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
    assert dashboard.onboarding.status == "baseline_approved"
    assert dashboard.baseline_review is not None
    assert dashboard.baseline_review.is_pending_review is False
    assert dashboard.baseline_review.authoritative_artifact_count == 1
    assert isinstance(dashboard.baseline_review.recent_decisions, list)
    assert dashboard.artifacts[0].provenance_label == "AI control surface"
    assert dashboard.baseline_version_count == 1
    assert dashboard.backfill.completed_job_count == 1
    assert dashboard.backfill.total_historical_versions == 3
    assert dashboard.backfill.total_historical_profiles == 3
    assert dashboard.pull_request_audit_count == 1
    assert dashboard.drift_summary.profile_count == 3
    assert len(dashboard.artifacts) == 1
    assert len(dashboard.insights) == 1
    assert len(dashboard.lower_confidence_insights) == 0
    assert dashboard.insights[0].artifact_path == "prompts/refund.txt"
    assert dashboard.insights[0].queue_lane == "primary"
    assert dashboard.insights[0].priority in {"review_now", "watch", "baseline_review"}
    assert dashboard.insights[0].confidence_label in {"high confidence", "medium confidence", "lower confidence"}
    assert len(dashboard.insights[0].attribute_profile) == 6
    assert dashboard.insights[0].evidence_label == "proposal + history"
    assert dashboard.insights[0].evidence_summary == "PR proposal evidence is available right now; start with PR #42, then compare against merged history from commit sha-3."
    assert dashboard.insights[0].baseline_label.startswith("Baseline: Approved")
    assert dashboard.insights[0].provenance_summary == "From · PR #42 · full semantic review · semantic complete · risk low · supporting merged history commit sha-3"
    assert dashboard.insights[0].review_target == "PR #42"
    assert dashboard.insights[0].review_url == "https://github.com/doria90/dummyAI/pull/42"
    assert dashboard.insights[0].supporting_review_target == "commit sha-3"
    assert dashboard.insights[0].supporting_review_url == "https://github.com/doria90/dummyAI/commit/sha-3"
    assert dashboard.insights[0].change_summary
    assert dashboard.insights[0].flag_summary.startswith("Flagged because")
    assert dashboard.insights[0].rationale == "The current PR proposal broadens authority relative to the current baseline, increasing blast radius and review urgency."
    assert dashboard.insights[0].recommended_action == "Escalate this surface to the AI platform owner, inspect the linked PR first, then compare it against the supporting merged history."
    assert "historical hotspot" in dashboard.insights[0].risk_reasons
    assert "proposal evidence" in dashboard.insights[0].risk_reasons
    assert "history-backed" in dashboard.insights[0].risk_reasons
    assert len(dashboard.control_surface_groups) == 1
    assert dashboard.control_surface_groups[0].group_key == "prompts"
    assert len(dashboard.history_timelines) == 1
    assert dashboard.history_timelines[0].artifact_path == "prompts/refund.txt"
    assert dashboard.history_timelines[0].point_count == 3
    assert dashboard.featured_storyline is not None
    assert dashboard.featured_storyline.artifact_path == "prompts/refund.txt"
    assert dashboard.featured_storyline.summary
    assert dashboard.featured_storyline.current_posture_label.startswith("Current posture:")
    assert dashboard.featured_storyline.episodes[0].episode_type == "baseline_milestone"
    assert dashboard.featured_storyline.episodes[-1].episode_type == "current_posture"
    assert len(dashboard.history_cues) >= 1
    assert dashboard.history_cues[0].artifact_paths[0] == "prompts/refund.txt"
    assert dashboard.governance_posture.review_quality in {"adequate", "mixed", "weak for recent high-risk change"}
    assert isinstance(dashboard.governance_posture.top_governance_anomalies, tuple)
    assert dashboard.journey_snapshots[0]["snapshot_type"] == "baseline_approved"
    assert dashboard.journey_snapshots[0]["input_summary"]["baseline_verified"] is True
    assert dashboard.journey_snapshots[-1]["snapshot_type"] == "current"
    assert dashboard.journey_comparison is not None
    assert dashboard.journey_comparison["comparison_kind"] == "baseline_vs_current"
    assert len(dashboard.design_profiles) == 1
    assert dashboard.design_profiles[0].artifact_path == "prompts/refund.txt"
    assert dashboard.design_profiles[0].baseline_provenance is not None
    assert dashboard.design_profiles[0].baseline_provenance.source_type == "approved_baseline"
    assert dashboard.design_profiles[0].baseline_provenance.is_authoritative is True
    assert dashboard.design_profiles[0].provenance is not None
    assert dashboard.design_profiles[0].provenance.source_type == "historical"
    assert dashboard.design_profiles[0].provenance.label == "Historical backfill"
    assert dashboard.design_profiles[0].provenance.source_ref == "commit sha-3"
    assert dashboard.design_profiles[0].provenance.source_url == "https://github.com/doria90/dummyAI/commit/sha-3"
    assert dashboard.design_profiles[0].provenance.review_context == "Historical snapshot from backfill"
    assert dashboard.design_profiles[0].headline_summary
    assert dashboard.design_profiles[0].drift_label in {"small drift", "medium drift", "large drift"}
    assert dashboard.design_profiles[0].drift_tone in {"low", "medium", "high"}
    assert dashboard.design_profiles[0].can_promote_source_to_baseline is True
    assert len(dashboard.design_profiles[0].attribute_profile) == 6
    assert any(
        dimension.attribute_key == "model_config_posture"
        for dimension in dashboard.design_profiles[0].attribute_profile
    )
    assert any(
        dimension.attribute_key == "control_surface_type"
        for dimension in dashboard.design_profiles[0].attribute_profile
    )
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
    assert dashboard.history_timelines[0].points[-1].baseline_provenance.is_authoritative is True
    assert dashboard.history_timelines[0].points[0].label == "Historical backfill"
    assert dashboard.history_timelines[0].points[0].source_ref == "commit sha-1"
    assert dashboard.history_timelines[0].points[0].source_url == "https://github.com/doria90/dummyAI/commit/sha-1"
    assert dashboard.history_timelines[0].points[0].review_context == "Historical snapshot from backfill"
    assert dashboard.history_timelines[0].points[-1].label == "Historical backfill"
    assert dashboard.history_timelines[0].points[-1].source_ref == "commit sha-3"
    assert dashboard.history_timelines[0].points[-1].source_url == "https://github.com/doria90/dummyAI/commit/sha-3"
    assert dashboard.history_timelines[0].points[-1].review_context == "Historical snapshot from backfill"


def test_build_repo_governance_posture_stays_neutral_when_repo_has_no_design_profiles():
    posture = build_repo_governance_posture(
        "doria90/empty-repo",
        design_profiles=[],
        artifacts=[],
        history_cues=[],
        insights=[],
    )

    assert posture.ownership_confidence == "established"
    assert posture.review_quality == "adequate"
    assert posture.repeated_drift_without_refresh_count == 0
    assert posture.baseline_freshness_status == "current"
    assert posture.top_governance_anomalies == ()


def test_live_branch_head_scan_becomes_current_repo_journey_checkpoint(tmp_path):
    db_path = str(tmp_path / "dashboard-live-head.db")
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
    job = create_branch_scan_job(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        commit_sha="livehead1",
        branch_ref="refs/heads/main",
        triggered_by="push_webhook",
    )

    with patch("services.branch_scan_worker.generate_jwt", return_value="jwt-token"), patch(
        "services.branch_scan_worker.get_installation_token", return_value="installation-token"
    ), patch(
        "services.branch_scan_worker.fetch_file_content", return_value=PROMPT_CURRENT
    ):
        result = process_branch_scan_job(
            job,
            BranchScanWorkerSettings(
                db_path=db_path,
                github_app_id="app-id",
                github_private_key_path="/tmp/test-key.pem",
            ),
        )

    assert result in {"completed", "completed_with_updates"}
    snapshots = build_repo_journey(db_path, "doria90/dummyAI")
    assert snapshots[0].snapshot_type == "baseline_approved"
    assert snapshots[-1].snapshot_type == "branch_head"
    assert snapshots[-1].commit_sha == "livehead1"
    assert snapshots[-1].input_summary["baseline_verified"] is True

    dashboard = build_repo_dashboard_view(db_path, "doria90/dummyAI")
    assert dashboard.journey_snapshots[-1]["snapshot_type"] == "branch_head"
    assert dashboard.journey_snapshots[-1]["source_ref"] == "main @ livehea"
    assert dashboard.journey_comparison is not None


def test_live_branch_head_scan_removes_deleted_artifacts(tmp_path):
    db_path = str(tmp_path / "dashboard-live-head-delete.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt", "config/policy.yml"],
        fetch_file_content_fn=lambda repo, path, token, ref: {
            "prompts/refund.txt": PROMPT_BASELINE,
            "config/policy.yml": "policy: strict\n",
        }[path],
    )

    job = create_branch_scan_job(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        commit_sha="livehead-delete",
        branch_ref="refs/heads/main",
        triggered_by="push_webhook",
    )

    def _fetch_content(repo, path, token, ref):
        if path == "config/policy.yml":
            raise HTTPError(f"https://example.test/{path}", 404, "Not Found", hdrs=None, fp=None)
        return PROMPT_CURRENT

    with patch("services.branch_scan_worker.generate_jwt", return_value="jwt-token"), patch(
        "services.branch_scan_worker.get_installation_token", return_value="installation-token"
    ), patch("services.branch_scan_worker.fetch_file_content", side_effect=_fetch_content):
        result = process_branch_scan_job(
            job,
            BranchScanWorkerSettings(
                db_path=db_path,
                github_app_id="app-id",
                github_private_key_path="/tmp/test-key.pem",
            ),
        )

    assert result in {"completed", "completed_with_updates"}
    dashboard = build_repo_dashboard_view(db_path, "doria90/dummyAI")
    assert [artifact.artifact_path for artifact in dashboard.artifacts] == ["prompts/refund.txt"]
    assert dashboard.onboarding is not None
    assert dashboard.onboarding.discovered_artifact_count == 1

def test_priority_from_fused_signals_raises_dashboard_priority_for_high_risk_audits():
    assert priority_from_fused_signals(0.41, risk_level="High") == "review_now"
    assert priority_from_fused_signals(0.41, risk_level="Medium") == "watch"
    assert priority_from_fused_signals(1.31, risk_level="Low") == "review_now"


def test_priority_sort_rank_matches_dashboard_lane_order():
    assert priority_sort_rank("review_now") == 0
    assert priority_sort_rank("watch") == 1
    assert priority_sort_rank("baseline_review") == 2
    assert priority_sort_rank("unexpected") == 9


def test_priority_weighted_risk_biases_toward_review_now_groups():
    assert priority_weighted_risk(0.4, "review_now") == 1.4
    assert priority_weighted_risk(0.4, "watch") == 0.75
    assert priority_weighted_risk(0.4, "baseline_review") == 0.4


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


def test_list_repo_dashboard_index_carries_repo_scope_metadata(tmp_path):
    db_path = str(tmp_path / "dashboard-scope.db")
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
        installation_id=1,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/b.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: "Use judgment carefully.",
    )

    index = list_repo_dashboard_index(
        db_path,
        allowed_repo_fulls={"doria90/repo-one", "doria90/repo-two"},
        repo_scope_by_full={"doria90/repo-one": "allocated", "doria90/repo-two": "connected_history"},
        allocation_status_by_full={"doria90/repo-one": "onboarded"},
    )

    assert [(entry.repo_full, entry.dashboard_scope, entry.allocation_status) for entry in index] == [
        ("doria90/repo-one", "allocated", "onboarded"),
        ("doria90/repo-two", "connected_history", None),
    ]


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
    assert overview.control_surface_risk[0].group_key == "prompts"
    assert overview.control_surface_risk[0].review_now_artifact_count >= 1
    assert len(overview.regression_patterns) >= 1
    assert overview.metrics[0].label == "Onboarded repositories"
    assert overview.metrics[0].value == 2
    assert overview.repos[0].historical_version_count >= 1
    assert len(overview.attention_repos) == 2
    assert overview.attention_repos[0].repo_full == "doria90/dummyAI"
    assert overview.attention_repos[0].highest_priority in {"review_now", "watch", "baseline_review"}
    assert overview.attention_repos[0].highest_evidence_label == "proposal + history"
    assert overview.attention_repos[0].highest_evidence_summary == "PR proposal evidence is available right now; start with PR #42, then compare against merged history from commit sha-1."
    assert overview.attention_repos[0].highest_baseline_label is not None
    assert overview.attention_repos[0].highest_review_target == "PR #42"
    assert overview.attention_repos[0].highest_review_url == "https://github.com/doria90/dummyAI/pull/42"
    assert overview.attention_repos[0].highest_change_summary
    assert (overview.attention_repos[0].highest_flag_summary or "").startswith("Flagged because")
    assert overview.attention_repos[0].lower_confidence_count == 0
    assert overview.highest_risk_items[0].baseline_label.startswith("Baseline: Approved")
    assert overview.highest_risk_items[0].evidence_label == "proposal + history"
    assert overview.highest_risk_items[0].evidence_summary == "PR proposal evidence is available right now; start with PR #42, then compare against merged history from commit sha-1."
    assert overview.highest_risk_items[0].review_target == "PR #42"
    assert overview.highest_risk_items[0].review_url == "https://github.com/doria90/dummyAI/pull/42"
    assert overview.highest_risk_items[0].change_summary
    assert overview.highest_risk_items[0].flag_summary.startswith("Flagged because")
    assert len(overview.highest_risk_items[0].attribute_profile) == 6
    assert overview.overview_sections.urgent_queue.repo_count >= 1
    assert overview.overview_sections.urgent_queue.repos[0].repo_full == "doria90/dummyAI"
    assert overview.overview_sections.recent_changes.repo_count == 2
    assert overview.overview_sections.recent_changes.repos[0].highest_priority in {"review_now", "watch", "baseline_review"}
    assert overview.overview_sections.posture_snapshot.risk_state is not None
    assert overview.overview_sections.posture_snapshot.metrics[0].label == "Onboarded repositories"


def test_build_dashboard_overview_view_skips_repo_journey_materialization(tmp_path, monkeypatch):
    db_path = str(tmp_path / "overview-no-journey.db")
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

    def fail_if_called(*args, **kwargs):
        raise AssertionError("repo journey materialization should not run when building the portfolio overview")

    monkeypatch.setattr("services.dashboard_views._build_repo_journey_panel", fail_if_called)

    overview = build_dashboard_overview_view(db_path)

    assert overview.metrics[0].value == 1
    assert overview.repos[0].repo_full == "doria90/dummyAI"
    assert len(overview.attention_repos) == 1
    assert overview.overview_sections.governance_attention.repos_with_anomalies_count >= 0
    assert isinstance(overview.overview_sections.governance_attention.ranked_issues_now, tuple)
    assert any(group.group_key == "prompts" for group in overview.control_surface_coverage)
    assert [repo.repo_full for repo in overview.repos] == ["doria90/dummyAI"]


def test_build_dashboard_overview_view_skips_repo_detail_section_materialization(tmp_path, monkeypatch):
    db_path = str(tmp_path / "overview-no-detail-sections.db")
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

    def fail_if_called(*args, **kwargs):
        raise AssertionError("repo detail sections should not materialize when building the portfolio overview")

    monkeypatch.setattr("services.dashboard_views._build_repo_history_timelines", fail_if_called)
    monkeypatch.setattr("services.dashboard_views._build_featured_storyline", fail_if_called)
    monkeypatch.setattr("services.dashboard_views._build_repo_history_cues", fail_if_called)
    monkeypatch.setattr("services.dashboard_views._build_repo_design_profiles", fail_if_called)

    overview = build_dashboard_overview_view(db_path)

    assert overview.metrics[0].value == 1
    assert overview.repos[0].repo_full == "doria90/dummyAI"
    assert len(overview.attention_repos) == 1


def test_build_dashboard_overview_view_does_not_materialize_full_repo_views(tmp_path, monkeypatch):
    db_path = str(tmp_path / "overview-no-full-repo-views.db")
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

    def fail_if_called(*args, **kwargs):
        raise AssertionError("portfolio overview should use its batched summary path instead of materializing full repo views")

    monkeypatch.setattr("services.dashboard_views.build_repo_dashboard_view", fail_if_called)

    overview = build_dashboard_overview_view(db_path)

    assert overview.metrics[0].value == 1
    assert overview.repos[0].repo_full == "doria90/dummyAI"


def test_build_dashboard_overview_view_reuses_cached_result_for_same_db_signature(tmp_path, monkeypatch):
    db_path = str(tmp_path / "overview-cache.db")
    init_db(db_path)

    call_count = 0

    def fake_uncached(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return DashboardOverviewView(
            risk_state=DashboardOverviewRiskState(
                status="baseline",
                headline="Stable",
                summary="cached",
                review_now_repo_count=0,
                watch_repo_count=0,
                baseline_review_repo_count=0,
                highest_risk_repo_full=None,
                highest_risk_artifact_path=None,
                highest_risk_title=None,
                highest_drift_magnitude=0.0,
            ),
            metrics=[],
            regression_patterns=[],
            highest_risk_items=[],
            control_surface_risk=[],
            attention_repos=[],
            control_surface_coverage=[],
            repos=[],
        )

    monkeypatch.setattr("services.dashboard_views._build_dashboard_overview_view_uncached", fake_uncached)

    first = build_dashboard_overview_view(db_path)
    second = build_dashboard_overview_view(db_path)

    assert first is second
    assert call_count == 1


def test_build_repo_dashboard_view_postgres_cache_refreshes_after_ttl(monkeypatch):
    db_path = "postgresql://user:pass@db.example.com/driftguard"
    call_count = 0

    invalidate_dashboard_caches()

    def fake_uncached(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        return RepoDashboardView(
            repo_full="doria90/dummyAI",
            onboarding=None,
            baseline_review=None,
            backfill=RepoDashboardBackfillSummary(0, 0, 0, 0, 0, 0, 0),
            pull_request_audit_count=call_count,
            baseline_version_count=0,
            drift_summary=RepoStaticDriftSummary("doria90/dummyAI", 0, 0, 0, 0.0, 0.0, 0.0, 0.0, None, 0.0),
            top_drifting_artifacts=[],
            insights=[],
            lower_confidence_insights=[],
            control_surface_groups=[],
            history_timelines=[],
            featured_storyline=None,
            history_cues=[],
            design_profiles=[],
            governance_posture=None,
            audit_brief=None,
            artifacts=[],
            journey_snapshots=[],
            journey_comparison=None,
            selected_baseline_source_snapshot_id=None,
            export_jobs=[],
        )

    monotonic_values = iter([100.0, 101.0, 111.0])
    monkeypatch.setattr("services.dashboard_views._build_repo_dashboard_view_uncached", fake_uncached)
    monkeypatch.setattr("services.dashboard_views.time.monotonic", lambda: next(monotonic_values))

    first = build_repo_dashboard_view(db_path, "doria90/dummyAI")
    second = build_repo_dashboard_view(db_path, "doria90/dummyAI")
    third = build_repo_dashboard_view(db_path, "doria90/dummyAI")

    assert first is second
    assert third is not second
    assert first.pull_request_audit_count == 1
    assert third.pull_request_audit_count == 2
    assert call_count == 2


def test_build_repo_dashboard_view_reuses_cached_result_for_same_db_signature(tmp_path, monkeypatch):
    db_path = str(tmp_path / "repo-cache.db")
    init_db(db_path)

    call_count = 0

    def fake_uncached(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return RepoDashboardView(
            repo_full="doria90/dummyAI",
            onboarding=None,
            baseline_review=None,
            backfill=RepoDashboardBackfillSummary(
                job_count=0,
                planned_job_count=0,
                processing_job_count=0,
                completed_job_count=0,
                failed_job_count=0,
                total_historical_versions=0,
                total_historical_profiles=0,
            ),
            pull_request_audit_count=0,
            baseline_version_count=0,
            drift_summary=RepoStaticDriftSummary(
                repo_full="doria90/dummyAI",
                artifact_count=0,
                profile_count=0,
                baseline_linked_profile_count=0,
                avg_semantic_distance=0.0,
                avg_guardrail_shift=0.0,
                avg_capability_shift=0.0,
                avg_autonomy_shift=0.0,
                highest_capability_artifact_path=None,
                highest_capability_delta=0.0,
            ),
            top_drifting_artifacts=[],
            insights=[],
            lower_confidence_insights=[],
            control_surface_groups=[],
            history_timelines=[],
            featured_storyline=None,
            history_cues=[],
            design_profiles=[],
            artifacts=[],
            journey_snapshots=[],
            journey_comparison=None,
        )

    monkeypatch.setattr("services.dashboard_views._build_repo_dashboard_view_uncached", fake_uncached)

    first = build_repo_dashboard_view(db_path, "doria90/dummyAI")
    second = build_repo_dashboard_view(db_path, "doria90/dummyAI")

    assert first is second
    assert call_count == 1


def test_collapse_storyline_episodes_groups_adjacent_low_signal_history_events():
    episodes = [
        DriftEpisode(
            episode_timestamp=1.0,
            source_type="baseline_promotion",
            source_label="Approved baseline",
            source_ref="baseline 1",
            episode_type="baseline_milestone",
            top_attributes=[],
            episode_summary="Baseline set.",
            severity="low",
            confidence="authoritative baseline",
            is_milestone=True,
        ),
        DriftEpisode(
            episode_timestamp=2.0,
            source_type="historical_backfill",
            source_label="Historical backfill",
            source_ref="commit aaa1111",
            source_url="https://github.com/doria90/dummyAI/commit/aaa1111",
            episode_type="capability_expansion",
            top_attributes=["Capability"],
            episode_summary="Capability expanded relative to baseline.",
            severity="medium",
            confidence="high confidence",
        ),
        DriftEpisode(
            episode_timestamp=3.0,
            source_type="historical_backfill",
            source_label="Historical backfill",
            source_ref="commit bbb2222",
            source_url="https://github.com/doria90/dummyAI/commit/bbb2222",
            episode_type="capability_expansion",
            top_attributes=["Capability"],
            episode_summary="Capability expanded again.",
            severity="medium",
            confidence="high confidence",
        ),
        DriftEpisode(
            episode_timestamp=4.0,
            source_type="historical",
            source_label="Current posture",
            source_ref="commit ccc3333",
            source_url="https://github.com/doria90/dummyAI/commit/ccc3333",
            episode_type="current_posture",
            top_attributes=["Capability"],
            episode_summary="Latest posture.",
            severity="high",
            confidence="high confidence",
            is_milestone=True,
        ),
    ]

    collapsed = _collapse_storyline_episodes(episodes)

    assert len(collapsed) == 3
    assert collapsed[0].episode_type == "baseline_milestone"
    assert collapsed[1].source_label == "Grouped historical drift"
    assert collapsed[1].episode_type == "capability_expansion"
    assert collapsed[1].source_ref == "commit aaa1111 -> commit bbb2222"
    assert "Review this as one continuing capability expansion pattern" in collapsed[1].episode_summary
    assert collapsed[-1].episode_type == "current_posture"


def test_build_repo_history_cues_uses_baseline_age_and_provenance_gaps():
    artifacts = [
        RepoDashboardArtifactEntry(
            artifact_path="prompts/a.txt",
            artifact_type="prompt",
            discovery_reason="prompt",
            discovery_confidence=0.9,
            baseline_line_count=10,
            historical_version_count=3,
            historical_profile_count=3,
            latest_historical_semantic_distance=0.2,
            latest_historical_drift_magnitude=0.6,
            latest_historical_capability_shift=0.1,
            latest_historical_guardrail_shift=-0.06,
            latest_historical_governance_shift=0.0,
            latest_historical_autonomy_shift=0.0,
            pr_profile_count=0,
            latest_pr_semantic_distance=0.0,
            latest_pr_capability_shift=0.0,
            latest_pr_guardrail_shift=0.0,
            latest_pr_governance_shift=0.0,
            latest_pr_autonomy_shift=0.0,
            leaderboard_drift_magnitude=0.6,
            latest_activity_at=4_000.0,
        ),
        RepoDashboardArtifactEntry(
            artifact_path="prompts/b.txt",
            artifact_type="prompt",
            discovery_reason="prompt",
            discovery_confidence=0.82,
            baseline_line_count=12,
            historical_version_count=1,
            historical_profile_count=1,
            latest_historical_semantic_distance=0.1,
            latest_historical_drift_magnitude=0.2,
            latest_historical_capability_shift=0.0,
            latest_historical_guardrail_shift=0.0,
            latest_historical_governance_shift=0.0,
            latest_historical_autonomy_shift=0.0,
            pr_profile_count=0,
            latest_pr_semantic_distance=0.0,
            latest_pr_capability_shift=0.0,
            latest_pr_guardrail_shift=0.0,
            latest_pr_governance_shift=0.0,
            latest_pr_autonomy_shift=0.0,
            leaderboard_drift_magnitude=0.2,
            latest_activity_at=2_000.0,
        ),
    ]
    baseline_by_path = {
        "prompts/a.txt": type("Baseline", (), {"created_at": 100.0})(),
        "prompts/b.txt": type("Baseline", (), {"created_at": 1_900.0})(),
    }
    profile_context_by_path = {
        "prompts/a.txt": _RepoArtifactEvidenceBundle(
            latest_historical=_RepoArtifactProfileContext(
                profile=None,
                source_type="historical",
                label="Historical backfill",
                source_ref="commit aaa1111",
                source_url=None,
                review_context="Historical snapshot from backfill",
                created_at=4_000.0,
                baseline_provenance=None,
                semantic_distance=0.2,
                attribute_deltas={"capability_risk": 0.1, "guardrail_robustness": -0.06},
                narrative=["Historical drift persisted."],
                signal_terms=[],
                content_text=None,
            )
        ),
        "prompts/b.txt": _RepoArtifactEvidenceBundle(
            latest_historical=_RepoArtifactProfileContext(
                profile=None,
                source_type="historical",
                label="Historical backfill",
                source_ref="commit bbb2222",
                source_url="https://github.com/doria90/dummyAI/commit/bbb2222",
                review_context="Historical snapshot from backfill",
                created_at=2_000.0,
                baseline_provenance=None,
                semantic_distance=0.05,
                attribute_deltas={"capability_risk": 0.0},
                narrative=["Minor change."],
                signal_terms=[],
                content_text=None,
            )
        ),
    }

    cues = _build_repo_history_cues(artifacts, baseline_by_path, profile_context_by_path)

    assert any(cue.label == "Baseline aging" for cue in cues)
    assert any(cue.label == "Provenance gaps" for cue in cues)
    baseline_aging = next(cue for cue in cues if cue.label == "Baseline aging")
    assert baseline_aging.artifact_paths[0] == "prompts/a.txt"
    provenance_gaps = next(cue for cue in cues if cue.label == "Provenance gaps")
    assert provenance_gaps.artifact_paths == ["prompts/a.txt"]


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
    assert dashboard.featured_storyline is not None
    assert dashboard.featured_storyline.limited_history_note is not None
    assert dashboard.design_profiles[0].provenance is None
    assert dashboard.design_profiles[0].can_promote_source_to_baseline is False
    assert len(dashboard.design_profiles[0].attribute_profile) == 6
    assert len(dashboard.insights[0].attribute_profile) == 6
    assert dashboard.insights[0].evidence_label == "baseline only"
    capability = next(
        dimension
        for dimension in dashboard.insights[0].attribute_profile
        if dimension.attribute_key == "capability_risk"
    )
    assert capability.baseline_value in {"low", "moderate", "high"}
    assert capability.current_value == "unknown"
    assert capability.state == "unknown"
    assert capability.confidence_label == "lower confidence"


def test_build_artifact_attribute_profile_degrades_with_partial_profile_data():
    profile = build_artifact_attribute_profile(
        artifact_path="prompts/refund.txt",
        artifact_type="prompt",
        baseline_profile=None,
        current_profile=_profile(),
        attribute_deltas={"capability_risk": 0.3},
        current_signal_terms=["refund"],
        current_content=PROMPT_CURRENT,
    )

    assert len(profile.dimensions) == 6
    guardrails = next(dimension for dimension in profile.dimensions if dimension.attribute_key == "guardrail_robustness")
    assert guardrails.baseline_value == "unknown"
    assert guardrails.current_value in {"strong", "moderate", "weak"}
    assert guardrails.state == "unknown"
    assert guardrails.confidence_label == "lower confidence"


def test_build_repo_dashboard_view_groups_low_signal_text_artifacts_into_one_queue_item(tmp_path):
    db_path = str(tmp_path / "dashboard-grouped-low-signal.db")
    init_db(db_path)

    files = {
        "notes/assistant-checklist.md": "Assistant operator checklist.",
        "guides/assistant-faq.md": "Assistant frequently asked questions.",
        "config/assistant-index.json": '{"assistant": "routing notes"}',
        "prompts/system.txt": PROMPT_BASELINE,
    }

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: list(files.keys()),
        fetch_file_content_fn=lambda repo, path, token, ref: files[path],
    )

    dashboard = build_repo_dashboard_view(db_path, "doria90/dummyAI")

    assert dashboard.onboarding is not None
    assert dashboard.onboarding.discovered_artifact_count == 2
    assert {artifact.artifact_path for artifact in dashboard.artifacts} == {"guides/assistant-faq.md", "prompts/system.txt"}
    assert len(dashboard.insights) == 1
    assert dashboard.insights[0].artifact_path == "prompts/system.txt"
    assert len(dashboard.lower_confidence_insights) == 1
    assert dashboard.lower_confidence_insights[0].artifact_path == "guides/assistant-faq.md"
    assert len(dashboard.control_surface_groups) == 2


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
    assert dashboard.insights[0].rationale == "A critical control surface broadened authority relative to the current baseline, increasing blast radius and review urgency."
    assert dashboard.insights[0].recommended_action == "Escalate this surface to the AI platform owner and inspect the linked merged commit first."
    assert "history-only evidence" in dashboard.insights[0].risk_reasons
    assert dashboard.featured_storyline is not None
    assert dashboard.featured_storyline.episodes[1].source_ref == "commit sha-1"


def test_build_repo_dashboard_view_uses_pr_target_when_only_proposal_evidence_exists(tmp_path):
    db_path = str(tmp_path / "dashboard-pr-only.db")
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
    _record_pr_profile(db_path)

    dashboard = build_repo_dashboard_view(db_path, "doria90/dummyAI")

    assert dashboard.insights[0].priority == "review_now"
    assert dashboard.insights[0].title == "Critical control surface expanded authority"
    assert dashboard.insights[0].evidence_label == "proposal only"
    assert dashboard.insights[0].evidence_summary == "Only PR proposal evidence is available right now; start with PR #42."
    assert dashboard.insights[0].review_target == "PR #42"
    assert dashboard.insights[0].review_url == "https://github.com/doria90/dummyAI/pull/42"
    assert dashboard.insights[0].supporting_review_target is None
    assert dashboard.insights[0].supporting_review_url is None
    assert dashboard.insights[0].rationale == "The current PR proposal broadens authority relative to the current baseline, increasing blast radius and review urgency."
    assert dashboard.insights[0].recommended_action == "Escalate this surface to the AI platform owner and inspect the linked PR before accepting the change."
    assert "proposal-only evidence" in dashboard.insights[0].risk_reasons


def test_insight_title_uses_proposal_wording_for_pr_only_drift_hotspots():
    artifact = RepoDashboardArtifactEntry(
        artifact_path="prompts/refund.txt",
        artifact_type="prompt",
        discovery_reason="ai_keyword",
        discovery_confidence=0.92,
        baseline_line_count=12,
        historical_version_count=0,
        historical_profile_count=0,
        latest_historical_semantic_distance=0.0,
        latest_historical_drift_magnitude=0.0,
        latest_historical_capability_shift=0.0,
        latest_historical_guardrail_shift=0.0,
        latest_historical_governance_shift=0.0,
        latest_historical_autonomy_shift=0.0,
        pr_profile_count=1,
        latest_pr_semantic_distance=0.34,
        latest_pr_capability_shift=0.01,
        latest_pr_guardrail_shift=0.0,
        latest_pr_governance_shift=0.0,
        latest_pr_autonomy_shift=0.02,
        leaderboard_drift_magnitude=0.0,
        latest_activity_at=1.0,
        provenance_kind="ai_control_surface",
        provenance_label="AI control surface",
    )
    evidence_bundle = _RepoArtifactEvidenceBundle(
        latest_pull_request=_RepoArtifactProfileContext(
            profile=_profile(),
            source_type="pull_request",
            label="Pull request proposal",
            source_ref="PR #42",
            source_url="https://github.com/doria90/dummyAI/pull/42",
            review_context="full semantic review · semantic complete · risk low",
            created_at=1.0,
            baseline_provenance=None,
            semantic_distance=0.34,
            attribute_deltas={
                "capability_risk": 0.01,
                "guardrail_robustness": 0.0,
                "governance_strength": 0.0,
                "autonomy_level": 0.02,
            },
            narrative=[],
            signal_terms=[],
            content_text=PROMPT_CURRENT,
        ),
    )

    assert _insight_title(artifact, "watch", evidence_bundle) == "Design drift hotspot needs review"