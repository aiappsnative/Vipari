"""Tests for the Control Plane Foundation V1 (issue #57).

Coverage:
- Persistence: machine_principals table creation, migration 0004, CRUD, idempotency
- JWT: issuance, valid token, expired, bad signature, wrong issuer, wrong audience
- /cp/* operator routes (admin-token gated): create principal, issue token, revoke
- /cp/* machine-auth routes (JWT bearer): read, low-risk write, high-risk write
- Scope enforcement: missing scope → 403, insufficient scope for high-risk → 403
- Workspace isolation: cross-workspace → 403
- Revoked principal: valid token but revoked record → 401
- Legacy admin-token routes: must remain functional
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import get_settings
from services.api_service import create_api_app
from services.audit_jobs import init_db
from services.control_plane_records import (
    allocate_repo_to_workspace,
    create_machine_principal,
    create_user,
    create_workspace,
    get_machine_principal_by_client_id,
    list_machine_principals_for_workspace,
    revoke_machine_principal,
    upsert_github_installation,
)
from services.internal_auth import (
    ALL_SCOPES,
    SCOPE_DRIFT_READ,
    SCOPE_DRIFT_WRITE_HIGH,
    SCOPE_DRIFT_WRITE_LOW,
    TokenValidationError,
    issue_cp_token,
    validate_cp_token,
)
from services.schema_migrations import migrate_database

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

ADMIN_TOKEN = "test-admin-token-xyz"
JWT_SECRET = "test-jwt-secret-that-is-long-enough"
JWT_ISSUER = "driftguard"
JWT_AUDIENCE = "driftguard-cp"


def _reset_settings_cache() -> None:
    get_settings.cache_clear()


def _configure_env(monkeypatch, db_path: str) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("API_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("APP_ENCRYPTION_KEY", "test-encryption-key-exactly32chars!")
    monkeypatch.setenv("INTERNAL_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("INTERNAL_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("INTERNAL_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_ENV", "local")
    _reset_settings_cache()


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


def _seed_workspace(db_path: str, slug: str = "ws-test") -> tuple[int, int]:
    """Create a user + workspace; return (user_id, workspace_id)."""
    user = create_user(db_path, display_name="Owner", primary_email=f"{slug}@example.com")
    workspace = create_workspace(db_path, slug=slug, display_name="Test WS", billing_owner_user_id=user.id)
    return user.id, workspace.id


def _seed_allocation(db_path: str, workspace_id: int, user_id: int, repo_full: str = "org/repo") -> None:
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
        repo_full=repo_full,
        baseline_mode="default_branch",
        activated_by_user_id=user_id,
    )


def _seed_principal(db_path: str, workspace_id: int, client_id: str = "test-client-1") -> str:
    create_machine_principal(
        db_path,
        workspace_id=workspace_id,
        display_name="test-bot",
        principal_kind="service_account",
        client_id=client_id,
        client_secret_encrypted="enc",
        scopes=[SCOPE_DRIFT_READ, SCOPE_DRIFT_WRITE_LOW, SCOPE_DRIFT_WRITE_HIGH],
    )
    return client_id


def _stub_dashboard():
    """Return a minimal RepoDashboardView for use in mocks."""
    from services.audit_records import RepoStaticDriftSummary
    from services.dashboard_views import RepoDashboardBackfillSummary, RepoDashboardView

    return RepoDashboardView(
        repo_full="org/repo",
        onboarding=None,
        baseline_review=None,
        backfill=RepoDashboardBackfillSummary(
            job_count=0,
            planned_job_count=0,
            processing_job_count=0,
            completed_job_count=0,
            failed_job_count=0,
            total_historical_versions=0,
            total_historical_profiles=0,
        ),
        pull_request_audit_count=0,
        baseline_version_count=0,
        drift_summary=RepoStaticDriftSummary(
            repo_full="org/repo",
            artifact_count=0,
            profile_count=0,
            baseline_linked_profile_count=0,
            avg_semantic_distance=0.0,
            avg_guardrail_shift=0.0,
            avg_capability_shift=0.0,
            avg_autonomy_shift=0.0,
            highest_capability_artifact_path=None,
            highest_capability_delta=0.0,
        ),
        top_drifting_artifacts=[],
        insights=[],
    )


# ===========================================================================
# Persistence: machine_principals table + migration
# ===========================================================================


def test_init_db_creates_machine_principals_table(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    init_db(db_path)
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "machine_principals" in tables


def test_migration_0004_creates_machine_principals_on_legacy_db(tmp_path):
    """Migration 0004 must handle DBs that were bootstrapped before the new table existed."""
    import sqlite3

    db_path = str(tmp_path / "legacy.db")
    init_db(db_path)

    # Simulate a pre-0004 state by dropping the table and removing the migration record.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS machine_principals")
        conn.execute(
            "DELETE FROM schema_migrations WHERE version = '0004_add_machine_principals'"
        )

    migrate_database(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "machine_principals" in tables


def test_migration_is_idempotent(tmp_path):
    db_path = str(tmp_path / "idem.db")
    init_db(db_path)
    # Running again must not raise.
    migrate_database(db_path)


def test_create_and_retrieve_machine_principal(tmp_path):
    db_path = str(tmp_path / "crud.db")
    init_db(db_path)
    _user_id, workspace_id = _seed_workspace(db_path, slug="ws-crud")

    principal = create_machine_principal(
        db_path,
        workspace_id=workspace_id,
        display_name="worker-bot",
        principal_kind="service_account",
        client_id="client-abc-123",
        client_secret_encrypted="encrypted-secret",
        scopes=[SCOPE_DRIFT_READ],
    )

    assert principal.client_id == "client-abc-123"
    assert principal.status == "active"
    assert principal.workspace_id == workspace_id

    fetched = get_machine_principal_by_client_id(db_path, "client-abc-123")
    assert fetched is not None
    assert fetched.client_id == principal.client_id
    assert fetched.status == "active"


def test_revoke_machine_principal(tmp_path):
    db_path = str(tmp_path / "revoke.db")
    init_db(db_path)
    _user_id, workspace_id = _seed_workspace(db_path, slug="ws-revoke")
    create_machine_principal(
        db_path,
        workspace_id=workspace_id,
        display_name="revoke-bot",
        principal_kind="service_account",
        client_id="client-revoke-1",
        client_secret_encrypted="enc",
        scopes=[SCOPE_DRIFT_READ],
    )

    revoked = revoke_machine_principal(db_path, "client-revoke-1")

    assert revoked is not None
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None


def test_list_machine_principals_scoped_to_workspace(tmp_path):
    db_path = str(tmp_path / "list.db")
    init_db(db_path)
    _ua, ws_a_id = _seed_workspace(db_path, slug="ws-a")
    _ub, ws_b_id = _seed_workspace(db_path, slug="ws-b")

    for cid in ("c-1", "c-2"):
        create_machine_principal(
            db_path,
            workspace_id=ws_a_id,
            display_name=f"bot-{cid}",
            principal_kind="service_account",
            client_id=cid,
            client_secret_encrypted="enc",
            scopes=[],
        )
    create_machine_principal(
        db_path,
        workspace_id=ws_b_id,
        display_name="bot-c-3",
        principal_kind="service_account",
        client_id="c-3",
        client_secret_encrypted="enc",
        scopes=[],
    )

    ws_a_principals = list_machine_principals_for_workspace(db_path, ws_a_id)
    assert len(ws_a_principals) == 2
    assert {p.client_id for p in ws_a_principals} == {"c-1", "c-2"}

    ws_b_principals = list_machine_principals_for_workspace(db_path, ws_b_id)
    assert len(ws_b_principals) == 1
    assert ws_b_principals[0].client_id == "c-3"


# ===========================================================================
# JWT: issuance and validation unit tests
# ===========================================================================


def test_issue_and_validate_cp_token_roundtrip():
    token = _make_token("client-1", workspace_id=42, scopes=[SCOPE_DRIFT_READ, SCOPE_DRIFT_WRITE_LOW])
    claims = validate_cp_token(token, secret=JWT_SECRET, issuer=JWT_ISSUER, audience=JWT_AUDIENCE)
    assert claims.subject == "client-1"
    assert claims.workspace_id == 42
    assert SCOPE_DRIFT_READ in claims.scopes
    assert SCOPE_DRIFT_WRITE_LOW in claims.scopes


def test_validate_cp_token_rejects_bad_signature():
    token = _make_token("client-1", workspace_id=42, scopes=[SCOPE_DRIFT_READ])
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # expected: short wrong-secret triggers PyJWT InsecureKeyLengthWarning
        with pytest.raises(TokenValidationError, match="invalid or malformed"):
            validate_cp_token(token, secret="wrong-secret", issuer=JWT_ISSUER, audience=JWT_AUDIENCE)


def test_validate_cp_token_rejects_expired_token():
    token = _make_token("client-1", workspace_id=42, scopes=[SCOPE_DRIFT_READ], ttl_seconds=-1)
    with pytest.raises(TokenValidationError, match="expired"):
        validate_cp_token(token, secret=JWT_SECRET, issuer=JWT_ISSUER, audience=JWT_AUDIENCE)


def test_validate_cp_token_rejects_wrong_issuer():
    token = issue_cp_token(
        client_id="c1",
        workspace_id=1,
        scopes=[SCOPE_DRIFT_READ],
        secret=JWT_SECRET,
        issuer="wrong-issuer",
        audience=JWT_AUDIENCE,
        ttl_seconds=3600,
    )
    with pytest.raises(TokenValidationError, match="issuer"):
        validate_cp_token(token, secret=JWT_SECRET, issuer=JWT_ISSUER, audience=JWT_AUDIENCE)


def test_validate_cp_token_rejects_wrong_audience():
    token = issue_cp_token(
        client_id="c1",
        workspace_id=1,
        scopes=[SCOPE_DRIFT_READ],
        secret=JWT_SECRET,
        issuer=JWT_ISSUER,
        audience="wrong-audience",
        ttl_seconds=3600,
    )
    with pytest.raises(TokenValidationError, match="audience"):
        validate_cp_token(token, secret=JWT_SECRET, issuer=JWT_ISSUER, audience=JWT_AUDIENCE)


def test_all_scopes_constant_is_complete():
    assert SCOPE_DRIFT_READ in ALL_SCOPES
    assert SCOPE_DRIFT_WRITE_LOW in ALL_SCOPES
    assert SCOPE_DRIFT_WRITE_HIGH in ALL_SCOPES


def test_short_jwt_secret_fails_runtime_validation():
    """INTERNAL_JWT_SECRET shorter than 32 bytes must be rejected at startup."""
    from unittest.mock import MagicMock
    from services.runtime_guardrails import validate_runtime_configuration

    mock_settings = MagicMock()
    mock_settings.queue_backend = "sqlite"
    mock_settings.service_role = "api"
    mock_settings.redis_url = ""
    mock_settings.github_webhook_secret = ""
    mock_settings.has_github_app_credentials = False
    mock_settings.has_internal_jwt_config = True
    mock_settings.internal_jwt_secret = "tooshort"  # 8 bytes — below minimum
    mock_settings.is_production = False

    with pytest.raises(RuntimeError, match="INTERNAL_JWT_SECRET must be at least 32 bytes"):
        validate_runtime_configuration(mock_settings)


def test_production_api_requires_jwt_secret():
    """Production API service must not start without INTERNAL_JWT_SECRET configured."""
    from unittest.mock import MagicMock
    from services.runtime_guardrails import validate_runtime_configuration

    mock_settings = MagicMock()
    mock_settings.queue_backend = "sqlite"
    mock_settings.service_role = "api"
    mock_settings.redis_url = ""
    mock_settings.github_webhook_secret = ""
    mock_settings.has_github_app_credentials = False
    mock_settings.has_internal_jwt_config = False
    mock_settings.internal_jwt_secret = ""
    mock_settings.is_production = True
    # Production checks
    mock_settings.app_base_url = "https://app.example.com"
    mock_settings.session_cookie_secure = True
    mock_settings.github_private_key_path = ""

    with pytest.raises(RuntimeError, match="INTERNAL_JWT_SECRET"):
        validate_runtime_configuration(mock_settings)


# /cp/* operator routes (admin-token gated)
# ===========================================================================


def test_cp_create_principal_requires_admin_token(tmp_path, monkeypatch):
    _configure_env(monkeypatch, str(tmp_path / "test.db"))
    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/principals",
            json={"workspace_id": 1, "display_name": "bot", "scopes": [SCOPE_DRIFT_READ]},
        )
    assert response.status_code == 401


def test_cp_create_principal_rejects_unknown_scopes(tmp_path, monkeypatch):
    _configure_env(monkeypatch, str(tmp_path / "test.db"))
    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/principals",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"workspace_id": 1, "display_name": "bot", "scopes": ["not.a.real.scope"]},
        )
    assert response.status_code == 400
    assert "Unknown scopes" in response.json()["detail"]


def test_cp_create_principal_and_issue_token(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    _user_id, workspace_id = _seed_workspace(db_path, slug="ws-op")

    with TestClient(create_api_app()) as client:
        create_resp = client.post(
            "/cp/principals",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"workspace_id": workspace_id, "display_name": "test-bot", "scopes": [SCOPE_DRIFT_READ]},
        )
        assert create_resp.status_code == 200
        data = create_resp.json()
        assert "client_id" in data
        assert "client_secret" in data
        assert data["workspace_id"] == workspace_id

        token_resp = client.post(
            f"/cp/principals/{data['client_id']}/token",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"workspace_id": workspace_id},
        )
        assert token_resp.status_code == 200
        token_data = token_resp.json()
        assert "token" in token_data

        # The returned token must be a valid JWT
        claims = validate_cp_token(
            token_data["token"],
            secret=JWT_SECRET,
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
        )
        assert claims.subject == data["client_id"]
        assert claims.workspace_id == workspace_id


def test_cp_revoke_principal_via_api(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    _user_id, workspace_id = _seed_workspace(db_path, slug="ws-revapi")

    with TestClient(create_api_app()) as client:
        create_resp = client.post(
            "/cp/principals",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"workspace_id": workspace_id, "display_name": "rev-bot", "scopes": [SCOPE_DRIFT_READ]},
        )
        client_id = create_resp.json()["client_id"]

        revoke_resp = client.delete(
            f"/cp/principals/{client_id}",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["status"] == "revoked"


def test_cp_issue_token_for_revoked_principal_is_rejected(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    _user_id, workspace_id = _seed_workspace(db_path, slug="ws-tok-rev")

    with TestClient(create_api_app()) as client:
        create_resp = client.post(
            "/cp/principals",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"workspace_id": workspace_id, "display_name": "b", "scopes": [SCOPE_DRIFT_READ]},
        )
        client_id = create_resp.json()["client_id"]
        client.delete(
            f"/cp/principals/{client_id}",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

        token_resp = client.post(
            f"/cp/principals/{client_id}/token",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"workspace_id": workspace_id},
        )
    assert token_resp.status_code == 400


def test_cp_issue_token_returns_404_for_unknown_principal(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/principals/no-such-client/token",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"workspace_id": 1},
        )
    assert response.status_code == 404


# ===========================================================================
# /cp/* machine-auth read route: GET /cp/workspaces/{id}/repos/{repo}/dashboard
# ===========================================================================


def test_cp_repo_dashboard_valid_token_with_drift_read(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-read")
    _seed_allocation(db_path, workspace_id, user_id)
    client_id = _seed_principal(db_path, workspace_id)
    token = _make_token(client_id, workspace_id, [SCOPE_DRIFT_READ])

    with patch("services.api_service.build_repo_dashboard_view", return_value=_stub_dashboard()):
        with TestClient(create_api_app()) as client:
            response = client.get(
                f"/cp/workspaces/{workspace_id}/repos/org/repo/dashboard",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert response.status_code == 200


def test_cp_repo_dashboard_missing_scope_returns_403(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-scope")
    _seed_allocation(db_path, workspace_id, user_id)
    client_id = _seed_principal(db_path, workspace_id)
    token = _make_token(client_id, workspace_id, [])  # no scopes

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403


def test_cp_repo_dashboard_expired_token_returns_401(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-exp")
    _seed_allocation(db_path, workspace_id, user_id)
    client_id = _seed_principal(db_path, workspace_id)
    token = _make_token(client_id, workspace_id, [SCOPE_DRIFT_READ], ttl_seconds=-1)

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401


def test_cp_repo_dashboard_invalid_token_returns_401(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)

    with TestClient(create_api_app()) as client:
        response = client.get(
            "/cp/workspaces/1/repos/org/repo/dashboard",
            headers={"Authorization": "Bearer not-a-valid-jwt"},
        )
    assert response.status_code == 401


def test_cp_repo_dashboard_no_auth_header_returns_401(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)

    with TestClient(create_api_app()) as client:
        response = client.get("/cp/workspaces/1/repos/org/repo/dashboard")
    assert response.status_code == 401


def test_cp_repo_dashboard_revoked_principal_returns_401(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-rvk")
    _seed_allocation(db_path, workspace_id, user_id)
    client_id = _seed_principal(db_path, workspace_id)
    token = _make_token(client_id, workspace_id, [SCOPE_DRIFT_READ])

    # Revoke the principal — the token is still signed-valid but the record is revoked.
    revoke_machine_principal(db_path, client_id)

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401


def test_cp_repo_dashboard_cross_workspace_returns_403(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-own")
    _user_b, other_workspace_id = _seed_workspace(db_path, slug="ws-other")
    _seed_allocation(db_path, workspace_id, user_id)
    client_id = _seed_principal(db_path, workspace_id)
    # Token is bound to workspace_id, URL targets other_workspace_id.
    token = _make_token(client_id, workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/workspaces/{other_workspace_id}/repos/org/repo/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403


def test_cp_repo_not_allocated_returns_404(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-noalloc")
    # Principal exists, workspace matches, but no allocation.
    client_id = _seed_principal(db_path, workspace_id)
    token = _make_token(client_id, workspace_id, [SCOPE_DRIFT_READ])

    with TestClient(create_api_app()) as client:
        response = client.get(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404


# ===========================================================================
# /cp/* machine-auth write routes: scope enforcement
# ===========================================================================


def test_cp_baseline_approve_requires_drift_write_high(tmp_path, monkeypatch):
    """drift.write.low must NOT be sufficient for baseline approval."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-bsl")
    _seed_allocation(db_path, workspace_id, user_id)
    client_id = _seed_principal(db_path, workspace_id)
    low_token = _make_token(client_id, workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    with TestClient(create_api_app()) as client:
        response = client.post(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/baseline/approve",
            headers={"Authorization": f"Bearer {low_token}"},
            json={},
        )
    assert response.status_code == 403


def test_cp_baseline_approve_accepts_drift_write_high(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-bsl-hi")
    _seed_allocation(db_path, workspace_id, user_id)
    client_id = _seed_principal(db_path, workspace_id)
    high_token = _make_token(client_id, workspace_id, [SCOPE_DRIFT_WRITE_HIGH])

    with patch("services.api_service.approve_repo_baseline", return_value=[]):
        with patch(
            "services.api_service.build_repo_dashboard_view",
            return_value=_stub_dashboard(),
        ):
            with TestClient(create_api_app()) as client:
                response = client.post(
                    f"/cp/workspaces/{workspace_id}/repos/org/repo/baseline/approve",
                    headers={"Authorization": f"Bearer {high_token}"},
                    json={},
                )
    assert response.status_code == 200
    assert response.json()["workspace_id"] == workspace_id


def test_cp_low_vs_high_scope_enforcement_is_distinct(tmp_path, monkeypatch):
    """Prove low-risk write succeeds while high-risk write is denied for the same token."""
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-split")
    _seed_allocation(db_path, workspace_id, user_id)
    client_id = _seed_principal(db_path, workspace_id)
    low_token = _make_token(client_id, workspace_id, [SCOPE_DRIFT_WRITE_LOW])

    mock_job = type("Job", (), {"id": 99})()
    with patch("services.api_service.create_export_job", return_value=mock_job):
        with TestClient(create_api_app()) as client:
            export_resp = client.post(
                f"/cp/workspaces/{workspace_id}/repos/org/repo/export",
                headers={"Authorization": f"Bearer {low_token}"},
                json={
                    "from_date": "2025-01-01",
                    "to_date": "2025-01-31",
                    "export_mode": "compliance",
                },
            )
    assert export_resp.status_code == 200
    assert export_resp.json()["job_id"] == 99

    with TestClient(create_api_app()) as client:
        approve_resp = client.post(
            f"/cp/workspaces/{workspace_id}/repos/org/repo/baseline/approve",
            headers={"Authorization": f"Bearer {low_token}"},
            json={},
        )
    assert approve_resp.status_code == 403


# ===========================================================================
# Compatibility: legacy admin-token routes must not break
# ===========================================================================


def test_legacy_health_route_is_unaffected(tmp_path, monkeypatch):
    _configure_env(monkeypatch, str(tmp_path / "test.db"))
    with TestClient(create_api_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_legacy_admin_api_route_still_works(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    with TestClient(create_api_app()) as client:
        response = client.get(
            "/api/persistence",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
    assert response.status_code == 200
    assert response.json()["backend"] == "sqlite"


# ===========================================================================
# Audit log emission tests
# ===========================================================================


def test_cp_create_principal_writes_audit_log(tmp_path, monkeypatch):
    """POST /cp/principals must emit a principal.created audit entry."""
    from services.control_plane_records import list_control_plane_audit_logs_for_workspace

    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    _user_id, workspace_id = _seed_workspace(db_path)

    with TestClient(create_api_app()) as client:
        response = client.post(
            "/cp/principals",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={
                "workspace_id": workspace_id,
                "display_name": "audit-bot",
                "principal_kind": "service_account",
                "scopes": [SCOPE_DRIFT_READ],
            },
        )
    assert response.status_code == 200

    entries = list_control_plane_audit_logs_for_workspace(db_path, workspace_id)
    assert any(e.event_type == "principal.created" for e in entries)


def test_cp_revoke_principal_writes_audit_log(tmp_path, monkeypatch):
    """DELETE /cp/principals/{id} must emit a principal.revoked audit entry."""
    from services.control_plane_records import list_control_plane_audit_logs_for_workspace

    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    _user_id, workspace_id = _seed_workspace(db_path)

    with TestClient(create_api_app()) as client:
        create_resp = client.post(
            "/cp/principals",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={
                "workspace_id": workspace_id,
                "display_name": "revoke-bot",
                "principal_kind": "service_account",
                "scopes": [SCOPE_DRIFT_READ],
            },
        )
        assert create_resp.status_code == 200
        client_id = create_resp.json()["client_id"]

        revoke_resp = client.delete(
            f"/cp/principals/{client_id}",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
    assert revoke_resp.status_code == 200

    entries = list_control_plane_audit_logs_for_workspace(db_path, workspace_id)
    assert any(e.event_type == "principal.revoked" for e in entries)


def test_cp_approve_baseline_writes_audit_log(tmp_path, monkeypatch):
    """POST baseline/approve must emit a baseline.approved audit entry."""
    from services.control_plane_records import list_control_plane_audit_logs_for_workspace

    db_path = str(tmp_path / "test.db")
    _configure_env(monkeypatch, db_path)
    init_db(db_path)
    user_id, workspace_id = _seed_workspace(db_path, slug="ws-aud-bsl")
    _seed_allocation(db_path, workspace_id, user_id)
    client_id = _seed_principal(db_path, workspace_id, client_id="audit-bsl-client")
    high_token = _make_token(client_id, workspace_id, [SCOPE_DRIFT_WRITE_HIGH])

    with patch("services.api_service.approve_repo_baseline", return_value=[]):
        with patch(
            "services.api_service.build_repo_dashboard_view",
            return_value=_stub_dashboard(),
        ):
            with TestClient(create_api_app()) as client:
                response = client.post(
                    f"/cp/workspaces/{workspace_id}/repos/org/repo/baseline/approve",
                    headers={"Authorization": f"Bearer {high_token}"},
                    json={},
                )
    assert response.status_code == 200

    entries = list_control_plane_audit_logs_for_workspace(db_path, workspace_id)
    assert any(e.event_type == "baseline.approved" for e in entries)
