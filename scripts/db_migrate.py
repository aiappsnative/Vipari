from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import get_settings
from services.persistence import resolve_db_path
from services.runtime_guardrails import validate_migration_configuration
from services.schema_migrations import list_applied_migrations, migrate_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PromptDrift database bootstrap and schema migrations.")
    parser.add_argument("--db", help="Optional database path or DATABASE_URL override.")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    validate_migration_configuration(get_settings(), resolved_db_path=db_path)
    result = migrate_database(db_path)
    payload = asdict(result)
    payload["applied_migrations_detail"] = [asdict(item) for item in list_applied_migrations(db_path)]
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())