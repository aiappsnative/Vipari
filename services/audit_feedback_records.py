"""Persistence layer for audit feedback events and triage events (issue #60).

Both tables are append-only.  Feedback records signal quality (helpful, noisy,
etc.) and are never mutated after creation.  Triage events record operational
state transitions (acknowledged, suppressed, escalated) without modifying the
underlying audit record.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from .persistence import connect_sqlite


# ---------------------------------------------------------------------------
# Valid vocabulary — validated at the API layer via Pydantic, enforced here
# ---------------------------------------------------------------------------

VALID_FEEDBACK_KINDS: frozenset[str] = frozenset(
    {
        "helpful",
        "noisy",
        "recommendation_followed",
        "recommendation_ignored",
        "likely_low_confidence",
        "agent_suggested_deprioritize",
    }
)

VALID_TRIAGE_STATES: frozenset[str] = frozenset(
    {
        "acknowledged",
        "suppressed_low_value",
        "escalated",
    }
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditFeedbackEvent:
    id: int
    audit_id: int
    workspace_id: int
    source: str
    kind: str
    comment: str | None
    metadata: dict[str, str]
    created_at: float


@dataclass(frozen=True)
class AuditTriageEvent:
    id: int
    audit_id: int
    workspace_id: int
    state: str
    reason: str | None
    created_at: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _connect(db_path: str) -> sqlite3.Connection:
    return connect_sqlite(db_path, foreign_keys=True)


def _row_to_feedback(row: sqlite3.Row) -> AuditFeedbackEvent:
    metadata_raw = row["metadata_json"]
    try:
        metadata = json.loads(metadata_raw) if metadata_raw else {}
    except (ValueError, TypeError):
        metadata = {}
    return AuditFeedbackEvent(
        id=row["id"],
        audit_id=row["audit_id"],
        workspace_id=row["workspace_id"],
        source=row["source"],
        kind=row["kind"],
        comment=row["comment"],
        metadata=metadata,
        created_at=row["created_at"],
    )


def _row_to_triage(row: sqlite3.Row) -> AuditTriageEvent:
    return AuditTriageEvent(
        id=row["id"],
        audit_id=row["audit_id"],
        workspace_id=row["workspace_id"],
        state=row["state"],
        reason=row["reason"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


def init_audit_feedback_db(db_path: str) -> None:
    """Create feedback and triage tables if they do not already exist."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_feedback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL,
                workspace_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                comment TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(audit_id) REFERENCES pull_request_audits(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_audit_id ON audit_feedback_events(audit_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_workspace_id ON audit_feedback_events(workspace_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_triage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL,
                workspace_id INTEGER NOT NULL,
                state TEXT NOT NULL,
                reason TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY(audit_id) REFERENCES pull_request_audits(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_triage_audit_id ON audit_triage_events(audit_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_triage_workspace_id ON audit_triage_events(workspace_id)"
        )


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def add_audit_feedback(
    db_path: str,
    *,
    audit_id: int,
    workspace_id: int,
    source: str,
    kind: str,
    comment: str | None = None,
    metadata: dict[str, str] | None = None,
) -> AuditFeedbackEvent:
    """Append a feedback event.  Caller must validate *kind* before calling."""
    now = time.time()
    metadata_json = json.dumps(metadata or {})
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO audit_feedback_events
                (audit_id, workspace_id, source, kind, comment, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (audit_id, workspace_id, source, kind, comment, metadata_json, now),
        )
        row_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM audit_feedback_events WHERE id = ?", (row_id,)
        ).fetchone()
    return _row_to_feedback(row)


def add_audit_triage(
    db_path: str,
    *,
    audit_id: int,
    workspace_id: int,
    state: str,
    reason: str | None = None,
) -> AuditTriageEvent:
    """Append a triage event.  Caller must validate *state* before calling."""
    now = time.time()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO audit_triage_events
                (audit_id, workspace_id, state, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (audit_id, workspace_id, state, reason, now),
        )
        row_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM audit_triage_events WHERE id = ?", (row_id,)
        ).fetchone()
    return _row_to_triage(row)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def list_feedback_for_audit(db_path: str, audit_id: int) -> list[AuditFeedbackEvent]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_feedback_events WHERE audit_id = ? ORDER BY created_at ASC",
            (audit_id,),
        ).fetchall()
    return [_row_to_feedback(row) for row in rows]


def list_triage_for_audit(db_path: str, audit_id: int) -> list[AuditTriageEvent]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_triage_events WHERE audit_id = ? ORDER BY created_at ASC",
            (audit_id,),
        ).fetchall()
    return [_row_to_triage(row) for row in rows]


def get_latest_triage_for_audit(
    db_path: str, audit_id: int
) -> Optional[AuditTriageEvent]:
    """Return the most recent triage event for an audit, or None."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM audit_triage_events WHERE audit_id = ? ORDER BY created_at DESC LIMIT 1",
            (audit_id,),
        ).fetchone()
    return _row_to_triage(row) if row else None
