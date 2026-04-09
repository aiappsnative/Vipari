from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from .persistence import connect_sqlite


@dataclass(frozen=True)
class RepoPostureSnapshotRecord:
    id: int
    snapshot_key: str
    repo_full: str
    commit_sha: str | None
    pr_number: int | None
    author: str | None
    created_at: float
    snapshot_type: str
    baseline_reference: str | None
    default_branch: str | None
    source_ref: str | None
    source_url: str | None
    attribute_vector: dict[str, float]
    artifact_coverage: dict[str, object]
    artifact_state: dict[str, dict[str, object]]
    change_summary: dict[str, object]
    change_breakdown: dict[str, object]
    drift_summary: dict[str, object]
    risk_summary: dict[str, object]
    change_labels: list[str]
    baseline_authority: dict[str, object]
    input_summary: dict[str, object]
    distance_from_baseline: float
    distance_from_previous: float
    materializer_version: int
    updated_at: float


def _connect(db_path: str) -> sqlite3.Connection:
    return connect_sqlite(db_path, foreign_keys=True)


def init_repo_journey_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_posture_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_key TEXT NOT NULL UNIQUE,
                repo_full TEXT NOT NULL,
                commit_sha TEXT,
                pr_number INTEGER,
                author TEXT,
                created_at REAL NOT NULL,
                snapshot_type TEXT NOT NULL,
                baseline_reference TEXT,
                default_branch TEXT,
                source_ref TEXT,
                source_url TEXT,
                attribute_vector_json TEXT NOT NULL,
                artifact_coverage_json TEXT NOT NULL,
                artifact_state_json TEXT NOT NULL,
                change_summary_json TEXT NOT NULL,
                change_breakdown_json TEXT NOT NULL,
                drift_summary_json TEXT NOT NULL,
                risk_summary_json TEXT NOT NULL,
                change_labels_json TEXT NOT NULL,
                baseline_authority_json TEXT NOT NULL,
                input_summary_json TEXT NOT NULL,
                distance_from_baseline REAL NOT NULL DEFAULT 0,
                distance_from_previous REAL NOT NULL DEFAULT 0,
                materializer_version INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_repo_posture_snapshots_repo_created ON repo_posture_snapshots(repo_full, created_at, id)"
        )


