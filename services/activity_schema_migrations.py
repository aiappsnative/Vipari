from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .persistence import connect_sqlite, get_database_backend, resolve_activity_db_path


@dataclass(frozen=True)
class AppliedActivityMigration:
    version: str
    description: str
    applied_at: float


@dataclass(frozen=True)
class ActivityMigrationResult:
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


def _bootstrap_activity_schema(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT,
                occurred_at REAL NOT NULL,
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                workspace_id INTEGER,
                actor_user_id INTEGER,
                actor_label TEXT,
                repo_full TEXT,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                search_text TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_events_occurred_at_id ON activity_events(occurred_at DESC, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_events_workspace_occurred_at ON activity_events(workspace_id, occurred_at DESC, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_events_repo_occurred_at ON activity_events(repo_full, occurred_at DESC, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_events_type_occurred_at ON activity_events(event_type, occurred_at DESC, id DESC)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_events_external_id ON activity_events(external_id) WHERE external_id IS NOT NULL"
        )


def _ensure_activity_external_id_column(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(activity_events)").fetchall()}
        if "external_id" not in columns:
            conn.execute("ALTER TABLE activity_events ADD COLUMN external_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_events_external_id ON activity_events(external_id) WHERE external_id IS NOT NULL"
        )


ActivityMigrationHandler = Callable[[str], None]
ACTIVITY_MIGRATIONS: tuple[tuple[str, str, ActivityMigrationHandler], ...] = (
    (
        "0001_bootstrap_activity_schema",
        "Create the append-only activity_events table and its primary timeline/filter indexes.",
        _bootstrap_activity_schema,
    ),
    (
        "0002_add_activity_external_ids",
        "Add stable external IDs so mirrored admin activity can be deduplicated against primary historical rows.",
        _ensure_activity_external_id_column,
    ),
)


def list_applied_activity_migrations(db_path: str) -> list[AppliedActivityMigration]:
    _ensure_schema_migrations_table(db_path)
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            "SELECT version, description, applied_at FROM schema_migrations ORDER BY applied_at ASC, version ASC"
        ).fetchall()
    return [
        AppliedActivityMigration(version=row["version"], description=row["description"], applied_at=row["applied_at"])
        for row in rows
    ]


def migrate_activity_database(db_path: str) -> ActivityMigrationResult:
    resolved_locator = resolve_activity_db_path(db_path)
    backend = get_database_backend(resolved_locator)
    _ensure_schema_migrations_table(db_path)

    applied_versions = {item.version for item in list_applied_activity_migrations(db_path)}
    newly_applied: list[str] = []

    for version, description, migrate in ACTIVITY_MIGRATIONS:
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

    pending_versions = [version for version, _description, _migrate in ACTIVITY_MIGRATIONS if version not in applied_versions]
    return ActivityMigrationResult(
        backend=backend,
        database_locator=resolved_locator,
        applied_versions=newly_applied,
        pending_versions=pending_versions,
    )