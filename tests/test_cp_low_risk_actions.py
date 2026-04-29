"""Tests for control-plane low-risk write actions (issue #60).

Coverage:
- POST /cp/audits/{audit_id}/feedback
    - valid feedback creates event, returns 200 with expected fields
    - multiple feedback calls on same audit all succeed (append-only)
    - invalid kind → 422
    - missing/wrong scope → 403
    - audit not found → 404
    - audit belongs to different workspace → 404 (no info leakage)
    - audit log entry is written
- POST /cp/audits/{audit_id}/triage
    - valid triage creates event, returns 200 with expected fields
    - multiple triage calls succeed (append-only)
    - invalid state → 422
    - missing scope → 403
    - audit not found → 404
    - cross-workspace → 404
- GET /cp/workspaces/{workspace_id}/exports/{export_id}
    - returns status fields but NOT result_blob
    - export not found → 404
    - wrong workspace → 404
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

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
from services.export_jobs import create_export_job
from services.internal_auth import (
    SCOPE_DRIFT_READ,
    SCOPE_DRIFT_WRITE_LOW,
    issue_cp_token,
)
from services.secure_store import encrypt_text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADMIN_TOKEN = "test-admin-token-xyz"
JWT_SECRET = "test-jwt-secret-that-is-long-enough"
JWT_ISSUER = "driftguard"
JWT_AUDIENCE = "driftguard-cp"
ENCRYPTION_KEY = "test-encryption-key-exactly32chars!"

REPO_FULL = "org/repo-test"
REPO_FULL_OTHER = "org/repo-other"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    """Bootstrap DB, create a user + workspace. Returns (user_id, workspace_id)."""
    init_db(db_path)
    user = create_user(db_path, display_name="Owner", primary_email="owner@test.example.com")
    workspace = create_workspace(
        db_path,
        slug="ws-low-risk",
        display_name="Low Risk WS",
        billing_owner_user_id=user.id,
    )
    return user.id, workspace.id


def _seed_allocation(db_path: str, workspace_id: int, user_id: int, repo_full: str = REPO_FULL) -> None:
    """Allocate a repo to the workspace (needed for workspace isolation checks)."""
    installation = upsert_github_installation(
        db_path,
        workspace_id=workspace_id,
        installation_id=9999,
        account_id="acc-lr",
        account_login="org",
        account_type="Organization",
        target_type="Organization",
    )
    allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace_id,
        installation_id=installation.installation_id,
        repo_github_id="rlr-1",
        repo_full=repo_full,
        baseline_mode="default_branch",
        activated_by_user_id=user_id,
    )


def _seed_principal(
    db_path: str,
    workspace_id: int,
    scopes: list[str],
    client_id: str = "lr-client-1",
    raw_secret: str = "lr-secret",
) -> None:
    encrypted = encrypt_text(raw_secret, ENCRYPTION_KEY)
    create_machine_principal(
        db_path,
        workspace_id=workspace_id,
        display_name="lr-bot",
        principal_kind="service_account",
        client_id=client_id,
        client_secret_encrypted=encrypted,
        scopes=scopes,
    )


def _make_token(client_id: str, workspace_id: int, scopes: list[str]) -> str:
    return issue_cp_token(
        client_id=client_id,
        workspace_id=workspace_id,
        scopes=scopes,
        secret=JWT_SECRET,
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        ttl_seconds=3600,
    )


def _seed_audit_row(db_path: str, audit_id: int = 1, repo_full: str = REPO_FULL) -> None:
    """Insert a minimal pull_request_audits row directly via SQL."""
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
                audit_id * 100,   # job_id
                repo_full,
                1,                # pr_number
                9999,             # installation_id
                "abc123",         # head_sha
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


# ===========================================================================
# POST /cp/audits/{audit_id}/feedback
# ===========================================================================


def test_add_audit_feedback_valid_returns_200(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/1/feedback",
            json={"source": "human-reviewer", "kind": "helpful"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["audit_id"] == 1
    assert body["kind"] == "helpful"
    assert body["source"] == "human-reviewer"
    assert "id" in body
    assert "created_at" in body


def test_add_audit_feedback_append_only_multiple_calls_all_succeed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        r1 = client.post(
            "/cp/audits/1/feedback",
            json={"source": "bot", "kind": "helpful"},
            headers={"Authorization": f"Bearer {token}"},
        )
        r2 = client.post(
            "/cp/audits/1/feedback",
            json={"source": "bot", "kind": "noisy"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Each call must return a distinct event id
    assert r1.json()["id"] != r2.json()["id"]


def test_add_audit_feedback_invalid_kind_returns_422(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/1/feedback",
            json={"source": "bot", "kind": "not_a_real_kind"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


def test_add_audit_feedback_wrong_scope_returns_403(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_READ])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    # Token carries only drift.read — not drift.write.low
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/1/feedback",
            json={"source": "bot", "kind": "helpful"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_add_audit_feedback_unknown_audit_returns_404(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    _seed_allocation(db_path, workspace_id, user_id)
    # Do NOT seed any audit row
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/9999/feedback",
            json={"source": "bot", "kind": "helpful"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404


def test_add_audit_feedback_cross_workspace_returns_404(tmp_path, monkeypatch):
    """Audit exists but belongs to a different workspace — must return 404 not 403."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    # Workspace A owns the audit
    user_a_id, ws_a_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_a_id, user_a_id, repo_full=REPO_FULL)
    _seed_audit_row(db_path, audit_id=1, repo_full=REPO_FULL)

    # Workspace B tries to access it
    user_b = create_user(db_path, display_name="B", primary_email="b@test.example.com")
    ws_b = create_workspace(db_path, slug="ws-b", display_name="WS B", billing_owner_user_id=user_b.id)
    ws_b_id = ws_b.id
    # Workspace B has its own allocation on a different repo
    _seed_principal(db_path, ws_b_id, scopes=[SCOPE_DRIFT_WRITE_LOW], client_id="lr-client-b", raw_secret="secret-b")
    token_b = _make_token("lr-client-b", ws_b_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/1/feedback",
            json={"source": "bot", "kind": "helpful"},
            headers={"Authorization": f"Bearer {token_b}"},
        )

    assert response.status_code == 404


