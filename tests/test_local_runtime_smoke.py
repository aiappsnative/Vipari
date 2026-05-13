from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import scripts.local_runtime_smoke as local_runtime_smoke


def test_local_runtime_smoke_passes_against_temp_database(tmp_path, monkeypatch):
    db_path = str(tmp_path / "smoke.db")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "monolith")
    monkeypatch.setenv("APP_BASE_URL", "https://example.com")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("API_ADMIN_TOKEN", "smoke-token")
    monkeypatch.setenv("DATABASE_URL", db_path)
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)

    assert local_runtime_smoke.main([]) == 0


def test_local_runtime_smoke_supports_api_service_role(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api-smoke.db")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_BASE_URL", "https://example.com")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("API_ADMIN_TOKEN", "smoke-token")
    monkeypatch.setenv("DATABASE_URL", db_path)
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)

    assert local_runtime_smoke.main(["--service-role", "api"]) == 0


def test_local_runtime_smoke_supports_webhook_service_role(tmp_path, monkeypatch):
    db_path = str(tmp_path / "webhook-smoke.db")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "webhook")
    monkeypatch.setenv("APP_BASE_URL", "https://example.com")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("DATABASE_URL", db_path)
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)

    assert local_runtime_smoke.main(["--service-role", "webhook"]) == 0


def test_local_runtime_smoke_supports_worker_service_role(tmp_path, monkeypatch):
    db_path = str(tmp_path / "worker-smoke.db")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("APP_BASE_URL", "https://example.com")
    monkeypatch.setenv("DATABASE_URL", db_path)
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("GITHUB_APP_ID", "app-id")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "/tmp/test-key.pem")

    with patch("services.runtime_guardrails._validate_github_app_private_key"):
        assert local_runtime_smoke.main(["--service-role", "worker"]) == 0


def test_local_runtime_smoke_rejects_production_app_env():
    with pytest.raises(SystemExit) as exc_info:
        local_runtime_smoke.main(["--app-env", "production"])

    assert exc_info.value.code == 2


def test_local_runtime_smoke_supports_dashboard_deep_link_smoke(tmp_path, monkeypatch):
    db_path = str(tmp_path / "dashboard-deep-link-smoke.db")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_ROLE", "monolith")
    monkeypatch.setenv("APP_BASE_URL", "https://example.com")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("API_ADMIN_TOKEN", "smoke-token")
    monkeypatch.setenv("DATABASE_URL", db_path)
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)

    assert local_runtime_smoke.main(["--dashboard-deep-link-smoke"]) == 0