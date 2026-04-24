import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import pytest

from config import get_settings
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
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    _reset_settings_cache()

    settings = get_settings()
    applied_migration = type("AppliedMigration", (), {"version": "0001_bootstrap_relational_schema"})()
    with patch("services.runtime_guardrails.connect_sqlite") as connect, patch(
        "services.runtime_guardrails.list_applied_migrations", return_value=[applied_migration]
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