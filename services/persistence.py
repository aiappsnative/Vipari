from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError:  # pragma: no cover - exercised only when PostgreSQL support isn't installed
    psycopg = None


CURRENT_PERSISTENCE_SCHEMA_VERSION = 3
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 5000
DEFAULT_PRODUCTION_TARGET = "postgresql"
OPERATIONAL_TABLES = (
    "audit_jobs",
    "user_sessions",
)
DURABLE_TABLES = (
    "schema_migrations",
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
    "repo_posture_snapshots",
)


_SQLITE_INIT_LOCK = threading.RLock()
_PREPARED_DB_DIRECTORIES: set[str] = set()
_WAL_CONFIGURED_DB_PATHS: set[str] = set()

_IDENTITY_PRIMARY_KEY_PATTERN = re.compile(r"\b(\w+)\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE)
_INTEGER_PATTERN = re.compile(r"\bINTEGER\b", re.IGNORECASE)
_BLOB_PATTERN = re.compile(r"\bBLOB\b", re.IGNORECASE)
_SQLITE_MASTER_PATTERN = re.compile(
    r"^\s*SELECT\s+sql\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*'([^']+)'\s*$",
    re.IGNORECASE,
)
_PRAGMA_TABLE_INFO_PATTERN = re.compile(r"^\s*PRAGMA\s+table_info\(([^)]+)\)\s*$", re.IGNORECASE)
_PRAGMA_FOREIGN_KEY_LIST_PATTERN = re.compile(r"^\s*PRAGMA\s+foreign_key_list\(([^)]+)\)\s*$", re.IGNORECASE)
_LAST_INSERT_ROWID_QUERY = re.compile(r"last_insert_rowid\s*\(\s*\)", re.IGNORECASE)
_INSERT_INTO_PATTERN = re.compile(
    r'^\s*INSERT\s+INTO\s+((?:"[^"]+"|\w+)(?:\.(?:"[^"]+"|\w+))?)',
    re.IGNORECASE,
)


def is_postgres_locator(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    return normalized.startswith("postgresql://") or normalized.startswith("postgres://")


def is_sqlite_locator(value: str | None) -> bool:
    if not value:
        return True
    return not is_postgres_locator(value)


def get_database_backend(value: str | None) -> str:
    return "postgresql" if is_postgres_locator(value) else "sqlite"


def sqlite_path_from_locator(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.strip()
    if normalized.lower().startswith("sqlite:///"):
        return normalized[10:]
    return normalized


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _normalize_table_name(raw_name: str) -> str:
    return raw_name.strip().strip('"').strip("'")


class DatabaseRow(Mapping[str, Any]):
    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._columns = list(columns)
        self._values = list(values)
        self._mapping = dict(zip(self._columns, self._values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping)

    def __len__(self) -> int:
        return len(self._mapping)


class DatabaseResult:
    def __init__(self, columns: Sequence[str], rows: Sequence[Sequence[Any]], *, lastrowid: int | None = None):
        self._columns = list(columns)
        self._rows = [DatabaseRow(self._columns, row) for row in rows]
        self._index = 0
        self.lastrowid = lastrowid

    def fetchone(self) -> DatabaseRow | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[DatabaseRow]:
        if self._index >= len(self._rows):
            return []
        remaining = self._rows[self._index :]
        self._index = len(self._rows)
        return remaining


class PostgresConnection:
    def __init__(self, dsn: str):
        if psycopg is None:
            raise RuntimeError("PostgreSQL persistence requires psycopg to be installed.")
        self._connection = psycopg.connect(dsn)
        self._last_insert_rowid: int | None = None
        self.backend = "postgresql"

    def __enter__(self) -> PostgresConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()

    def close(self) -> None:
        self._connection.close()

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> DatabaseResult:
        normalized_sql = sql.strip()
        params = tuple(params or ())

        pragma_table_info = _PRAGMA_TABLE_INFO_PATTERN.match(normalized_sql)
        if pragma_table_info:
            return self._handle_table_info(_normalize_table_name(pragma_table_info.group(1)))

        pragma_foreign_key_list = _PRAGMA_FOREIGN_KEY_LIST_PATTERN.match(normalized_sql)
        if pragma_foreign_key_list:
            return self._handle_foreign_key_list(_normalize_table_name(pragma_foreign_key_list.group(1)))

        if normalized_sql.upper().startswith("PRAGMA "):
            return DatabaseResult([], [])

        sqlite_master_query = _SQLITE_MASTER_PATTERN.match(normalized_sql)
        if sqlite_master_query:
            return DatabaseResult(["sql"], [])

        if normalized_sql.upper() == "SELECT LAST_INSERT_ROWID()":
            return DatabaseResult(["last_insert_rowid()"], [(self._last_insert_rowid,)], lastrowid=self._last_insert_rowid)

        if "last_insert_rowid()" in normalized_sql.lower() and self._last_insert_rowid is not None:
            normalized_sql = _LAST_INSERT_ROWID_QUERY.sub("%s", normalized_sql)
            params = (*params, self._last_insert_rowid)

        translated_sql = self._translate_sql(normalized_sql)
        returning_insert_id = self._needs_insert_returning_id(normalized_sql)
        if returning_insert_id:
            translated_sql = translated_sql.rstrip().rstrip(";") + " RETURNING id"

        with self._connection.cursor() as cursor:
            cursor.execute(translated_sql, params)
            columns = [description.name for description in (cursor.description or [])]
            if cursor.description is None:
                return DatabaseResult([], [])

            rows = cursor.fetchall()
            if returning_insert_id:
                lastrowid = int(rows[0][0]) if rows else None
                self._last_insert_rowid = lastrowid
                return DatabaseResult([], [], lastrowid=lastrowid)

            lastrowid = None
            if normalized_sql.upper().startswith("INSERT INTO") and rows and "id" in columns:
                try:
                    lastrowid = int(rows[0][columns.index("id")])
                except (TypeError, ValueError):
                    lastrowid = None
                self._last_insert_rowid = lastrowid

            return DatabaseResult(columns, rows, lastrowid=lastrowid)

    def executemany(self, sql: str, params_seq: Sequence[Sequence[Any]]) -> None:
        translated_sql = self._translate_sql(sql)
        with self._connection.cursor() as cursor:
            for params in params_seq:
                cursor.execute(translated_sql, tuple(params))

    def _handle_table_info(self, table_name: str) -> DatabaseResult:
        columns = ["cid", "name", "type", "notnull", "dflt_value", "pk"]
        sql = """
            SELECT
                ordinal_position - 1 AS cid,
                column_name,
                data_type,
                CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                column_default,
                CASE WHEN position('nextval' in COALESCE(column_default, '')) > 0 THEN 1 ELSE 0 END AS pk
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = %s
            ORDER BY ordinal_position
        """
        with self._connection.cursor() as cursor:
            cursor.execute(sql, (table_name,))
            rows = cursor.fetchall()
        normalized_rows = []
        for row in rows:
            normalized_rows.append((row[0], row[1], row[2], row[3], row[4], row[5]))
        return DatabaseResult(columns, normalized_rows)

    def _handle_foreign_key_list(self, table_name: str) -> DatabaseResult:
        sql = """
            SELECT
                0 AS id,
                0 AS seq,
                ccu.table_name AS referenced_table,
                kcu.column_name AS from_column,
                ccu.column_name AS to_column,
                rc.update_rule,
                rc.delete_rule,
                'NONE' AS match_type
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            JOIN information_schema.referential_constraints rc
              ON rc.constraint_name = tc.constraint_name
             AND rc.constraint_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = current_schema()
              AND tc.table_name = %s
            ORDER BY kcu.ordinal_position
        """
        with self._connection.cursor() as cursor:
            cursor.execute(sql, (table_name,))
            rows = cursor.fetchall()
        return DatabaseResult(
            ["id", "seq", "table", "from", "to", "on_update", "on_delete", "match"],
            rows,
        )

    def _translate_sql(self, sql: str) -> str:
        translated = sql.replace("COLLATE NOCASE", "")
        translated = _IDENTITY_PRIMARY_KEY_PATTERN.sub(r"\1 BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", translated)
        translated = _INTEGER_PATTERN.sub("BIGINT", translated)
        translated = _BLOB_PATTERN.sub("BYTEA", translated)
        translated = translated.replace("?", "%s")
        return translated

    def _needs_insert_returning_id(self, sql: str) -> bool:
        stripped = sql.lstrip().upper()
        if not stripped.startswith("INSERT INTO") or "RETURNING" in stripped:
            return False

        table_name = self._insert_target_table(sql)
        if not table_name:
            return False
        return self._table_has_id_column(table_name)

    def _table_has_id_column(self, table_name: str) -> bool:
        sql = """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND column_name = 'id'
            LIMIT 1
        """
        with self._connection.cursor() as cursor:
            cursor.execute(sql, (table_name,))
            rows = cursor.fetchall()
        return bool(rows)

    @staticmethod
    def _insert_target_table(sql: str) -> str | None:
        match = _INSERT_INTO_PATTERN.match(sql)
        if not match:
            return None
        raw_table = match.group(1).split(".")[-1]
        return _normalize_table_name(raw_table)


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

    database_url = os.getenv("DATABASE_URL", "").strip()
    if is_postgres_locator(database_url):
        return database_url
    if is_sqlite_locator(database_url) and database_url:
        sqlite_path = sqlite_path_from_locator(database_url)
        if sqlite_path:
            return sqlite_path

    return os.getenv("AUDIT_DB_PATH", str(Path(__file__).resolve().parent.parent / "promptdrift.db"))


def connect_sqlite(db_path: str, *, foreign_keys: bool = False) -> sqlite3.Connection:
    resolved_locator = resolve_db_path(db_path)
    if is_postgres_locator(resolved_locator):
        return PostgresConnection(resolved_locator)  # type: ignore[return-value]

    sqlite_path = sqlite_path_from_locator(resolved_locator)
    normalized_db_path = str(Path(sqlite_path).resolve())
    with _SQLITE_INIT_LOCK:
        if normalized_db_path not in _PREPARED_DB_DIRECTORIES:
            Path(normalized_db_path).parent.mkdir(parents=True, exist_ok=True)
            _PREPARED_DB_DIRECTORIES.add(normalized_db_path)
    connection = sqlite3.connect(sqlite_path)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {DEFAULT_SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA synchronous = NORMAL")
    with _SQLITE_INIT_LOCK:
        if normalized_db_path not in _WAL_CONFIGURED_DB_PATHS:
            connection.execute("PRAGMA journal_mode = WAL")
            _WAL_CONFIGURED_DB_PATHS.add(normalized_db_path)
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_persistence_metadata(db_path: str) -> None:
    now = time.time()
    resolved_locator = resolve_db_path(db_path)
    backend = "postgresql" if is_postgres_locator(resolved_locator) else "sqlite"
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
                backend,
                CURRENT_PERSISTENCE_SCHEMA_VERSION,
                DEFAULT_PRODUCTION_TARGET,
                DEFAULT_SQLITE_BUSY_TIMEOUT_MS if backend == "sqlite" else 0,
                1 if backend == "sqlite" else 0,
                json.dumps(list(OPERATIONAL_TABLES)),
                json.dumps(list(DURABLE_TABLES)),
                created_at,
                now,
            ),
        )


def get_persistence_status(db_path: str) -> PersistenceStatus:
    resolved_path = resolve_db_path(db_path)
    backend = "postgresql" if is_postgres_locator(resolved_path) else "sqlite"

    if backend == "sqlite":
        database_exists = Path(resolved_path).exists()
    else:
        database_exists = True

    if not database_exists:
        return PersistenceStatus(
            backend=backend,
            database_path=resolved_path,
            database_exists=False,
            schema_version=CURRENT_PERSISTENCE_SCHEMA_VERSION,
            production_target=DEFAULT_PRODUCTION_TARGET,
            sqlite_busy_timeout_ms=DEFAULT_SQLITE_BUSY_TIMEOUT_MS if backend == "sqlite" else 0,
            sqlite_wal_enabled=backend == "sqlite",
            operational_tables=list(OPERATIONAL_TABLES),
            durable_tables=list(DURABLE_TABLES),
        )

    try:
        with connect_sqlite(resolved_path) as conn:
            row = conn.execute("SELECT * FROM persistence_metadata WHERE id = 1").fetchone()
    except Exception:
        return PersistenceStatus(
            backend=backend,
            database_path=resolved_path,
            database_exists=(backend == "postgresql"),
            schema_version=CURRENT_PERSISTENCE_SCHEMA_VERSION,
            production_target=DEFAULT_PRODUCTION_TARGET,
            sqlite_busy_timeout_ms=DEFAULT_SQLITE_BUSY_TIMEOUT_MS if backend == "sqlite" else 0,
            sqlite_wal_enabled=backend == "sqlite",
            operational_tables=list(OPERATIONAL_TABLES),
            durable_tables=list(DURABLE_TABLES),
        )

    if row is None:
        return PersistenceStatus(
            backend=backend,
            database_path=resolved_path,
            database_exists=True,
            schema_version=CURRENT_PERSISTENCE_SCHEMA_VERSION,
            production_target=DEFAULT_PRODUCTION_TARGET,
            sqlite_busy_timeout_ms=DEFAULT_SQLITE_BUSY_TIMEOUT_MS if backend == "sqlite" else 0,
            sqlite_wal_enabled=backend == "sqlite",
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