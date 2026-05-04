import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import pytest

from config import AppEnv, get_settings
from services.audit_jobs import init_db
from services.cloud_worker import build_queue_backend as build_worker_queue_backend
from services.queue import RedisQueue
from services.runtime_guardrails import build_runtime_readiness, validate_runtime_configuration


def _reset_settings_cache():
    get_settings.cache_clear()


def test_production_api_rejects_sqlite_and_insecure_cookie(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_BASE_URL", "http://example.com")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./promptdrift.db")
    _reset_settings_cache()

    settings = get_settings()
    with pytest.raises(RuntimeError) as exc_info:
        validate_runtime_configuration(settings)

    message = str(exc_info.value)
    assert "HTTPS" in message
    assert "SESSION_COOKIE_SECURE=true" in message
    assert "SQLite persistence" in message


def test_production_worker_requires_redis_queue(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.com/driftguard")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("GITHUB_APP_ID", "app-id")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "line1\nline2")
    monkeypatch.setenv("QUEUE_BACKEND", "sqlite")
    _reset_settings_cache()

    settings = get_settings()
    with pytest.raises(RuntimeError) as exc_info:
        validate_runtime_configuration(settings)

    assert "QUEUE_BACKEND=redis" in str(exc_info.value)


def test_runtime_configuration_rejects_malformed_github_private_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./promptdrift.db")
    monkeypatch.setenv("GITHUB_APP_ID", "app-id")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "not-a-pem")
    _reset_settings_cache()

    settings = get_settings()
    with pytest.raises(RuntimeError) as exc_info:
        validate_runtime_configuration(settings)

    assert "signing key is invalid" in str(exc_info.value)


def test_runtime_configuration_rejects_local_owner_fallback_on_remote_host(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_BASE_URL", "https://preview.example.com")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./promptdrift.db")
    monkeypatch.setenv("OWNER_GITHUB_USER_ID", "")
    monkeypatch.setenv("OWNER_GITHUB_LOGIN", "")
    monkeypatch.setenv("OWNER_EMAIL", "")
    _reset_settings_cache()

    settings = get_settings()
    with pytest.raises(RuntimeError) as exc_info:
        validate_runtime_configuration(settings)

    assert "local billing-owner fallback is localhost-only" in str(exc_info.value)


def test_staging_rejects_dev_auth_fallbacks(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_BASE_URL", "https://staging.example.com")
    monkeypatch.setenv("LOCAL_DEBUG_DISABLE_LOGIN", "true")
    monkeypatch.setenv("OWNER_GITHUB_USER_ID", "")
    monkeypatch.setenv("OWNER_GITHUB_LOGIN", "")
    monkeypatch.setenv("OWNER_EMAIL", "")
    _reset_settings_cache()

    settings = get_settings()
    with pytest.raises(RuntimeError) as exc_info:
        validate_runtime_configuration(settings)

    message = str(exc_info.value)
    assert "Staging forbids dev auth fallbacks" in message
    assert "local_debug_disable_login" in message
    assert "local_owner_fallback" in message


def test_local_debug_disable_login_requires_local_env_and_localhost(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("LOCAL_DEBUG_DISABLE_LOGIN", "true")
    _reset_settings_cache()

    settings = get_settings()
    with pytest.raises(RuntimeError) as exc_info:
        validate_runtime_configuration(settings)

    assert "LOCAL_DEBUG_DISABLE_LOGIN is allowed only when APP_ENV=local." in str(exc_info.value)


def test_app_env_supports_staging(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    _reset_settings_cache()

    settings = get_settings()

    assert settings.app_env == AppEnv.STAGING
    assert settings.is_staging is True
    assert settings.is_internet_reachable_env is True


@pytest.mark.anyio
async def test_readiness_reports_invalid_github_private_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./promptdrift.db")
    monkeypatch.setenv("GITHUB_APP_ID", "app-id")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "not-a-pem")
    _reset_settings_cache()

    settings = get_settings()
    with patch("services.runtime_guardrails.connect_sqlite") as connect:
        readiness = await build_runtime_readiness(settings)

    assert readiness["status"] == "failed"
    assert any(check["name"] == "config" and "signing key is invalid" in check["detail"] for check in readiness["checks"])
    connect.assert_called_once_with("./promptdrift.db")


@pytest.mark.anyio
async def test_readiness_verifies_postgres_connectivity(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.com/driftguard")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    _reset_settings_cache()

    settings = get_settings()
    _all_versions = [
        "0001_bootstrap_relational_schema",
        "0002_add_pull_request_audits_fused_confidence",
        "0003_add_onboarding_approval_columns",
        "0004_add_machine_principals",
        "0005_add_session_flash",
        "0006_add_audit_feedback_and_triage_tables",
        "0007_add_high_risk_proposal_tables",
    ]
    applied_migrations = [type("AppliedMigration", (), {"version": v})() for v in _all_versions]
    with patch("services.runtime_guardrails.connect_sqlite") as connect, patch(
        "services.runtime_guardrails.list_applied_migrations", return_value=applied_migrations
    ):
        readiness = await build_runtime_readiness(settings)

    assert readiness["status"] == "ok"
    assert any(check["name"] == "persistence" and "PostgreSQL connectivity verified." in check["detail"] for check in readiness["checks"])
    connect.assert_called_once_with("postgresql://user:pass@db.example.com/driftguard")


@pytest.mark.anyio
async def test_readiness_fails_when_schema_migrations_are_missing(tmp_path, monkeypatch):
    db_path = str(tmp_path / "readiness-migrations.db")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    _reset_settings_cache()

    settings = get_settings()
    readiness = await build_runtime_readiness(settings)

    assert readiness["status"] == "failed"
    assert any(check["name"] == "persistence" and check["status"] == "ok" for check in readiness["checks"])
    assert any(
        check["name"] == "migrations"
        and check["status"] == "failed"
        and "0001_bootstrap_relational_schema" in check["detail"]
        for check in readiness["checks"]
    )


@pytest.mark.anyio
async def test_readiness_reports_migrations_ok_after_bootstrap(tmp_path, monkeypatch):
    db_path = str(tmp_path / "readiness-migrations-ready.db")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    _reset_settings_cache()

    init_db(db_path)
    settings = get_settings()
    readiness = await build_runtime_readiness(settings)

    assert readiness["status"] == "ok"
    assert any(check["name"] == "migrations" and check["status"] == "ok" for check in readiness["checks"])


def test_worker_build_queue_backend_supports_redis(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    _reset_settings_cache()

    settings = get_settings()
    backend = build_worker_queue_backend(settings)

    assert isinstance(backend, RedisQueue)