"""Tests for control-plane high-risk change proposals (issue #61).

Coverage:

Baseline proposals — /cp/artifacts/{artifact_id}/baseline/proposals
  - POST: happy path returns 201 with proposal fields
  - POST: missing scope (drift.write.low) → 403
  - POST: artifact not in caller's workspace → 404 (no info leakage)
  - POST: flood limit (5 pending) → 409
  - POST: rationale too long → 422
  - POST: metadata too many keys → 422
  - POST: linked_audit_ids too many items → 422
  - POST: linked_audit_ids contains non-positive integer → 422
  - GET: lists proposals, requires drift.read
  - GET: cross-workspace artifact → 404
  - POST /approve: happy path returns 200 with approved status
  - POST /approve: requires drift.write.high → 403 with drift.write.low
  - POST /approve: requires human_operator kind → 403 with service_account
  - POST /approve: non-pending proposal → 409
  - POST /approve: expired proposal → 409
  - POST /approve: audit log entry is written
  - POST /reject: happy path returns 200 with rejected status
  - POST /reject: non-pending proposal → 409
  - POST /reject: does NOT require human_operator kind (service_account is fine)
  - POST /reject: requires drift.write.high

Repo onboarding proposals — /cp/workspaces/{workspace_id}/repos/onboarding-proposals
  - POST: happy path returns 201
  - POST: missing scope → 403
  - POST: cross-workspace → 403
  - POST: flood limit (20 pending) → 409
  - GET: returns list, requires drift.read
  - POST /approve: happy path, requires human_operator
  - POST /approve: requires human_operator kind → 403 with service_account
  - POST /reject: happy path

Principal creation scope-kind guard
  - Creating a service_account with drift.write.high → 400
  - Creating a human_operator with drift.write.high succeeds

Migration smoke
  - Running migration 0007 creates the new proposal tables
"""
from __future__ import annotations

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
from services.internal_auth import (
    SCOPE_DRIFT_READ,
    SCOPE_DRIFT_WRITE_HIGH,
    SCOPE_DRIFT_WRITE_LOW,
    issue_cp_token,
)
from services.secure_store import encrypt_text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADMIN_TOKEN = "test-admin-token-proposals"
JWT_SECRET = "test-jwt-secret-for-proposals-tests!"
JWT_ISSUER = "driftguard"
JWT_AUDIENCE = "driftguard-cp"
ENCRYPTION_KEY = "proposals-encryption-key-exactly32!"

REPO_FULL = "org/proposals-repo"
REPO_FULL_OTHER = "org/other-repo"

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
    """Bootstrap DB, create user + workspace. Returns (user_id, workspace_id)."""
    init_db(db_path)
    user = create_user(db_path, display_name="Owner", primary_email="owner@proposals.example.com")
    workspace = create_workspace(
        db_path,
        slug="ws-proposals",
        display_name="Proposals WS",
        billing_owner_user_id=user.id,
    )
    return user.id, workspace.id


def _seed_allocation(
    db_path: str,
    workspace_id: int,
    user_id: int,
    repo_full: str = REPO_FULL,
    installation_id: int = 9100,
) -> None:
    installation = upsert_github_installation(
        db_path,
        workspace_id=workspace_id,
        installation_id=installation_id,
        account_id="acc-prop",
        account_login="org",
        account_type="Organization",
        target_type="Organization",
    )
    allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace_id,
        installation_id=installation.installation_id,
        repo_github_id=f"rgid-{installation_id}",
        repo_full=repo_full,
        baseline_mode="default_branch",
        activated_by_user_id=user_id,
    )


