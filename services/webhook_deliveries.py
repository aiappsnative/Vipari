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
                enqueued INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(webhook_deliveries)").fetchall()}
        if "status" not in columns:
            conn.execute("ALTER TABLE webhook_deliveries ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
            conn.execute(
                "UPDATE webhook_deliveries SET status = CASE WHEN enqueued = 1 THEN 'enqueued' ELSE 'pending' END"
            )


def claim_webhook_delivery(db_path: str, delivery_id: str, event_type: str) -> bool:
    init_webhook_delivery_db(db_path)
    now = time.time()
    with connect_sqlite(db_path) as conn:
        inserted = conn.execute(
            """
            INSERT INTO webhook_deliveries (delivery_id, received_at, event_type, enqueued, status)
            VALUES (?, ?, ?, 0, 'processing')
            ON CONFLICT(delivery_id) DO NOTHING
            RETURNING delivery_id
            """,
            (delivery_id, now, event_type),
        ).fetchone()
        if inserted is not None:
            return True

        reclaimed = conn.execute(
            """
            UPDATE webhook_deliveries
            SET received_at = ?,
                event_type = ?,
                status = 'processing'
            WHERE delivery_id = ?
              AND status = 'pending'
            RETURNING delivery_id
            """,
            (now, event_type, delivery_id),
        ).fetchone()
    return reclaimed is not None


def mark_webhook_delivery_enqueued(db_path: str, delivery_id: str) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            "UPDATE webhook_deliveries SET enqueued = 1, status = 'enqueued' WHERE delivery_id = ?",
            (delivery_id,),
        )


def mark_webhook_delivery_pending(db_path: str, delivery_id: str) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            "UPDATE webhook_deliveries SET enqueued = 0, status = 'pending' WHERE delivery_id = ?",
            (delivery_id,),
        )


def cleanup_webhook_deliveries(db_path: str, *, now: float | None = None) -> None:
    cutoff = (now or time.time()) - DELIVERY_TTL_SECONDS
    with connect_sqlite(db_path) as conn:
        conn.execute("DELETE FROM webhook_deliveries WHERE received_at < ?", (cutoff,))
