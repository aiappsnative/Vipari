import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.audit_jobs import init_db
from services.audit_records import list_static_profiles_for_repo_artifact, record_audit_result


BASELINE_DIFF = """diff --git a/prompts/refund.txt b/prompts/refund.txt
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


RISKIER_DIFF = """diff --git a/prompts/refund.txt b/prompts/refund.txt
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


BASELINE_SNAPSHOT = """# Refund Copilot
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Log every refund action for audit.
Use the billing sandbox tool in read mode before proposing any refund.
max_steps: 2
temperature: 0.2
"""


RISKIER_SNAPSHOT = """# Refund Copilot
You can refund customers directly in production using the billing tool.
Use judgment when deciding whether approval is necessary.
Update billing records and send confirmations.
parallel plan with multi-step execution
max_steps: 6
temperature: 0.8
"""


def _record_profile_version(db_path: str, *, job_id: int, pr_number: int, head_sha: str, diff_text: str, snapshot_text: str):
    analysis = analyze_diff(diff_text)
    assert analysis.artifacts

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
        artifact_snapshots={"prompts/refund.txt": snapshot_text},
    )


def test_static_profiles_are_persisted_and_link_to_previous_baseline(tmp_path):
    db_path = str(tmp_path / "profiles.db")
    init_db(db_path)

    _record_profile_version(
        db_path,
        job_id=1,
        pr_number=101,
        head_sha="sha-101",
        diff_text=BASELINE_DIFF,
        snapshot_text=BASELINE_SNAPSHOT,
    )
    _record_profile_version(
        db_path,
        job_id=2,
        pr_number=102,
        head_sha="sha-102",
        diff_text=RISKIER_DIFF,
        snapshot_text=RISKIER_SNAPSHOT,
    )

    profiles = list_static_profiles_for_repo_artifact(db_path, "doria90/dummyAI", "prompts/refund.txt")

    assert len(profiles) == 2

    baseline, current = profiles
    assert baseline.baseline_profile_id is None
    assert baseline.semantic_distance == 0.0
    assert baseline.attribute_deltas["capability_risk"] == 0.0

    assert current.baseline_profile_id == baseline.id
    assert current.semantic_distance > 0.0
    assert current.attribute_deltas["capability_risk"] > 0.0
    assert current.attribute_deltas["guardrail_robustness"] < 0.0
    assert current.attribute_deltas["autonomy_level"] > 0.0
    assert any("Capability risk increased" in line for line in current.narrative)
