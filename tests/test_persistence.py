import os
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.audit_jobs import init_db
from services.persistence import PostgresConnection, get_persistence_status, resolve_db_path
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


def test_resolve_db_path_prefers_postgres_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.com/driftguard")
    monkeypatch.delenv("AUDIT_DB_PATH", raising=False)

    assert resolve_db_path(None) == "postgresql://user:pass@db.example.com/driftguard"


def test_migrate_database_records_bootstrap_migration(tmp_path):
    db_path = str(tmp_path / "migration.db")

    result = migrate_database(db_path)
    applied = list_applied_migrations(db_path)

    assert result.backend == "sqlite"
    assert result.applied_versions == ["0001_bootstrap_relational_schema"]
    assert result.pending_versions == []
    assert [item.version for item in applied] == ["0001_bootstrap_relational_schema"]


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