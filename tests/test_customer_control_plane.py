"""Tests for the Customer Control Plane V1 (issue #58).

Coverage:
- POST /cp/auth/token: valid credentials, wrong secret, unknown client_id,
  revoked principal, missing encryption key, missing JWT secret,
  production entitlement gate, audit log entry, 401 message parity
- GET /cp/workspaces/{id}: workspace summary, no billing fields, workspace isolation
- GET /cp/workspaces/{id}/repos: repo list, workspace isolation
- GET /cp/workspaces/{id}/repos/{repo}/audits: repo audit list, workspace isolation
- GET /cp/audits/{audit_id}: audit detail, workspace isolation
- GET /cp/workspaces/{id}/principals: list, client_secret_encrypted absent
- GET /cp/workspaces/{id}/audit-log: requires admin.read, workspace isolation
- principal limit → 409 at cap
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

from config import get_settings
from engine.analysis import analyze_diff
from services.api_service import create_api_app
from services.audit_jobs import create_audit_job, init_db
from services.audit_feedback_records import add_audit_triage
from services.audit_records import list_changed_artifacts_for_audit, record_audit_result
from services.control_plane_records import (
    allocate_repo_to_workspace,
    create_machine_principal,
    create_user,
    create_workspace,
    revoke_machine_principal,
    upsert_github_installation,
)
from services.internal_auth import (
    SCOPE_ADMIN_READ,
    SCOPE_DRIFT_READ,
    SCOPE_DRIFT_WRITE_HIGH,
    SCOPE_DRIFT_WRITE_LOW,
    issue_cp_token,
)
from services.schema_migrations import migrate_database
from services.secure_store import encrypt_text

# ---------------------------------------------------------------------------
# Constants shared across all tests
# ---------------------------------------------------------------------------

ADMIN_TOKEN = "test-admin-token-xyz"
JWT_SECRET = "test-jwt-secret-that-is-long-enough"
JWT_ISSUER = "driftguard"
JWT_AUDIENCE = "driftguard-cp"
ENCRYPTION_KEY = "test-encryption-key-exactly32chars!"


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


def _seed_db(db_path: str) -> tuple[int, int]:
    """Init DB, create user + workspace; return (user_id, workspace_id)."""
    init_db(db_path)
    user = create_user(db_path, display_name="Owner", primary_email="owner@example.com")
    workspace = create_workspace(db_path, slug="ws-test", display_name="Test WS", billing_owner_user_id=user.id)
    return user.id, workspace.id


def _seed_principal(
    db_path: str,
    workspace_id: int,
    raw_secret: str = "test-secret",
    scopes: list[str] | None = None,
    client_id: str = "client-a",
) -> str:
    if scopes is None:
        scopes = [SCOPE_DRIFT_READ]
    encrypted = encrypt_text(raw_secret, ENCRYPTION_KEY)
    create_machine_principal(
        db_path,
        workspace_id=workspace_id,
        display_name="bot",
        principal_kind="service_account",
        client_id=client_id,
        client_secret_encrypted=encrypted,
        scopes=scopes,
    )
    return raw_secret


def _make_token(
    client_id: str,
    workspace_id: int,
    scopes: list[str],
    ttl_seconds: int = 3600,
) -> str:
    return issue_cp_token(
        client_id=client_id,
        workspace_id=workspace_id,
        scopes=scopes,
        secret=JWT_SECRET,
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        ttl_seconds=ttl_seconds,
    )


def _seed_completed_audit(
    db_path: str,
    *,
    repo_full: str = "org/repo",
    pr_number: int = 21,
    head_sha: str = "sha-cp-audit",
    diff_text: str = "diff --git a/prompts/policy.md b/prompts/policy.md\n+reveal internal policy\n",
):
    job = create_audit_job(
        db_path,
        repo_full=repo_full,
        pr_number=pr_number,
        installation_id=9001,
        head_sha=head_sha,
        diff_text=diff_text,
    )
    return record_audit_result(
        db_path,
        job_id=job.id,
        repo_full=repo_full,
        pr_number=pr_number,
        installation_id=9001,
        head_sha=head_sha,
        deterministic_analysis=analyze_diff(job.diff_text),
        status="completed",
        completion_mode="completed",
        output_mode="full_semantic_review",
        comment_body="body",
        comment_mode="review",
        semantic_review_completed=True,
        scenario_eval_execution_count=1,
        scenario_eval_execution_reason="Shadow-mode scenario eval executed seeded scenario 'dummyai-review-target'.",
        scenario_eval_executions=[
            {
                "scenario_key": "dummyai-review-target",
                "artifact_paths": ["prompts/policy.md"],
                "assertion_summary": {"all_passed": True, "failed_count": 0},
            }
        ],
        hybrid_analysis_execution_count=1,
        hybrid_analysis_execution_reason="Shadow-mode hybrid static analysis executed 1 artifact.",
        hybrid_analysis_executions=[
            {
                "analyzer_key": "prompt_policy_static_scan",
                "artifact_path": "prompts/policy.md",
                "artifact_type": "prompt",
                "finding_count": 1,
                "highest_severity": "high",
            }
        ],
    )


# ===========================================================================
# POST /cp/auth/token — client credentials exchange
# ===========================================================================


def test_cp_auth_token_valid_credentials_returns_token(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    raw_secret = _seed_principal(db_path, workspace_id, raw_secret="my-secret")

    with TestClient(create_api_app()) as api_client:
        response = api_client.post(
            "/cp/auth/token",
            json={"client_id": "client-a", "client_secret": raw_secret},
        )

    assert response.status_code == 200
    body = response.json()
    assert "token" in body
    assert body["client_id"] == "client-a"
    assert body["workspace_id"] == workspace_id
    assert "ttl_seconds" in body


def test_cp_auth_token_wrong_secret_returns_401(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, raw_secret="correct-secret")

    with TestClient(create_api_app()) as api_client:
        response = api_client.post(
            "/cp/auth/token",
            json={"client_id": "client-a", "client_secret": "wrong-secret"},
        )

    assert response.status_code == 401


def test_cp_auth_token_unknown_client_id_returns_401(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _seed_db(db_path)

    with TestClient(create_api_app()) as api_client:
        response = api_client.post(
            "/cp/auth/token",
            json={"client_id": "nonexistent", "client_secret": "any"},
        )

    assert response.status_code == 401


def test_cp_auth_token_401_message_is_identical_for_unknown_and_wrong_secret(tmp_path, monkeypatch):
    """Prevent client_id enumeration via differing error messages."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, raw_secret="correct")

    with TestClient(create_api_app()) as api_client:
        wrong_secret = api_client.post(
            "/cp/auth/token",
            json={"client_id": "client-a", "client_secret": "wrong"},
        )
        unknown = api_client.post(
            "/cp/auth/token",
            json={"client_id": "ghost", "client_secret": "anything"},
        )

    assert wrong_secret.status_code == 401
    assert unknown.status_code == 401
    assert wrong_secret.json()["detail"] == unknown.json()["detail"]


