from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AuditJob:
    id: int
    repo_full: str
    pr_number: int
    installation_id: int
    head_sha: str
    diff_text: str
    status: str
    attempt_count: int
    next_attempt_at: float
    last_error: str | None = None
    comment_body: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


def _connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                installation_id INTEGER NOT NULL,
                head_sha TEXT NOT NULL,
                diff_text TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL,
                last_error TEXT,
                comment_body TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(repo_full, pr_number, head_sha)
            )
            """
        )
    from .audit_records import init_audit_record_db
    from .onboarding_records import init_onboarding_record_db

    init_audit_record_db(db_path)
    init_onboarding_record_db(db_path)


def _row_to_job(row: sqlite3.Row) -> AuditJob:
    return AuditJob(
        id=row["id"],
        repo_full=row["repo_full"],
        pr_number=row["pr_number"],
        installation_id=row["installation_id"],
        head_sha=row["head_sha"],
        diff_text=row["diff_text"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        next_attempt_at=row["next_attempt_at"],
        last_error=row["last_error"],
        comment_body=row["comment_body"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_audit_job(
    db_path: str,
    *,
    repo_full: str,
    pr_number: int,
    installation_id: int,
    head_sha: str,
    diff_text: str,
) -> AuditJob:
    now = time.time()
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM audit_jobs WHERE repo_full = ? AND pr_number = ? AND head_sha = ?",
            (repo_full, pr_number, head_sha),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO audit_jobs (
                    repo_full, pr_number, installation_id, head_sha, diff_text,
                    status, attempt_count, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)
                """,
                (repo_full, pr_number, installation_id, head_sha, diff_text, now, now, now),
            )
        elif existing["status"] == "failed":
            conn.execute(
                """
                UPDATE audit_jobs
                SET installation_id = ?,
                    diff_text = ?,
                    status = 'queued',
                    attempt_count = 0,
                    next_attempt_at = ?,
                    last_error = NULL,
                    comment_body = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (installation_id, diff_text, now, now, existing["id"]),
            )

        row = conn.execute(
            "SELECT * FROM audit_jobs WHERE repo_full = ? AND pr_number = ? AND head_sha = ?",
            (repo_full, pr_number, head_sha),
        ).fetchone()
    if row is None:
        raise RuntimeError("Failed to create or load audit job.")
    return _row_to_job(row)


def claim_next_job(db_path: str, now: float | None = None) -> Optional[AuditJob]:
    current_time = now or time.time()
    with _connect(db_path) as conn:
        claimed = conn.execute(
            """
            UPDATE audit_jobs
            SET status = 'processing',
                attempt_count = attempt_count + 1,
                updated_at = ?
            WHERE id = (
                SELECT id
                FROM audit_jobs
                WHERE status IN ('queued', 'retry_wait')
                  AND next_attempt_at <= ?
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            )
            RETURNING *
            """,
            (current_time, current_time),
        ).fetchone()
    return _row_to_job(claimed) if claimed is not None else None


def mark_job_retry(db_path: str, job_id: int, *, error_message: str, retry_at: float) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE audit_jobs
            SET status = 'retry_wait',
                next_attempt_at = ?,
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (retry_at, error_message, time.time(), job_id),
        )


def mark_job_completed(db_path: str, job_id: int, *, comment_body: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE audit_jobs
            SET status = 'completed',
                comment_body = ?,
                last_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (comment_body, time.time(), job_id),
        )


def mark_job_fallback_posted(db_path: str, job_id: int, *, comment_body: str, error_message: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE audit_jobs
            SET status = 'fallback_posted',
                comment_body = ?,
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (comment_body, error_message, time.time(), job_id),
        )


def mark_job_failed(db_path: str, job_id: int, *, error_message: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE audit_jobs
            SET status = 'failed',
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (error_message, time.time(), job_id),
        )


def get_job(db_path: str, job_id: int) -> Optional[AuditJob]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM audit_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row is not None else None
