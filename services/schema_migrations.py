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


def _ensure_pull_request_audit_fused_confidence(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        audit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(pull_request_audits)").fetchall()}
        if "fused_confidence" not in audit_columns:
            conn.execute("ALTER TABLE pull_request_audits ADD COLUMN fused_confidence TEXT")
    
def _ensure_onboarding_approval_columns(db_path: str) -> None:
    # Each group is guarded by checking whether the table exists at all (PRAGMA table_info
    # returns an empty result for a non-existent table, which would otherwise make every
    # 'not in' check true and cause ALTER TABLE to fail on a fresh minimal database).
    with connect_sqlite(db_path) as conn:
        onboarding_columns = {row["name"] for row in conn.execute("PRAGMA table_info(repository_onboardings)").fetchall()}
        if onboarding_columns:
            if "approved_by" not in onboarding_columns:
                conn.execute("ALTER TABLE repository_onboardings ADD COLUMN approved_by TEXT")
            if "approved_at" not in onboarding_columns:
                conn.execute("ALTER TABLE repository_onboardings ADD COLUMN approved_at REAL")

        baseline_columns = {row["name"] for row in conn.execute("PRAGMA table_info(onboarding_baseline_versions)").fetchall()}
        if baseline_columns:
            if "content_text" not in baseline_columns:
                conn.execute("ALTER TABLE onboarding_baseline_versions ADD COLUMN content_text TEXT")
            if "approval_status" not in baseline_columns:
                conn.execute("ALTER TABLE onboarding_baseline_versions ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'pending'")
            if "approved_by" not in baseline_columns:
                conn.execute("ALTER TABLE onboarding_baseline_versions ADD COLUMN approved_by TEXT")
            if "approved_at" not in baseline_columns:
                conn.execute("ALTER TABLE onboarding_baseline_versions ADD COLUMN approved_at REAL")
            if "approval_note" not in baseline_columns:
                conn.execute("ALTER TABLE onboarding_baseline_versions ADD COLUMN approval_note TEXT")

        baseline_audit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(baseline_audit_log)").fetchall()}
        if baseline_audit_columns:
            if "decision_type" not in baseline_audit_columns:
                conn.execute("ALTER TABLE baseline_audit_log ADD COLUMN decision_type TEXT")
            if "linked_findings_json" not in baseline_audit_columns:
                conn.execute("ALTER TABLE baseline_audit_log ADD COLUMN linked_findings_json TEXT NOT NULL DEFAULT '[]'")


def _ensure_machine_principals_schema(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS machine_principals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                principal_kind TEXT NOT NULL DEFAULT 'service_account',
                client_id TEXT NOT NULL UNIQUE,
                client_secret_encrypted TEXT NOT NULL,
                scopes_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                created_by_user_id INTEGER,
                revoked_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_machine_principals_workspace_id ON machine_principals(workspace_id)"
        )


def _ensure_session_flash_column(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(user_sessions)").fetchall()}
        if columns and "flash_json" not in columns:
            conn.execute("ALTER TABLE user_sessions ADD COLUMN flash_json TEXT")


def _ensure_audit_feedback_and_triage_tables(db_path: str) -> None:
    from .audit_feedback_records import init_audit_feedback_db

    init_audit_feedback_db(db_path)


MigrationHandler = Callable[[str], None]
MIGRATIONS: tuple[tuple[str, str, MigrationHandler], ...] = (
    (
        "0001_bootstrap_relational_schema",
        "Create and repair the relational application schema for the active backend.",
        _bootstrap_relational_schema,
    ),
    (
        "0002_add_pull_request_audits_fused_confidence",
        "Ensure pull_request_audits includes fused_confidence for legacy databases bootstrapped before the column existed.",
        _ensure_pull_request_audit_fused_confidence,
    ),
    (
        "0003_add_onboarding_approval_columns",
        "Ensure legacy onboarding approval tables include the later approval and audit-log columns required by baseline review flows.",
        _ensure_onboarding_approval_columns,
    ),
    (
        "0004_add_machine_principals",
        "Create the machine_principals table for workspace-bound service-account identities used by the internal control-plane auth layer.",
        _ensure_machine_principals_schema,
    ),
    (
        "0005_add_session_flash",
        "Add flash_json column to user_sessions for secure one-time secret delivery (session flash pattern).",
        _ensure_session_flash_column,
    ),
    (
        "0006_add_audit_feedback_and_triage_tables",
        "Create audit_feedback_events and audit_triage_events tables for low-risk control-plane write actions (issue #60).",
        _ensure_audit_feedback_and_triage_tables,
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