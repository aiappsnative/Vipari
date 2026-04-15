#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.audit_jobs import init_db
from services.schema_migrations import migrate_database


def _load_main_module():
    import importlib

    if "main" in sys.modules:
        return importlib.reload(sys.modules["main"])
    return importlib.import_module("main")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local PromptDrift runtime smoke test.")
    parser.add_argument("--db", help="Optional database path or DATABASE_URL override.")
    parser.add_argument("--app-env", default="local", choices=["local", "test", "production"], help="App environment to use for the smoke run.")
    parser.add_argument("--service-role", default="monolith", choices=["monolith", "api", "webhook", "worker"], help="Service role to use for the smoke run.")
    args = parser.parse_args(argv)

    load_dotenv(PROJECT_ROOT / ".env")

    if args.db:
        os.environ["DATABASE_URL"] = args.db
        os.environ["AUDIT_DB_PATH"] = args.db

    os.environ["APP_ENV"] = args.app_env
    os.environ["SERVICE_ROLE"] = args.service_role
    os.environ.setdefault("APP_BASE_URL", "https://example.com")
    os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
    os.environ.setdefault("API_ADMIN_TOKEN", "smoke-token")

    main_module = _load_main_module()
    db_path = main_module.AUDIT_DB_PATH

    init_db(db_path)
    migration_result = migrate_database(db_path)

    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as client:
        health = client.get("/health")
        ready = client.get("/health/ready")
        landing = client.get("/")
        login = client.get("/login")
        pricing = client.get("/pricing")
        app_redirect = client.get("/app", follow_redirects=False)

    checks = [
        ("health", health.status_code == 200),
        ("ready", ready.status_code in {200, 503}),
        ("landing", landing.status_code == 200),
        ("login", login.status_code == 200),
        ("pricing", pricing.status_code == 200),
        ("app_redirect", app_redirect.status_code in {302, 303, 307, 308} and app_redirect.headers.get("location") == "/login"),
        ("migration_bootstrap", "0001_bootstrap_relational_schema" in migration_result.applied_versions or not migration_result.pending_versions),
    ]

    failed = [name for name, passed in checks if not passed]
    if failed:
        print(f"Smoke test failed: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(
        "Smoke test passed: "
        f"health={health.status_code}, ready={ready.status_code}, landing={landing.status_code}, "
        f"login={login.status_code}, pricing={pricing.status_code}, app_redirect={app_redirect.status_code}, "
        f"migrations={migration_result.applied_versions or 'already applied'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())