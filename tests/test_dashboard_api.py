import os
import sys
import time
from urllib.error import HTTPError

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient
from unittest.mock import patch

import main
from services.audit_jobs import init_db
from services.audit_records import record_audit_result
from services.branch_scan_jobs import create_branch_scan_job
from services.branch_scan_worker import BranchScanWorkerSettings, process_branch_scan_job
from engine.analysis import analyze_diff
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from services.entitlements import derive_entitlement_payload
from services.repo_journey import build_repo_journey
from services.repo_journey_records import list_repo_posture_snapshots_for_repo


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
    assert len(overview_payload["highest_risk_items"][0]["attribute_profile"]) == 6
    assert overview_payload["control_surface_risk"][0]["group_key"] == "prompts"
    assert overview_payload["metrics"][0]["label"] == "Onboarded repositories"
    assert overview_payload["attention_repos"][0]["repo_full"] == "doria90/dummyAI"
    assert overview_payload["attention_repos"][0]["highest_evidence_label"] == "history only"
    assert overview_payload["attention_repos"][0]["highest_evidence_summary"] == "Only merged-history evidence is available right now; start with commit sha-2."
    assert overview_payload["attention_repos"][0]["highest_baseline_label"].startswith("Baseline: Approved")
    assert overview_payload["attention_repos"][0]["highest_review_url"] == "https://github.com/doria90/dummyAI/commit/sha-2"
    assert overview_payload["attention_repos"][0]["highest_change_summary"]
    assert overview_payload["attention_repos"][0]["highest_flag_summary"].startswith("Flagged because")
    assert overview_payload["repos"][0]["historical_version_count"] >= 1

    assert index_response.status_code == 200
    assert index_response.json()["repos"][0]["repo_full"] == "doria90/dummyAI"

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["repo_full"] == "doria90/dummyAI"
    assert payload["onboarding"]["default_branch"] == "main"
    assert payload["onboarding"]["status"] == "baseline_approved"
    assert payload["baseline_review"]["is_pending_review"] is False
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
    assert payload["featured_storyline"]["artifact_path"] == "prompts/refund.txt"
    assert payload["featured_storyline"]["summary"]
    assert payload["featured_storyline"]["episodes"][0]["episode_type"] == "baseline_milestone"
    assert payload["featured_storyline"]["episodes"][-1]["episode_type"] == "current_posture"
    assert payload["history_cues"][0]["label"]
    assert payload["design_profiles"][0]["artifact_path"] == "prompts/refund.txt"
    assert payload["design_profiles"][0]["baseline_provenance"]["source_type"] == "approved_baseline"
    assert payload["design_profiles"][0]["baseline_provenance"]["is_authoritative"] is True
    assert payload["design_profiles"][0]["provenance"]["label"] == "Historical backfill"
    assert payload["design_profiles"][0]["provenance"]["source_ref"] == "commit sha-2"
    assert payload["design_profiles"][0]["provenance"]["source_url"] == "https://github.com/doria90/dummyAI/commit/sha-2"
    assert payload["design_profiles"][0]["provenance"]["review_context"] == "Historical snapshot from backfill"
    assert payload["design_profiles"][0]["headline_summary"]
    assert payload["design_profiles"][0]["drift_label"] in ["small drift", "medium drift", "large drift"]
    assert payload["design_profiles"][0]["drift_tone"] in ["low", "medium", "high"]
    assert payload["design_profiles"][0]["can_promote_source_to_baseline"] is True
    assert len(payload["design_profiles"][0]["attribute_profile"]) == 6
    assert any(
        dimension["attribute_key"] == "model_config_posture"
        for dimension in payload["design_profiles"][0]["attribute_profile"]
    )
    assert any(
        dimension["attribute_key"] == "control_surface_type"
        for dimension in payload["design_profiles"][0]["attribute_profile"]
    )
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
    assert payload["history_timelines"][0]["points"][-1]["baseline_provenance"]["is_authoritative"] is True
    assert payload["artifacts"][0]["artifact_path"] == "prompts/refund.txt"
    assert payload["journey_snapshots"][0]["snapshot_type"] == "baseline_approved"
    assert payload["journey_snapshots"][0]["input_summary"]["baseline_verified"] is True
    assert payload["journey_snapshots"][-1]["snapshot_type"] == "current"
    assert payload["journey_comparison"]["comparison_kind"] == "baseline_vs_current"
    assert payload["journey_comparison"]["change_breakdown"]["critical_surfaces_changed"] >= 1