def _seed_principal(
    db_path: str,
    workspace_id: int,
    scopes: list[str],
    client_id: str,
    raw_secret: str = "prop-secret",
    principal_kind: str = "service_account",
) -> None:
    encrypted = encrypt_text(raw_secret, ENCRYPTION_KEY)
    create_machine_principal(
        db_path,
        workspace_id=workspace_id,
        display_name="prop-bot",
        principal_kind=principal_kind,
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


def _seed_onboarding_and_artifact(
    db_path: str,
    repo_full: str = REPO_FULL,
    installation_id: int = 9100,
) -> int:
    """Inserts a minimal repository_onboardings row and a child onboarded_artifact.
    Returns the artifact id."""
    now = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repository_onboardings
                (repo_full, installation_id, default_branch, status,
                 discovered_artifact_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_full, installation_id, "main", "completed", 1, now, now),
        )
        onboarding_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO onboarded_artifacts
                (onboarding_id, repo_full, artifact_path, artifact_type,
                 discovery_reason, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (onboarding_id, repo_full, "prompts/main.txt", "prompt",
             "heuristic", 0.95, now),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ===========================================================================
# Baseline proposals — POST /cp/artifacts/{artifact_id}/baseline/proposals
# ===========================================================================


def test_create_baseline_proposal_happy_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-write-low")
    token = _make_token("c-write-low", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"rationale": "New stable version", "linked_audit_ids": [1, 2]},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["artifact_id"] == artifact_id
    assert body["status"] == "pending"
    assert body["rationale"] == "New stable version"
    assert body["linked_audit_ids"] == [1, 2]
    assert body["workspace_id"] == ws_id
    assert "id" in body
    assert "expires_at" in body
    assert body["expires_at"] > time.time()


def test_create_baseline_proposal_missing_scope_returns_403(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_READ], "c-read-only")
    token = _make_token("c-read-only", ws_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"rationale": "should fail"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_create_baseline_proposal_cross_workspace_returns_404(tmp_path, monkeypatch):
    """Artifact belongs to a different workspace — must return 404, not 403."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id_a, ws_id_a = _seed_db(db_path)
    # Create a second workspace
    user_b = create_user(db_path, display_name="B", primary_email="b@test.example.com")
    ws_b = create_workspace(db_path, slug="ws-b", display_name="WS B", billing_owner_user_id=user_b.id)

    _seed_allocation(db_path, ws_id_a, user_id_a, REPO_FULL, installation_id=9100)
    _seed_allocation(db_path, ws_b.id, user_b.id, REPO_FULL_OTHER, installation_id=9101)

    # Artifact in ws_a
    artifact_id = _seed_onboarding_and_artifact(db_path, REPO_FULL)

    # Principal is in ws_b
    _seed_principal(db_path, ws_b.id, [SCOPE_DRIFT_WRITE_LOW], "c-ws-b")
    token = _make_token("c-ws-b", ws_b.id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"rationale": "cross-workspace attempt"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404


def test_create_baseline_proposal_flood_limit_returns_409(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-flood")
    token = _make_token("c-flood", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        for i in range(5):
            r = client.post(
                f"/cp/artifacts/{artifact_id}/baseline/proposals",
                json={"rationale": f"proposal {i}"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 201

        # 6th proposal must hit the flood limit
        r_overflow = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"rationale": "one too many"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r_overflow.status_code == 409


def test_create_baseline_proposal_rationale_too_long_returns_422(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-rationale")
    token = _make_token("c-rationale", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"rationale": "x" * 2001},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


def test_create_baseline_proposal_metadata_too_many_keys_returns_422(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-meta")
    token = _make_token("c-meta", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"metadata": {f"key{i}": "val" for i in range(21)}},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


def test_create_baseline_proposal_linked_audit_ids_too_many_returns_422(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-audit-ids")
    token = _make_token("c-audit-ids", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"linked_audit_ids": list(range(1, 52))},  # 51 items
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


def test_create_baseline_proposal_linked_audit_ids_non_positive_returns_422(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-audit-neg")
    token = _make_token("c-audit-neg", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"linked_audit_ids": [1, -5]},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


# ===========================================================================
# Baseline proposals — GET /cp/artifacts/{artifact_id}/baseline/proposals
# ===========================================================================


def test_list_baseline_proposals_happy_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW, SCOPE_DRIFT_READ], "c-list")
    token = _make_token("c-list", ws_id, [SCOPE_DRIFT_WRITE_LOW, SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"rationale": "p1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"rationale": "p2"},
            headers={"Authorization": f"Bearer {token}"},
        )
        response = client.get(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "proposals" in body
    assert len(body["proposals"]) == 2


def test_list_baseline_proposals_requires_drift_read(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-no-read")
    token = _make_token("c-no-read", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_list_baseline_proposals_cross_workspace_returns_404(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id_a, ws_id_a = _seed_db(db_path)
    user_b = create_user(db_path, display_name="B2", primary_email="b2@test.example.com")
    ws_b = create_workspace(db_path, slug="ws-b2", display_name="WS B2", billing_owner_user_id=user_b.id)

    _seed_allocation(db_path, ws_id_a, user_id_a, REPO_FULL, installation_id=9200)
    _seed_allocation(db_path, ws_b.id, user_b.id, REPO_FULL_OTHER, installation_id=9201)

    artifact_id = _seed_onboarding_and_artifact(db_path, REPO_FULL)
    _seed_principal(db_path, ws_b.id, [SCOPE_DRIFT_READ], "c-ws-b-read")
    token = _make_token("c-ws-b-read", ws_b.id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404


# ===========================================================================
# Baseline proposals — approve
# ===========================================================================


def test_approve_baseline_proposal_happy_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)

    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-proposer")
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-approver",
                    principal_kind="human_operator")

    proposer_token = _make_token("c-proposer", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    approver_token = _make_token("c-approver", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        create_resp = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"rationale": "ready to promote"},
            headers={"Authorization": f"Bearer {proposer_token}"},
        )
        assert create_resp.status_code == 201
        proposal_id = create_resp.json()["id"]

        approve_resp = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{proposal_id}/approve",
            json={"decision_note": "Looks good"},
            headers={"Authorization": f"Bearer {approver_token}"},
        )

    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["status"] == "approved"
    assert body["decision_note"] == "Looks good"
    assert body["decided_at"] is not None


def test_approve_baseline_proposal_requires_write_high(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)

    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-low-only")
    token = _make_token("c-low-only", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        # Create
        cr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert cr.status_code == 201
        proposal_id = cr.json()["id"]

        # Approve with low scope → 403
        ar = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{proposal_id}/approve",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert ar.status_code == 403


def test_approve_baseline_proposal_requires_human_operator_kind(tmp_path, monkeypatch):
    """A service_account with drift.write.high must be blocked from approving."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)

    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-proposer2")
    # service_account with high scope — kind guard should block it
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-svc-high",
                    principal_kind="service_account")

    proposer_token = _make_token("c-proposer2", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    svc_high_token = _make_token("c-svc-high", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={},
            headers={"Authorization": f"Bearer {proposer_token}"},
        )
        assert cr.status_code == 201
        proposal_id = cr.json()["id"]

        ar = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{proposal_id}/approve",
            json={},
            headers={"Authorization": f"Bearer {svc_high_token}"},
        )

    assert ar.status_code == 403


