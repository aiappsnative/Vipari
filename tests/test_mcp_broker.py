from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

import main
from services.audit_jobs import init_db
from services.control_plane_records import (
    allocate_repo_to_workspace,
    create_machine_principal,
    create_workspace,
    list_control_plane_audit_logs_for_workspace,
    replace_repo_connections,
    upsert_entitlement,
    upsert_github_identity,
    upsert_github_installation,
    upsert_subscription,
    update_repo_allocation_status,
)
from services.entitlements import derive_entitlement_payload
from services.onboarding import onboard_repository
from services.secure_store import encrypt_text


PROMPT_BASELINE = """# Refund Copilot
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Use the billing sandbox tool in read mode.
max_steps: 2
temperature: 0.2
"""


def _issue_broker_token(client: TestClient, client_id: str, client_secret: str) -> str:
    response = client.post(
        "/api/agent-integrations/mcp/token",
        json={"client_id": client_id, "client_secret": client_secret},
    )
    assert response.status_code == 200
    return response.json()["token"]


def _bearer_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_mcp_workspace(db_path: str, *, repo_full: str = "doria90/dummyAI") -> tuple[str, str]:
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
        scopes=["drift.read"],
    )
    return client_id, client_secret


def test_mcp_broker_tools_and_read_calls(tmp_path):
    db_path = str(tmp_path / "mcp-broker.db")
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_jwt_secret = main.settings.internal_jwt_secret
    main.AUDIT_DB_PATH = db_path
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.internal_jwt_secret = "broker-token-secret-with-32-bytes!!"

    client_id, client_secret = _seed_mcp_workspace(db_path)

    with TestClient(main.app) as client:
        broker_token = _issue_broker_token(client, client_id, client_secret)
        tools_response = client.get(
            "/api/agent-integrations/mcp/tools",
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

    main.AUDIT_DB_PATH = original_db_path
    main.settings.app_encryption_key = original_enc
    main.settings.internal_jwt_secret = original_jwt_secret

    assert tools_response.status_code == 200
    tool_names = {tool["name"] for tool in tools_response.json()["tools"]}
    assert "vipari.list_repos" in tool_names
    assert "vipari.get_repo_posture" in tool_names
    assert repos_response.status_code == 200
    assert repos_response.json()["result"]["repos"][0]["repo_full"] == "doria90/dummyAI"
    assert posture_response.status_code == 200
    assert posture_response.json()["result"]["repo_full"] == "doria90/dummyAI"
    assert casefile_response.status_code == 200
    assert casefile_response.json()["result"]["coverage_summary"]["discovered_artifact_count"] >= 1
    assert escalations_response.status_code == 200
    assert escalations_response.json()["result"]["workspace_id"] >= 1
    entries = list_control_plane_audit_logs_for_workspace(db_path, 1)
    assert any(entry.event_type == "mcp_broker.token_issued" for entry in entries)
    assert any(entry.event_type == "mcp_broker.tool_invoked" for entry in entries)


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