def upsert_repo_posture_snapshot(
    db_path: str,
    *,
    snapshot_key: str,
    repo_full: str,
    commit_sha: str | None,
    pr_number: int | None,
    author: str | None,
    created_at: float,
    snapshot_type: str,
    baseline_reference: str | None,
    default_branch: str | None,
    source_ref: str | None,
    source_url: str | None,
    attribute_vector: dict[str, float],
    artifact_coverage: dict[str, object],
    artifact_state: dict[str, dict[str, object]],
    change_summary: dict[str, object],
    change_breakdown: dict[str, object],
    drift_summary: dict[str, object],
    risk_summary: dict[str, object],
    change_labels: list[str],
    baseline_authority: dict[str, object],
    input_summary: dict[str, object],
    distance_from_baseline: float,
    distance_from_previous: float,
    materializer_version: int,
) -> RepoPostureSnapshotRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repo_posture_snapshots (
                snapshot_key, repo_full, commit_sha, pr_number, author, created_at, snapshot_type,
                baseline_reference, default_branch, source_ref, source_url,
                attribute_vector_json, artifact_coverage_json, artifact_state_json,
                change_summary_json, change_breakdown_json, drift_summary_json, risk_summary_json,
                change_labels_json, baseline_authority_json, input_summary_json,
                distance_from_baseline, distance_from_previous, materializer_version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_key) DO UPDATE SET
                repo_full = excluded.repo_full,
                commit_sha = excluded.commit_sha,
                pr_number = excluded.pr_number,
                author = excluded.author,
                created_at = excluded.created_at,
                snapshot_type = excluded.snapshot_type,
                baseline_reference = excluded.baseline_reference,
                default_branch = excluded.default_branch,
                source_ref = excluded.source_ref,
                source_url = excluded.source_url,
                attribute_vector_json = excluded.attribute_vector_json,
                artifact_coverage_json = excluded.artifact_coverage_json,
                artifact_state_json = excluded.artifact_state_json,
                change_summary_json = excluded.change_summary_json,
                change_breakdown_json = excluded.change_breakdown_json,
                drift_summary_json = excluded.drift_summary_json,
                risk_summary_json = excluded.risk_summary_json,
                change_labels_json = excluded.change_labels_json,
                baseline_authority_json = excluded.baseline_authority_json,
                input_summary_json = excluded.input_summary_json,
                distance_from_baseline = excluded.distance_from_baseline,
                distance_from_previous = excluded.distance_from_previous,
                materializer_version = excluded.materializer_version,
                updated_at = excluded.updated_at
            """,
            (
                snapshot_key,
                repo_full,
                commit_sha,
                pr_number,
                author,
                created_at,
                snapshot_type,
                baseline_reference,
                default_branch,
                source_ref,
                source_url,
                json.dumps(attribute_vector),
                json.dumps(artifact_coverage),
                json.dumps(artifact_state),
                json.dumps(change_summary),
                json.dumps(change_breakdown),
                json.dumps(drift_summary),
                json.dumps(risk_summary),
                json.dumps(change_labels),
                json.dumps(baseline_authority),
                json.dumps(input_summary),
                distance_from_baseline,
                distance_from_previous,
                materializer_version,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM repo_posture_snapshots WHERE snapshot_key = ?", (snapshot_key,)).fetchone()
    if row is None:
        raise RuntimeError("Failed to persist repo posture snapshot.")
    return _row_to_repo_posture_snapshot(row)


def delete_repo_posture_snapshots_not_in_keys(db_path: str, repo_full: str, snapshot_keys: set[str]) -> None:
    with _connect(db_path) as conn:
        if snapshot_keys:
            placeholders = ", ".join("?" for _ in snapshot_keys)
            conn.execute(
                f"DELETE FROM repo_posture_snapshots WHERE repo_full = ? AND snapshot_key NOT IN ({placeholders})",
                (repo_full, *sorted(snapshot_keys)),
            )
            return
        conn.execute("DELETE FROM repo_posture_snapshots WHERE repo_full = ?", (repo_full,))


def list_repo_posture_snapshots_for_repo(db_path: str, repo_full: str) -> list[RepoPostureSnapshotRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM repo_posture_snapshots WHERE repo_full = ? ORDER BY created_at ASC, id ASC",
            (repo_full,),
        ).fetchall()
    return [_row_to_repo_posture_snapshot(row) for row in rows]


def get_repo_posture_snapshot(db_path: str, snapshot_id: int) -> RepoPostureSnapshotRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM repo_posture_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return _row_to_repo_posture_snapshot(row) if row is not None else None


def _row_to_repo_posture_snapshot(row: sqlite3.Row) -> RepoPostureSnapshotRecord:
    return RepoPostureSnapshotRecord(
        id=row["id"],
        snapshot_key=row["snapshot_key"],
        repo_full=row["repo_full"],
        commit_sha=row["commit_sha"],
        pr_number=row["pr_number"],
        author=row["author"],
        created_at=float(row["created_at"]),
        snapshot_type=row["snapshot_type"],
        baseline_reference=row["baseline_reference"],
        default_branch=row["default_branch"],
        source_ref=row["source_ref"],
        source_url=row["source_url"],
        attribute_vector=json.loads(row["attribute_vector_json"]),
        artifact_coverage=json.loads(row["artifact_coverage_json"]),
        artifact_state=json.loads(row["artifact_state_json"]),
        change_summary=json.loads(row["change_summary_json"]),
        change_breakdown=json.loads(row["change_breakdown_json"]),
        drift_summary=json.loads(row["drift_summary_json"]),
        risk_summary=json.loads(row["risk_summary_json"]),
        change_labels=json.loads(row["change_labels_json"]),
        baseline_authority=json.loads(row["baseline_authority_json"]),
        input_summary=json.loads(row["input_summary_json"]),
        distance_from_baseline=float(row["distance_from_baseline"]),
        distance_from_previous=float(row["distance_from_previous"]),
        materializer_version=int(row["materializer_version"]),
        updated_at=float(row["updated_at"]),
    )