def test_approve_baseline_proposal_already_approved_returns_409(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)

    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-prop3")
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-appr2",
                    principal_kind="human_operator")

    pt = _make_token("c-prop3", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    at = _make_token("c-appr2", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={},
            headers={"Authorization": f"Bearer {pt}"},
        )
        pid = cr.json()["id"]
        r1 = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{pid}/approve",
            json={},
            headers={"Authorization": f"Bearer {at}"},
        )
        assert r1.status_code == 200
        # Second approve must fail
        r2 = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{pid}/approve",
            json={},
            headers={"Authorization": f"Bearer {at}"},
        )

    assert r2.status_code == 409


def test_approve_baseline_proposal_expired_returns_409(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)

    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-prop-exp")
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-appr-exp",
                    principal_kind="human_operator")

    pt = _make_token("c-prop-exp", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    at = _make_token("c-appr-exp", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={},
            headers={"Authorization": f"Bearer {pt}"},
        )
        assert cr.status_code == 201
        pid = cr.json()["id"]

    # Back-date the expiry to force expiry
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE cp_baseline_proposals SET expires_at = ? WHERE id = ?",
            (time.time() - 1, pid),
        )

    with TestClient(create_api_app()) as client:
        ar = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{pid}/approve",
            json={},
            headers={"Authorization": f"Bearer {at}"},
        )

    assert ar.status_code == 409


def test_approve_baseline_proposal_writes_audit_log(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)

    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-prop-al")
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-appr-al",
                    principal_kind="human_operator")

    pt = _make_token("c-prop-al", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    at = _make_token("c-appr-al", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={},
            headers={"Authorization": f"Bearer {pt}"},
        )
        pid = cr.json()["id"]
        client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{pid}/approve",
            json={"decision_note": "audit test"},
            headers={"Authorization": f"Bearer {at}"},
        )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT event_type, subject_type, subject_id FROM control_plane_audit_logs "
            "WHERE event_type IN ('proposal.created', 'proposal.approved') ORDER BY created_at"
        ).fetchall()

    event_types = [r[0] for r in rows]
    assert "proposal.created" in event_types
    assert "proposal.approved" in event_types


