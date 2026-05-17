from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .persistence import connect_sqlite


@dataclass(frozen=True)
class ActivityEventRecord:
    id: int
    occurred_at: float
    source: str
    event_type: str
    workspace_id: int | None
    actor_user_id: int | None
    actor_label: str | None
    repo_full: str | None
    subject_type: str
    subject_id: str
    details_json: str
    search_text: str


def _row_to_activity_event(row: Any) -> ActivityEventRecord:
    return ActivityEventRecord(
        id=int(row["id"]),
        occurred_at=float(row["occurred_at"]),
        source=str(row["source"]),
        event_type=str(row["event_type"]),
        workspace_id=(int(row["workspace_id"]) if row["workspace_id"] is not None else None),
        actor_user_id=(int(row["actor_user_id"]) if row["actor_user_id"] is not None else None),
        actor_label=(str(row["actor_label"]) if row["actor_label"] else None),
        repo_full=(str(row["repo_full"]) if row["repo_full"] else None),
        subject_type=str(row["subject_type"]),
        subject_id=str(row["subject_id"]),
        details_json=str(row["details_json"] or "{}"),
        search_text=str(row["search_text"] or ""),
    )


def _detail_text(details: dict[str, Any] | None) -> str:
    if not details:
        return ""
    segments: list[str] = []
    for key in sorted(details):
        value = details[key]
        if value is None:
            continue
        if isinstance(value, dict):
            nested = ", ".join(f"{nested_key}={value[nested_key]}" for nested_key in sorted(value))
            segments.append(f"{key}:{nested}")
            continue
        if isinstance(value, (list, tuple, set)):
            segments.append(f"{key}:{', '.join(str(item) for item in value)}")
            continue
        segments.append(f"{key}={value}")
    return " | ".join(segments)


def _search_text(
    *,
    source: str,
    event_type: str,
    workspace_id: int | None,
    actor_label: str | None,
    repo_full: str | None,
    subject_type: str,
    subject_id: str,
    details: dict[str, Any] | None,
) -> str:
    return " ".join(
        filter(
            None,
            [
                source,
                event_type,
                str(workspace_id) if workspace_id is not None else "global",
                actor_label or "",
                repo_full or "",
                subject_type,
                subject_id,
                _detail_text(details),
            ],
        )
    ).lower()


def create_activity_event(
    db_path: str,
    *,
    occurred_at: float,
    source: str,
    event_type: str,
    workspace_id: int | None,
    actor_user_id: int | None,
    actor_label: str | None,
    repo_full: str | None,
    subject_type: str,
    subject_id: str,
    details: dict[str, Any] | None = None,
) -> ActivityEventRecord:
    details_json = json.dumps(details or {}, sort_keys=True, default=str)
    search_text = _search_text(
        source=source,
        event_type=event_type,
        workspace_id=workspace_id,
        actor_label=actor_label,
        repo_full=repo_full,
        subject_type=subject_type,
        subject_id=subject_id,
        details=details,
    )
    with connect_sqlite(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO activity_events (
                occurred_at,
                source,
                event_type,
                workspace_id,
                actor_user_id,
                actor_label,
                repo_full,
                subject_type,
                subject_id,
                details_json,
                search_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                occurred_at,
                source,
                event_type,
                workspace_id,
                actor_user_id,
                actor_label,
                repo_full,
                subject_type,
                subject_id,
                details_json,
                search_text,
            ),
        )
        row = conn.execute("SELECT * FROM activity_events WHERE id = ?", (int(cursor.lastrowid or 0),)).fetchone()
    if row is None:
        raise RuntimeError("Failed to persist activity event.")
    return _row_to_activity_event(row)


def record_activity_event_if_configured(
    *,
    occurred_at: float,
    source: str,
    event_type: str,
    workspace_id: int | None,
    actor_user_id: int | None,
    actor_label: str | None,
    repo_full: str | None,
    subject_type: str,
    subject_id: str,
    details: dict[str, Any] | None = None,
) -> ActivityEventRecord | None:
    from config import get_settings

    settings = get_settings()
    if not settings.has_activity_database_config:
        return None
    try:
        return create_activity_event(
            settings.resolved_activity_db_path,
            occurred_at=occurred_at,
            source=source,
            event_type=event_type,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            repo_full=repo_full,
            subject_type=subject_type,
            subject_id=subject_id,
            details=details,
        )
    except Exception:
        return None


def list_recent_activity_events(db_path: str, *, limit: int = 100) -> list[ActivityEventRecord]:
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM activity_events ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (max(int(limit), 1),),
        ).fetchall()
    return [_row_to_activity_event(row) for row in rows]