def test_dashboard_api_can_approve_pending_baseline_and_rebaseline_from_snapshot(tmp_path):
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
        pending_response = client.get("/api/repos/doria90/dummyAI/baseline/pending")
        approve_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/approve",
            json={"note": "Approved for live posture tracking."},
        )
        journey_response = client.get("/api/repos/doria90/dummyAI/journey")

    assert pending_response.status_code == 200
    pending_payload = pending_response.json()
    assert pending_payload["is_pending_review"] is False
    assert pending_payload["approved_count"] == 1
    assert pending_payload["artifact_count"] == 1
    assert pending_payload["authoritative_artifact_count"] == 1

    assert approve_response.status_code == 200
    payload = approve_response.json()
    assert payload["approved_baseline_count"] == 1
    assert payload["dashboard"]["onboarding"]["status"] == "baseline_approved"
    assert payload["dashboard"]["baseline_review"]["is_pending_review"] is False
    assert payload["dashboard"]["journey_snapshots"][0]["input_summary"]["baseline_verified"] is True
    assert journey_response.status_code == 200
    current_snapshot = next(snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "current")
    assert current_snapshot["input_summary"]["baseline_verified"] is True

    current_snapshot_id = current_snapshot["id"]
    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_file_content", return_value=PROMPT_CURRENT), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": current_snapshot_id, "rationale": ""},
        )

    assert rebaseline_response.status_code == 200
    rebaseline_payload = rebaseline_response.json()
    assert rebaseline_payload["created_baseline_count"] == 1
    assert rebaseline_payload["dashboard"]["onboarding"]["status"] == "baseline_approved"
    assert rebaseline_payload["dashboard"]["baseline_review"]["is_pending_review"] is False
    assert rebaseline_payload["dashboard"]["baseline_version_count"] == 2
    assert rebaseline_payload["dashboard"]["selected_baseline_source_snapshot_id"] == current_snapshot_id


def test_dashboard_api_rebaseline_skips_artifacts_missing_in_selected_snapshot(tmp_path):
    db_path = str(tmp_path / "api-dashboard-missing-artifact.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False

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
        fetch_file_content_fn=lambda repo, path, token, ref: {
            ("prompts/refund.txt", "sha-1"): PROMPT_CURRENT,
            ("config/policy.yml", "sha-1"): "policy: strict\n",
        }[(path, ref)],
    )

    with TestClient(main.app) as client:
        approve_repo = client.post(
            "/api/repos/doria90/dummyAI/baseline/approve",
            json={"note": "approve repo baseline"},
        )
        journey_response = client.get("/api/repos/doria90/dummyAI/journey")

    assert approve_repo.status_code == 200
    current_snapshot = next(snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "current")

    def _fetch_content(repo, path, token, ref):
        if path == "config/policy.yml":
            raise HTTPError(f"https://example.test/{path}", 404, "Not Found", hdrs=None, fp=None)
        return PROMPT_CURRENT

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_file_content", side_effect=_fetch_content), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": current_snapshot["id"], "rationale": ""},
        )

    assert rebaseline_response.status_code == 200
    rebaseline_payload = rebaseline_response.json()
    assert rebaseline_payload["created_baseline_count"] == 1
    assert rebaseline_payload["dashboard"]["onboarding"]["status"] == "baseline_approved"


def test_dashboard_api_rebaseline_to_branch_head_updates_selected_baseline_source_snapshot(tmp_path):
    db_path = str(tmp_path / "api-dashboard-branch-head.db")
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

    branch_job = create_branch_scan_job(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        commit_sha="livehead1",
        branch_ref="refs/heads/main",
        triggered_by="push_webhook",
    )

    with patch("services.branch_scan_worker.generate_jwt", return_value="jwt-token"), patch(
        "services.branch_scan_worker.get_installation_token", return_value="installation-token"
    ), patch("services.branch_scan_worker.fetch_file_content", return_value=PROMPT_CURRENT):
        result = process_branch_scan_job(
            branch_job,
            BranchScanWorkerSettings(
                db_path=db_path,
                github_app_id="app-id",
                github_private_key_path="/tmp/test-key.pem",
            ),
        )

    assert result in {"completed", "completed_with_updates"}

    with TestClient(main.app) as client:
        journey_response = client.get("/api/repos/doria90/dummyAI/journey")

    assert journey_response.status_code == 200
    branch_head_snapshot = next(
        snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "branch_head"
    )

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_file_content", return_value=PROMPT_CURRENT), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": branch_head_snapshot["id"], "rationale": ""},
        )
        dashboard_response = client.get("/api/repos/doria90/dummyAI/dashboard")

    assert rebaseline_response.status_code == 200
    rebaseline_payload = rebaseline_response.json()
    assert rebaseline_payload["dashboard"]["selected_baseline_source_snapshot_id"] == branch_head_snapshot["id"]

    assert dashboard_response.status_code == 200
    dashboard_payload = dashboard_response.json()
    assert dashboard_payload["selected_baseline_source_snapshot_id"] == branch_head_snapshot["id"]
    assert any(
        snapshot["id"] == branch_head_snapshot["id"] and snapshot["snapshot_type"] == "branch_head"
        for snapshot in dashboard_payload["journey_snapshots"]
    )