# ===========================================================================
# Baseline proposals — reject
# ===========================================================================


def test_reject_baseline_proposal_happy_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)

    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-prop-rej")
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-rejector",
                    principal_kind="service_account")  # service_account is fine for reject

    pt = _make_token("c-prop-rej", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    rt = _make_token("c-rejector", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={"rationale": "maybe"},
            headers={"Authorization": f"Bearer {pt}"},
        )
        assert cr.status_code == 201
        pid = cr.json()["id"]

        rr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{pid}/reject",
            json={"decision_note": "not ready"},
            headers={"Authorization": f"Bearer {rt}"},
        )

    assert rr.status_code == 200
    body = rr.json()
    assert body["status"] == "rejected"
    assert body["decision_note"] == "not ready"


def test_reject_baseline_proposal_already_rejected_returns_409(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)

    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-prop-rej2")
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-rej2",
                    principal_kind="service_account")

    pt = _make_token("c-prop-rej2", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    rt = _make_token("c-rej2", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={},
            headers={"Authorization": f"Bearer {pt}"},
        )
        pid = cr.json()["id"]
        r1 = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{pid}/reject",
            json={},
            headers={"Authorization": f"Bearer {rt}"},
        )
        assert r1.status_code == 200
        r2 = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{pid}/reject",
            json={},
            headers={"Authorization": f"Bearer {rt}"},
        )

    assert r2.status_code == 409


