from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from .control_plane_records import EntitlementRecord, get_workspace_entitlement
from .entitlements import get_analysis_budget_limit, get_analysis_budget_window_seconds
from .persistence import connect_sqlite


DEFAULT_ADVANCED_ANALYSIS_WINDOW_SECONDS = 30 * 24 * 60 * 60
FEATURE_UNIT_FLOORS = {
    "scenario": 5,
    "hybrid": 2,
    "semantic_review": 5,
    "verifier": 5,
}


class AdvancedAnalysisBudgetExceededError(RuntimeError):
    pass


PUBLIC_BUDGET_EXHAUSTED_REASON = "advanced analysis budget exhausted for the active window"


@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int | None = None


@dataclass(frozen=True)
class AnalysisBudgetPolicy:
    unit_limit: int | None
    window_seconds: int = DEFAULT_ADVANCED_ANALYSIS_WINDOW_SECONDS

    @property
    def is_limited(self) -> bool:
        return self.unit_limit is not None


@dataclass(frozen=True)
class AnalysisBudgetReservation:
    allowed: bool
    reservation_key: str | None
    estimated_units: int
    used_units: int
    unit_limit: int | None
    window_start: float | None
    reason: str | None = None


def init_analysis_budget_db(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_analysis_budget_windows (
                workspace_id INTEGER NOT NULL,
                window_start REAL NOT NULL,
                window_end REAL NOT NULL,
                unit_limit INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (workspace_id, window_start)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_analysis_budget_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                window_start REAL NOT NULL,
                reservation_key TEXT NOT NULL UNIQUE,
                feature_key TEXT NOT NULL,
                audit_job_id INTEGER,
                audit_id INTEGER,
                units_reserved INTEGER NOT NULL DEFAULT 0,
                units_consumed INTEGER NOT NULL DEFAULT 0,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                provider TEXT,
                model TEXT,
                status TEXT NOT NULL DEFAULT 'reserved',
                note TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspace_analysis_budget_events_workspace_window ON workspace_analysis_budget_events(workspace_id, window_start)"
        )


def resolve_analysis_budget_policy(
    db_path: str,
    *,
    workspace_id: int,
    entitlement: EntitlementRecord | None = None,
) -> AnalysisBudgetPolicy:
    resolved_entitlement = entitlement or get_workspace_entitlement(db_path, workspace_id)
    if resolved_entitlement is None:
        return AnalysisBudgetPolicy(unit_limit=None)

    unit_limit = get_analysis_budget_limit(
        resolved_entitlement.plan_code,
        resolved_entitlement.feature_flags_json,
    )
    window_seconds = get_analysis_budget_window_seconds(resolved_entitlement.feature_flags_json)

    return AnalysisBudgetPolicy(unit_limit=unit_limit, window_seconds=window_seconds)


def estimate_feature_units(feature_key: str, *, request_count: int = 1) -> int:
    multiplier = max(1, int(request_count))
    return FEATURE_UNIT_FLOORS.get(feature_key, 1) * multiplier


def compute_token_units(usage: LlmUsage | None) -> int:
    if usage is None:
        return 0
    return math.ceil(max(0, usage.prompt_tokens) / 1000) + (2 * math.ceil(max(0, usage.completion_tokens) / 1000))


def compute_feature_units(feature_key: str, usage: LlmUsage | None, *, request_count: int = 1) -> int:
    return max(estimate_feature_units(feature_key, request_count=request_count), compute_token_units(usage))


def extract_llm_usage(response: object) -> LlmUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None

    def _read(value: object, key: str) -> int | None:
        if isinstance(value, dict):
            candidate = value.get(key)
        else:
            candidate = getattr(value, key, None)
        if candidate is None:
            return None
        try:
            return int(candidate)
        except (TypeError, ValueError):
            return None

    prompt_tokens = _read(usage, "prompt_tokens")
    completion_tokens = _read(usage, "completion_tokens")
    if completion_tokens is None:
        completion_tokens = _read(usage, "output_tokens")
    total_tokens = _read(usage, "total_tokens")
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    return LlmUsage(prompt_tokens=prompt_tokens or 0, completion_tokens=completion_tokens or 0, total_tokens=total_tokens)


def reserve_analysis_budget(
    db_path: str,
    *,
    workspace_id: int,
    feature_key: str,
    reservation_key: str,
    estimated_units: int,
    audit_job_id: int | None = None,
    audit_id: int | None = None,
    entitlement: EntitlementRecord | None = None,
    now: float | None = None,
) -> AnalysisBudgetReservation:
    policy = resolve_analysis_budget_policy(db_path, workspace_id=workspace_id, entitlement=entitlement)
    if not policy.is_limited:
        return AnalysisBudgetReservation(
            allowed=True,
            reservation_key=None,
            estimated_units=estimated_units,
            used_units=0,
            unit_limit=None,
            window_start=None,
        )

    init_analysis_budget_db(db_path)
    current_time = now or time.time()
    window_start = _window_start_for_time(current_time, policy.window_seconds)
    window_end = window_start + policy.window_seconds
    scoped_reservation_key = _window_scoped_reservation_key(reservation_key, window_start=window_start)

    with connect_sqlite(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM workspace_analysis_budget_events WHERE reservation_key = ?",
            (scoped_reservation_key,),
        ).fetchone()
        legacy_existing = None
        if existing is None:
            legacy_existing = conn.execute(
                "SELECT * FROM workspace_analysis_budget_events WHERE reservation_key = ?",
                (reservation_key,),
            ).fetchone()
        if legacy_existing is not None:
            if float(legacy_existing["window_start"] or 0.0) != window_start:
                archived_reservation_key = (
                    f"{reservation_key}:window:{int(float(legacy_existing['window_start'] or 0.0))}:archived:{legacy_existing['id']}"
                )
                conn.execute(
                    "UPDATE workspace_analysis_budget_events SET reservation_key = ? WHERE id = ?",
                    (archived_reservation_key, legacy_existing["id"]),
                )
            else:
                conn.execute(
                    "UPDATE workspace_analysis_budget_events SET reservation_key = ? WHERE id = ?",
                    (scoped_reservation_key, legacy_existing["id"]),
                )
                existing = conn.execute(
                    "SELECT * FROM workspace_analysis_budget_events WHERE reservation_key = ?",
                    (scoped_reservation_key,),
                ).fetchone()

        if existing is not None:
            if existing["status"] == "released":
                conn.execute(
                    """
                    UPDATE workspace_analysis_budget_events
                    SET status = 'reserved', units_reserved = ?, note = NULL, updated_at = ?
                    WHERE reservation_key = ?
                    """,
                    (estimated_units, current_time, scoped_reservation_key),
                )
            else:
                return AnalysisBudgetReservation(
                    allowed=True,
                    reservation_key=scoped_reservation_key,
                    estimated_units=int(existing["units_reserved"] or estimated_units),
                    used_units=_sum_reserved_or_consumed_units(conn, workspace_id=workspace_id, window_start=window_start),
                    unit_limit=policy.unit_limit,
                    window_start=window_start,
                )

        if existing is not None:
            if existing["status"] == "released":
                conn.execute(
                    """
                    UPDATE workspace_analysis_budget_events
                    SET status = 'reserved', units_reserved = ?, note = NULL, updated_at = ?
                    WHERE reservation_key = ?
                    """,
                    (estimated_units, current_time, scoped_reservation_key),
                )
            return AnalysisBudgetReservation(
                allowed=True,
                reservation_key=scoped_reservation_key,
                estimated_units=int(existing["units_reserved"] or estimated_units),
                used_units=_sum_reserved_or_consumed_units(conn, workspace_id=workspace_id, window_start=window_start),
                unit_limit=policy.unit_limit,
                window_start=window_start,
            )

        used_units = _sum_reserved_or_consumed_units(conn, workspace_id=workspace_id, window_start=window_start)
        if policy.unit_limit is not None and used_units + estimated_units > policy.unit_limit:
            return AnalysisBudgetReservation(
                allowed=False,
                reservation_key=None,
                estimated_units=estimated_units,
                used_units=used_units,
                unit_limit=policy.unit_limit,
                window_start=window_start,
                reason=PUBLIC_BUDGET_EXHAUSTED_REASON,
            )

        conn.execute(
            """
            INSERT INTO workspace_analysis_budget_windows (workspace_id, window_start, window_end, unit_limit, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, window_start) DO UPDATE SET
                window_end = excluded.window_end,
                unit_limit = excluded.unit_limit,
                updated_at = excluded.updated_at
            """,
            (workspace_id, window_start, window_end, policy.unit_limit, current_time, current_time),
        )
        conn.execute(
            """
            INSERT INTO workspace_analysis_budget_events (
                workspace_id, window_start, reservation_key, feature_key, audit_job_id, audit_id,
                units_reserved, units_consumed, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'reserved', ?, ?)
            """,
            (workspace_id, window_start, scoped_reservation_key, feature_key, audit_job_id, audit_id, estimated_units, current_time, current_time),
        )

    return AnalysisBudgetReservation(
        allowed=True,
        reservation_key=scoped_reservation_key,
        estimated_units=estimated_units,
        used_units=used_units,
        unit_limit=policy.unit_limit,
        window_start=window_start,
    )


def consume_analysis_budget(
    db_path: str,
    *,
    reservation_key: str | None,
    consumed_units: int,
    usage: LlmUsage | None = None,
    provider: str | None = None,
    model: str | None = None,
    note: str | None = None,
) -> None:
    if not reservation_key:
        return
    init_analysis_budget_db(db_path)
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            UPDATE workspace_analysis_budget_events
            SET units_consumed = ?,
                prompt_tokens = ?,
                completion_tokens = ?,
                provider = ?,
                model = ?,
                status = 'consumed',
                note = ?,
                updated_at = ?
            WHERE reservation_key = ?
            """,
            (
                consumed_units,
                usage.prompt_tokens if usage is not None else None,
                usage.completion_tokens if usage is not None else None,
                provider,
                model,
                note,
                time.time(),
                reservation_key,
            ),
        )


def release_analysis_budget(db_path: str, *, reservation_key: str | None, note: str | None = None) -> None:
    if not reservation_key:
        return
    init_analysis_budget_db(db_path)
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            UPDATE workspace_analysis_budget_events
            SET status = 'released', units_consumed = 0, note = ?, updated_at = ?
            WHERE reservation_key = ?
            """,
            (note, time.time(), reservation_key),
        )


def list_analysis_budget_events(db_path: str, *, workspace_id: int) -> list[dict[str, Any]]:
    init_analysis_budget_db(db_path)
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM workspace_analysis_budget_events WHERE workspace_id = ? ORDER BY created_at ASC, id ASC",
            (workspace_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _window_start_for_time(now: float, window_seconds: int) -> float:
    return float(int(now // window_seconds) * window_seconds)


def _window_scoped_reservation_key(reservation_key: str, *, window_start: float) -> str:
    return f"{reservation_key}:budget-window:{int(window_start)}"


def _sum_reserved_or_consumed_units(conn, *, workspace_id: int, window_start: float) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(
            CASE
                WHEN status = 'released' THEN 0
                WHEN status = 'consumed' THEN CASE WHEN units_consumed > 0 THEN units_consumed ELSE units_reserved END
                ELSE units_reserved
            END
        ), 0) AS total_units
        FROM workspace_analysis_budget_events
        WHERE workspace_id = ? AND window_start = ?
        """,
        (workspace_id, window_start),
    ).fetchone()
    return int((row["total_units"] if row is not None else 0) or 0)