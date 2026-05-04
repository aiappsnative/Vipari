import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

import main
from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile


def _seed_compliance_workspace(db_path: str):
    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.export_jobs import create_export_job, update_export_job_status
    from services.onboarding_records import DiscoveredArtifactInput, record_repository_onboarding

    user, _identity = upsert_github_identity(
        db_path,
        github_user_id="980",
        github_login="compliance-api-owner",
        display_name="Compliance API Owner",
        primary_email="compliance-api-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        db_path,
        slug="compliance-api-workspace",
        display_name="Compliance API Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        db_path,
        session_id="compliance-api-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        db_path,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_compliance_api",
        stripe_price_id="price_compliance_api",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        db_path,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=9800,
        account_id="9800",
        account_login="compliance-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        db_path,
        workspace_id=workspace.id,
        installation_id=9800,
        repositories=[
            {
                "repo_github_id": "1",
                "repo_full": "compliance-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
            {
                "repo_github_id": "2",
                "repo_full": "compliance-org/repo-two",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )
    record_repository_onboarding(
        db_path,
        repo_full="compliance-org/repo-one",
        installation_id=9800,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                discovery_reason="Prompt file",
                confidence=0.9,
                baseline_content="You must follow the approved workflow.",
            ),
            DiscoveredArtifactInput(
                artifact_path="policies/governance.md",
                artifact_type="policy",
                discovery_reason="Governance policy",
                confidence=0.8,
                baseline_content="Human review is required for sensitive changes.",
            ),
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    record_repository_onboarding(
        db_path,
        repo_full="compliance-org/repo-two",
        installation_id=9800,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="tools/agent_tool.py",
                artifact_type="tool",
                discovery_reason="Tool implementation",
                confidence=0.8,
                baseline_content="def run_tool():\n    return 'ok'",
            ),
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    stale_timestamp = time.time() - (45 * 86400)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE repository_onboardings SET updated_at = ? WHERE repo_full = ?",
            (stale_timestamp, "compliance-org/repo-two"),
        )
    job = create_export_job(
        db_path=db_path,
        repo_full="compliance-org/repo-one",
        from_ts=1700000000,
        to_ts=1700086400,
        workspace_id=workspace.id,
        requested_by_user_id=user.id,
        requested_by_github_login="compliance-api-owner",
        export_mode="compliance",
        include_artifact_content=False,
    )
    update_export_job_status(
        db_path,
        job.id,
        "completed",
        result_size_bytes=14,
        result_sha256="abc123",
        result_blob=b"zip-artifact",
    )
    return session


def test_compliance_api_exposes_readiness_frameworks_and_evidence_payloads(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-api.db")
    main.init_db(main.AUDIT_DB_PATH)

    session = _seed_compliance_workspace(main.AUDIT_DB_PATH)

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        readiness_response = client.get("/api/compliance/readiness")
        frameworks_response = client.get("/api/compliance/frameworks")
        evidence_response = client.get("/api/compliance/evidence")

    assert readiness_response.status_code == 200
    readiness_payload = readiness_response.json()
    assert readiness_payload["workspace_name"] == "Compliance API Workspace"
    assert readiness_payload["verdict"]["tone"] == "warning"
    assert readiness_payload["repo_rows"][0]["repo_full"] == "compliance-org/repo-one"
    assert any(item["key"] == "missing_governance" for item in readiness_payload["top_gaps"])

    assert frameworks_response.status_code == 200
    frameworks_payload = frameworks_response.json()
    assert len(frameworks_payload["framework_cards"]) == 3
    assert frameworks_payload["framework_cards"][0]["title"] == "EU AI Act"

    assert evidence_response.status_code == 200
    evidence_payload = evidence_response.json()
    stale_row = next(row for row in evidence_payload["evidence_rows"] if row["repo_full"] == "compliance-org/repo-two")
    assert stale_row["freshness_label"] == "Stale (45d)"
    assert any(row["repo_full"] == "compliance-org/repo-two" for row in evidence_payload["repo_rows"])

    main.AUDIT_DB_PATH = original_db_path


def test_compliance_api_exports_returns_summary_and_jobs(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-exports-api.db")
    main.init_db(main.AUDIT_DB_PATH)

    session = _seed_compliance_workspace(main.AUDIT_DB_PATH)

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        response = client.get("/api/compliance/exports")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["ready_repo_count"] == 1
    assert payload["summary"]["completed_count"] == 1
    assert payload["summary"]["latest_status_label"] == "Completed"
    assert len(payload["jobs"]) == 1
    assert payload["jobs"][0]["repo_full"] == "compliance-org/repo-one"
    assert payload["jobs"][0]["download_url"]

    main.AUDIT_DB_PATH = original_db_path