from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient
import pytest

import main
import services.mcp_broker as mcp_broker
from config import Settings
from services.analysis_budget import consume_analysis_budget, reserve_analysis_budget
from services.audit_jobs import init_db
from services.control_plane_records import (
    allocate_repo_to_workspace,
    create_machine_principal,
    create_workspace,
    list_control_plane_audit_logs_for_workspace,
    replace_repo_connections,
    revoke_machine_principal,
    upsert_entitlement,
    upsert_github_identity,
    upsert_github_installation,
    upsert_subscription,
    update_repo_allocation_status,
)
from services.entitlements import derive_entitlement_payload
from services.export_jobs import create_export_job
from services.onboarding import onboard_repository
from services.secure_store import encrypt_text


PROMPT_BASELINE = """# Refund Copilot
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Use the billing sandbox tool in read mode.
max_steps: 2
temperature: 0.2
"""


@pytest.fixture(autouse=True)
def _reset_mcp_broker_rate_limiters():
    mcp_broker._mcp_token_endpoint_limiter = mcp_broker._SlidingWindowRateLimiter(limit=20, window_seconds=60.0)
    mcp_broker._mcp_invoke_limiter = mcp_broker._SlidingWindowRateLimiter(limit=120, window_seconds=60.0)
    mcp_broker._mcp_mutation_limiter = mcp_broker._SlidingWindowRateLimiter(limit=12, window_seconds=60.0)
    yield


@pytest.fixture(autouse=True)
def _disable_local_debug_login_for_mcp_tests():
    original = main.settings.local_debug_disable_login
    main.settings.local_debug_disable_login = False
    try:
        yield
    finally:
        main.settings.local_debug_disable_login = original


def _issue_broker_token(client: TestClient, client_id: str, client_secret: str) -> str:
    response = client.post(
        "/api/agent-integrations/mcp/token",
        json={"client_id": client_id, "client_secret": client_secret},
    )
    assert response.status_code == 200
    return response.json()["token"]


def _bearer_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_mcp_workspace(
    db_path: str,
    *,
    repo_full: str = "doria90/dummyAI",
    scopes: list[str] | None = None,
) -> tuple[str, str]:
    init_db(db_path)
    user, _identity = upsert_github_identity(
        db_path,
        github_user_id="mcp-user-1",
        github_login="mcp-owner",
        display_name="MCP Owner",
        primary_email="mcp-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        db_path,
        slug="mcp-workspace",
        display_name="MCP Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_subscription(
        db_path,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_mcp",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=time.time() + 86400,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=derive_entitlement_payload("team", "active"))
    upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=123,
        account_id="acct-123",
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
        installation_id=123,
        repo_github_id=repo_full.split("/", 1)[1],
        repo_full=repo_full,
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(db_path, allocation.id, "onboarded")
    onboard_repository(
        db_path,
        repo_full=repo_full,
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda _repo, _token: "main",
        list_repository_files_fn=lambda _repo, _token, ref=None: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda _repo, _path, _token, ref=None: PROMPT_BASELINE,
    )

    client_id = "mcp-client-id"
    client_secret = "mcp-secret-value"
    create_machine_principal(
        db_path,
        workspace_id=workspace.id,
        display_name="Customer MCP",
        principal_kind="service_account",
        client_id=client_id,
        client_secret_encrypted=encrypt_text(client_secret, main.settings.app_encryption_key),
        scopes=scopes or ["drift.read"],
    )
    return client_id, client_secret


def _seed_audit_row(db_path: str, audit_id: int = 1, repo_full: str = "doria90/dummyAI") -> None:
    now = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pull_request_audits
                (id, job_id, repo_full, pr_number, installation_id, head_sha,
                 pr_state, pr_merged, pr_closed_at, pr_merged_at, pr_merge_commit_sha,
                 pr_updated_at, status, completion_mode, output_mode,
                 deterministic_score, suggested_risk_level, semantic_review_completed,
                 error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                audit_id * 100,
                repo_full,
                1,
                123,
                "abc123",
                "open",
                0,
                None,
                None,
                None,
                now,
                "completed",
                "full",
                "json",
                75,
                "medium",
                1,
                None,
                now,
                now,
            ),
        )


