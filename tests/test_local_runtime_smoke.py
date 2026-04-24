from __future__ import annotations

import os
import sys

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