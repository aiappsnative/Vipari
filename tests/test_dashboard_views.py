import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.audit_jobs import init_db
from services.audit_records import record_audit_result
from services.dashboard_views import build_dashboard_overview_view, build_repo_dashboard_view, list_repo_dashboard_index
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
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_CURRENT,
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
    assert dashboard.backfill.total_historical_versions == 2
    assert dashboard.backfill.total_historical_profiles == 2
    assert dashboard.pull_request_audit_count == 1
    assert dashboard.drift_summary.profile_count == 1
    assert len(dashboard.artifacts) == 1
    assert len(dashboard.insights) == 1
    assert dashboard.insights[0].artifact_path == "prompts/refund.txt"
    assert dashboard.insights[0].priority in {"review_now", "watch", "baseline_review"}
    assert len(dashboard.control_surface_groups) == 1
    assert dashboard.control_surface_groups[0].group_key == "prompts"
    assert len(dashboard.history_timelines) == 1
    assert dashboard.history_timelines[0].artifact_path == "prompts/refund.txt"
    assert dashboard.history_timelines[0].point_count == 3
    assert dashboard.artifacts[0].artifact_path == "prompts/refund.txt"
    assert dashboard.artifacts[0].historical_version_count == 2
    assert dashboard.artifacts[0].pr_profile_count == 1


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
        repo_full="doria90/repo-one",
        installation_id=1,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/a.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_CURRENT,
    )
    plan_repository_history_backfill(
        db_path,
        repo_full="doria90/repo-one",
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-2", "sha-1"][:limit],
    )
    execute_repository_history_backfill(
        db_path,
        repo_full="doria90/repo-one",
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
    assert overview.metrics[0].label == "Onboarded repositories"
    assert overview.metrics[0].value == 2
    assert len(overview.attention_repos) == 2
    assert overview.attention_repos[0].repo_full == "doria90/repo-one"
    assert overview.attention_repos[0].highest_priority in {"review_now", "watch", "baseline_review"}
    assert any(group.group_key == "prompts" for group in overview.control_surface_coverage)
    assert [repo.repo_full for repo in overview.repos] == ["doria90/repo-one", "doria90/repo-two"]