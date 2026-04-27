from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import scripts.railway_preflight as railway_preflight
from services.queue import LocalSQLiteQueue


def test_railway_preflight_returns_failure_when_readiness_fails(capsys):
    payload = {
        "status": "failed",
        "app_env": "production",
        "service_role": "api",
        "checks": [{"name": "persistence", "status": "failed", "detail": "connection refused"}],
    }

    with patch("scripts.railway_preflight._run_readiness", return_value=payload):
        exit_code = railway_preflight.main(["--service-role", "api", "--app-env", "production"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Preflight failed" in captured.err
    assert "connection refused" in captured.err


def test_railway_preflight_returns_success_when_readiness_passes(capsys):
    payload = {
        "status": "ok",
        "app_env": "production",
        "service_role": "worker",
        "checks": [
            {"name": "config", "status": "ok", "detail": "Runtime configuration validated."},
            {"name": "persistence", "status": "ok", "detail": "PostgreSQL connectivity verified."},
            {"name": "queue", "status": "ok", "detail": "Queue backend reachable (depth=0)."},
        ],
    }

    with patch("scripts.railway_preflight._run_readiness", return_value=payload):
        exit_code = railway_preflight.main(["--service-role", "worker", "--app-env", "production"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Preflight passed" in captured.out
    assert '"status": "ok"' in captured.out


def test_preflight_does_not_build_sqlite_queue_for_invalid_production_worker():
    settings = SimpleNamespace(
        service_role="worker",
        queue_backend="sqlite",
        is_production=True,
        resolved_db_path="./promptdrift.db",
    )

    backend = railway_preflight._build_queue_backend(settings)

    assert backend is None


def test_preflight_still_builds_sqlite_queue_for_local_worker(tmp_path):
    settings = SimpleNamespace(
        service_role="worker",
        queue_backend="sqlite",
        is_production=False,
        resolved_db_path=str(tmp_path / "queue.db"),
    )

    backend = railway_preflight._build_queue_backend(settings)

    assert isinstance(backend, LocalSQLiteQueue)
    assert backend.db_path == str(tmp_path / "queue.db")