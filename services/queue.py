from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import boto3

from .persistence import connect_sqlite


DEFAULT_VISIBILITY_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    receipt_handle: str
    payload: dict[str, Any]
    attempt_count: int


class QueueBackend(Protocol):
    async def enqueue(self, message: dict[str, Any]) -> str: ...

    async def dequeue(self, batch_size: int) -> list[QueueMessage]: ...

    async def ack(self, receipt_handle: str) -> None: ...

    async def nack(self, receipt_handle: str, delay_seconds: int) -> None: ...

    async def move_to_dlq(self, receipt_handle: str) -> None: ...


class LocalSQLiteQueue:
    def __init__(self, db_path: str, *, visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS):
        self.db_path = db_path
        self.visibility_timeout_seconds = visibility_timeout_seconds
        self._init_db()

    def _connect(self):
        return connect_sqlite(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queue_messages (
                    message_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    receipt_handle TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL,
                    locked_until REAL,
                    in_dlq INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_messages_ready ON queue_messages(in_dlq, available_at, created_at)"
            )

    async def enqueue(self, message: dict[str, Any]) -> str:
        message_id = str(uuid.uuid4())
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO queue_messages (
                    message_id, payload_json, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, json.dumps(message), now, now, now),
            )
        return message_id

    async def dequeue(self, batch_size: int) -> list[QueueMessage]:
        now = time.time()
        locked_until = now + self.visibility_timeout_seconds
        messages: list[QueueMessage] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id
                FROM queue_messages
                WHERE in_dlq = 0
                  AND available_at <= ?
                  AND (locked_until IS NULL OR locked_until <= ?)
                ORDER BY created_at ASC, message_id ASC
                LIMIT ?
                """,
                (now, now, batch_size),
            ).fetchall()

            for row in rows:
                receipt_handle = str(uuid.uuid4())
                updated = conn.execute(
                    """
                    UPDATE queue_messages
                    SET receipt_handle = ?,
                        locked_until = ?,
                        attempt_count = attempt_count + 1,
                        updated_at = ?
                    WHERE message_id = ?
                      AND in_dlq = 0
                      AND available_at <= ?
                      AND (locked_until IS NULL OR locked_until <= ?)
                    RETURNING message_id, payload_json, receipt_handle, attempt_count
                    """,
                    (receipt_handle, locked_until, now, row["message_id"], now, now),
                ).fetchone()
                if updated is None:
                    continue
                messages.append(
                    QueueMessage(
                        message_id=updated["message_id"],
                        receipt_handle=updated["receipt_handle"],
                        payload=json.loads(updated["payload_json"]),
                        attempt_count=updated["attempt_count"],
                    )
                )
        return messages

    async def ack(self, receipt_handle: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM queue_messages WHERE receipt_handle = ?", (receipt_handle,))

    async def nack(self, receipt_handle: str, delay_seconds: int) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE queue_messages
                SET available_at = ?,
                    locked_until = NULL,
                    receipt_handle = NULL,
                    updated_at = ?
                WHERE receipt_handle = ?
                """,
                (now + delay_seconds, now, receipt_handle),
            )

    async def move_to_dlq(self, receipt_handle: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE queue_messages
                SET in_dlq = 1,
                    locked_until = NULL,
                    updated_at = ?,
                    receipt_handle = NULL
                WHERE receipt_handle = ?
                """,
                (time.time(), receipt_handle),
            )

    async def depth(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM queue_messages WHERE in_dlq = 0").fetchone()
        return int(row["count"]) if row is not None else 0


class SQSQueue:
    def __init__(self, queue_url: str, dlq_url: str):
        self.queue_url = queue_url
        self.dlq_url = dlq_url
        self.client = boto3.client("sqs")

    async def enqueue(self, message: dict[str, Any]) -> str:
        response = await asyncio.to_thread(
            self.client.send_message,
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(message),
        )
        return response["MessageId"]

    async def dequeue(self, batch_size: int) -> list[QueueMessage]:
        response = await asyncio.to_thread(
            self.client.receive_message,
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=min(batch_size, 10),
            WaitTimeSeconds=1,
            VisibilityTimeout=DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = []
        for item in response.get("Messages", []):
            messages.append(
                QueueMessage(
                    message_id=item["MessageId"],
                    receipt_handle=item["ReceiptHandle"],
                    payload=json.loads(item["Body"]),
                    attempt_count=int(item.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
                )
            )
        return messages

    async def ack(self, receipt_handle: str) -> None:
        await asyncio.to_thread(self.client.delete_message, QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)

    async def nack(self, receipt_handle: str, delay_seconds: int) -> None:
        await asyncio.to_thread(
            self.client.change_message_visibility,
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=max(0, delay_seconds),
        )

    async def move_to_dlq(self, receipt_handle: str) -> None:
        message = await asyncio.to_thread(
            self.client.receive_message,
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            VisibilityTimeout=0,
        )
        body = None
        for item in message.get("Messages", []):
            if item["ReceiptHandle"] == receipt_handle:
                body = item["Body"]
                break
        if body is not None:
            await asyncio.to_thread(self.client.send_message, QueueUrl=self.dlq_url, MessageBody=body)
        await self.ack(receipt_handle)
