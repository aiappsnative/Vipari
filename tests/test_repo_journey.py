import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.audit_jobs import init_db
from services.audit_records import record_audit_result
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from services.repo_journey import build_repo_journey, compare_repo_snapshots, get_repo_snapshot_detail
from services.repo_journey_records import list_repo_posture_snapshots_for_repo


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


def _record_merged_pr_profile(db_path: str):
    analysis = analyze_diff(PROMPT_DIFF)
    merged_at = 2_000_000_000.0
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
        pr_state="closed",
        pr_merged=True,
        pr_merged_at=merged_at,
        pr_merge_commit_sha="sha-merge",
        pr_updated_at=merged_at,
    )


def _seed_repo_history(db_path: str):
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
            "sha-2": PROMPT_MEDIUM,
        }[ref],
    )
    _record_merged_pr_profile(db_path)


def test_build_repo_journey_materializes_meaningful_snapshots(tmp_path):
    db_path = str(tmp_path / "journey.db")
    init_db(db_path)
    _seed_repo_history(db_path)

    snapshots = build_repo_journey(db_path, "doria90/dummyAI")

    assert snapshots[0].snapshot_type == "baseline_approved"
    assert snapshots[0].input_summary["baseline_verified"] is False
    assert snapshots[-1].snapshot_type == "current"
    assert any(snapshot.snapshot_type == "historical_commit" for snapshot in snapshots)
    assert any(snapshot.snapshot_type == "merge" for snapshot in snapshots)
    assert snapshots[0].artifact_coverage["artifact_count"] == 1
    assert snapshots[-1].distance_from_baseline >= snapshots[1].distance_from_baseline
    assert snapshots[-1].drift_summary["changed_since_baseline"]["critical_surfaces_changed"] >= 1
    assert snapshots[-1].risk_summary["risk_level"] in {"low", "medium", "high"}


def test_onboarding_and_backfill_persist_repo_journey_without_read_trigger(tmp_path):
    db_path = str(tmp_path / "journey-write-through.db")
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

    snapshots_after_onboarding = list_repo_posture_snapshots_for_repo(db_path, "doria90/dummyAI")
    assert [snapshot.snapshot_type for snapshot in snapshots_after_onboarding] == ["baseline_approved", "current"]
    assert all(snapshot.input_summary["baseline_verified"] is False for snapshot in snapshots_after_onboarding)

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
            "sha-2": PROMPT_MEDIUM,
        }[ref],
    )

    snapshots_after_backfill = list_repo_posture_snapshots_for_repo(db_path, "doria90/dummyAI")
    assert any(snapshot.snapshot_type == "historical_commit" for snapshot in snapshots_after_backfill)
    assert snapshots_after_backfill[-1].snapshot_type == "current"


def test_compare_repo_snapshots_returns_change_drift_and_risk(tmp_path):
    db_path = str(tmp_path / "journey-compare.db")
    init_db(db_path)
    _seed_repo_history(db_path)

    snapshots = build_repo_journey(db_path, "doria90/dummyAI")
    baseline_snapshot = next(snapshot for snapshot in snapshots if snapshot.snapshot_type == "baseline_approved")
    current_snapshot = next(snapshot for snapshot in snapshots if snapshot.snapshot_type == "current")
    comparison = compare_repo_snapshots(db_path, "doria90/dummyAI", baseline_snapshot.id, current_snapshot.id)

    assert comparison.comparison_kind == "baseline_vs_current"
    assert comparison.vector_delta["capability"] > 0
    assert comparison.change_breakdown["critical_surfaces_changed"] >= 1
    assert comparison.drift_summary["drift_delta"] >= 0
    assert comparison.risk_summary["risk_level"] in {"low", "medium", "high"}
    assert "capability_expanded" in comparison.change_labels


def test_repo_journey_snapshots_do_not_collide_across_repositories(tmp_path):
    db_path = str(tmp_path / "journey-multi-repo.db")
    init_db(db_path)
    _seed_repo_history(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/openfang",
        installation_id=124,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["agents/worker.py"],
        fetch_file_content_fn=lambda repo, path, token, ref: "def run_agent():\n    return 'ok'\n",
    )

    dummy_snapshots = build_repo_journey(db_path, "doria90/dummyAI")
    openfang_snapshots = build_repo_journey(db_path, "doria90/openfang")

    assert all(snapshot.repo_full == "doria90/dummyAI" for snapshot in dummy_snapshots)
    assert all(snapshot.repo_full == "doria90/openfang" for snapshot in openfang_snapshots)
    assert {snapshot.snapshot_key for snapshot in dummy_snapshots}.isdisjoint(
        {snapshot.snapshot_key for snapshot in openfang_snapshots}
    )


def test_get_repo_snapshot_detail_is_scoped_to_requested_repository(tmp_path):
    db_path = str(tmp_path / "journey-scoped-detail.db")
    init_db(db_path)
    _seed_repo_history(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/openfang",
        installation_id=124,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["agents/worker.py"],
        fetch_file_content_fn=lambda repo, path, token, ref: "def run_agent():\n    return 'ok'\n",
    )

    dummy_snapshot = build_repo_journey(db_path, "doria90/dummyAI")[0]
    build_repo_journey(db_path, "doria90/openfang")

    assert get_repo_snapshot_detail(db_path, "doria90/openfang", dummy_snapshot.id) is None