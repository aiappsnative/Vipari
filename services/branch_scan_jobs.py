from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from .persistence import connect_sqlite


@dataclass(frozen=True)
class BranchScanJob:
    id: int
    repo_full: str
    installation_id: int
    commit_sha: str
    branch_ref: str
    triggered_by: str
    status: str
    attempt_count: int
    next_attempt_at: float
    last_error: str | None
    created_at: float
    updated_at: float


def _connect(db_path: str) -> sqlite3.Connection:
    return connect_sqlite(db_path)


def init_branch_scan_job_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS branch_scan_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full TEXT NOT NULL,
                installation_id INTEGER NOT NULL,
                commit_sha TEXT NOT NULL,
                branch_ref TEXT NOT NULL,
                triggered_by TEXT NOT NULL DEFAULT 'push_webhook',
                status TEXT NOT NULL DEFAULT 'queued',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(repo_full, commit_sha)
            )
            """
        )


def _row_to_job(row: sqlite3.Row) -> BranchScanJob:
    return BranchScanJob(
        id=row["id"],
        repo_full=row["repo_full"],
        installation_id=row["installation_id"],
        commit_sha=row["commit_sha"],
        branch_ref=row["branch_ref"],
        triggered_by=row["triggered_by"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        next_attempt_at=row["next_attempt_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_branch_scan_job(
    db_path: str,
    *,
    repo_full: str,
    installation_id: int,
    commit_sha: str,
    branch_ref: str,
    triggered_by: str = "push_webhook",
) -> BranchScanJob:
    init_branch_scan_job_db(db_path)
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO branch_scan_jobs (
                repo_full, installation_id, commit_sha, branch_ref, triggered_by,
                status, attempt_count, next_attempt_at, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, NULL, ?, ?)
            ON CONFLICT(repo_full, commit_sha) DO UPDATE SET
                installation_id = excluded.installation_id,
                branch_ref = excluded.branch_ref,
                triggered_by = excluded.triggered_by,
                updated_at = excluded.updated_at
            """,
            (repo_full, installation_id, commit_sha, branch_ref, triggered_by, now, now, now),
        )
        row = conn.execute(
            "SELECT * FROM branch_scan_jobs WHERE repo_full = ? AND commit_sha = ?",
            (repo_full, commit_sha),
        ).fetchone()
    if row is None:
        raise RuntimeError("Failed to create or load branch scan job.")
    return _row_to_job(row)


def claim_next_branch_scan_job(db_path: str, now: float | None = None) -> BranchScanJob | None:
    current_time = now or time.time()
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            UPDATE branch_scan_jobs
            SET status = 'processing',
                attempt_count = attempt_count + 1,
                updated_at = ?
            WHERE id = (
                SELECT id
                FROM branch_scan_jobs
                WHERE status IN ('queued', 'retry_wait')
                  AND next_attempt_at <= ?
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            )
            RETURNING *
            """,
            (current_time, current_time),
        ).fetchone()
    return _row_to_job(row) if row is not None else None


def mark_branch_scan_job_completed(db_path: str, job_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE branch_scan_jobs
            SET status = 'completed',
                last_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (time.time(), job_id),
        )


def mark_branch_scan_job_retry(db_path: str, job_id: int, *, error_message: str, retry_at: float) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE branch_scan_jobs
            SET status = 'retry_wait',
                next_attempt_at = ?,
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (retry_at, error_message, time.time(), job_id),
        )


def mark_branch_scan_job_failed(db_path: str, job_id: int, *, error_message: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE branch_scan_jobs
            SET status = 'failed',
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (error_message, time.time(), job_id),
        )


def get_branch_scan_job(db_path: str, job_id: int) -> BranchScanJob | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM branch_scan_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row is not None else None