def test_dashboard_api_exposes_repo_journey_and_compare(tmp_path):
    db_path = str(tmp_path / "api-journey.db")
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
    snapshots = build_repo_journey(db_path, "doria90/dummyAI")
    baseline_snapshot = next(snapshot for snapshot in snapshots if snapshot.snapshot_type == "baseline_approved")
    current_snapshot = next(snapshot for snapshot in snapshots if snapshot.snapshot_type == "current")

    with TestClient(main.app) as client:
        journey_response = client.get("/api/repos/doria90/dummyAI/journey")
        snapshot_response = client.get(f"/api/repos/doria90/dummyAI/snapshots/{baseline_snapshot.id}")
        compare_response = client.get(
            f"/api/repos/doria90/dummyAI/compare?left={baseline_snapshot.id}&right={current_snapshot.id}"
        )

    assert journey_response.status_code == 200
    journey_payload = journey_response.json()
    assert journey_payload["repo_full"] == "doria90/dummyAI"
    assert journey_payload["snapshots"][0]["snapshot_type"] == "baseline_approved"
    assert journey_payload["snapshots"][-1]["snapshot_type"] == "current"

    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["snapshot"]["snapshot_key"] == baseline_snapshot.snapshot_key

    assert compare_response.status_code == 200
    compare_payload = compare_response.json()
    assert compare_payload["comparison_kind"] == "baseline_vs_current"
    assert compare_payload["change_breakdown"]["critical_surfaces_changed"] >= 1
    assert compare_payload["risk_summary"]["risk_level"] in {"low", "medium", "high"}


def test_dashboard_api_snapshot_detail_is_repo_scoped(tmp_path):
    db_path = str(tmp_path / "api-journey-scope.db")
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

    with TestClient(main.app) as client:
        response = client.get(f"/api/repos/doria90/openfang/snapshots/{dummy_snapshot.id}")

    assert response.status_code == 404


def test_dashboard_api_filters_overview_to_allocated_workspace_repos(tmp_path):
    db_path = str(tmp_path / "dashboard-workspace-filter.db")
    init_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        db_path,
        github_user_id="123",
        github_login="doria90",
        display_name="Doria",
        primary_email="doria@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        db_path,
        slug="doria-workspace",
        display_name="Doria Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        db_path,
        session_id="dashboard-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        db_path,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_123",
        stripe_price_id="price_123",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=time.time() + 86400,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        db_path,
        workspace_id=workspace.id,
        payload=derive_entitlement_payload("team", "active"),
    )
    upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=123,
        account_id="acct-1",
        account_login="doria90",
        account_type="User",
        target_type="User",
        status="active",
    )
    replace_repo_connections(
        db_path,
        workspace_id=workspace.id,
        installation_id=123,
        repositories=[
            {
                "repo_github_id": "dummyAI",
                "repo_full": "doria90/dummyAI",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
            {
                "repo_github_id": "openfang",
                "repo_full": "doria90/openfang",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )

    for repo_full in ["doria90/dummyAI", "doria90/openfang"]:
        onboard_repository(
            db_path,
            repo_full=repo_full,
            installation_id=123,
            token="token",
            get_default_branch_fn=lambda repo, token: "main",
            list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
            fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
        )

    allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=123,
        repo_github_id="dummyAI",
        repo_full="doria90/dummyAI",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(db_path, allocation.id, "onboarded")

    with TestClient(main.app) as client:
        overview_response = client.get(
            "/api/dashboard/overview",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        index_response = client.get(
            "/api/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        unallocated_repo_response = client.get(
            "/api/repos/doria90/openfang/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        mutation_response = client.post(
            "/api/repos/doria90/openfang/artifacts/prompts/refund.txt/baseline",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path

    assert overview_response.status_code == 200
    assert [repo["repo_full"] for repo in overview_response.json()["repos"]] == ["doria90/dummyAI", "doria90/openfang"]
    assert overview_response.json()["repos"][0]["dashboard_scope"] == "allocated"
    assert overview_response.json()["repos"][1]["dashboard_scope"] == "connected_history"
    assert [repo["repo_full"] for repo in index_response.json()["repos"]] == ["doria90/dummyAI", "doria90/openfang"]
    assert index_response.json()["repos"][1]["dashboard_scope"] == "connected_history"
    assert unallocated_repo_response.status_code == 200
    assert mutation_response.status_code == 404


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
    assert len(payload["design_profiles"][0]["attribute_profile"]) == 6
    assert payload["insights"][0]["evidence_label"] == "baseline only"
    assert payload["insights"][0]["evidence_summary"] == "No merged-history evidence yet."


def test_dashboard_api_returns_artifact_storyline_endpoint(tmp_path):
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
        response = client.get("/api/repos/doria90/dummyAI/artifacts/prompts/refund.txt/episodes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_full"] == "doria90/dummyAI"
    assert payload["artifact_path"] == "prompts/refund.txt"
    assert payload["storyline"]["artifact_path"] == "prompts/refund.txt"
    assert payload["storyline"]["summary"]
    assert payload["storyline"]["episodes"][0]["episode_type"] == "baseline_milestone"
    assert payload["storyline"]["episodes"][-1]["episode_type"] == "current_posture"
    assert len(payload["storyline"]["episodes"]) >= 4


def test_dashboard_api_returns_404_for_missing_artifact_storyline(tmp_path):
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
        response = client.get("/api/repos/doria90/dummyAI/artifacts/prompts/missing.txt/episodes")

    assert response.status_code == 404
    assert response.json()["detail"] == "No artifact storyline is available for this repo artifact."