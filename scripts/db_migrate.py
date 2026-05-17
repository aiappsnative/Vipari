from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import get_settings
from services.activity_schema_migrations import list_applied_activity_migrations, migrate_activity_database
from services.persistence import resolve_activity_db_path, resolve_db_path
from services.runtime_guardrails import validate_activity_migration_configuration, validate_migration_configuration
from services.schema_migrations import list_applied_migrations, migrate_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PromptDrift database bootstrap and schema migrations.")
    parser.add_argument("--db", help="Optional database path or DATABASE_URL override.")
    parser.add_argument("--target", choices=["primary", "activity"], default="primary", help="Select which database contract to migrate.")
    args = parser.parse_args()

    settings = get_settings()
    if args.target == "activity":
        db_path = resolve_activity_db_path(args.db)
        validate_activity_migration_configuration(settings, resolved_db_path=db_path)
        result = migrate_activity_database(db_path)
        applied_detail = [asdict(item) for item in list_applied_activity_migrations(db_path)]
    else:
        db_path = resolve_db_path(args.db)
        validate_migration_configuration(settings, resolved_db_path=db_path)
        result = migrate_database(db_path)
        applied_detail = [asdict(item) for item in list_applied_migrations(db_path)]
    payload = asdict(result)
    payload["target"] = args.target
    payload["applied_migrations_detail"] = applied_detail
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())