def test_reject_baseline_proposal_requires_write_high(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_allocation(db_path, ws_id, user_id)
    artifact_id = _seed_onboarding_and_artifact(db_path)

    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-low-rej")
    token = _make_token("c-low-rej", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        pid = cr.json()["id"]
        rr = client.post(
            f"/cp/artifacts/{artifact_id}/baseline/proposals/{pid}/reject",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert rr.status_code == 403


# ===========================================================================
# Repo onboarding proposals — POST /cp/workspaces/{ws}/repos/onboarding-proposals
# ===========================================================================


def test_create_onboarding_proposal_happy_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-ob-create")
    token = _make_token("c-ob-create", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
            json={"repo_full": "acme/new-service", "rationale": "needs monitoring"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["workspace_id"] == ws_id
    assert body["repo_full"] == "acme/new-service"
    assert body["status"] == "pending"
    assert body["proposal_kind"] == "onboard"
    assert body["expires_at"] > time.time()


def test_create_onboarding_proposal_missing_scope_returns_403(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_READ], "c-ob-nowrite")
    token = _make_token("c-ob-nowrite", ws_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
            json={"repo_full": "acme/blocked"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_create_onboarding_proposal_cross_workspace_returns_403(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id_a, ws_id_a = _seed_db(db_path)
    user_b = create_user(db_path, display_name="B3", primary_email="b3@test.example.com")
    ws_b = create_workspace(db_path, slug="ws-b3", display_name="WS B3", billing_owner_user_id=user_b.id)

    _seed_principal(db_path, ws_b.id, [SCOPE_DRIFT_WRITE_LOW], "c-ob-cross")
    token = _make_token("c-ob-cross", ws_b.id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/workspaces/{ws_id_a}/repos/onboarding-proposals",
            json={"repo_full": "acme/spy-attempt"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_create_onboarding_proposal_flood_limit_returns_409(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-ob-flood")
    token = _make_token("c-ob-flood", ws_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        for i in range(20):
            r = client.post(
                f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
                json={"repo_full": f"acme/repo-{i}"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 201

        r_overflow = client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
            json={"repo_full": "acme/one-too-many"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r_overflow.status_code == 409


# ===========================================================================
# Repo onboarding proposals — GET
# ===========================================================================


def test_list_onboarding_proposals_happy_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW, SCOPE_DRIFT_READ], "c-ob-list")
    token = _make_token("c-ob-list", ws_id, [SCOPE_DRIFT_WRITE_LOW, SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
            json={"repo_full": "acme/list-a"},
            headers={"Authorization": f"Bearer {token}"},
        )
        client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
            json={"repo_full": "acme/list-b"},
            headers={"Authorization": f"Bearer {token}"},
        )
        response = client.get(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["proposals"]) == 2


# ===========================================================================
# Repo onboarding proposals — approve / reject
# ===========================================================================


def test_approve_onboarding_proposal_happy_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-ob-prop")
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-ob-appr",
                    principal_kind="human_operator")

    pt = _make_token("c-ob-prop", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    at = _make_token("c-ob-appr", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
            json={"repo_full": "acme/approve-me"},
            headers={"Authorization": f"Bearer {pt}"},
        )
        assert cr.status_code == 201
        pid = cr.json()["id"]

        ar = client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals/{pid}/approve",
            json={"decision_note": "go ahead"},
            headers={"Authorization": f"Bearer {at}"},
        )

    assert ar.status_code == 200
    body = ar.json()
    assert body["status"] == "approved"
    assert body["decision_note"] == "go ahead"


def test_approve_onboarding_proposal_requires_human_operator(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-ob-prop2")
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-ob-svc-high",
                    principal_kind="service_account")

    pt = _make_token("c-ob-prop2", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    svc = _make_token("c-ob-svc-high", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
            json={"repo_full": "acme/blocked-approve"},
            headers={"Authorization": f"Bearer {pt}"},
        )
        pid = cr.json()["id"]

        ar = client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals/{pid}/approve",
            json={},
            headers={"Authorization": f"Bearer {svc}"},
        )

    assert ar.status_code == 403


def test_reject_onboarding_proposal_happy_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    user_id, ws_id = _seed_db(db_path)
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_LOW], "c-ob-prop3")
    _seed_principal(db_path, ws_id, [SCOPE_DRIFT_WRITE_HIGH], "c-ob-rej",
                    principal_kind="service_account")

    pt = _make_token("c-ob-prop3", ws_id, [SCOPE_DRIFT_WRITE_LOW])
    rt = _make_token("c-ob-rej", ws_id, [SCOPE_DRIFT_WRITE_HIGH])

    with TestClient(create_api_app()) as client:
        cr = client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals",
            json={"repo_full": "acme/reject-me"},
            headers={"Authorization": f"Bearer {pt}"},
        )
        pid = cr.json()["id"]

        rr = client.post(
            f"/cp/workspaces/{ws_id}/repos/onboarding-proposals/{pid}/reject",
            json={"decision_note": "not now"},
            headers={"Authorization": f"Bearer {rt}"},
        )

    assert rr.status_code == 200
    assert rr.json()["status"] == "rejected"


# ===========================================================================
# Principal creation — scope/kind guard
# ===========================================================================


def test_create_service_account_with_write_high_is_rejected(tmp_path, monkeypatch):
    """service_account must not be creatable with drift.write.high."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _seed_db(db_path)
    with sqlite3.connect(db_path) as conn:
        ws_id = conn.execute("SELECT id FROM workspaces LIMIT 1").fetchone()[0]

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/principals",
            json={
                "workspace_id": ws_id,
                "display_name": "bad-bot",
                "principal_kind": "service_account",
                "scopes": [SCOPE_DRIFT_WRITE_HIGH],
            },
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

    assert response.status_code == 400


def test_create_human_operator_with_write_high_succeeds(tmp_path, monkeypatch):
    """human_operator is allowed to hold drift.write.high."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    _seed_db(db_path)
    with sqlite3.connect(db_path) as conn:
        ws_id = conn.execute("SELECT id FROM workspaces LIMIT 1").fetchone()[0]

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/principals",
            json={
                "workspace_id": ws_id,
                "display_name": "human-approver",
                "principal_kind": "human_operator",
                "scopes": [SCOPE_DRIFT_WRITE_HIGH],
            },
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["principal_kind"] == "human_operator"


# ===========================================================================
# Migration smoke
# ===========================================================================


def test_migration_0007_creates_proposal_tables(tmp_path):
    db_path = str(tmp_path / "mig-test.db")
    init_db(db_path)

    from services.schema_migrations import migrate_database
    result = migrate_database(db_path)

    assert "0007_add_high_risk_proposal_tables" in result.applied_versions or \
           "0007_add_high_risk_proposal_tables" not in result.pending_versions

    with sqlite3.connect(db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

    assert "cp_baseline_proposals" in tables
    assert "cp_repo_onboarding_proposals" in tables
