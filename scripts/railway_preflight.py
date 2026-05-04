from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import get_settings
from services.queue import LocalSQLiteQueue, RedisQueue, SQSQueue, close_queue_backend
from services.runtime_guardrails import build_runtime_readiness


def _build_queue_backend(settings):
    if settings.service_role not in {"webhook", "worker"}:
        return None
    if settings.is_production and settings.queue_backend not in {"redis", "sqs"}:
        # Let readiness report the invalid config without touching the local SQLite queue path first.
        return None
    if settings.queue_backend == "sqs":
        return SQSQueue(settings.sqs_queue_url, settings.sqs_dlq_url)
    if settings.queue_backend == "redis":
        return RedisQueue(settings.redis_url)
    return LocalSQLiteQueue(settings.resolved_db_path)


async def _run_readiness(settings):
    queue_backend = _build_queue_backend(settings)
    try:
        return await build_runtime_readiness(settings, queue_backend=queue_backend)
    finally:
        await close_queue_backend(queue_backend)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate DriftGuard Railway production configuration.")
    parser.add_argument("--service-role", choices=["monolith", "api", "webhook", "worker"], help="Override SERVICE_ROLE for this check.")
    parser.add_argument("--app-env", choices=["local", "test", "staging", "production"], help="Override APP_ENV for this check.")
    args = parser.parse_args(argv)

    get_settings.cache_clear()
    settings = get_settings()
    if args.service_role:
        settings.service_role = args.service_role
    if args.app_env:
        settings.app_env = args.app_env

    readiness = asyncio.run(_run_readiness(settings))
    if readiness["status"] != "ok":
        print(
            f"Preflight failed for role={settings.service_role} env={settings.app_env}: "
            f"{json.dumps(readiness, sort_keys=True)}",
            file=sys.stderr,
        )
        return 1

    print(
        f"Preflight passed for role={settings.service_role} env={settings.app_env}: "
        f"{json.dumps(readiness, sort_keys=True)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())