import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import pytest

from config import get_settings
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


@pytest.mark.anyio
async def test_readiness_reports_postgres_runtime_blocker(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.com/driftguard")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    _reset_settings_cache()

    settings = get_settings()
    readiness = await build_runtime_readiness(settings)

    assert readiness["status"] == "failed"
    assert any(check["name"] == "persistence" and "not implemented" in check["detail"] for check in readiness["checks"])


def test_worker_build_queue_backend_supports_redis(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    _reset_settings_cache()

    settings = get_settings()
    backend = build_worker_queue_backend(settings)

    assert isinstance(backend, RedisQueue)