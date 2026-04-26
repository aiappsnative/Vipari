import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.audit_jobs import init_db
from services.audit_records import get_repo_static_drift_summary, list_top_drifting_artifacts_for_repo, record_audit_result
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill


PROMPT_BASELINE_DIFF = """diff --git a/prompts/refund.txt b/prompts/refund.txt
index 1..2 100644
--- a/prompts/refund.txt
+++ b/prompts/refund.txt
@@ -1 +1,4 @@
-You are a refund assistant.
+You are a refund assistant.
+You must never refund above 200 dollars.
+If unsure, escalate to a manager.
+Use the billing sandbox tool in read mode before proposing any refund.
"""

PROMPT_RISKIER_DIFF = """diff --git a/prompts/refund.txt b/prompts/refund.txt
index 2..3 100644
--- a/prompts/refund.txt
+++ b/prompts/refund.txt
@@ -1,4 +1,4 @@
 You are a refund assistant.
-You must never refund above 200 dollars.
-If unsure, escalate to a manager.
-Use the billing sandbox tool in read mode before proposing any refund.
+You can refund customers directly in production.
+Use judgment when deciding whether approval is necessary.
+Update billing records and send confirmations.
"""

MODEL_BASELINE_DIFF = """diff --git a/config/model.yaml b/config/model.yaml
index 1..2 100644
--- a/config/model.yaml
+++ b/config/model.yaml
@@ -1,2 +1,2 @@
-model: gpt-4o-mini
+model: gpt-4o-mini
 temperature: 0.1
"""

MODEL_CHANGED_DIFF = """diff --git a/config/model.yaml b/config/model.yaml
index 2..3 100644
--- a/config/model.yaml
+++ b/config/model.yaml
@@ -1,2 +1,2 @@
-model: gpt-4o-mini
+model: gpt-4.1
 temperature: 0.7
"""

PROMPT_BASELINE_SNAPSHOT = """# Refund Copilot
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Log every refund action for audit.
Use the billing sandbox tool in read mode before proposing any refund.
max_steps: 2
temperature: 0.2
"""

PROMPT_RISKIER_SNAPSHOT = """# Refund Copilot
You can refund customers directly in production using the billing tool.
Use judgment when deciding whether approval is necessary.
Update billing records and send confirmations.
parallel plan with multi-step execution
max_steps: 6
temperature: 0.8
"""

MODEL_BASELINE_SNAPSHOT = """model: gpt-4o-mini
temperature: 0.1
approval: required
"""

MODEL_CHANGED_SNAPSHOT = """model: gpt-4.1
temperature: 0.7
approval: required
"""

PROMPT_CURRENT_SNAPSHOT = """# Refund Copilot
You can issue refunds after policy validation.
Escalate anything unusual.
Use the billing sandbox in read mode.
max_steps: 3
temperature: 0.3
"""

MODEL_CURRENT_SNAPSHOT = """model: gpt-4.1
temperature: 0.3
approval: manager
"""


def _record(db_path: str, *, job_id: int, pr_number: int, head_sha: str, diff_text: str, artifact_path: str, snapshot_text: str):
    analysis = analyze_diff(diff_text)
    record_audit_result(
        db_path,
        job_id=job_id,
        repo_full="doria90/dummyAI",
        pr_number=pr_number,
        installation_id=123,
        head_sha=head_sha,
        deterministic_analysis=analysis,
        status="completed",
        completion_mode="completed",
        output_mode="full_semantic_review",
        comment_body=None,
        comment_mode=None,
        semantic_review_completed=True,
        artifact_snapshots={artifact_path: snapshot_text},
    )


def _seed_landed_history(db_path: str) -> None:
    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt", "config/model.yaml"],
        fetch_file_content_fn=lambda repo, path, token, ref: {
            "prompts/refund.txt": PROMPT_CURRENT_SNAPSHOT,
            "config/model.yaml": MODEL_CURRENT_SNAPSHOT,
        }[path],
    )

    plan_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-2", "sha-1"][:limit],
    )

    historical_contents = {
        ("prompts/refund.txt", "sha-1"): PROMPT_BASELINE_SNAPSHOT,
        ("prompts/refund.txt", "sha-2"): PROMPT_RISKIER_SNAPSHOT,
        ("config/model.yaml", "sha-1"): MODEL_BASELINE_SNAPSHOT,
        ("config/model.yaml", "sha-2"): MODEL_CHANGED_SNAPSHOT,
    }

    execute_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        fetch_file_content_fn=lambda repo, path, token, ref: historical_contents[(path, ref)],
    )


def test_repo_static_drift_summary_aggregates_latest_profile_history(tmp_path):
    db_path = str(tmp_path / "trends.db")
    init_db(db_path)

    _seed_landed_history(db_path)

    summary = get_repo_static_drift_summary(db_path, "doria90/dummyAI")

    assert summary.repo_full == "doria90/dummyAI"
    assert summary.artifact_count == 2
    assert summary.profile_count == 4
    assert summary.baseline_linked_profile_count == 4
    assert summary.avg_semantic_distance > 0.0
    assert summary.avg_capability_shift > 0.0
    assert summary.highest_capability_artifact_path == "prompts/refund.txt"
    assert summary.highest_capability_delta > 0.0


def test_list_top_drifting_artifacts_ranks_latest_profiles_by_magnitude(tmp_path):
    db_path = str(tmp_path / "trends.db")
    init_db(db_path)

    _seed_landed_history(db_path)

    leaderboard = list_top_drifting_artifacts_for_repo(db_path, "doria90/dummyAI")

    assert len(leaderboard) == 2
    assert leaderboard[0].artifact_path == "prompts/refund.txt"
    assert leaderboard[0].sample_count == 2
    assert leaderboard[0].drift_magnitude > leaderboard[1].drift_magnitude
    assert leaderboard[0].capability_shift > 0.0
    assert leaderboard[0].autonomy_shift > 0.0
    assert leaderboard[1].artifact_path == "config/model.yaml"