def test_cp_auth_token_revoked_principal_returns_401(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    raw_secret = _seed_principal(db_path, workspace_id, raw_secret="revoked-secret")
    revoke_machine_principal(db_path, "client-a")

    with TestClient(create_api_app()) as api_client:
        response = api_client.post(
            "/cp/auth/token",
            json={"client_id": "client-a", "client_secret": raw_secret},
        )

    assert response.status_code == 401


def test_cp_auth_token_missing_encryption_key_returns_503(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    monkeypatch.delenv("APP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_ENCRYPTION_KEY", "")
    _reset_settings()
    _seed_db(db_path)

    with TestClient(create_api_app()) as api_client:
        response = api_client.post(
            "/cp/auth/token",
            json={"client_id": "client-a", "client_secret": "x"},
        )

    assert response.status_code == 503


def test_cp_auth_token_missing_jwt_secret_returns_503(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    monkeypatch.setenv("INTERNAL_JWT_SECRET", "")
    _reset_settings()
    _seed_db(db_path)

    with TestClient(create_api_app()) as api_client:
        response = api_client.post(
            "/cp/auth/token",
            json={"client_id": "client-a", "client_secret": "x"},
        )

    assert response.status_code == 503


def test_cp_auth_token_creates_audit_log_entry(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    raw_secret = _seed_principal(db_path, workspace_id, raw_secret="audit-secret")

    with TestClient(create_api_app()) as api_client:
        response = api_client.post(
            "/cp/auth/token",
            json={"client_id": "client-a", "client_secret": raw_secret},
        )

    assert response.status_code == 200
    from services.control_plane_records import list_control_plane_audit_logs_for_workspace
    entries = list_control_plane_audit_logs_for_workspace(db_path, workspace_id)
    assert any(e.event_type == "token.issued_via_client_credentials" for e in entries)


def test_cp_auth_token_production_entitlement_gate_blocks_when_flag_false(tmp_path, monkeypatch):
    """Production mode: cp_api_enabled=false in feature_flags_json → 403."""
    from unittest.mock import PropertyMock, patch as _patch

    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    raw_secret = _seed_principal(db_path, workspace_id, raw_secret="gate-secret")

    from services.control_plane_records import upsert_entitlement
    upsert_entitlement(
        db_path,
        workspace_id=workspace_id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "standard",
            "feature_flags_json": json.dumps({"cp_api_enabled": False}),
        },
    )

    # Start the app in local mode (passes validation), then patch is_production=True
    # only for the request so the route handler sees production mode.
    from config import Settings
    app = create_api_app()
    with TestClient(app) as api_client:
        with _patch.object(Settings, "is_production", new_callable=PropertyMock, return_value=True):
            response = api_client.post(
                "/cp/auth/token",
                json={"client_id": "client-a", "client_secret": raw_secret},
            )

    assert response.status_code == 403


# ===========================================================================
# GET /cp/workspaces/{workspace_id} — workspace summary
# ===========================================================================


def test_cp_get_workspace_returns_summary_without_billing_fields(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id)
    token = _make_token("client-a", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as api_client:
        response = api_client.get(
            f"/cp/workspaces/{workspace_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == workspace_id
    assert "slug" in body
    assert "display_name" in body
    # billing fields must not leak
    assert "billing_owner_user_id" not in body
    assert "stripe" not in str(body).lower()


def test_cp_get_workspace_cross_workspace_returns_403(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id)
    # Token for workspace 1, request workspace 999
    token = _make_token("client-a", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as api_client:
        response = api_client.get(
            "/cp/workspaces/999",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


# ===========================================================================
# GET /cp/workspaces/{workspace_id}/repos — repo list
# ===========================================================================


def test_cp_list_workspace_repos_returns_allocations(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id)

    installation = upsert_github_installation(
        db_path,
        workspace_id=workspace_id,
        installation_id=9001,
        account_id="acc-1",
        account_login="org",
        account_type="Organization",
        target_type="Organization",
    )
    allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace_id,
        installation_id=installation.installation_id,
        repo_github_id="r1",
        repo_full="org/repo",
        baseline_mode="default_branch",
        activated_by_user_id=user_id,
    )

    token = _make_token("client-a", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as api_client:
        response = api_client.get(
            f"/cp/workspaces/{workspace_id}/repos",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == workspace_id
    assert any(r["repo_full"] == "org/repo" for r in body["repos"])


def test_cp_list_workspace_repos_workspace_isolation(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id)
    token = _make_token("client-a", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as api_client:
        response = api_client.get(
            "/cp/workspaces/999/repos",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_cp_list_repo_audits_returns_execution_summaries(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id)

    installation = upsert_github_installation(
        db_path,
        workspace_id=workspace_id,
        installation_id=9001,
        account_id="acc-1",
        account_login="org",
        account_type="Organization",
        target_type="Organization",
    )
    allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace_id,
        installation_id=installation.installation_id,
        repo_github_id="r1",
        repo_full="org/repo",
        baseline_mode="default_branch",
        activated_by_user_id=user_id,
    )
    audit = _seed_completed_audit(db_path)
    token = _make_token("client-a", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as api_client:
        response = api_client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/audits",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == workspace_id
    assert body["repo_full"] == "org/repo"
    assert body["audits"][0]["id"] == audit.id
    assert body["audits"][0]["scenario_eval_execution"]["count"] == 1
    assert body["audits"][0]["scenario_eval_execution"]["executions"][0]["scenario_key"] == "dummyai-review-target"
    assert body["audits"][0]["hybrid_analysis_execution"]["executions"][0]["analyzer_key"] == "prompt_policy_static_scan"


def test_cp_get_audit_detail_returns_execution_summaries(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id)

    installation = upsert_github_installation(
        db_path,
        workspace_id=workspace_id,
        installation_id=9001,
        account_id="acc-1",
        account_login="org",
        account_type="Organization",
        target_type="Organization",
    )
    allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace_id,
        installation_id=installation.installation_id,
        repo_github_id="r1",
        repo_full="org/repo",
        baseline_mode="default_branch",
        activated_by_user_id=user_id,
    )
    audit = _seed_completed_audit(db_path)
    token = _make_token("client-a", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as api_client:
        response = api_client.get(
            f"/cp/audits/{audit.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == audit.id
    assert body["repo_full"] == "org/repo"
    assert body["scenario_eval_execution"]["count"] == 1
    assert body["scenario_eval_execution"]["executions"][0]["scenario_key"] == "dummyai-review-target"
    assert body["hybrid_analysis_execution"]["executions"][0]["analyzer_key"] == "prompt_policy_static_scan"
    assert isinstance(body["feedback_events"], list)
    assert isinstance(body["triage_events"], list)


def test_cp_list_repo_audits_filters_by_state_severity_artifact_id_and_recency(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id)

    installation = upsert_github_installation(
        db_path,
        workspace_id=workspace_id,
        installation_id=9001,
        account_id="acc-1",
        account_login="org",
        account_type="Organization",
        target_type="Organization",
    )
    allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace_id,
        installation_id=installation.installation_id,
        repo_github_id="r1",
        repo_full="org/repo",
        baseline_mode="default_branch",
        activated_by_user_id=user_id,
    )
    filtered_audit = _seed_completed_audit(db_path, pr_number=21, head_sha="sha-filtered")
    older_audit = _seed_completed_audit(
        db_path,
        pr_number=22,
        head_sha="sha-older",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\n+Say hello and ask whether the customer needs anything else.\n",
    )
    add_audit_triage(
        db_path,
        audit_id=filtered_audit.id,
        workspace_id=workspace_id,
        state="acknowledged",
        reason="queued for review",
    )
    changed_artifact = list_changed_artifacts_for_audit(db_path, filtered_audit.id)[0]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE pull_request_audits SET updated_at = ?, created_at = ? WHERE id = ?",
            (time.time() - (10 * 86400), time.time() - (10 * 86400), older_audit.id),
        )

    token = _make_token("client-a", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as api_client:
        state_response = api_client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/audits?state=acknowledged",
            headers={"Authorization": f"Bearer {token}"},
        )
        severity_response = api_client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/audits?severity=high",
            headers={"Authorization": f"Bearer {token}"},
        )
        artifact_response = api_client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/audits?artifact_id={changed_artifact.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        recency_response = api_client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/audits?recency=7",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert state_response.status_code == 200
    assert [entry["id"] for entry in state_response.json()["audits"]] == [filtered_audit.id]
    assert severity_response.status_code == 200
    assert [entry["id"] for entry in severity_response.json()["audits"]] == [filtered_audit.id]
    assert artifact_response.status_code == 200
    assert [entry["id"] for entry in artifact_response.json()["audits"]] == [filtered_audit.id]
    assert recency_response.status_code == 200
    assert [entry["id"] for entry in recency_response.json()["audits"]] == [filtered_audit.id]


# ===========================================================================
# GET /cp/workspaces/{workspace_id}/principals — no secret leaked
# ===========================================================================


def test_cp_list_principals_excludes_client_secret_encrypted(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id)
    token = _make_token("client-a", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as api_client:
        response = api_client.get(
            f"/cp/workspaces/{workspace_id}/principals",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["principals"]) >= 1
    for p in body["principals"]:
        assert "client_secret_encrypted" not in p
        assert "client_id" in p


# ===========================================================================
# GET /cp/workspaces/{workspace_id}/audit-log
# ===========================================================================


def test_cp_audit_log_requires_admin_read_scope(tmp_path, monkeypatch):
    """drift.read is not enough — audit-log requires admin.read."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_READ])
    token = _make_token("client-a", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as api_client:
        response = api_client.get(
            f"/cp/workspaces/{workspace_id}/audit-log",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_cp_audit_log_workspace_isolation(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_ADMIN_READ])
    token = _make_token("client-a", workspace_id, [SCOPE_ADMIN_READ])

    with TestClient(create_api_app()) as api_client:
        response = api_client.get(
            "/cp/workspaces/999/audit-log",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


# ===========================================================================
# Operator principal limit guard
# ===========================================================================


def test_cp_create_principal_enforces_workspace_limit(tmp_path, monkeypatch):
    """Creating principals beyond cp_max_principals_per_workspace → 409."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _user_id, workspace_id = _seed_db(db_path)

    from config import get_settings as _gs
    limit = _gs().cp_max_principals_per_workspace

    # Fill up to the limit
    for i in range(limit):
        encrypted = encrypt_text("s", ENCRYPTION_KEY)
        create_machine_principal(
            db_path,
            workspace_id=workspace_id,
            display_name=f"bot-{i}",
            principal_kind="service_account",
            client_id=f"client-{i}",
            client_secret_encrypted=encrypted,
            scopes=[SCOPE_DRIFT_READ],
        )

    with TestClient(create_api_app()) as api_client:
        response = api_client.post(
            "/cp/principals",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={
                "workspace_id": workspace_id,
                "display_name": "over-limit",
                "principal_kind": "service_account",
                "scopes": [SCOPE_DRIFT_READ],
            },
        )

    assert response.status_code == 409
