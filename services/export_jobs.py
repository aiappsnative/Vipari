from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass

from .persistence import connect_sqlite


@dataclass(frozen=True)
class ExportJob:
    id: int
    repo_full: str
    from_ts: float
    to_ts: float
    workspace_id: int | None
    requested_by_user_id: int | None
    requested_by_github_login: str | None
    export_mode: str
    include_artifact_content: bool
    export_version: str
    status: str
    attempt_count: int
    next_attempt_at: float
    last_error: str | None = None
    download_token: str | None = None
    result_size_bytes: int | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float | None = None


def _connect(db_path: str) -> sqlite3.Connection:
    return connect_sqlite(db_path)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _rebuild_export_jobs_table(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "export_jobs")
    has_workspace_id = "workspace_id" in columns
    has_requested_by_user_id = "requested_by_user_id" in columns
    has_requested_by_github_login = "requested_by_github_login" in columns
    conn.execute(
        """
        CREATE TABLE export_jobs_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_full TEXT NOT NULL,
            from_ts REAL NOT NULL,
            to_ts REAL NOT NULL,
            workspace_id INTEGER,
            requested_by_user_id INTEGER,
            requested_by_github_login TEXT,
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
    conn.execute(
        f"""
        INSERT INTO export_jobs_v2 (
            id,
            repo_full,
            from_ts,
            to_ts,
            workspace_id,
            requested_by_user_id,
            requested_by_github_login,
            export_mode,
            include_artifact_content,
            export_version,
            status,
            attempt_count,
            next_attempt_at,
            last_error,
            download_token,
            result_size_bytes,
            created_at,
            updated_at,
            completed_at
        )
        SELECT
            id,
            repo_full,
            from_ts,
            to_ts,
            {"workspace_id" if has_workspace_id else 'NULL'},
            {"requested_by_user_id" if has_requested_by_user_id else 'NULL'},
            {"requested_by_github_login" if has_requested_by_github_login else 'NULL'},
            export_mode,
            include_artifact_content,
            export_version,
            status,
            attempt_count,
            next_attempt_at,
            last_error,
            download_token,
            result_size_bytes,
            created_at,
            updated_at,
            completed_at
        FROM export_jobs
        """
    )
    conn.execute("DROP TABLE export_jobs")
    conn.execute("ALTER TABLE export_jobs_v2 RENAME TO export_jobs")


def init_export_job_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS export_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full TEXT NOT NULL,
                from_ts REAL NOT NULL,
                to_ts REAL NOT NULL,
                workspace_id INTEGER,
                requested_by_user_id INTEGER,
                requested_by_github_login TEXT,
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
        table_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'export_jobs'"
        ).fetchone()
        table_sql = table_sql_row[0] if table_sql_row else ""
        columns = _table_columns(conn, "export_jobs")
        if "workspace_id" not in columns:
            conn.execute("ALTER TABLE export_jobs ADD COLUMN workspace_id INTEGER")
        if "requested_by_user_id" not in columns:
            conn.execute("ALTER TABLE export_jobs ADD COLUMN requested_by_user_id INTEGER")
        if "requested_by_github_login" not in columns:
            conn.execute("ALTER TABLE export_jobs ADD COLUMN requested_by_github_login TEXT")
        if "UNIQUE(repo_full, from_ts, to_ts, export_mode, include_artifact_content)" in table_sql:
            _rebuild_export_jobs_table(conn)


def _row_to_job(row: sqlite3.Row) -> ExportJob:
    return ExportJob(
        id=row["id"],
        repo_full=row["repo_full"],
        from_ts=row["from_ts"],
        to_ts=row["to_ts"],
        workspace_id=row["workspace_id"],
        requested_by_user_id=row["requested_by_user_id"],
        requested_by_github_login=row["requested_by_github_login"],
        export_mode=row["export_mode"],
        include_artifact_content=bool(row["include_artifact_content"]),
        export_version=row["export_version"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        next_attempt_at=row["next_attempt_at"],
        last_error=row["last_error"],
        download_token=row["download_token"],
        result_size_bytes=row["result_size_bytes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def create_export_job(
    db_path: str,
    repo_full: str,
    from_ts: float,
    to_ts: float,
    export_mode: str,
    include_artifact_content: bool,
    workspace_id: int | None = None,
    requested_by_user_id: int | None = None,
    requested_by_github_login: str | None = None,
    export_version: str = "1",
) -> ExportJob:
    now = time.time()
    download_token = str(uuid.uuid4())
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO export_jobs (
                repo_full, from_ts, to_ts, workspace_id, requested_by_user_id, requested_by_github_login,
                export_mode, include_artifact_content, export_version,
                status, attempt_count, next_attempt_at, download_token, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
            """,
            (
                repo_full,
                from_ts,
                to_ts,
                workspace_id,
                requested_by_user_id,
                requested_by_github_login,
                export_mode,
                int(include_artifact_content),
                export_version,
                now,
                download_token,
                now,
                now,
            ),
        )
        job_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM export_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row)


def get_export_job(db_path: str, job_id: int) -> ExportJob | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM export_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def claim_export_job(db_path: str) -> ExportJob | None:
    now = time.time()
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM export_jobs
            WHERE status = 'queued' AND next_attempt_at <= ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if row:
            job_id = row["id"]
            conn.execute(
                """
                UPDATE export_jobs
                SET status = 'in_progress', attempt_count = attempt_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, job_id),
            )
            row = conn.execute("SELECT * FROM export_jobs WHERE id = ?", (job_id,)).fetchone()
            return _row_to_job(row)
    return None


def update_export_job_status(
    db_path: str,
    job_id: int,
    status: str,
    last_error: str | None = None,
    result_size_bytes: int | None = None,
) -> None:
    now = time.time()
    with _connect(db_path) as conn:
        if status == "completed":
            conn.execute(
                """
                UPDATE export_jobs
                SET status = ?, last_error = ?, result_size_bytes = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, last_error, result_size_bytes, now, now, job_id),
            )
        else:
            next_attempt_at = now + (2 ** (get_export_job(db_path, job_id).attempt_count)) * 60  # exponential backoff
            conn.execute(
                """
                UPDATE export_jobs
                SET status = ?, last_error = ?, next_attempt_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, last_error, next_attempt_at, now, job_id),
            )


def list_export_jobs_for_repo(db_path: str, repo_full: str, limit: int = 10) -> list[ExportJob]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM export_jobs WHERE repo_full = ? ORDER BY created_at DESC LIMIT ?",
            (repo_full, limit),
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def list_export_jobs_for_requester(
    db_path: str,
    repo_full: str,
    workspace_id: int,
    requested_by_user_id: int,
    limit: int = 10,
) -> list[ExportJob]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM export_jobs
            WHERE repo_full = ?
              AND workspace_id = ?
              AND requested_by_user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (repo_full, workspace_id, requested_by_user_id, limit),
        ).fetchall()
    return [_row_to_job(row) for row in rows]