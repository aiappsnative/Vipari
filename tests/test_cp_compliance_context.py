from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

from config import get_settings
from services.api_service import create_api_app
from services.audit_jobs import init_db
from services.control_plane_records import (
    allocate_repo_to_workspace,
    create_machine_principal,
    create_user,
    create_workspace,
    upsert_github_installation,
)
from services.internal_auth import SCOPE_DRIFT_READ, SCOPE_DRIFT_WRITE_LOW, issue_cp_token
from services.secure_store import encrypt_text

ADMIN_TOKEN = "test-admin-token-compliance-context"
JWT_SECRET = "test-jwt-secret-compliance-context!"
JWT_ISSUER = "driftguard"
JWT_AUDIENCE = "driftguard-cp"
ENCRYPTION_KEY = "compliance-context-key-exactly32!!"


def _reset_settings() -> None:
    get_settings.cache_clear()


def _configure_env(monkeypatch, db_path: str) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("API_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("APP_ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("INTERNAL_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("INTERNAL_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("INTERNAL_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_ENV", "local")
    _reset_settings()


def _seed_workspace_with_repo(db_path: str) -> tuple[int, str, int]:
    init_db(db_path)
    user = create_user(db_path, display_name="Owner", primary_email="owner@cc.example.com")
    workspace = create_workspace(
        db_path,
        slug="cp-compliance-context",
        display_name="CP Compliance Context",
        billing_owner_user_id=user.id,
    )
    installation = upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=7001,
        account_id="acct-7001",
        account_login="org",
        account_type="Organization",
        target_type="Organization",
    )
    repo_full = "org/repo-cc"
    allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repo_github_id="repo-cc-id",
        repo_full=repo_full,
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    return workspace.id, repo_full, allocation.id


def _seed_principal(db_path: str, workspace_id: int, *, scopes: list[str], client_id: str) -> None:
    create_machine_principal(
        db_path,
        workspace_id=workspace_id,
        display_name="cp-bot",
        principal_kind="service_account",
        client_id=client_id,
        client_secret_encrypted=encrypt_text("secret", ENCRYPTION_KEY),
        scopes=scopes,
    )


def _make_token(workspace_id: int, *, scopes: list[str], client_id: str) -> str:
    return issue_cp_token(
        client_id=client_id,
        workspace_id=workspace_id,
        scopes=scopes,
        secret=JWT_SECRET,
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        ttl_seconds=3600,
    )


def test_cp_workspace_compliance_context_get_and_put(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cp-compliance-context.db")
    _configure_env(monkeypatch, db_path)
    workspace_id, _repo_full, _allocation_id = _seed_workspace_with_repo(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_READ, SCOPE_DRIFT_WRITE_LOW], client_id="cp-cc-1")
    token = _make_token(workspace_id, scopes=[SCOPE_DRIFT_READ, SCOPE_DRIFT_WRITE_LOW], client_id="cp-cc-1")

    with TestClient(create_api_app()) as client:
        get_before = client.get(
            f"/cp/workspaces/{workspace_id}/compliance-context",
            headers={"Authorization": f"Bearer {token}"},
        )
        put_response = client.put(
            f"/cp/workspaces/{workspace_id}/compliance-context",
            json={
                "context": {
                    "risk_tier": "limited",
                    "customer_impact": "customer_facing",
                    "human_oversight": "required",
                    "handles_personal_data": True,
                    "deployment_regions": ["eu-west-1", "us-east-1"],
                    "notes": "workspace baseline",
                }
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        get_after = client.get(
            f"/cp/workspaces/{workspace_id}/compliance-context",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert get_before.status_code == 200
    assert get_before.json()["context"]["risk_tier"] == "unclassified"

    assert put_response.status_code == 200
    put_payload = put_response.json()
    assert put_payload["context"]["risk_tier"] == "limited"
    assert put_payload["context"]["handles_personal_data"] is True
    assert put_payload["context_hash"]

    assert get_after.status_code == 200
    get_payload = get_after.json()
    assert get_payload["context"]["customer_impact"] == "customer_facing"
    assert get_payload["context"]["deployment_regions"] == ["eu-west-1", "us-east-1"]


def test_cp_repo_compliance_context_override_get_and_put(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cp-repo-compliance-context.db")
    _configure_env(monkeypatch, db_path)
    workspace_id, repo_full, _allocation_id = _seed_workspace_with_repo(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_READ, SCOPE_DRIFT_WRITE_LOW], client_id="cp-cc-2")
    token = _make_token(workspace_id, scopes=[SCOPE_DRIFT_READ, SCOPE_DRIFT_WRITE_LOW], client_id="cp-cc-2")

    with TestClient(create_api_app()) as client:
        client.put(
            f"/cp/workspaces/{workspace_id}/compliance-context",
            json={"context": {"risk_tier": "limited", "customer_impact": "customer_facing"}},
            headers={"Authorization": f"Bearer {token}"},
        )

        put_override = client.put(
            f"/cp/workspaces/{workspace_id}/repos/{repo_full}/compliance-context",
            json={"context_override": {"risk_tier": "high", "handles_personal_data": True}},
            headers={"Authorization": f"Bearer {token}"},
        )
        get_effective = client.get(
            f"/cp/workspaces/{workspace_id}/repos/{repo_full}/compliance-context",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert put_override.status_code == 200
    put_payload = put_override.json()
    assert put_payload["source"] == "repo_override"
    assert put_payload["override"]["risk_tier"] == "high"
    assert put_payload["effective_context"]["customer_impact"] == "customer_facing"

    assert get_effective.status_code == 200
    get_payload = get_effective.json()
    assert get_payload["source"] == "repo_override"
    assert get_payload["effective_context"]["risk_tier"] == "high"
    assert get_payload["effective_context"]["handles_personal_data"] is True


def test_cp_repo_compliance_context_requires_workspace_repo_allocation(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cp-repo-compliance-context-404.db")
    _configure_env(monkeypatch, db_path)
    workspace_id, _repo_full, _allocation_id = _seed_workspace_with_repo(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_READ], client_id="cp-cc-3")
    token = _make_token(workspace_id, scopes=[SCOPE_DRIFT_READ], client_id="cp-cc-3")

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/not-allocated/compliance-context",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Repository is not allocated to this workspace."
