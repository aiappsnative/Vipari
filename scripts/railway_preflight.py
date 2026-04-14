from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import get_settings
from services.runtime_guardrails import validate_runtime_configuration


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate DriftGuard Railway production configuration.")
    parser.add_argument("--service-role", choices=["monolith", "api", "webhook", "worker"], help="Override SERVICE_ROLE for this check.")
    parser.add_argument("--app-env", choices=["local", "test", "production"], help="Override APP_ENV for this check.")
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()
    if args.service_role:
        settings.service_role = args.service_role
    if args.app_env:
        settings.app_env = args.app_env

    try:
        validate_runtime_configuration(settings)
    except RuntimeError as exc:
        print(f"Preflight failed for role={settings.service_role} env={settings.app_env}: {exc}", file=sys.stderr)
        return 1

    print(f"Preflight passed for role={settings.service_role} env={settings.app_env}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())