from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


CURRENT_PERSISTENCE_SCHEMA_VERSION = 2
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 5000
DEFAULT_PRODUCTION_TARGET = "postgresql"
OPERATIONAL_TABLES = (
    "audit_jobs",
    "user_sessions",
)
DURABLE_TABLES = (
    "pull_request_audits",
    "changed_artifacts",
    "findings",
    "audit_comments",
    "artifact_versions",
    "static_artifact_profiles",
    "repository_onboardings",
    "onboarded_artifacts",
    "onboarding_baseline_versions",
    "historical_backfill_jobs",
    "historical_artifact_versions",
    "historical_static_profiles",
    "users",
    "github_identities",
    "user_sessions",
    "workspaces",
    "workspace_memberships",
    "billing_customers",
    "subscriptions",
    "entitlements",
    "github_installations",
    "repo_connections",
    "repo_allocations",
    "control_plane_audit_logs",
    "webhook_event_receipts",
)


@dataclass(frozen=True)
class PersistenceStatus:
    backend: str
    database_path: str
    database_exists: bool
    schema_version: int
    production_target: str
    sqlite_busy_timeout_ms: int
    sqlite_wal_enabled: bool
    operational_tables: list[str]
    durable_tables: list[str]


def resolve_db_path(explicit_path: str | None = None) -> str:
    if explicit_path:
        return explicit_path
    return os.getenv("AUDIT_DB_PATH", str(Path(__file__).resolve().parent.parent / "promptdrift.db"))


def connect_sqlite(db_path: str, *, foreign_keys: bool = False) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {DEFAULT_SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_persistence_metadata(db_path: str) -> None:
    now = time.time()
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persistence_metadata (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                backend TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                production_target TEXT NOT NULL,
                sqlite_busy_timeout_ms INTEGER NOT NULL,
                sqlite_wal_enabled INTEGER NOT NULL,
                operational_tables_json TEXT NOT NULL,
                durable_tables_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

        existing = conn.execute("SELECT created_at FROM persistence_metadata WHERE id = 1").fetchone()
        created_at = existing["created_at"] if existing is not None else now
        conn.execute(
            """
            INSERT INTO persistence_metadata (
                id,
                backend,
                schema_version,
                production_target,
                sqlite_busy_timeout_ms,
                sqlite_wal_enabled,
                operational_tables_json,
                durable_tables_json,
                created_at,
                updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                backend = excluded.backend,
                schema_version = excluded.schema_version,
                production_target = excluded.production_target,
                sqlite_busy_timeout_ms = excluded.sqlite_busy_timeout_ms,
                sqlite_wal_enabled = excluded.sqlite_wal_enabled,
                operational_tables_json = excluded.operational_tables_json,
                durable_tables_json = excluded.durable_tables_json,
                updated_at = excluded.updated_at
            """,
            (
                "sqlite",
                CURRENT_PERSISTENCE_SCHEMA_VERSION,
                DEFAULT_PRODUCTION_TARGET,
                DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
                1,
                json.dumps(list(OPERATIONAL_TABLES)),
                json.dumps(list(DURABLE_TABLES)),
                created_at,
                now,
            ),
        )


def get_persistence_status(db_path: str) -> PersistenceStatus:
    resolved_path = resolve_db_path(db_path)
    database_exists = Path(resolved_path).exists()
    if not database_exists:
        return PersistenceStatus(
            backend="sqlite",
            database_path=resolved_path,
            database_exists=False,
            schema_version=CURRENT_PERSISTENCE_SCHEMA_VERSION,
            production_target=DEFAULT_PRODUCTION_TARGET,
            sqlite_busy_timeout_ms=DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
            sqlite_wal_enabled=True,
            operational_tables=list(OPERATIONAL_TABLES),
            durable_tables=list(DURABLE_TABLES),
        )

    with connect_sqlite(resolved_path) as conn:
        row = conn.execute("SELECT * FROM persistence_metadata WHERE id = 1").fetchone()

    if row is None:
        return PersistenceStatus(
            backend="sqlite",
            database_path=resolved_path,
            database_exists=True,
            schema_version=CURRENT_PERSISTENCE_SCHEMA_VERSION,
            production_target=DEFAULT_PRODUCTION_TARGET,
            sqlite_busy_timeout_ms=DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
            sqlite_wal_enabled=True,
            operational_tables=list(OPERATIONAL_TABLES),
            durable_tables=list(DURABLE_TABLES),
        )

    return PersistenceStatus(
        backend=row["backend"],
        database_path=resolved_path,
        database_exists=True,
        schema_version=row["schema_version"],
        production_target=row["production_target"],
        sqlite_busy_timeout_ms=row["sqlite_busy_timeout_ms"],
        sqlite_wal_enabled=bool(row["sqlite_wal_enabled"]),
        operational_tables=json.loads(row["operational_tables_json"]),
        durable_tables=json.loads(row["durable_tables_json"]),
    )