import os
import sys
import time
from urllib.error import HTTPError

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient as FastAPITestClient
from unittest.mock import PropertyMock, patch

import main
from services.audit_jobs import create_audit_job, init_db
from services.audit_records import record_audit_feedback_event, record_audit_result, record_pre_audit_relevance_decision
from services.branch_scan_jobs import create_branch_scan_job
from services.branch_scan_worker import BranchScanWorkerSettings, process_branch_scan_job
from engine.analysis import analyze_diff
from engine.diff_parser import extract_changed_files
from engine.relevance import classify_changed_file, resolve_relevance_with_micro_classifier
from services.onboarding_records import list_baseline_audit_log_for_onboarding, record_baseline_audit_log
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from services.entitlements import derive_entitlement_payload
from services.persistence import connect_sqlite
from services.repo_journey import build_repo_journey
from services.repo_journey_records import list_repo_posture_snapshots_for_repo
from services.dashboard_views import build_repo_pr_review_routes_payload


class TestClient(FastAPITestClient):
    def request(self, method, url, *args, cookies=None, **kwargs):
        if not cookies:
            return super().request(method, url, *args, **kwargs)

        previous = {key: self.cookies.get(key) for key in cookies}
        for key, value in cookies.items():
            self.cookies.set(key, value)
        try:
            return super().request(method, url, *args, **kwargs)
        finally:
            for key, previous_value in previous.items():
                if previous_value is None:
                    self.cookies.pop(key, None)
                else:
                    self.cookies.set(key, previous_value)


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


def _record_pre_audit_relevance(db_path: str, *, repo_full: str, pr_number: int, head_sha: str):
    diff_text = "diff --git a/src/assistant_router.py b/src/assistant_router.py\nindex 1..2\n+route update\n"
    changed_file = extract_changed_files(diff_text)[0]
    relevance = resolve_relevance_with_micro_classifier(
        classify_changed_file(changed_file),
        is_relevant=False,
        reason="General routing code; not an AI control surface.",
        status="completed",
    )
    record_pre_audit_relevance_decision(
        db_path,
        repo_full=repo_full,
        pr_number=pr_number,
        head_sha=head_sha,
        relevance=relevance,
    )


