import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

import main
from services.audit_jobs import init_db
from services.audit_records import record_audit_result
from engine.analysis import analyze_diff
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill


PROMPT_BASELINE = """# Refund Copilot
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Use the billing sandbox tool in read mode.
max_steps: 2
temperature: 0.2
"""

PROMPT_CURRENT = """# Refund Copilot
You can refund customers directly in production using the billing tool.
Use judgment when deciding whether approval is necessary.
Update billing records and send confirmations.
parallel plan with multi-step execution
max_steps: 6
temperature: 0.8
"""

PROMPT_MEDIUM = """# Refund Copilot
Refund customers after checking the billing sandbox.
Escalate unusual cases for approval.
max_steps: 4
temperature: 0.4
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


def test_dashboard_api_returns_repo_view_for_seeded_repo(tmp_path):
    db_path = str(tmp_path / "api-dashboard.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False

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

    with TestClient(main.app) as client:
        overview_response = client.get("/api/dashboard/overview")
        index_response = client.get("/api/repos")
        repo_response = client.get("/api/repos/doria90/dummyAI/dashboard")

    assert overview_response.status_code == 200
    overview_payload = overview_response.json()
    assert overview_payload["risk_state"]["headline"]
    assert overview_payload["highest_risk_items"][0]["repo_full"] == "doria90/dummyAI"
    assert overview_payload["highest_risk_items"][0]["review_target"] == "commit sha-2"
    assert overview_payload["highest_risk_items"][0]["review_url"] == "https://github.com/doria90/dummyAI/commit/sha-2"
    assert overview_payload["control_surface_risk"][0]["group_key"] == "prompts"
    assert overview_payload["metrics"][0]["label"] == "Onboarded repositories"
    assert overview_payload["attention_repos"][0]["repo_full"] == "doria90/dummyAI"
    assert overview_payload["attention_repos"][0]["highest_evidence_label"] == "history only"
    assert overview_payload["attention_repos"][0]["highest_evidence_summary"] == "Only merged-history evidence is available right now; start with commit sha-2."
    assert overview_payload["attention_repos"][0]["highest_baseline_label"].startswith("Baseline: Approved")
    assert overview_payload["attention_repos"][0]["highest_review_url"] == "https://github.com/doria90/dummyAI/commit/sha-2"
    assert overview_payload["attention_repos"][0]["highest_change_summary"]
    assert overview_payload["attention_repos"][0]["highest_flag_summary"].startswith("Flagged because")

    assert index_response.status_code == 200
    assert index_response.json()["repos"][0]["repo_full"] == "doria90/dummyAI"

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["repo_full"] == "doria90/dummyAI"
    assert payload["onboarding"]["default_branch"] == "main"
    assert payload["backfill"]["completed_job_count"] == 1
    assert payload["insights"][0]["artifact_path"] == "prompts/refund.txt"
    assert payload["insights"][0]["queue_lane"] == "primary"
    assert payload["insights"][0]["evidence_label"] == "history only"
    assert payload["insights"][0]["evidence_summary"] == "Only merged-history evidence is available right now; start with commit sha-2."
    assert payload["insights"][0]["baseline_label"].startswith("Baseline: Approved")
    assert payload["insights"][0]["review_target"] == "commit sha-2"
    assert payload["insights"][0]["review_url"] == "https://github.com/doria90/dummyAI/commit/sha-2"
    assert payload["insights"][0]["supporting_review_target"] is None
    assert payload["insights"][0]["supporting_review_url"] is None
    assert payload["insights"][0]["change_summary"]
    assert payload["insights"][0]["flag_summary"].startswith("Flagged because")
    assert payload["insights"][0]["risk_reasons"]
    assert payload["lower_confidence_insights"] == []
    assert payload["control_surface_groups"][0]["group_key"] == "prompts"
    assert payload["history_timelines"][0]["artifact_path"] == "prompts/refund.txt"
    assert payload["history_timelines"][0]["point_count"] == 2
    assert payload["design_profiles"][0]["artifact_path"] == "prompts/refund.txt"
    assert payload["design_profiles"][0]["baseline_provenance"]["source_type"] == "approved_baseline"
    assert payload["design_profiles"][0]["provenance"]["label"] == "Historical backfill"
    assert payload["design_profiles"][0]["provenance"]["source_ref"] == "commit sha-2"
    assert payload["design_profiles"][0]["provenance"]["source_url"] == "https://github.com/doria90/dummyAI/commit/sha-2"
    assert payload["design_profiles"][0]["provenance"]["review_context"] == "Historical snapshot from backfill"
    assert payload["design_profiles"][0]["headline_summary"]
    assert payload["design_profiles"][0]["drift_label"] in ["small drift", "medium drift", "large drift"]
    assert payload["design_profiles"][0]["drift_tone"] in ["low", "medium", "high"]
    assert payload["design_profiles"][0]["can_promote_source_to_baseline"] is True
    assert isinstance(payload["design_profiles"][0]["attribute_findings"], list)
    if payload["design_profiles"][0]["attribute_findings"]:
        assert payload["design_profiles"][0]["attribute_findings"][0]["reason"]
        assert isinstance(payload["design_profiles"][0]["attribute_findings"][0]["evidence"], list)
        assert payload["design_profiles"][0]["attribute_findings"][0]["remediation"]
    assert any(
        finding["attribute_key"] == "stability_vs_creativity"
        for finding in payload["design_profiles"][0]["attribute_findings"]
    )
    assert payload["design_profiles"][0]["baseline_profile"]["guardrail_robustness"] >= 0
    assert payload["history_timelines"][0]["points"][0]["label"] == "Historical backfill"
    assert payload["history_timelines"][0]["points"][0]["source_ref"] == "commit sha-1"
    assert payload["history_timelines"][0]["points"][0]["source_url"] == "https://github.com/doria90/dummyAI/commit/sha-1"
    assert payload["history_timelines"][0]["points"][0]["review_context"] == "Historical snapshot from backfill"
    assert payload["history_timelines"][0]["points"][-1]["label"] == "Historical backfill"
    assert payload["history_timelines"][0]["points"][-1]["source_ref"] == "commit sha-2"
    assert payload["history_timelines"][0]["points"][-1]["source_url"] == "https://github.com/doria90/dummyAI/commit/sha-2"
    assert payload["history_timelines"][0]["points"][-1]["review_context"] == "Historical snapshot from backfill"
    assert payload["history_timelines"][0]["points"][-1]["baseline_provenance"]["source_type"] == "approved_baseline"
    assert payload["artifacts"][0]["artifact_path"] == "prompts/refund.txt"


def test_dashboard_api_can_promote_current_source_to_baseline(tmp_path):
    db_path = str(tmp_path / "api-dashboard.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False

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
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-1"][:limit],
    )
    execute_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        fetch_file_content_fn=lambda repo, path, token, ref: {"sha-1": PROMPT_CURRENT}[ref],
    )

    with TestClient(main.app) as client:
        response = client.post("/api/repos/doria90/dummyAI/artifacts/prompts/refund.txt/baseline")

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifact_path"] == "prompts/refund.txt"
    assert payload["baseline"]["artifact_path"] == "prompts/refund.txt"
    assert payload["baseline"]["content_text"] == PROMPT_CURRENT
    assert payload["dashboard"]["baseline_version_count"] == 2


def test_dashboard_api_promotes_landed_history_over_pr_snapshots(tmp_path):
    db_path = str(tmp_path / "api-dashboard.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False

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
    plan_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-2"][:limit],
    )
    execute_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        fetch_file_content_fn=lambda repo, path, token, ref: {"sha-2": PROMPT_MEDIUM}[ref],
    )

    with TestClient(main.app) as client:
        response = client.post("/api/repos/doria90/dummyAI/artifacts/prompts/refund.txt/baseline")

    assert response.status_code == 200
    payload = response.json()
    assert payload["baseline"]["content_text"] == PROMPT_MEDIUM


def test_dashboard_api_marks_baseline_only_profiles_as_not_promotable(tmp_path):
    db_path = str(tmp_path / "api-dashboard.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )

    with TestClient(main.app) as client:
        repo_response = client.get("/api/repos/doria90/dummyAI/dashboard")

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["design_profiles"][0]["provenance"] is None
    assert payload["design_profiles"][0]["can_promote_source_to_baseline"] is False
    assert payload["insights"][0]["evidence_label"] == "baseline only"
    assert payload["insights"][0]["evidence_summary"] == "No merged-history evidence yet."