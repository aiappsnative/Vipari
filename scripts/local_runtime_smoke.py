#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings
from services.audit_jobs import init_db
from services.queue import close_queue_backend
from services.runtime_guardrails import build_runtime_readiness
from services.schema_migrations import migrate_database


def _load_main_module():
    import importlib

    if "main" in sys.modules:
        return importlib.reload(sys.modules["main"])
    return importlib.import_module("main")


def _build_http_app(service_role: str):
    if service_role == "monolith":
        return _load_main_module().app
    if service_role == "api":
        from services.api_service import create_api_app

        return create_api_app()
    if service_role == "webhook":
        from services.webhook_service import create_webhook_app

        return create_webhook_app()
    raise ValueError(f"Unsupported HTTP smoke role: {service_role}")


def _run_worker_smoke(settings, migration_result) -> int:
    from services.cloud_worker import build_queue_backend

    async def exercise() -> tuple[dict[str, object], list[str]]:
        queue_backend = build_queue_backend(settings)
        try:
            readiness = await build_runtime_readiness(settings, queue_backend=queue_backend)
        finally:
            await close_queue_backend(queue_backend)
        checks = [
            ("ready", readiness.get("status") == "ok"),
            (
                "migration_bootstrap",
                "0001_bootstrap_relational_schema" in migration_result.applied_versions or not migration_result.pending_versions,
            ),
        ]
        failed = [name for name, passed in checks if not passed]
        return readiness, failed

    readiness, failed = asyncio.run(exercise())
    if failed:
        print(f"Worker smoke test failed: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(
        "Worker smoke test passed: "
        f"ready={readiness.get('status')}, migrations={migration_result.applied_versions or 'already applied'}"
    )
    return 0


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

    get_settings.cache_clear()
    settings = get_settings()
    db_path = settings.resolved_db_path

    init_db(db_path)
    migration_result = migrate_database(db_path)

    if args.service_role == "worker":
        return _run_worker_smoke(settings, migration_result)

    app = _build_http_app(args.service_role)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/health/ready")
        checks = [
            ("health", health.status_code == 200),
            ("ready", ready.status_code in {200, 503}),
            (
                "migration_bootstrap",
                "0001_bootstrap_relational_schema" in migration_result.applied_versions or not migration_result.pending_versions,
            ),
        ]
        summary_bits = [f"health={health.status_code}", f"ready={ready.status_code}"]

        if args.service_role == "monolith":
            landing = client.get("/")
            login = client.get("/login")
            pricing = client.get("/pricing")
            app_redirect = client.get("/app", follow_redirects=False)
            checks.extend(
                [
                    ("landing", landing.status_code == 200),
                    ("login", login.status_code == 200),
                    ("pricing", pricing.status_code == 200),
                    (
                        "app_redirect",
                        app_redirect.status_code in {302, 303, 307, 308}
                        and app_redirect.headers.get("location") == "/login",
                    ),
                ]
            )
            summary_bits.extend(
                [
                    f"landing={landing.status_code}",
                    f"login={login.status_code}",
                    f"pricing={pricing.status_code}",
                    f"app_redirect={app_redirect.status_code}",
                ]
            )

    failed = [name for name, passed in checks if not passed]
    if failed:
        print(f"Smoke test failed: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(
        "Smoke test passed: "
        + ", ".join(summary_bits)
        + f", migrations={migration_result.applied_versions or 'already applied'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())