def _create_dashboard_owner_session(db_path: str, *, repo_full: str = "doria90/dummyAI", installation_id: int = 123):
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
        github_user_id=f"dashboard-user-{installation_id}",
        github_login=f"dashboard-owner-{installation_id}",
        display_name="Dashboard Owner",
        primary_email=f"dashboard-owner-{installation_id}@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        db_path,
        slug=f"dashboard-workspace-{installation_id}",
        display_name="Dashboard Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        db_path,
        session_id=f"dashboard-session-{installation_id}",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        db_path,
        workspace_id=workspace.id,
        stripe_subscription_id=f"sub_dashboard_{installation_id}",
        stripe_price_id=f"price_dashboard_{installation_id}",
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
        installation_id=installation_id,
        account_id=f"acct-{installation_id}",
        account_login="doria90",
        account_type="User",
        target_type="User",
        status="active",
    )
    replace_repo_connections(
        db_path,
        workspace_id=workspace.id,
        installation_id=installation_id,
        repositories=[
            {
                "repo_github_id": repo_full.split("/", 1)[1],
                "repo_full": repo_full,
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=installation_id,
        repo_github_id=repo_full.split("/", 1)[1],
        repo_full=repo_full,
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(db_path, allocation.id, "onboarded")
    return session


def test_dashboard_api_returns_repo_view_for_seeded_repo(tmp_path):
    db_path = str(tmp_path / "api-dashboard.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path)

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
        overview_response = client.get("/api/dashboard/overview", cookies={main.settings.session_cookie_name: session.session_id})
        index_response = client.get("/api/repos", cookies={main.settings.session_cookie_name: session.session_id})
        repo_response = client.get("/api/repos/doria90/dummyAI/dashboard", cookies={main.settings.session_cookie_name: session.session_id})

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
    assert overview_payload["attention_repos"][0]["highest_review_pr_number"] is None
    assert overview_payload["attention_repos"][0]["highest_review_head_sha"] is None
    assert overview_payload["attention_repos"][0]["highest_change_summary"]
    assert overview_payload["attention_repos"][0]["highest_flag_summary"].startswith("Flagged because")
    assert overview_payload["repos"][0]["historical_version_count"] >= 1
    assert overview_payload["nav_repos"][0]["repo_full"] == "doria90/dummyAI"
    assert overview_payload["overview_sections"]["urgent_queue"]["repo_count"] >= 1
    assert overview_payload["overview_sections"]["urgent_queue"]["repos"][0]["repo_full"] == "doria90/dummyAI"
    assert overview_payload["overview_sections"]["recent_changes"]["repo_count"] >= 1
    assert overview_payload["overview_sections"]["posture_snapshot"]["risk_state"]["headline"]
    assert overview_payload["overview_sections"]["governance_attention"]["repos_with_anomalies_count"] >= 0
    assert isinstance(overview_payload["overview_sections"]["governance_attention"]["ranked_issues_now"], list)

    assert index_response.status_code == 200
    assert index_response.json()["repos"][0]["repo_full"] == "doria90/dummyAI"

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["repo_full"] == "doria90/dummyAI"
    assert payload["onboarding"]["default_branch"] == "main"
    assert payload["onboarding"]["status"] == "baseline_approved"
    assert payload["baseline_review"]["is_pending_review"] is False
    assert isinstance(payload["baseline_review"]["recent_decisions"], list)
    assert payload["artifacts"][0]["provenance_label"] == "AI control surface"
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
    assert payload["governance_posture"]["review_quality"]
    assert isinstance(payload["governance_posture"]["top_governance_anomalies"], list)
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
    assert payload["audit_brief"]["recommendation_label"] == "Review now"
    assert payload["audit_brief"]["review_now_count"] >= 1


def test_dashboard_api_exposes_history_bootstrap_cue_before_backfill(tmp_path):
    db_path = str(tmp_path / "api-dashboard-bootstrap-cue.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path)

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
        repo_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["history_cues"][0]["cue_key"] == "history_bootstrap_pending"
    assert payload["history_cues"][0]["label"] == "History bootstrap pending"
    assert payload["history_cues"][0]["artifact_paths"] == []
    assert [snapshot["snapshot_type"] for snapshot in payload["journey_snapshots"]] == ["baseline_approved", "current"]
    assert payload["audit_brief"]["changed_artifact_count"] >= 1
    assert payload["audit_brief"]["findings"][0]["artifact_path"] == "prompts/refund.txt"


def test_dashboard_api_returns_audit_brief_without_review_now_findings(tmp_path):
    db_path = str(tmp_path / "api-dashboard-safe-brief.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path)

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
        repo_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["audit_brief"]["recommendation_label"] != "Unavailable"
    assert payload["audit_brief"]["why_now"]
    assert payload["audit_brief"]["summary"]
    assert payload["audit_brief"]["review_now_count"] == 0
    assert payload["audit_brief"]["baseline_status"] == "approved"
    assert payload["audit_brief"]["baseline_reference"] != "none-yet"


def test_dashboard_overview_api_filter_critical_limits_repo_lists(tmp_path):
    db_path = str(tmp_path / "api-dashboard-overview-critical.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path, repo_full="doria90/dummyAI", installation_id=1)

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

    onboard_repository(
        db_path,
        repo_full="doria90/repo-two",
        installation_id=2,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["agents/worker.py"],
        fetch_file_content_fn=lambda repo, path, token, ref: "def run_agent():\n    return 'ok'\n",
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/dashboard/overview?filter=critical",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [repo["repo_full"] for repo in payload["repos"]] == ["doria90/dummyAI"]
    assert [repo["repo_full"] for repo in payload["nav_repos"]] == ["doria90/dummyAI"]
    assert [repo["repo_full"] for repo in payload["attention_repos"]] == ["doria90/dummyAI"]
    assert [repo["repo_full"] for repo in payload["overview_sections"]["recent_changes"]["repos"]] == ["doria90/dummyAI"]
    assert payload["overview_sections"]["urgent_queue"]["watch_count"] == 0


def test_dashboard_overview_api_range_24h_limits_recent_activity(tmp_path):
    db_path = str(tmp_path / "api-dashboard-overview-range.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path, repo_full="doria90/dummyAI", installation_id=1)

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

    onboard_repository(
        db_path,
        repo_full="doria90/repo-two",
        installation_id=2,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["agents/worker.py"],
        fetch_file_content_fn=lambda repo, path, token, ref: "def run_agent():\n    return 'ok'\n",
    )

    stale_timestamp = time.time() - (45 * 86400)
    with connect_sqlite(db_path) as conn:
        conn.execute(
            "UPDATE repository_onboardings SET updated_at = ? WHERE repo_full = ?",
            (stale_timestamp, "doria90/repo-two"),
        )
        conn.commit()

    with TestClient(main.app) as client:
        response = client.get(
            "/api/dashboard/overview?range=24h",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [repo["repo_full"] for repo in payload["repos"]] == ["doria90/dummyAI"]
    assert [repo["repo_full"] for repo in payload["nav_repos"]] == ["doria90/dummyAI"]
    assert [repo["repo_full"] for repo in payload["overview_sections"]["recent_changes"]["repos"]] == ["doria90/dummyAI"]
    assert payload["risk_state"]["review_now_repo_count"] >= 1


def test_repo_dashboard_api_exposes_ai_act_relevance_inputs(tmp_path):
    db_path = str(tmp_path / "api-dashboard-ai-act-inputs.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: [
            "prompts/refund.txt",
            "tools/ai_agent_tool.py",
            "config/model.json",
            "policies/policy.md",
        ],
        fetch_file_content_fn=lambda repo, path, token, ref: {
            "prompts/refund.txt": PROMPT_BASELINE,
            "tools/ai_agent_tool.py": "def run_tool():\n    return 'ok'\n",
            "config/model.json": '{"model": "gpt-4o-mini", "temperature": 0.2}',
            "policies/policy.md": "Human review is required before sensitive changes ship.\n",
        }[path],
    )

    with TestClient(main.app) as client:
        repo_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["onboarding"]["status"] == "baseline_approved"
    assert payload["baseline_review"]["is_pending_review"] is False
    assert payload["baseline_review"]["approved_count"] == 4
    provenance_kinds = {artifact["provenance_kind"] for artifact in payload["artifacts"]}
    assert "ai_control_surface" in provenance_kinds
    assert "ai_tool_surface" in provenance_kinds
    assert "model_behavior_surface" in provenance_kinds
    assert "human_governance_surface" in provenance_kinds


def test_repo_dashboard_api_includes_scoped_pre_audit_relevance_for_pr_deep_link(tmp_path):
    db_path = str(tmp_path / "api-dashboard-pre-audit-relevance.db")
    init_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_app_base_url = main.settings.app_base_url
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    main.settings.app_base_url = "https://app.promptdrift.test"
    main.settings.local_debug_disable_login = False

    session = _create_dashboard_owner_session(db_path)
    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )
    _record_pre_audit_relevance(db_path, repo_full="doria90/dummyAI", pr_number=21, head_sha="sha-relevance-21")

    with TestClient(main.app) as client:
        repo_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard?pr=21&head_sha=sha-relevance-21",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_base_url = original_app_base_url
    main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["pre_audit_relevance"]["pr_number"] == 21
    assert payload["pre_audit_relevance"]["head_sha"] == "sha-relevance-21"
    assert payload["pre_audit_relevance"]["decision_count"] == 1
    assert payload["pre_audit_relevance"]["decisions"][0]["artifact_path"] == "src/assistant_router.py"
    assert payload["pre_audit_relevance"]["decisions"][0]["classifier_is_relevant"] is False


def test_repo_dashboard_api_includes_pr_review_routes_for_selected_episode(tmp_path):
    db_path = str(tmp_path / "api-dashboard-routes.db")
    init_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.settings.local_debug_disable_login = False

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )

    older_audit = record_audit_result(
        db_path,
        job_id=101,
        repo_full="doria90/dummyAI",
        pr_number=20,
        pr_title="Stabilize refund policy prompts",
        installation_id=123,
        head_sha="sha-older-20",
        deterministic_analysis=analyze_diff(PROMPT_DIFF),
        status="completed",
        completion_mode="completed",
        output_mode="full_semantic_review",
        comment_body="## ✅ Vipari: Review complete\nSummary: This earlier route stayed within the approved operating lane.",
        comment_mode="review",
        semantic_review_completed=True,
        artifact_snapshots={"prompts/refund.txt": PROMPT_MEDIUM},
    )
    record_audit_feedback_event(
        db_path,
        audit_id=older_audit.id,
        kind="explicit_feedback",
        source="feedback_link",
        payload_json='{"sentiment":"helpful","notes":"Useful"}',
    )

    selected_audit = record_audit_result(
        db_path,
        job_id=102,
        repo_full="doria90/dummyAI",
        pr_number=21,
        pr_title="Expand direct refund authority",
        installation_id=123,
        head_sha="sha-relevance-21",
        deterministic_analysis=analyze_diff(PROMPT_DIFF),
        status="completed",
        completion_mode="completed",
        output_mode="full_semantic_review",
        comment_body="## ❌ Vipari: Escalate before merge\nSummary: This PR expands direct refund authority and needs human review.\nRisk Level: High",
        comment_mode="review",
        semantic_review_completed=True,
        artifact_snapshots={"prompts/refund.txt": PROMPT_CURRENT},
    )
    record_audit_feedback_event(
        db_path,
        audit_id=selected_audit.id,
        kind="reaction",
        source="github_reaction",
        payload_json='{"content":"+1","target_kind":"review"}',
    )
    record_audit_feedback_event(
        db_path,
        audit_id=selected_audit.id,
        kind="explicit_feedback",
        source="feedback_link",
        payload_json='{"sentiment":"helpful","notes":"Accurate callout."}',
    )
    record_audit_feedback_event(
        db_path,
        audit_id=selected_audit.id,
        kind="pr_outcome",
        source="lifecycle",
        payload_json='{"outcome":"merged_despite_warning","recommendation_lane":"escalate_before_merge"}',
    )

    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        repo_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard?pr=21&head_sha=sha-relevance-21",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["pr_review_routes"]["route_count"] == 2
    assert payload["pr_review_routes"]["selected_route"]["pr_number"] == 21
    assert payload["pr_review_routes"]["selected_route"]["pr_title"] == "Expand direct refund authority"
    assert payload["pr_review_routes"]["selected_route"]["head_sha"] == "sha-relevance-21"
    assert payload["pr_review_routes"]["selected_route"]["summary"] == "This PR expands direct refund authority and needs human review."
    assert payload["pr_review_routes"]["selected_route"]["review_body"].startswith("## ❌ Vipari: Escalate before merge")
    assert payload["pr_review_routes"]["selected_route"]["review_excerpt"] == "This PR expands direct refund authority and needs human review."
    assert payload["pr_review_routes"]["selected_route"]["changed_artifact_count"] == 1
    assert payload["pr_review_routes"]["selected_route"]["finding_count"] == len(payload["pr_review_routes"]["selected_route"]["top_findings"])
    assert payload["pr_review_routes"]["selected_route"]["feedback"]["reaction_count"] == 1
    assert payload["pr_review_routes"]["selected_route"]["feedback"]["helpful_count"] == 1
    assert payload["pr_review_routes"]["selected_route"]["feedback"]["outcome_count"] == 1
    assert payload["pr_review_routes"]["selected_route"]["recent_feedback"][0]["title"] == "PR outcome: merged despite warning"
    assert payload["pr_review_routes"]["selected_route"]["recent_feedback"][1]["title"] == "Marked helpful"
    comparison_summary = payload["pr_review_routes"]["selected_route"]["baseline_comparison"]["summary"]
    assert comparison_summary["touched_artifact_count"] == 1
    assert comparison_summary["flagged_artifact_count"] == payload["pr_review_routes"]["selected_route"]["finding_count"]
    assert comparison_summary["authoritative_baseline_count"] + comparison_summary["fallback_reference_count"] + comparison_summary["missing_baseline_count"] == comparison_summary["touched_artifact_count"]
    assert payload["pr_review_routes"]["selected_route"]["baseline_comparison"]["artifact_rows"][0]["artifact_path"] == "prompts/refund.txt"
    assert payload["pr_review_routes"]["selected_route"]["baseline_comparison"]["artifact_rows"][0]["findings_count"] == payload["pr_review_routes"]["selected_route"]["finding_count"]
    assert payload["pr_review_routes"]["selected_route"]["baseline_comparison"]["artifact_rows"][0]["comparison"]["dominant_shifts"]
    assert payload["pr_review_routes"]["route_search_entries"][0]["pr_label"] == "PR #21"
    assert payload["pr_review_routes"]["route_search_entries"][0]["pr_title"] == "Expand direct refund authority"
    assert payload["pr_review_routes"]["route_search_entries"][0]["short_head_sha"] == "sha-rel"
    assert payload["pr_review_routes"]["route_search_entries"][1]["pr_label"] == "PR #20"
    assert payload["pr_review_routes"]["route_search_entries"][1]["pr_title"] == "Stabilize refund policy prompts"
    assert payload["pr_review_routes"]["routes"][0]["selected"] is True
    assert payload["pr_review_routes"]["routes"][0]["dashboard_url"].endswith("/dashboard/doria90%2FdummyAI?tab=pr-reviews&pr=21&head_sha=sha-relevance-21")


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
    session = _create_dashboard_owner_session(db_path)
    with TestClient(main.app) as client:
        pending_response = client.get(
            "/api/repos/doria90/dummyAI/baseline/pending",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        approve_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/approve",
            json={"note": "Approved for live posture tracking."},
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

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
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 200
    rebaseline_payload = rebaseline_response.json()
    assert rebaseline_payload["created_baseline_count"] == 1
    assert rebaseline_payload["dashboard"]["onboarding"]["status"] == "pending_baseline_approval"
    assert rebaseline_payload["dashboard"]["baseline_review"]["is_pending_review"] is True
    assert rebaseline_payload["dashboard"]["baseline_version_count"] == 2
    assert rebaseline_payload["dashboard"]["selected_baseline_source_snapshot_id"] is None

    with TestClient(main.app) as client:
        approve_rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/approve",
            json={"note": "Approve the candidate baseline."},
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert approve_rebaseline_response.status_code == 200
    approved_rebaseline_payload = approve_rebaseline_response.json()
    assert approved_rebaseline_payload["dashboard"]["onboarding"]["status"] == "baseline_approved"
    assert approved_rebaseline_payload["dashboard"]["baseline_review"]["is_pending_review"] is False
    assert approved_rebaseline_payload["dashboard"]["selected_baseline_source_snapshot_id"] == current_snapshot_id


def test_pr_review_routes_payload_marks_lifecycle_only_merged_routes(tmp_path):
    db_path = str(tmp_path / "dashboard-pr-lifecycle.db")
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

    lifecycle_job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=77,
        pr_title="Merge tracked refund update",
        installation_id=123,
        head_sha="sha-lifecycle-77",
        diff_text="",
        pr_state="closed",
        pr_merged=True,
        pr_merged_at=time.time(),
    )
    record_audit_result(
        db_path,
        job_id=lifecycle_job.id,
        repo_full="doria90/dummyAI",
        pr_number=77,
        pr_title="Merge tracked refund update",
        installation_id=123,
        head_sha="sha-lifecycle-77",
        pr_state="closed",
        pr_merged=True,
        pr_merged_at=time.time(),
        deterministic_analysis=analyze_diff(""),
        status="completed",
        completion_mode="lifecycle_only",
        output_mode="lifecycle_tracking",
        comment_body=None,
        comment_mode=None,
        semantic_review_completed=False,
        suggested_risk_level="unknown",
    )

    payload = build_repo_pr_review_routes_payload(db_path, "doria90/dummyAI")

    assert payload["route_count"] == 1
    assert payload["selected_route"]["pr_number"] == 77
    assert payload["selected_route"]["lifecycle_label"] == "Merged"
    assert payload["selected_route"]["output_mode"] == "lifecycle_tracking"
    assert payload["selected_route"]["summary"] == "Vipari recorded this PR lifecycle route as merged to keep history and merge tracking visible."
    assert payload["route_search_entries"][0]["lifecycle_label"] == "Merged"


def test_pr_review_routes_payload_aggregates_merged_state_across_pr_routes(tmp_path):
    db_path = str(tmp_path / "dashboard-pr-route-aggregate.db")
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

    open_job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=88,
        pr_title="Multi-route PR",
        installation_id=123,
        head_sha="sha-route-open",
        diff_text=PROMPT_DIFF,
        pr_state="open",
        pr_merged=False,
    )
    record_audit_result(
        db_path,
        job_id=open_job.id,
        repo_full="doria90/dummyAI",
        pr_number=88,
        pr_title="Multi-route PR",
        installation_id=123,
        head_sha="sha-route-open",
        pr_state="open",
        pr_merged=False,
        deterministic_analysis=analyze_diff(PROMPT_DIFF),
        status="completed",
        completion_mode="completed",
        output_mode="full_semantic_review",
        comment_body=None,
        comment_mode=None,
        semantic_review_completed=True,
    )

    merged_job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=88,
        pr_title="Multi-route PR",
        installation_id=123,
        head_sha="sha-route-merged",
        diff_text="",
        pr_state="closed",
        pr_merged=True,
        pr_merged_at=time.time(),
    )
    record_audit_result(
        db_path,
        job_id=merged_job.id,
        repo_full="doria90/dummyAI",
        pr_number=88,
        pr_title="Multi-route PR",
        installation_id=123,
        head_sha="sha-route-merged",
        pr_state="closed",
        pr_merged=True,
        pr_merged_at=time.time(),
        deterministic_analysis=analyze_diff(""),
        status="completed",
        completion_mode="lifecycle_only",
        output_mode="lifecycle_tracking",
        comment_body=None,
        comment_mode=None,
        semantic_review_completed=False,
        suggested_risk_level="unknown",
    )

    payload = build_repo_pr_review_routes_payload(db_path, "doria90/dummyAI", pr_number=88)

    assert payload["selected_route"]["lifecycle_label"] == "Merged"
    assert all(route["lifecycle_label"] == "Merged" for route in payload["routes"] if route["pr_number"] == 88)
    assert all(entry["lifecycle_label"] == "Merged" for entry in payload["route_search_entries"] if entry["pr_number"] == 88)


def test_repo_dashboard_api_keeps_selected_older_pr_route_when_outside_recent_limit(tmp_path):
    db_path = str(tmp_path / "api-dashboard-routes-older-selection.db")
    init_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.settings.local_debug_disable_login = False

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )

    for offset in range(10):
        record_audit_result(
            db_path,
            job_id=200 + offset,
            repo_full="doria90/dummyAI",
            pr_number=50 + offset,
            installation_id=123,
            head_sha=f"sha-route-{offset}",
            deterministic_analysis=analyze_diff(PROMPT_DIFF),
            status="completed",
            completion_mode="completed",
            output_mode="full_semantic_review",
            comment_body=f"## Review {offset}\nSummary: Route {offset} review.",
            comment_mode="review",
            semantic_review_completed=True,
        )

    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        repo_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard?pr=50&head_sha=sha-route-0",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["pr_review_routes"]["route_count"] == 10
    assert len(payload["pr_review_routes"]["routes"]) == 8
    assert payload["pr_review_routes"]["selected_route"]["pr_number"] == 50
    assert payload["pr_review_routes"]["selected_route"]["head_sha"] == "sha-route-0"
    assert payload["pr_review_routes"]["routes"][0]["selected"] is True
    assert payload["pr_review_routes"]["routes"][0]["pr_number"] == 50


def test_dashboard_api_local_debug_still_requires_session_for_mutations(tmp_path):
    db_path = str(tmp_path / "api-dashboard-local-debug-mutations.db")
    init_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    main.settings.local_debug_disable_login = True

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )
    _create_dashboard_owner_session(db_path)

    try:
        with TestClient(main.app) as client:
            promote_response = client.post(
                "/api/repos/doria90/dummyAI/artifacts/prompts/refund.txt/baseline",
            )
    finally:
        main.AUDIT_DB_PATH = original_db_path
        main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert promote_response.status_code == 401
    assert promote_response.json()["detail"] == "Authentication required."


def test_dashboard_api_local_debug_allows_read_without_session(tmp_path):
    db_path = str(tmp_path / "api-dashboard-local-debug-read.db")
    init_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    main.settings.local_debug_disable_login = True

    from services.control_plane_records import create_workspace, upsert_github_identity

    user, _identity = upsert_github_identity(
        db_path,
        github_user_id="702",
        github_login="debug-owner",
        display_name="Debug Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    create_workspace(
        db_path,
        slug="debug-workspace",
        display_name="Debug Workspace",
        billing_owner_user_id=user.id,
    )

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )

    try:
        with TestClient(main.app) as client:
            overview_response = client.get("/api/dashboard/overview")
    finally:
        main.AUDIT_DB_PATH = original_db_path
        main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert overview_response.status_code == 200
    assert "attention_repos" in overview_response.json()


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
    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        approve_repo = client.post(
            "/api/repos/doria90/dummyAI/baseline/approve",
            json={"note": "approve repo baseline"},
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert approve_repo.status_code == 200

    def test_init_db_repairs_legacy_baseline_audit_log_columns(tmp_path):
        db_path = str(tmp_path / "legacy-baseline-approval.db")

        with connect_sqlite(db_path, foreign_keys=True) as conn:
            conn.execute(
                """
                CREATE TABLE schema_migrations (
                    version TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                ("0001_bootstrap_relational_schema", "legacy bootstrap", 1.0),
            )
            conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                ("0002_add_pull_request_audits_fused_confidence", "legacy audit repair", 2.0),
            )
            conn.execute(
                """
                CREATE TABLE repository_onboardings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_full TEXT NOT NULL,
                    installation_id INTEGER NOT NULL,
                    default_branch TEXT NOT NULL,
                    status TEXT NOT NULL,
                    discovered_artifact_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    approved_by TEXT,
                    approved_at REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE onboarding_baseline_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    onboarding_id INTEGER NOT NULL,
                    onboarded_artifact_id INTEGER NOT NULL,
                    normalized_artifact_id TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    version_hash TEXT NOT NULL,
                    signal_terms_json TEXT NOT NULL,
                    line_count INTEGER NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    content_text TEXT,
                    approval_status TEXT NOT NULL DEFAULT 'pending',
                    approved_by TEXT,
                    approved_at REAL,
                    approval_note TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE baseline_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_full TEXT NOT NULL,
                    onboarding_id INTEGER NOT NULL,
                    artifact_path TEXT,
                    action TEXT NOT NULL,
                    actor_login TEXT,
                    note TEXT,
                    baseline_version_id INTEGER,
                    snapshot_id INTEGER,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO repository_onboardings (
                    id, repo_full, installation_id, default_branch, status, discovered_artifact_count, created_at, updated_at, approved_by, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "doria90/hermes-agent", 123, "main", "pending_baseline_approval", 1, 10.0, 10.0, None, None),
            )

        init_db(db_path)

        with connect_sqlite(db_path, foreign_keys=True) as conn:
            baseline_audit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(baseline_audit_log)").fetchall()}
        assert "decision_type" in baseline_audit_columns
        assert "linked_findings_json" in baseline_audit_columns

        record = record_baseline_audit_log(
            db_path,
            repo_full="doria90/hermes-agent",
            onboarding_id=1,
            artifact_path=None,
            action="approve_repo_baseline",
            decision_type="human_review_approved",
            actor_login="reviewer",
            note="Approved after review",
            linked_findings=[],
            snapshot_id=None,
        )

        assert record.decision_type == "human_review_approved"
        assert record.linked_findings == []
        assert list_baseline_audit_log_for_onboarding(db_path, 1)[-1].action == "approve_repo_baseline"
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
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 200
    rebaseline_payload = rebaseline_response.json()
    assert rebaseline_payload["created_baseline_count"] == 1
    assert rebaseline_payload["dashboard"]["onboarding"]["status"] == "pending_baseline_approval"


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
    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

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
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        dashboard_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 200
    rebaseline_payload = rebaseline_response.json()
    assert rebaseline_payload["dashboard"]["onboarding"]["status"] == "pending_baseline_approval"
    assert rebaseline_payload["dashboard"]["selected_baseline_source_snapshot_id"] is None

    with TestClient(main.app) as client:
        approve_rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/approve",
            json={"note": "Approve branch-head rebaseline."},
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        dashboard_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert approve_rebaseline_response.status_code == 200

    assert dashboard_response.status_code == 200
    dashboard_payload = dashboard_response.json()
    assert dashboard_payload["selected_baseline_source_snapshot_id"] == branch_head_snapshot["id"]
    assert any(
        snapshot["id"] == branch_head_snapshot["id"] and snapshot["snapshot_type"] == "branch_head"
        for snapshot in dashboard_payload["journey_snapshots"]
    )


def test_dashboard_api_rebaseline_route_uses_resolved_github_private_key(tmp_path):
    db_path = str(tmp_path / "api-dashboard-rebaseline-key.db")
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
    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        approve_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/approve",
            json={"note": "Approve baseline before re-baselining."},
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert approve_response.status_code == 200
    current_snapshot = next(snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "current")

    with patch.object(type(main.settings), "resolved_github_private_key", new_callable=PropertyMock, return_value="resolved-private-key"), patch(
        "services.runtime_guardrails.generate_jwt", return_value="jwt-token"
    ), patch("main.generate_jwt", return_value="jwt-token") as generate_jwt_mock, patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_file_content", return_value=PROMPT_CURRENT), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": current_snapshot["id"], "rationale": "Use current checkpoint."},
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 200
    generate_jwt_mock.assert_called_once_with(
        main.GITHUB_APP_ID,
        main.GITHUB_PRIVATE_KEY_PATH,
        "resolved-private-key",
    )


def test_dashboard_api_rebaseline_route_passes_snapshot_ref_as_keyword_argument(tmp_path):
    db_path = str(tmp_path / "api-dashboard-rebaseline-ref-keyword.db")
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
    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert journey_response.status_code == 200
    current_snapshot = next(snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "current")

    def _keyword_only_fetch(repo, path, token, *, ref):
        assert ref == current_snapshot["commit_sha"]
        return PROMPT_CURRENT

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_file_content", side_effect=_keyword_only_fetch), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": current_snapshot["id"], "rationale": None},
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 200
    assert rebaseline_response.json()["created_baseline_count"] == 1


def test_dashboard_api_rebaseline_returns_502_when_github_access_fails(tmp_path):
    db_path = str(tmp_path / "api-dashboard-rebaseline-github-failure.db")
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
    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert journey_response.status_code == 200
    current_snapshot = next(snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "current")

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token",
        side_effect=HTTPError("https://api.github.com/app/installations/123/access_tokens", 403, "Forbidden", hdrs=None, fp=None),
    ), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": current_snapshot["id"], "rationale": "Retry later."},
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 502
    assert "Unable to access repository contents" in rebaseline_response.json()["detail"]


