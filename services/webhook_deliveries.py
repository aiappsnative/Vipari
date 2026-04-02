from __future__ import annotations

import time

from .persistence import connect_sqlite


DELIVERY_TTL_HOURS = 48
# Keep delivery dedupe records for two days so repeated GitHub redeliveries are ignored during retries/debugging.
DELIVERY_TTL_SECONDS = DELIVERY_TTL_HOURS * 60 * 60


def init_webhook_delivery_db(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                delivery_id TEXT PRIMARY KEY,
                received_at REAL NOT NULL,
                event_type TEXT NOT NULL,
                enqueued INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def register_webhook_delivery(db_path: str, delivery_id: str, event_type: str) -> bool:
    init_webhook_delivery_db(db_path)
    with connect_sqlite(db_path) as conn:
        existing = conn.execute(
            "SELECT enqueued FROM webhook_deliveries WHERE delivery_id = ?",
            (delivery_id,),
        ).fetchone()
        if existing is not None:
            return False
        conn.execute(
            """
            INSERT INTO webhook_deliveries (delivery_id, received_at, event_type, enqueued)
            VALUES (?, ?, ?, 0)
            """,
            (delivery_id, time.time(), event_type),
        )
    return True


def mark_webhook_delivery_enqueued(db_path: str, delivery_id: str) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            "UPDATE webhook_deliveries SET enqueued = 1 WHERE delivery_id = ?",
            (delivery_id,),
        )


def cleanup_webhook_deliveries(db_path: str, *, now: float | None = None) -> None:
    cutoff = (now or time.time()) - DELIVERY_TTL_SECONDS
    with connect_sqlite(db_path) as conn:
        conn.execute("DELETE FROM webhook_deliveries WHERE received_at < ?", (cutoff,))
