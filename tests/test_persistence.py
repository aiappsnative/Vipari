import os
import sqlite3
import sys
from dataclasses import dataclass
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import pytest

from config import get_settings
from scripts import db_migrate as db_migrate_script
from services.audit_jobs import init_db
from services.persistence import PostgresConnection, get_persistence_status, persistence_status_payload, resolve_db_path
from services.schema_migrations import list_applied_migrations, migrate_database


def test_init_db_persists_backend_metadata_and_table_groups(tmp_path):
    db_path = str(tmp_path / "driftguard.db")

    init_db(db_path)
    status = get_persistence_status(db_path)

    assert status.backend == "sqlite"
    assert status.database_exists is True
    assert status.production_target == "postgresql"
    assert "audit_jobs" in status.operational_tables
    assert "pull_request_audits" in status.durable_tables
    assert "historical_static_profiles" in status.durable_tables
    assert "repo_posture_snapshots" in status.durable_tables

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT backend, schema_version FROM persistence_metadata WHERE id = 1").fetchone()

    assert row == ("sqlite", 3)


def test_persistence_status_payload_omits_database_path_by_default(tmp_path):
    db_path = str(tmp_path / "driftguard.db")

    init_db(db_path)
    payload = persistence_status_payload(get_persistence_status(db_path))

    assert payload["backend"] == "sqlite"
    assert "database_path" not in payload


def test_resolve_db_path_prefers_postgres_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.com/driftguard")
    monkeypatch.delenv("AUDIT_DB_PATH", raising=False)

    assert resolve_db_path(None) == "postgresql://user:pass@db.example.com/driftguard"


def test_get_persistence_status_reports_postgres_unreachable_as_not_existing():
    locator = "postgresql://user:pass@db.example.com/driftguard"

    with patch("services.persistence.connect_sqlite", side_effect=RuntimeError("db unreachable")):
        status = get_persistence_status(locator)

    assert status.backend == "postgresql"
    assert status.database_exists is False
    assert status.production_target == "postgresql"


def test_migrate_database_records_bootstrap_migration(tmp_path):
    db_path = str(tmp_path / "migration.db")

    result = migrate_database(db_path)
    applied = list_applied_migrations(db_path)

    _all_versions = [
        "0001_bootstrap_relational_schema",
        "0002_add_pull_request_audits_fused_confidence",
        "0003_add_onboarding_approval_columns",
        "0004_add_machine_principals",
        "0005_add_session_flash",
        "0006_add_audit_feedback_and_triage_tables",
        "0007_add_high_risk_proposal_tables",
        "0008_ensure_ai_system_registry_schema",
        "0009_ensure_export_jobs_snapshot_columns",
    ]
    assert result.backend == "sqlite"
    assert result.applied_versions == _all_versions
    assert result.pending_versions == []
    assert [item.version for item in applied] == _all_versions


def test_migrate_database_repairs_missing_ai_systems_table_for_legacy_db(tmp_path):
    db_path = str(tmp_path / "legacy-ai-systems.db")

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE ai_systems")
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", ("0008_ensure_ai_system_registry_schema",))

    result = migrate_database(db_path)

    assert "0008_ensure_ai_system_registry_schema" in result.applied_versions

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ai_systems'"
        ).fetchone()

    assert row == ("ai_systems",)


def test_migrate_database_repairs_missing_export_job_snapshot_columns_for_legacy_db(tmp_path):
    db_path = str(tmp_path / "legacy-export-jobs.db")

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE export_jobs")
        conn.execute(
            """
            CREATE TABLE export_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full TEXT NOT NULL,
                from_ts REAL NOT NULL,
                to_ts REAL NOT NULL,
                export_mode TEXT NOT NULL,
                include_artifact_content INTEGER NOT NULL,
                export_version TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL,
                last_error TEXT,
                download_token TEXT,
                result_size_bytes INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL
            )
            """
        )
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", ("0009_ensure_export_jobs_snapshot_columns",))

    result = migrate_database(db_path)

    assert "0009_ensure_export_jobs_snapshot_columns" in result.applied_versions

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(export_jobs)").fetchall()
        }

    assert "ai_system_provenance_label" in columns
    assert "ai_system_review_detail" in columns
    assert "ai_system_risk_level" in columns
    assert "ai_system_eu_ai_act_domain" in columns
    assert "ai_system_purpose_summary" in columns
    assert "result_sha256" in columns
    assert "result_blob" in columns


