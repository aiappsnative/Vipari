#!/usr/bin/env python
from __future__ import annotations

# DEV-ONLY: This helper is for local and internal smoke validation.
# Do not use it as a production deployment or production readiness entrypoint.

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


def _seed_dashboard_deep_link_smoke_state(db_path: str) -> str:
    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.entitlements import derive_entitlement_payload

    user, _identity = upsert_github_identity(
        db_path,
        github_user_id="smoke-dashboard-user",
        github_login="smoke-dashboard-owner",
        display_name="Smoke Dashboard Owner",
        primary_email="smoke-dashboard@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "user:email", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        db_path,
        slug="smoke-dashboard-workspace",
        display_name="Smoke Dashboard Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        db_path,
        session_id="smoke-dashboard-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="smoke-csrf",
        expires_at=10_000_000_000,
    )
    upsert_subscription(
        db_path,
        workspace_id=workspace.id,
        stripe_subscription_id="smoke-dashboard-subscription",
        stripe_price_id="smoke-dashboard-price",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=1,
        current_period_end_at=2,
        next_payment_at=3,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        db_path,
        workspace_id=workspace.id,
        payload=derive_entitlement_payload("team", "active"),
    )
    upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=99031,
        account_id="99031",
        account_login="smoke-dashboard-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        db_path,
        workspace_id=workspace.id,
        installation_id=99031,
        repositories=[
            {
                "repo_github_id": "doria90/dummyAI",
                "repo_full": "doria90/dummyAI",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=99031,
        repo_github_id="doria90/dummyAI",
        repo_full="doria90/dummyAI",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(db_path, allocation.id, "active")
    return session.session_id


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
        print(f"Worker readiness smoke test failed: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(
        "Worker readiness smoke test passed: "
        f"ready={readiness.get('status')}, migrations={migration_result.applied_versions or 'already applied'}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local/internal Vipari runtime smoke test.")
    parser.add_argument("--db", help="Optional database path or DATABASE_URL override.")
    parser.add_argument("--app-env", default="local", choices=["local", "test"], help="Non-production app environment to use for the smoke run.")
    parser.add_argument("--service-role", default="monolith", choices=["monolith", "api", "webhook", "worker"], help="Service role to use for the smoke run.")
    parser.add_argument(
        "--dashboard-deep-link-smoke",
        action="store_true",
        help="For monolith runs, seed a local workspace and verify repo dashboard deep-link context survives the blocked shell.",
    )
    args = parser.parse_args(argv)

    load_dotenv(PROJECT_ROOT / ".env")

    if args.db:
        os.environ["DATABASE_URL"] = args.db
        os.environ["AUDIT_DB_PATH"] = args.db

    os.environ["APP_ENV"] = args.app_env
    os.environ["SERVICE_ROLE"] = args.service_role
    os.environ.setdefault("APP_BASE_URL", "https://example.com")
    os.environ["LOCAL_DEBUG_DISABLE_LOGIN"] = "false"
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
            landing = client.get("/", follow_redirects=False)
            login = client.get("/login")
            pricing = client.get("/pricing")
            app_redirect = client.get("/app", follow_redirects=False)
            checks.extend(
                [
                    (
                        "landing_redirect",
                        landing.status_code in {302, 303, 307, 308}
                        and landing.headers.get("location") == "/login",
                    ),
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

            if args.dashboard_deep_link_smoke:
                session_id = _seed_dashboard_deep_link_smoke_state(db_path)
                previous_cookie = client.cookies.get(settings.session_cookie_name)
                client.cookies.set(settings.session_cookie_name, session_id)
                try:
                    repo_response = client.get(
                        "/dashboard/doria90/dummyAI?artifact=prompts%2Fpolicy.md&pr=42&head_sha=sha-current"
                    )
                finally:
                    if previous_cookie is None:
                        client.cookies.pop(settings.session_cookie_name, None)
                    else:
                        client.cookies.set(settings.session_cookie_name, previous_cookie)
                deep_link_checks = [
                    ("dashboard_deep_link_status", repo_response.status_code == 200),
                    ("dashboard_deep_link_artifact", 'content="prompts/policy.md"' in repo_response.text),
                    ("dashboard_deep_link_pr", 'content="42"' in repo_response.text),
                    ("dashboard_deep_link_head_sha", 'content="sha-current"' in repo_response.text),
                    (
                        "dashboard_deep_link_href",
                        'href="/dashboard/doria90%2FdummyAI?tab=drift&artifact=prompts%2Fpolicy.md&pr=42&head_sha=sha-current"' in repo_response.text,
                    ),
                ]
                checks.extend(deep_link_checks)
                summary_bits.append(f"dashboard_deep_link={repo_response.status_code}")

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