def test_dashboard_api_rebaseline_succeeds_when_post_write_rebuild_fails(tmp_path):
    db_path = str(tmp_path / "api-dashboard-rebaseline-post-write-failure.db")
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
    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert journey_response.status_code == 200
    current_snapshot = next(snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "current")

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_file_content", return_value=PROMPT_CURRENT), patch(
        "services.baseline_approval_service.build_repo_journey", side_effect=RuntimeError("journey rebuild failed")
    ), patch("main.build_repo_dashboard_view", side_effect=RuntimeError("dashboard rebuild failed")), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": current_snapshot["id"], "rationale": "Use this checkpoint."},
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 200
    rebaseline_payload = rebaseline_response.json()
    assert rebaseline_payload["created_baseline_count"] == 1
    assert rebaseline_payload["dashboard"] is None


def test_dashboard_api_rebaseline_returns_structured_500_for_internal_failure(tmp_path):
    db_path = str(tmp_path / "api-dashboard-rebaseline-internal-failure.db")
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
    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert journey_response.status_code == 200
    current_snapshot = next(snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "current")

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_file_content", return_value=PROMPT_CURRENT), patch(
        "services.baseline_approval_service.create_onboarding_baseline_version",
        side_effect=RuntimeError("insert failed"),
    ), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": current_snapshot["id"], "rationale": None},
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 500
    assert rebaseline_response.headers["content-type"].startswith("application/json")
    assert "Unable to store a baseline candidate" in rebaseline_response.json()["detail"]