def test_db_migrate_rejects_sqlite_target_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.com/driftguard")
    get_settings.cache_clear()

    with patch.object(sys, "argv", ["db_migrate.py", "--db", "sqlite:///./unsafe.db"]), patch(
        "scripts.db_migrate.migrate_database"
    ) as migrate_database_mock:
        with pytest.raises(RuntimeError) as exc_info:
            db_migrate_script.main()

    assert "cannot target SQLite persistence" in str(exc_info.value)
    migrate_database_mock.assert_not_called()


def test_db_migrate_allows_postgres_target_in_production(monkeypatch):
    @dataclass
    class _MigrationRecord:
        version: str
        description: str
        applied_at: float

    @dataclass
    class _MigrationResult:
        backend: str
        database_locator: str
        applied_versions: list[str]
        pending_versions: list[str]

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.com/driftguard")
    get_settings.cache_clear()

    with patch.object(sys, "argv", ["db_migrate.py"]), patch(
        "scripts.db_migrate.migrate_database",
        return_value=_MigrationResult(
            backend="postgresql",
            database_locator="postgresql://user:pass@db.example.com/driftguard",
            applied_versions=["0001_bootstrap_relational_schema"],
            pending_versions=[],
        ),
    ) as migrate_database_mock, patch(
        "scripts.db_migrate.list_applied_migrations",
        return_value=[_MigrationRecord("0001_bootstrap_relational_schema", "bootstrap", 123.0)],
    ):
        exit_code = db_migrate_script.main()

    assert exit_code == 0
    migrate_database_mock.assert_called_once_with("postgresql://user:pass@db.example.com/driftguard")


def test_postgres_connection_translates_last_insert_lookup():
    fake_connection = _FakePsycopgConnection(
        [
            _FakeCursor(rows=[(1,)], description=[_FakeDescription("exists")]),
            _FakeCursor(rows=[(41,)], description=[_FakeDescription("id")]),
            _FakeCursor(rows=[(41, "Ada")], description=[_FakeDescription("id"), _FakeDescription("display_name")]),
        ]
    )

    with patch("services.persistence.psycopg", _FakePsycopgModule(fake_connection)):
        with PostgresConnection("postgresql://user:pass@db.example.com/driftguard") as conn:
            insert_result = conn.execute("INSERT INTO users (display_name) VALUES (?)", ("Ada",))
            lookup_result = conn.execute("SELECT * FROM users WHERE id = last_insert_rowid()")

    assert insert_result.lastrowid == 41
    assert fake_connection.executed[1][0].endswith("RETURNING id")
    assert fake_connection.executed[1][1] == ("Ada",)
    assert fake_connection.executed[2][1] == (41,)
    assert "%s" in fake_connection.executed[2][0]
    rows = lookup_result.fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == 41
    assert rows[0]["display_name"] == "Ada"


def test_postgres_connection_does_not_append_returning_id_for_tables_without_id():
    fake_connection = _FakePsycopgConnection(
        [
            _FakeCursor(rows=[], description=[_FakeDescription("exists")]),
            _FakeCursor(rows=[], description=None),
        ]
    )

    with patch("services.persistence.psycopg", _FakePsycopgModule(fake_connection)):
        with PostgresConnection("postgresql://user:pass@db.example.com/driftguard") as conn:
            result = conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                ("0001", "bootstrap", 123.0),
            )

    assert result.lastrowid is None
    assert fake_connection.executed[1][1] == ("0001", "bootstrap", 123.0)
    assert "RETURNING id" not in fake_connection.executed[1][0]


class _FakeDescription:
    def __init__(self, name: str):
        self.name = name


class _FakeCursor:
    def __init__(self, *, rows, description):
        self._rows = list(rows)
        self.description = description

    def execute(self, sql, params):
        self._executed = (sql, params)

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePsycopgConnection:
    def __init__(self, cursors):
        self._cursors = list(cursors)
        self.executed = []

    def cursor(self):
        cursor = self._cursors.pop(0)
        original_execute = cursor.execute

        def record(sql, params):
            self.executed.append((sql, params))
            original_execute(sql, params)

        cursor.execute = record
        return cursor

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class _FakePsycopgModule:
    def __init__(self, connection):
        self._connection = connection

    def connect(self, _dsn):
        return self._connection
