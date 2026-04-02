import asyncio
import hashlib
import hmac
import json
import os
import sys
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import get_settings
from engine.analysis import analyze_diff
from services.audit_jobs import create_audit_job, init_db
from services.audit_records import record_audit_result
from services.cloud_worker import _process_message
from services.observability import configure_logging
from services.queue import LocalSQLiteQueue
from services.token_cache import clear_local_token_cache, get_installation_token, set_installation_token
from services.webhook_service import create_webhook_app


def _reset_settings_cache():
    get_settings.cache_clear()


def test_local_sqlite_queue_round_trip(tmp_path):
    queue = LocalSQLiteQueue(str(tmp_path / "queue.db"), visibility_timeout_seconds=1)

    async def exercise_queue():
        message_id = await queue.enqueue({"hello": "world"})
        messages = await queue.dequeue(1)
        assert len(messages) == 1
        assert messages[0].message_id == message_id
        assert messages[0].payload == {"hello": "world"}

        await queue.nack(messages[0].receipt_handle, 1)
        assert await queue.dequeue(1) == []
        await asyncio.sleep(1.1)

        messages = await queue.dequeue(1)
        assert len(messages) == 1
        await queue.move_to_dlq(messages[0].receipt_handle)
        assert await queue.dequeue(1) == []

        second_id = await queue.enqueue({"foo": "bar"})
        messages = await queue.dequeue(1)
        assert messages[0].message_id == second_id
        await queue.ack(messages[0].receipt_handle)
        assert await queue.dequeue(1) == []

    asyncio.run(exercise_queue())


def test_webhook_deduplication_only_enqueues_once(tmp_path, monkeypatch):
    db_path = str(tmp_path / "webhook.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    _reset_settings_cache()

    queue = LocalSQLiteQueue(db_path)
    app = create_webhook_app(queue)
    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {"number": 7, "base": {"sha": "base"}, "head": {"sha": "head"}},
    }
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    with TestClient(app) as client:
        headers = {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-1",
            "Content-Type": "application/json",
        }
        first = client.post("/webhook", content=body, headers=headers)
        second = client.post("/webhook", content=body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    messages = asyncio.run(queue.dequeue(10))
    assert len(messages) == 1
    assert messages[0].payload["delivery_id"] == "delivery-1"


def test_worker_skips_completed_idempotent_message(tmp_path, monkeypatch):
    db_path = str(tmp_path / "worker.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_APP_ID", "app-id")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "/tmp/test-key.pem")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _reset_settings_cache()

    init_db(db_path)
    created = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=9,
        installation_id=123,
        head_sha="sha-9",
        diff_text="diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n",
    )
    analysis = analyze_diff(created.diff_text)
    record_audit_result(
        db_path,
        job_id=created.id,
        repo_full=created.repo_full,
        pr_number=created.pr_number,
        installation_id=created.installation_id,
        head_sha=created.head_sha,
        deterministic_analysis=analysis,
        status="completed",
        completion_mode="completed",
        output_mode="full_review",
        comment_body="done",
        comment_mode="full_review",
        semantic_review_completed=True,
    )

    queue = LocalSQLiteQueue(db_path)

    async def exercise_worker():
        await queue.enqueue(
            {
                "action": "opened",
                "installation_id": 123,
                "repo_full": "doria90/dummyAI",
                "pr_number": 9,
                "head_sha": "sha-9",
            }
        )
        message = (await queue.dequeue(1))[0]
        await _process_message(queue, message, get_settings(), configure_logging("worker-test"), Mock())
        assert await queue.dequeue(1) == []

    with patch("services.cloud_worker.fetch_diff_with_retry") as fetch_diff:
        asyncio.run(exercise_worker())
    fetch_diff.assert_not_called()


def test_token_cache_falls_back_to_in_process_cache(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    _reset_settings_cache()
    clear_local_token_cache()

    async def exercise_cache():
        assert await get_installation_token(321) is None
        await set_installation_token(321, "cached-token", 60)
        assert await get_installation_token(321) == "cached-token"

    asyncio.run(exercise_cache())