def _seed_export_job(db_path: str, workspace_id: int, repo_full: str = "doria90/dummyAI") -> int:
    job = create_export_job(
        db_path,
        repo_full=repo_full,
        from_ts=1_000_000.0,
        to_ts=1_100_000.0,
        workspace_id=workspace_id,
        requested_by_user_id=None,
        requested_by_github_login=None,
        export_mode="compliance",
        include_artifact_content=False,
    )
    return job.id


def _seed_onboarding_artifact(db_path: str, repo_full: str = "doria90/dummyAI", installation_id: int = 123) -> int:
    now = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repository_onboardings
                (repo_full, installation_id, default_branch, status, discovered_artifact_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_full, installation_id, "main", "completed", 1, now, now),
        )
        onboarding_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO onboarded_artifacts
                (onboarding_id, repo_full, artifact_path, artifact_type, discovery_reason, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (onboarding_id, repo_full, "prompts/main.txt", "prompt", "heuristic", 0.95, now),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_mcp_broker_tools_and_read_calls(tmp_path):
    db_path = str(tmp_path / "mcp-broker.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)
    scenario_eval_executions = [
        {
            "scenario_key": "dummyai-review-target",
            "artifact_paths": ["prompts/refund.txt"],
            "assertion_summary": {"all_passed": True, "failed_count": 0},
            "candidate_source": "seeded",
        }
    ]
    hybrid_analysis_executions = [
        {
            "analyzer_key": "prompt_policy_static_scan",
            "artifact_path": "prompts/refund.txt",
            "artifact_type": "prompt",
            "finding_count": 1,
            "highest_severity": "high",
            "findings": [{"finding_key": "internal_policy_disclosure", "severity": "high"}],
        }
    ]
    _seed_audit_row(db_path, audit_id=1)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE pull_request_audits
            SET scenario_eval_execution_count = ?,
                scenario_eval_execution_reason = ?,
                scenario_eval_executions_json = ?,
                hybrid_analysis_execution_count = ?,
                hybrid_analysis_execution_reason = ?,
                hybrid_analysis_executions_json = ?
            WHERE id = ?
            """,
            (
                1,
                "Shadow-mode scenario eval executed seeded scenario 'dummyai-review-target'.",
                json.dumps(scenario_eval_executions),
                1,
                "Shadow-mode hybrid static analysis executed 1 artifact.",
                json.dumps(hybrid_analysis_executions),
                1,
            ),
        )

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        tools_response = client.get(
            "/api/agent-integrations/mcp/tools",
            headers=_bearer_header(broker_token),
        )
        available_tools_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.list_available_tools", "arguments": {}},
            headers=_bearer_header(broker_token),
        )
        repos_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.list_repos", "arguments": {}},
            headers=_bearer_header(broker_token),
        )
        posture_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_repo_posture", "arguments": {"repo_full": "doria90/dummyAI"}},
            headers=_bearer_header(broker_token),
        )
        casefile_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_repo_casefile", "arguments": {"repo_full": "doria90/dummyAI"}},
            headers=_bearer_header(broker_token),
        )
        escalations_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.list_escalations", "arguments": {}},
            headers=_bearer_header(broker_token),
        )
        budget_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_workspace_budget_status", "arguments": {}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert tools_response.status_code == 200
    tool_names = {tool["name"] for tool in tools_response.json()["tools"]}
    assert available_tools_response.status_code == 200
    assert available_tools_response.json()["result"]["tool_count"] == len(tool_names)
    assert "drift.read" in available_tools_response.json()["result"]["granted_scopes"]
    assert "vipari.list_repos" in tool_names
    assert "vipari.get_repo_posture" in tool_names
    assert "vipari.get_workspace_budget_status" in tool_names
    assert repos_response.status_code == 200
    assert repos_response.json()["result"]["repos"][0]["repo_full"] == "doria90/dummyAI"
    assert posture_response.status_code == 200
    assert posture_response.json()["result"]["repo_full"] == "doria90/dummyAI"
    assert casefile_response.status_code == 200
    assert casefile_response.json()["result"]["coverage_summary"]["discovered_artifact_count"] >= 1
    assert casefile_response.json()["result"]["audit_brief"]["latest_execution"]["scenario_eval_execution"]["count"] == 1
    assert casefile_response.json()["result"]["audit_brief"]["latest_execution"]["scenario_eval_execution"]["executions"][0]["scenario_key"] == "dummyai-review-target"
    assert casefile_response.json()["result"]["audit_brief"]["latest_execution"]["hybrid_analysis_execution"]["executions"][0]["analyzer_key"] == "prompt_policy_static_scan"
    assert escalations_response.status_code == 200
    assert escalations_response.json()["result"]["workspace_id"] >= 1
    assert budget_response.status_code == 200
    assert budget_response.json()["result"]["workspace_display_name"] == "MCP Workspace"
    entries = list_control_plane_audit_logs_for_workspace(db_path, 1)
    assert any(entry.event_type == "mcp_broker.token_issued" for entry in entries)
    assert any(entry.event_type == "mcp_broker.tool_invoked" for entry in entries)


def test_mcp_broker_read_tool_returns_workspace_budget_usage(tmp_path):
    db_path = str(tmp_path / "mcp-broker-budget-read.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)
    upsert_entitlement(
        db_path,
        workspace_id=1,
        payload={
            **derive_entitlement_payload("team", "active"),
            "feature_flags_json": json.dumps(
                {
                    "advanced_analysis_units_limit": 20,
                    "advanced_analysis_window_seconds": 86400,
                    "advanced_analysis_price_threshold_usd": 0.01,
                    "advanced_analysis_provider_costs": {
                        "openai": {
                            "gpt-4o": {
                                "prompt_per_1k_usd": 0.01,
                                "completion_per_1k_usd": 0.03,
                            }
                        }
                    },
                }
            ),
        },
    )
    reservation = reserve_analysis_budget(
        db_path,
        workspace_id=1,
        feature_key="semantic_review",
        reservation_key="mcp-budget-read-semantic",
        estimated_units=5,
        now=time.time(),
    )
    consume_analysis_budget(
        db_path,
        reservation_key=reservation.reservation_key,
        consumed_units=5,
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=1000),
        provider="openai",
        model="gpt-4o",
        note="semantic review completed",
    )

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_workspace_budget_status", "arguments": {}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 200
    assert response.json()["result"]["workspace_display_name"] == "MCP Workspace"
    assert response.json()["result"]["used_units"] == 5
    assert response.json()["result"]["estimated_cost_usd"] == 0.04
    assert response.json()["result"]["feature_breakdown"][0]["feature_key"] == "semantic_review"
    assert "plan_code" not in response.json()["result"]
    assert "subscription_status" not in response.json()["result"]


def test_mcp_broker_read_only_token_hides_write_tools_and_rejects_invocation(tmp_path):
    db_path = str(tmp_path / "mcp-broker-read-only.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)
    _seed_audit_row(db_path, audit_id=1)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        tools_response = client.get(
            "/api/agent-integrations/mcp/tools",
            headers=_bearer_header(broker_token),
        )
        invoke_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.add_audit_feedback", "arguments": {"audit_id": 1, "kind": "helpful"}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert tools_response.status_code == 200
    tool_names = {tool["name"] for tool in tools_response.json()["tools"]}
    assert "vipari.list_available_tools" in tool_names
    assert "vipari.get_export_status" in tool_names
    assert "vipari.list_baseline_proposals" in tool_names
    assert "vipari.list_onboarding_proposals" in tool_names
    assert "vipari.add_audit_feedback" not in tool_names
    assert "vipari.create_compliance_export" not in tool_names
    assert "vipari.create_baseline_proposal" not in tool_names
    assert "vipari.create_onboarding_proposal" not in tool_names
    assert "vipari.triage_audit" not in tool_names
    assert invoke_response.status_code == 403
    assert invoke_response.json()["detail"]["error"] == "insufficient_scope"
    assert invoke_response.json()["detail"]["required_scope"] == "drift.write.low"
    assert invoke_response.json()["detail"]["granted_scopes"] == ["drift.read"]
    entries = list_control_plane_audit_logs_for_workspace(db_path, 1)
    denial_payloads = [json.loads(entry.payload_json) for entry in entries if entry.event_type == "mcp_broker.tool_denied"]
    assert any(
        payload["error"] == "insufficient_scope"
        and payload["tool_name"] == "vipari.add_audit_feedback"
        and payload["required_scope"] == "drift.write.low"
        for payload in denial_payloads
    )


def test_mcp_broker_read_token_can_get_workspace_export_status(tmp_path):
    db_path = str(tmp_path / "mcp-broker-export-read.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)
    export_id = _seed_export_job(db_path, workspace_id=1)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_export_status", "arguments": {"export_id": export_id}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 200
    assert response.json()["result"]["id"] == export_id
    assert response.json()["result"]["workspace_id"] == 1


def test_mcp_broker_export_status_returns_structured_not_found_error(tmp_path):
    db_path = str(tmp_path / "mcp-broker-export-missing.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_export_status", "arguments": {"export_id": 999999}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "export_not_found"
    assert response.json()["detail"]["message"] == "Export not found."


def test_mcp_broker_write_low_token_can_submit_feedback_and_triage(tmp_path):
    db_path = str(tmp_path / "mcp-broker-write-low.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path, scopes=["drift.read", "drift.write.low"])
    _seed_audit_row(db_path, audit_id=1)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        available_tools_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.list_available_tools", "arguments": {}},
            headers=_bearer_header(broker_token),
        )
        tools_response = client.get(
            "/api/agent-integrations/mcp/tools",
            headers=_bearer_header(broker_token),
        )
        feedback_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={
                "tool_name": "vipari.add_audit_feedback",
                "arguments": {
                    "audit_id": 1,
                    "kind": "helpful",
                    "comment": "useful finding",
                    "source": "human-admin",
                },
            },
            headers=_bearer_header(broker_token),
        )
        triage_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={
                "tool_name": "vipari.triage_audit",
                "arguments": {"audit_id": 1, "state": "acknowledged", "reason": "queued for review"},
            },
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert tools_response.status_code == 200
    assert available_tools_response.status_code == 200
    tool_names = {tool["name"] for tool in tools_response.json()["tools"]}
    available_tool_names = {tool["name"] for tool in available_tools_response.json()["result"]["tools"]}
    assert available_tool_names == tool_names
    assert "drift.write.low" in available_tools_response.json()["result"]["granted_scopes"]
    assert "vipari.add_audit_feedback" in tool_names
    assert "vipari.triage_audit" in tool_names
    assert feedback_response.status_code == 200
    assert feedback_response.json()["result"]["kind"] == "helpful"
    assert feedback_response.json()["result"]["source"] == "mcp:mcp-client-id"
    assert feedback_response.json()["result"]["client_id"] == "mcp-client-id"
    assert triage_response.status_code == 200
    assert triage_response.json()["result"]["state"] == "acknowledged"
    entries = list_control_plane_audit_logs_for_workspace(db_path, 1)
    assert any(entry.event_type == "audit.feedback_added" for entry in entries)
    assert any(entry.event_type == "audit.triage_state_changed" for entry in entries)


def test_mcp_broker_mutation_tools_are_rate_limited_without_blocking_reads(tmp_path):
    db_path = str(tmp_path / "mcp-broker-mutation-limit.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    original_mutation_limiter = mcp_broker._mcp_mutation_limiter
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"
    mcp_broker._mcp_mutation_limiter = mcp_broker._SlidingWindowRateLimiter(limit=1, window_seconds=60.0)

    client_id, client_secret = _seed_mcp_workspace(db_path, scopes=["drift.read", "drift.write.low"])
    _seed_audit_row(db_path, audit_id=1)

    try:
        with TestClient(main.app) as client:
            broker_token = _issue_broker_token(client, client_id, client_secret)
            first_feedback = client.post(
                "/api/agent-integrations/mcp/invoke",
                json={"tool_name": "vipari.add_audit_feedback", "arguments": {"audit_id": 1, "kind": "helpful"}},
                headers=_bearer_header(broker_token),
            )
            second_feedback = client.post(
                "/api/agent-integrations/mcp/invoke",
                json={"tool_name": "vipari.add_audit_feedback", "arguments": {"audit_id": 1, "kind": "helpful"}},
                headers=_bearer_header(broker_token),
            )
            repos_response = client.post(
                "/api/agent-integrations/mcp/invoke",
                json={"tool_name": "vipari.list_repos", "arguments": {}},
                headers=_bearer_header(broker_token),
            )
    finally:
        mcp_broker._mcp_mutation_limiter = original_mutation_limiter
        main.AUDIT_DB_PATH = original_db_path
        main.settings.app_encryption_key = original_enc
        main.settings.internal_jwt_secret = original_jwt_secret

    assert first_feedback.status_code == 200
    assert second_feedback.status_code == 429
    assert second_feedback.json()["detail"]["error"] == "rate_limited"
    assert second_feedback.json()["detail"]["tool_name"] == "vipari.add_audit_feedback"
    assert repos_response.status_code == 200
    entries = list_control_plane_audit_logs_for_workspace(db_path, 1)
    denial_payloads = [json.loads(entry.payload_json) for entry in entries if entry.event_type == "mcp_broker.tool_denied"]
    assert any(
        payload["error"] == "rate_limited"
        and payload["tool_name"] == "vipari.add_audit_feedback"
        and payload["retry_after_seconds"] == 60
        for payload in denial_payloads
    )


def test_mcp_broker_existing_token_loses_write_access_after_scope_downgrade(tmp_path):
    db_path = str(tmp_path / "mcp-broker-scope-downgrade.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path, scopes=["drift.read", "drift.write.low"])
    _seed_audit_row(db_path, audit_id=1)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE machine_principals SET scopes_json = ?, updated_at = ? WHERE client_id = ?",
                (json.dumps(["drift.read"]), time.time(), client_id),
            )
        tools_response = client.get(
            "/api/agent-integrations/mcp/tools",
            headers=_bearer_header(broker_token),
        )
        invoke_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.add_audit_feedback", "arguments": {"audit_id": 1, "kind": "helpful"}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert tools_response.status_code == 200
    tool_names = {tool["name"] for tool in tools_response.json()["tools"]}
    assert "vipari.add_audit_feedback" not in tool_names
    assert invoke_response.status_code == 403
    assert invoke_response.json()["detail"]["error"] == "insufficient_scope"
    assert invoke_response.json()["detail"]["granted_scopes"] == ["drift.read"]


def test_mcp_broker_existing_token_is_blocked_when_cp_api_is_disabled(tmp_path):
    db_path = str(tmp_path / "mcp-broker-feature-disabled.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        disabled_payload = derive_entitlement_payload("team", "active")
        disabled_payload["feature_flags_json"] = json.dumps({"cp_api_enabled": False})
        upsert_entitlement(db_path, workspace_id=1, payload=disabled_payload)
        with patch.object(Settings, "is_production", new_callable=PropertyMock, return_value=True):
            response = client.get(
                "/api/agent-integrations/mcp/tools",
                headers=_bearer_header(broker_token),
            )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "feature_disabled"
    assert response.json()["detail"]["message"] == "Control plane API is not enabled for this workspace."


def test_mcp_broker_existing_token_is_blocked_after_principal_revocation(tmp_path):
    db_path = str(tmp_path / "mcp-broker-revoked-principal.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        revoke_machine_principal(db_path, client_id)
        response = client.get(
            "/api/agent-integrations/mcp/tools",
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "invalid_token"
    assert response.json()["detail"]["message"] == "Machine principal is not active."
    entries = list_control_plane_audit_logs_for_workspace(db_path, 1)
    denial_payloads = [json.loads(entry.payload_json) for entry in entries if entry.event_type == "mcp_broker.auth_denied"]
    assert any(
        payload["error"] == "invalid_token"
        and payload["message"] == "Machine principal is not active."
        and payload["status_code"] == 401
        for payload in denial_payloads
    )


def test_mcp_broker_token_issuance_is_blocked_for_revoked_principal(tmp_path):
    db_path = str(tmp_path / "mcp-broker-revoked-principal-token.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)
    revoke_machine_principal(db_path, client_id)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/agent-integrations/mcp/token",
            json={"client_id": client_id, "client_secret": client_secret},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 401


def test_mcp_broker_rejects_existing_token_when_principal_scope_config_is_invalid(tmp_path):
    db_path = str(tmp_path / "mcp-broker-invalid-scope-config.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE machine_principals SET scopes_json = ?, updated_at = ? WHERE client_id = ?",
                ("not-json", time.time(), client_id),
            )
        response = client.get(
            "/api/agent-integrations/mcp/tools",
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "invalid_token"
    assert response.json()["detail"]["message"] == "Current principal scope configuration is invalid."


def test_mcp_broker_token_issuance_rejects_incompatible_service_account_scope_config(tmp_path):
    db_path = str(tmp_path / "mcp-broker-human-only-scope.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE machine_principals SET scopes_json = ?, updated_at = ? WHERE client_id = ?",
            (json.dumps(["drift.read", "admin.write"]), time.time(), client_id),
        )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/agent-integrations/mcp/token",
            json={"client_id": client_id, "client_secret": client_secret},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 401


def test_mcp_broker_rejects_invalid_positive_integer_argument_with_structured_error(tmp_path):
    db_path = str(tmp_path / "mcp-broker-invalid-argument.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_export_status", "arguments": {"export_id": "bad-id"}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_argument"
    assert response.json()["detail"]["message"] == "export_id must be a positive integer."


def test_mcp_broker_rejects_invalid_metadata_argument_with_structured_error(tmp_path):
    db_path = str(tmp_path / "mcp-broker-invalid-metadata.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path, scopes=["drift.read", "drift.write.low"])
    _seed_audit_row(db_path, audit_id=1)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={
                "tool_name": "vipari.add_audit_feedback",
                "arguments": {"audit_id": 1, "kind": "helpful", "metadata": ["not", "an", "object"]},
            },
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_argument"
    assert response.json()["detail"]["message"] == "metadata must be an object."


def test_mcp_broker_write_low_token_can_create_and_read_export(tmp_path):
    db_path = str(tmp_path / "mcp-broker-export-write.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path, scopes=["drift.read", "drift.write.low"])

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        tools_response = client.get(
            "/api/agent-integrations/mcp/tools",
            headers=_bearer_header(broker_token),
        )
        create_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={
                "tool_name": "vipari.create_compliance_export",
                "arguments": {
                    "repo_full": "doria90/dummyAI",
                    "from_date": "2024-01-01",
                    "to_date": "2024-01-31",
                    "export_mode": "compliance",
                    "include_artifact_content": False,
                },
            },
            headers=_bearer_header(broker_token),
        )
        job_id = create_response.json()["result"]["job_id"]
        status_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_export_status", "arguments": {"export_id": job_id}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert tools_response.status_code == 200
    tool_names = {tool["name"] for tool in tools_response.json()["tools"]}
    assert "vipari.get_export_status" in tool_names
    assert "vipari.create_compliance_export" in tool_names
    assert create_response.status_code == 200
    assert create_response.json()["result"]["repo_full"] == "doria90/dummyAI"
    assert status_response.status_code == 200
    assert status_response.json()["result"]["id"] == job_id
    entries = list_control_plane_audit_logs_for_workspace(db_path, 1)
    assert any(entry.event_type == "export.created" for entry in entries)


def test_mcp_broker_write_low_token_can_create_and_list_baseline_proposals(tmp_path):
    db_path = str(tmp_path / "mcp-broker-baseline-proposals.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path, scopes=["drift.read", "drift.write.low"])
    artifact_id = _seed_onboarding_artifact(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        create_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={
                "tool_name": "vipari.create_baseline_proposal",
                "arguments": {"artifact_id": artifact_id, "rationale": "stable prompt candidate", "linked_audit_ids": [1, 2]},
            },
            headers=_bearer_header(broker_token),
        )
        list_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.list_baseline_proposals", "arguments": {"artifact_id": artifact_id}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert create_response.status_code == 200
    assert create_response.json()["result"]["artifact_id"] == artifact_id
    assert create_response.json()["result"]["status"] == "pending"
    assert list_response.status_code == 200
    assert list_response.json()["result"]["proposals"][0]["artifact_id"] == artifact_id


def test_mcp_broker_write_low_token_can_create_and_list_onboarding_proposals(tmp_path):
    db_path = str(tmp_path / "mcp-broker-onboarding-proposals.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path, scopes=["drift.read", "drift.write.low"])

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        create_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={
                "tool_name": "vipari.create_onboarding_proposal",
                "arguments": {"repo_full": "doria90/dummyAI", "rationale": "needs onboarding review"},
            },
            headers=_bearer_header(broker_token),
        )
        list_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.list_onboarding_proposals", "arguments": {}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert create_response.status_code == 200
    assert create_response.json()["result"]["repo_full"] == "doria90/dummyAI"
    assert create_response.json()["result"]["status"] == "pending"
    assert list_response.status_code == 200
    assert list_response.json()["result"]["proposals"][0]["repo_full"] == "doria90/dummyAI"


def test_mcp_broker_blocks_repo_outside_workspace(tmp_path):
    db_path = str(tmp_path / "mcp-broker-outside.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_repo_posture", "arguments": {"repo_full": "doria90/not-allocated"}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "repo_not_allocated"
    assert response.json()["detail"]["message"] == "Repository is not allocated to this workspace."


def test_mcp_broker_hides_repo_after_allocation_is_deactivated(tmp_path):
    db_path = str(tmp_path / "mcp-broker-inactive-allocation.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)
    with sqlite3.connect(db_path) as conn:
        allocation_row = conn.execute(
            "SELECT id FROM repo_allocations WHERE workspace_id = ? AND repo_full = ?",
            (1, "doria90/dummyAI"),
        ).fetchone()
    update_repo_allocation_status(db_path, allocation_row[0], "inactive")

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        repos_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.list_repos", "arguments": {}},
            headers=_bearer_header(broker_token),
        )
        posture_response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.get_repo_posture", "arguments": {"repo_full": "doria90/dummyAI"}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert repos_response.status_code == 200
    assert repos_response.json()["result"]["repos"] == []
    assert posture_response.status_code == 404
    assert posture_response.json()["detail"]["error"] == "repo_not_allocated"
def test_mcp_broker_rejects_unknown_tool_with_structured_error(tmp_path):
    db_path = str(tmp_path / "mcp-broker-unknown-tool.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "vipari.unknown_tool", "arguments": {}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "tool_not_found"
    assert response.json()["detail"]["message"] == "MCP tool not found."


def test_mcp_broker_accepts_legacy_promptdrift_aliases(tmp_path):
    db_path = str(tmp_path / "mcp-broker-legacy.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        response = client.post(
            "/api/agent-integrations/mcp/invoke",
            json={"tool_name": "promptdrift.list_repos", "arguments": {}},
            headers=_bearer_header(broker_token),
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 200
    assert response.json()["result"]["repos"][0]["repo_full"] == "doria90/dummyAI"


def test_mcp_broker_requires_bearer_auth(tmp_path):
    db_path = str(tmp_path / "mcp-broker-auth.db")
    original_db_path = main.AUDIT_DB_PATH
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"
    init_db(db_path)

    with TestClient(main.app) as client:
        response = client.get("/api/agent-integrations/mcp/tools")

    main.AUDIT_DB_PATH = original_db_path
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "invalid_request"
    assert response.json()["detail"]["message"] == "Missing or malformed Authorization header."


def test_mcp_broker_rejects_invalid_bearer_token_with_structured_error(tmp_path):
    db_path = str(tmp_path / "mcp-broker-invalid-token.db")
    original_db_path = main.AUDIT_DB_PATH
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"
    init_db(db_path)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/agent-integrations/mcp/tools",
            headers={"Authorization": "Bearer not-a-real-token"},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "invalid_token"
    assert response.json()["detail"]["message"]


def test_mcp_broker_token_requires_valid_client_credentials(tmp_path):
    db_path = str(tmp_path / "mcp-broker-token-auth.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, _client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/agent-integrations/mcp/token",
            json={"client_id": client_id, "client_secret": "wrong-secret"},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 401
    entries = list_control_plane_audit_logs_for_workspace(db_path, 1)
    denial_entries = [entry for entry in entries if entry.event_type == "mcp_broker.token_denied"]
    denial_payloads = [json.loads(entry.payload_json) for entry in denial_entries]
    assert any(entry.subject_id == client_id for entry in denial_entries)
    assert any(
        payload["message"] == "Invalid client credentials."
        and payload["status_code"] == 401
        for payload in denial_payloads
    )


def test_mcp_broker_token_requires_internal_jwt_config(tmp_path):
    db_path = str(tmp_path / "mcp-broker-token-secret.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = ""

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/agent-integrations/mcp/token",
            json={"client_id": client_id, "client_secret": client_secret},
        )

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "service_unavailable"
    assert response.json()["detail"]["message"] == "Internal JWT auth is not configured."