def test_add_audit_feedback_creates_cp_audit_log_entry(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/1/feedback",
            json={"source": "bot", "kind": "helpful"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    from services.control_plane_records import list_control_plane_audit_logs_for_workspace

    entries = list_control_plane_audit_logs_for_workspace(db_path, workspace_id)
    event_types = [e.event_type for e in entries]
    assert "audit.feedback_added" in event_types


# ===========================================================================
# POST /cp/audits/{audit_id}/triage
# ===========================================================================


def test_triage_audit_valid_returns_200(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/1/triage",
            json={"state": "acknowledged", "reason": "reviewed by team"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["audit_id"] == 1
    assert body["state"] == "acknowledged"
    assert "id" in body
    assert "created_at" in body


def test_triage_audit_append_only_multiple_states_all_succeed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        r1 = client.post(
            "/cp/audits/1/triage",
            json={"state": "acknowledged"},
            headers={"Authorization": f"Bearer {token}"},
        )
        r2 = client.post(
            "/cp/audits/1/triage",
            json={"state": "escalated"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] != r2.json()["id"]


def test_triage_audit_does_not_mutate_pull_request_audit(tmp_path, monkeypatch):
    """Triage writes only to audit_triage_events; pull_request_audits must not change."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT updated_at FROM pull_request_audits WHERE id = 1").fetchone()[0]

    with TestClient(create_api_app()) as client:
        client.post(
            "/cp/audits/1/triage",
            json={"state": "acknowledged"},
            headers={"Authorization": f"Bearer {token}"},
        )

    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT updated_at FROM pull_request_audits WHERE id = 1").fetchone()[0]

    assert before == after


def test_triage_audit_invalid_state_returns_422(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/1/triage",
            json={"state": "not_a_real_state"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


def test_triage_audit_wrong_scope_returns_403(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_READ])
    _seed_allocation(db_path, workspace_id, user_id)
    _seed_audit_row(db_path, audit_id=1)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/1/triage",
            json={"state": "acknowledged"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_triage_audit_unknown_audit_returns_404(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_WRITE_LOW])
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/audits/9999/triage",
            json={"state": "acknowledged"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404


# ===========================================================================
# GET /cp/workspaces/{workspace_id}/exports/{export_id}
# ===========================================================================


def _seed_export_job(db_path: str, workspace_id: int) -> int:
    """Create an export job and return its id."""
    job = create_export_job(
        db_path,
        repo_full=REPO_FULL,
        from_ts=1_000_000.0,
        to_ts=1_100_000.0,
        workspace_id=workspace_id,
        requested_by_user_id=None,
        requested_by_github_login=None,
        export_mode="compliance",
        include_artifact_content=False,
    )
    return job.id


def test_get_export_valid_returns_status_fields(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_READ])
    _seed_allocation(db_path, workspace_id, user_id)
    export_id = _seed_export_job(db_path, workspace_id)
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/workspaces/{workspace_id}/exports/{export_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == export_id
    assert body["workspace_id"] == workspace_id
    assert body["export_mode"] == "compliance"
    assert "status" in body
    assert "result_blob" not in body


def test_get_export_not_found_returns_404(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, workspace_id = _seed_db(db_path)
    _seed_principal(db_path, workspace_id, scopes=[SCOPE_DRIFT_READ])
    token = _make_token("lr-client-1", workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/workspaces/{workspace_id}/exports/9999",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404


def test_get_export_wrong_workspace_returns_404(tmp_path, monkeypatch):
    """Export exists but is owned by a different workspace — must return 404."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_a_id, ws_a_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_a_id, user_a_id)
    export_id = _seed_export_job(db_path, ws_a_id)

    user_b = create_user(db_path, display_name="B", primary_email="b2@test.example.com")
    ws_b = create_workspace(db_path, slug="ws-b2", display_name="WS B2", billing_owner_user_id=user_b.id)
    ws_b_id = ws_b.id
    _seed_principal(db_path, ws_b_id, scopes=[SCOPE_DRIFT_READ], client_id="lr-client-b2", raw_secret="secret-b2")
    token_b = _make_token("lr-client-b2", ws_b_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/workspaces/{ws_b_id}/exports/{export_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )

    assert response.status_code == 404