def test_dashboard_api_uses_selected_baseline_for_journey_comparison(tmp_path):
    db_path = str(tmp_path / "api-dashboard-selected-baseline.db")
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
            "sha-1": PROMPT_MEDIUM,
            "sha-2": PROMPT_CURRENT,
        }[ref],
    )
    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert journey_response.status_code == 200
    historical_snapshot = next(
        snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "historical_commit"
    )

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_file_content", return_value=PROMPT_CURRENT), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": historical_snapshot["id"], "rationale": ""},
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        dashboard_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 200
    assert dashboard_response.status_code == 200
    dashboard_payload = dashboard_response.json()
    assert dashboard_payload["selected_baseline_source_snapshot_id"] is None

    with TestClient(main.app) as client:
        approve_rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/approve",
            json={"note": "Approve historical rebaseline."},
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        approved_dashboard_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert approve_rebaseline_response.status_code == 200
    assert approved_dashboard_response.status_code == 200
    approved_dashboard_payload = approved_dashboard_response.json()
    assert approved_dashboard_payload["selected_baseline_source_snapshot_id"] == historical_snapshot["id"]
    assert approved_dashboard_payload["journey_comparison"]["left"]["id"] == historical_snapshot["id"]
    assert approved_dashboard_payload["journey_comparison"]["drift_summary"]["pair_distance"] > 0
    assert approved_dashboard_payload["journey_comparison"]["drift_summary"]["right_distance_from_selected_baseline"] > 0
    approved_snapshots = approved_dashboard_payload["journey_snapshots"]
    selected_snapshot = next(snapshot for snapshot in approved_snapshots if snapshot["id"] == historical_snapshot["id"])
    original_baseline_snapshot = next(snapshot for snapshot in approved_snapshots if snapshot["snapshot_type"] == "baseline_approved")
    current_anchor_snapshot = next(snapshot for snapshot in approved_snapshots if snapshot["is_current_anchor"])

    assert selected_snapshot["is_reference_baseline"] is True
    assert selected_snapshot["is_approved_baseline"] is True
    assert selected_snapshot["baseline_marker_label"] == "Reference baseline"
    assert original_baseline_snapshot["is_approved_baseline"] is True
    assert original_baseline_snapshot["baseline_marker_label"] == "Approved baseline"
    assert current_anchor_snapshot["snapshot_type"] == "current"


