from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .persistence import connect_sqlite, get_database_backend, resolve_db_path


@dataclass(frozen=True)
class AppliedMigration:
    version: str
    description: str
    applied_at: float


@dataclass(frozen=True)
class MigrationResult:
    backend: str
    database_locator: str
    applied_versions: list[str]
    pending_versions: list[str]


def _ensure_schema_migrations_table(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at REAL NOT NULL
            )
            """
        )


def _bootstrap_relational_schema(db_path: str) -> None:
    from .audit_jobs import bootstrap_application_schema

    bootstrap_application_schema(db_path)


MigrationHandler = Callable[[str], None]
MIGRATIONS: tuple[tuple[str, str, MigrationHandler], ...] = (
    (
        "0001_bootstrap_relational_schema",
        "Create and repair the relational application schema for the active backend.",
        _bootstrap_relational_schema,
    ),
)


def list_applied_migrations(db_path: str) -> list[AppliedMigration]:
    _ensure_schema_migrations_table(db_path)
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            "SELECT version, description, applied_at FROM schema_migrations ORDER BY applied_at ASC, version ASC"
        ).fetchall()
    return [AppliedMigration(version=row["version"], description=row["description"], applied_at=row["applied_at"]) for row in rows]


def migrate_database(db_path: str) -> MigrationResult:
    resolved_locator = resolve_db_path(db_path)
    backend = get_database_backend(resolved_locator)
    _ensure_schema_migrations_table(db_path)

    applied_versions = {item.version for item in list_applied_migrations(db_path)}
    newly_applied: list[str] = []

    for version, description, migrate in MIGRATIONS:
        if version in applied_versions:
            continue
        migrate(db_path)
        with connect_sqlite(db_path) as conn:
            conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                (version, description, time.time()),
            )
        newly_applied.append(version)
        applied_versions.add(version)

    pending_versions = [version for version, _description, _migrate in MIGRATIONS if version not in applied_versions]
    return MigrationResult(
        backend=backend,
        database_locator=resolved_locator,
        applied_versions=newly_applied,
        pending_versions=pending_versions,
    )