def test_dashboard_api_rebaseline_auto_approves_when_workspace_policy_is_auto(tmp_path):
    db_path = str(tmp_path / "api-rebaseline-auto-approve.db")
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
            "sha-1": PROMPT_MEDIUM,
            "sha-2": PROMPT_CURRENT,
        }[ref],
    )
    session = _create_dashboard_owner_session(db_path)

    from services.control_plane_records import update_workspace_baseline_approval_mode

    update_workspace_baseline_approval_mode(db_path, session.workspace_id, baseline_approval_mode="auto")

    with TestClient(main.app) as client:
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    historical_snapshot = next(
        snapshot for snapshot in journey_response.json()["snapshots"] if snapshot["snapshot_type"] == "historical_commit"
    )

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_file_content", return_value=PROMPT_CURRENT), TestClient(main.app) as client:
        rebaseline_response = client.post(
            "/api/repos/doria90/dummyAI/baseline/rebaseline",
            json={"snapshot_id": historical_snapshot["id"], "rationale": "Auto-approve baseline."},
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        dashboard_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert rebaseline_response.status_code == 200
    assert rebaseline_response.json()["baseline_candidate_status"] == "approved"
    assert rebaseline_response.json()["auto_approved"] is True
    assert dashboard_response.status_code == 200
    dashboard_payload = dashboard_response.json()
    assert dashboard_payload["selected_baseline_source_snapshot_id"] == historical_snapshot["id"]
    assert dashboard_payload["baseline_review"]["is_pending_review"] is False


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
    session = _create_dashboard_owner_session(db_path)
    snapshots = build_repo_journey(db_path, "doria90/dummyAI")
    baseline_snapshot = next(snapshot for snapshot in snapshots if snapshot.snapshot_type == "baseline_approved")
    current_snapshot = next(snapshot for snapshot in snapshots if snapshot.snapshot_type == "current")

    with TestClient(main.app) as client:
        journey_response = client.get(
            "/api/repos/doria90/dummyAI/journey",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        snapshot_response = client.get(
            f"/api/repos/doria90/dummyAI/snapshots/{baseline_snapshot.id}",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        compare_response = client.get(
            f"/api/repos/doria90/dummyAI/compare?left={baseline_snapshot.id}&right={current_snapshot.id}",
            cookies={main.settings.session_cookie_name: session.session_id},
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

    session = _create_dashboard_owner_session(db_path, repo_full="doria90/openfang", installation_id=124)
    dummy_snapshot = build_repo_journey(db_path, "doria90/dummyAI")[0]
    build_repo_journey(db_path, "doria90/openfang")

    with TestClient(main.app) as client:
        response = client.get(
            f"/api/repos/doria90/openfang/snapshots/{dummy_snapshot.id}",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

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


def test_dashboard_overview_api_filter_mine_limits_repos_to_current_allocator(tmp_path):
    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        init_control_plane_db,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
        update_repo_allocation_status,
    )

    db_path = str(tmp_path / "api-dashboard-overview-mine.db")
    init_db(db_path)
    init_control_plane_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_app_base_url = main.settings.app_base_url
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    main.settings.app_base_url = "https://app.promptdrift.test"
    main.settings.local_debug_disable_login = False

    primary_user, _primary_identity = upsert_github_identity(
        db_path,
        github_user_id="101",
        github_login="primary-user",
        display_name="Primary User",
        primary_email="primary@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-primary-token",
    )
    secondary_user, _secondary_identity = upsert_github_identity(
        db_path,
        github_user_id="202",
        github_login="secondary-user",
        display_name="Secondary User",
        primary_email="secondary@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-secondary-token",
    )

    workspace = create_workspace(
        db_path,
        slug="mine-filter-workspace",
        display_name="Mine Filter Workspace",
        billing_owner_user_id=primary_user.id,
    )
    session = create_user_session(
        db_path,
        session_id="mine-filter-session",
        user_id=primary_user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        db_path,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_mine_filter",
        stripe_price_id="price_mine_filter",
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
        account_id="acct-123",
        account_login="promptdrift-org",
        account_type="Organization",
        target_type="Organization",
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
            fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_CURRENT if repo == "doria90/dummyAI" else PROMPT_BASELINE,
        )

    primary_allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=123,
        repo_github_id="dummyAI",
        repo_full="doria90/dummyAI",
        baseline_mode="onboarding",
        activated_by_user_id=primary_user.id,
    )
    update_repo_allocation_status(db_path, primary_allocation.id, "onboarded")
    secondary_allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=123,
        repo_github_id="openfang",
        repo_full="doria90/openfang",
        baseline_mode="onboarding",
        activated_by_user_id=secondary_user.id,
    )
    update_repo_allocation_status(db_path, secondary_allocation.id, "onboarded")

    with TestClient(main.app) as client:
        response = client.get(
            "/api/dashboard/overview?filter=mine",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_base_url = original_app_base_url
    main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert response.status_code == 200
    payload = response.json()
    assert [repo["repo_full"] for repo in payload["repos"]] == ["doria90/dummyAI"]
    assert [repo["repo_full"] for repo in payload["nav_repos"]] == ["doria90/dummyAI"]
    assert [repo["repo_full"] for repo in payload["overview_sections"]["recent_changes"]["repos"]] == ["doria90/dummyAI"]
    assert payload["overview_sections"]["urgent_queue"]["repo_count"] >= 1


def test_pending_proposals_api_requires_repo_visibility(tmp_path):
    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        init_control_plane_db,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
        update_repo_allocation_status,
    )

    db_path = str(tmp_path / "api-dashboard-pending-visibility.db")
    init_db(db_path)
    init_control_plane_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_app_base_url = main.settings.app_base_url
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    main.settings.app_base_url = "https://app.promptdrift.test"
    main.settings.local_debug_disable_login = False

    user, _identity = upsert_github_identity(
        db_path,
        github_user_id="301",
        github_login="visibility-user",
        display_name="Visibility User",
        primary_email="visibility@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-visibility-token",
    )
    workspace = create_workspace(
        db_path,
        slug="pending-visibility-workspace",
        display_name="Pending Visibility Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        db_path,
        session_id="pending-visibility-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        db_path,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_pending_visibility",
        stripe_price_id="price_pending_visibility",
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
        installation_id=321,
        account_id="acct-321",
        account_login="promptdrift-org-visibility",
        account_type="Organization",
        target_type="Organization",
        status="active",
    )
    replace_repo_connections(
        db_path,
        workspace_id=workspace.id,
        installation_id=321,
        repositories=[
            {
                "repo_github_id": "visible-repo",
                "repo_full": "doria90/visible-repo",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )

    onboard_repository(
        db_path,
        repo_full="doria90/visible-repo",
        installation_id=321,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )
    visible_allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=321,
        repo_github_id="visible-repo",
        repo_full="doria90/visible-repo",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(db_path, visible_allocation.id, "onboarded")

    onboard_repository(
        db_path,
        repo_full="doria90/hidden-repo",
        installation_id=999,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/repos/doria90/hidden-repo/proposals/pending",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_base_url = original_app_base_url
    main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert response.status_code == 404


def test_pending_proposals_api_scopes_to_workspace_and_preserves_agent_origin(tmp_path):
    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_machine_principal,
        create_user_session,
        create_workspace,
        init_control_plane_db,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
        update_repo_allocation_status,
    )
    from services.internal_auth import PRINCIPAL_KIND_HUMAN_OPERATOR, PRINCIPAL_KIND_SERVICE_ACCOUNT
    from services.proposals_records import create_baseline_proposal

    db_path = str(tmp_path / "api-dashboard-pending-workspace-scope.db")
    init_db(db_path)
    init_control_plane_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_app_base_url = main.settings.app_base_url
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    main.settings.app_base_url = "https://app.promptdrift.test"
    main.settings.local_debug_disable_login = False

    primary_user, _primary_identity = upsert_github_identity(
        db_path,
        github_user_id="401",
        github_login="primary-proposal-user",
        display_name="Primary Proposal User",
        primary_email="primary-proposal@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-primary-proposal-token",
    )
    secondary_user, _secondary_identity = upsert_github_identity(
        db_path,
        github_user_id="402",
        github_login="secondary-proposal-user",
        display_name="Secondary Proposal User",
        primary_email="secondary-proposal@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-secondary-proposal-token",
    )

    primary_workspace = create_workspace(
        db_path,
        slug="primary-proposals-workspace",
        display_name="Primary Proposals Workspace",
        billing_owner_user_id=primary_user.id,
    )
    secondary_workspace = create_workspace(
        db_path,
        slug="secondary-proposals-workspace",
        display_name="Secondary Proposals Workspace",
        billing_owner_user_id=secondary_user.id,
    )
    session = create_user_session(
        db_path,
        session_id="pending-scope-session",
        user_id=primary_user.id,
        workspace_id=primary_workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    for workspace_id, suffix in ((primary_workspace.id, "primary"), (secondary_workspace.id, "secondary")):
        upsert_subscription(
            db_path,
            workspace_id=workspace_id,
            stripe_subscription_id=f"sub_pending_scope_{suffix}",
            stripe_price_id=f"price_pending_scope_{suffix}",
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
            workspace_id=workspace_id,
            payload=derive_entitlement_payload("team", "active"),
        )

    upsert_github_installation(
        db_path,
        workspace_id=primary_workspace.id,
        installation_id=123,
        account_id="acct-123",
        account_login="promptdrift-org-primary",
        account_type="Organization",
        target_type="Organization",
        status="active",
    )
    upsert_github_installation(
        db_path,
        workspace_id=secondary_workspace.id,
        installation_id=456,
        account_id="acct-456",
        account_login="promptdrift-org-secondary",
        account_type="Organization",
        target_type="Organization",
        status="active",
    )
    replace_repo_connections(
        db_path,
        workspace_id=primary_workspace.id,
        installation_id=123,
        repositories=[
            {
                "repo_github_id": "shared-repo-a",
                "repo_full": "doria90/shared-repo",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )
    replace_repo_connections(
        db_path,
        workspace_id=secondary_workspace.id,
        installation_id=456,
        repositories=[
            {
                "repo_github_id": "shared-repo-b",
                "repo_full": "doria90/shared-repo",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )

    onboarding_result = onboard_repository(
        db_path,
        repo_full="doria90/shared-repo",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )
    artifact_id = onboarding_result.artifacts[0].id

    primary_allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=primary_workspace.id,
        installation_id=123,
        repo_github_id="shared-repo-a",
        repo_full="doria90/shared-repo",
        baseline_mode="onboarding",
        activated_by_user_id=primary_user.id,
    )
    secondary_allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=secondary_workspace.id,
        installation_id=456,
        repo_github_id="shared-repo-b",
        repo_full="doria90/shared-repo",
        baseline_mode="onboarding",
        activated_by_user_id=secondary_user.id,
    )
    update_repo_allocation_status(db_path, primary_allocation.id, "onboarded")
    update_repo_allocation_status(db_path, secondary_allocation.id, "onboarded")

    primary_principal = create_machine_principal(
        db_path,
        workspace_id=primary_workspace.id,
        display_name="Primary Agent",
        principal_kind=PRINCIPAL_KIND_SERVICE_ACCOUNT,
        client_id="primary-agent",
        client_secret_encrypted="encrypted-primary-secret",
        scopes=["drift.write.low"],
    )
    secondary_principal = create_machine_principal(
        db_path,
        workspace_id=secondary_workspace.id,
        display_name="Secondary Human",
        principal_kind=PRINCIPAL_KIND_HUMAN_OPERATOR,
        client_id="secondary-human",
        client_secret_encrypted="encrypted-secondary-secret",
        scopes=["drift.write.low"],
    )

    create_baseline_proposal(
        db_path,
        artifact_id=artifact_id,
        repo_full="doria90/shared-repo",
        workspace_id=primary_workspace.id,
        snapshot_id=None,
        rationale="Primary workspace proposal",
        linked_audit_ids=[],
        metadata={},
        proposer_principal_id=primary_principal.id,
    )
    create_baseline_proposal(
        db_path,
        artifact_id=artifact_id,
        repo_full="doria90/shared-repo",
        workspace_id=secondary_workspace.id,
        snapshot_id=None,
        rationale="Secondary workspace proposal",
        linked_audit_ids=[],
        metadata={},
        proposer_principal_id=secondary_principal.id,
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/repos/doria90/shared-repo/proposals/pending",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_base_url = original_app_base_url
    main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert response.status_code == 200
    payload = response.json()
    assert payload["pending_count"] == 1
    assert len(payload["proposals"]) == 1
    assert payload["proposals"][0]["rationale"] == "Primary workspace proposal"
    assert payload["proposals"][0]["proposer_principal_id"] == primary_principal.id
    assert payload["proposals"][0]["is_agent_proposal"] is True
    assert payload["proposals"][0]["artifact_path"] == "prompts/refund.txt"


def test_repo_relevance_decisions_api_returns_repo_scoped_decisions(tmp_path):
    db_path = str(tmp_path / "api-dashboard-relevance-decisions.db")
    init_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_app_base_url = main.settings.app_base_url
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    main.settings.app_base_url = "https://app.promptdrift.test"
    main.settings.local_debug_disable_login = False

    session = _create_dashboard_owner_session(db_path, repo_full="doria90/dummyAI", installation_id=123)
    _record_pre_audit_relevance(db_path, repo_full="doria90/dummyAI", pr_number=21, head_sha="sha-relevance-21")

    with TestClient(main.app) as client:
        response = client.get(
            "/api/repos/doria90/dummyAI/relevance-decisions?pr_number=21&head_sha=sha-relevance-21",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_base_url = original_app_base_url
    main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_full"] == "doria90/dummyAI"
    assert payload["pr_number"] == 21
    assert payload["head_sha"] == "sha-relevance-21"
    assert payload["decision_count"] == 1
    assert payload["decisions"][0]["artifact_path"] == "src/assistant_router.py"
    assert payload["decisions"][0]["confidence_tier"] == "uncertain"
    assert payload["decisions"][0]["classifier_is_relevant"] is False
    assert payload["decisions"][0]["matched_signals"][0]["source"] == "path"


def test_repo_relevance_decisions_api_is_repo_scoped(tmp_path):
    db_path = str(tmp_path / "api-dashboard-relevance-scope.db")
    init_db(db_path)
    original_db_path = main.AUDIT_DB_PATH
    original_app_base_url = main.settings.app_base_url
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    main.settings.app_base_url = "https://app.promptdrift.test"
    main.settings.local_debug_disable_login = False

    session = _create_dashboard_owner_session(db_path, repo_full="doria90/visible-repo", installation_id=222)
    _record_pre_audit_relevance(db_path, repo_full="doria90/hidden-repo", pr_number=22, head_sha="sha-hidden-22")

    with TestClient(main.app) as client:
        response = client.get(
            "/api/repos/doria90/hidden-repo/relevance-decisions?pr_number=22&head_sha=sha-hidden-22",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_base_url = original_app_base_url
    main.settings.local_debug_disable_login = original_local_debug_disable_login

    assert response.status_code == 404


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
    session = _create_dashboard_owner_session(db_path)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/repos/doria90/dummyAI/artifacts/prompts/refund.txt/baseline",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["baseline"]["content_text"] == PROMPT_MEDIUM


def test_dashboard_api_exposes_pr_review_target_with_supporting_history(tmp_path):
    db_path = str(tmp_path / "api-dashboard-pr-evidence.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path)

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
    _record_pr_profile(db_path)

    with TestClient(main.app) as client:
        overview_response = client.get(
            "/api/dashboard/overview",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        escalation_response = client.get(
            "/api/dashboard/escalation-queue?include_watch=true",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        repo_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert overview_response.status_code == 200
    overview_payload = overview_response.json()
    assert overview_payload["attention_repos"][0]["highest_review_target"] == "PR #42"
    assert overview_payload["attention_repos"][0]["highest_review_pr_number"] == 42
    assert overview_payload["attention_repos"][0]["highest_review_head_sha"] == "sha-current"
    assert overview_payload["highest_risk_items"][0]["review_pr_number"] == 42
    assert overview_payload["highest_risk_items"][0]["review_head_sha"] == "sha-current"

    assert escalation_response.status_code == 200
    escalation_payload = escalation_response.json()
    assert escalation_payload["items"][0]["review_pr_number"] == 42
    assert escalation_payload["items"][0]["review_head_sha"] == "sha-current"

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["insights"][0]["evidence_label"] == "proposal + history"
    assert payload["insights"][0]["evidence_summary"] == "PR proposal evidence is available right now; start with PR #42, then compare against merged history from commit sha-2."
    assert payload["insights"][0]["review_target"] == "PR #42"
    assert payload["insights"][0]["review_url"] == "https://github.com/doria90/dummyAI/pull/42"
    assert payload["insights"][0]["review_pr_number"] == 42
    assert payload["insights"][0]["review_head_sha"] == "sha-current"
    assert payload["insights"][0]["supporting_review_target"] == "commit sha-2"
    assert payload["insights"][0]["supporting_review_url"] == "https://github.com/doria90/dummyAI/commit/sha-2"


def test_dashboard_api_marks_baseline_only_profiles_as_not_promotable(tmp_path):
    db_path = str(tmp_path / "api-dashboard.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path)

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
        repo_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

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
    session = _create_dashboard_owner_session(db_path)

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
        response = client.get(
            "/api/repos/doria90/dummyAI/artifacts/prompts/refund.txt/episodes",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

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
    session = _create_dashboard_owner_session(db_path)

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
        response = client.get(
            "/api/repos/doria90/dummyAI/artifacts/prompts/missing.txt/episodes",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "No artifact storyline is available for this repo artifact."


def test_dashboard_api_groups_low_signal_artifacts_into_one_lower_confidence_item(tmp_path):
    db_path = str(tmp_path / "api-dashboard-grouped-low-signal.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path)

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

    with TestClient(main.app) as client:
        repo_response = client.get(
            "/api/repos/doria90/dummyAI/dashboard",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["onboarding"]["discovered_artifact_count"] == 2
    assert {artifact["artifact_path"] for artifact in payload["artifacts"]} == {
        "guides/assistant-faq.md",
        "prompts/system.txt",
    }
    assert len(payload["insights"]) == 1
    assert payload["insights"][0]["artifact_path"] == "prompts/system.txt"
    assert len(payload["lower_confidence_insights"]) == 1
    assert payload["lower_confidence_insights"][0]["artifact_path"] == "guides/assistant-faq.md"
