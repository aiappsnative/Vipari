import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import get_settings
from engine.analysis import analyze_diff
from services.audit_jobs import claim_next_job, create_audit_job, get_job, init_db
from services.branch_scan_jobs import claim_next_branch_scan_job
from services.audit_records import get_audit_comment_episode_for_pr_head_sha, has_completed_audit, record_audit_result
from services.cloud_worker import _message_still_authorized, _process_message, run_worker
from services.observability import configure_logging
from services.control_plane_records import (
    allocate_repo_to_workspace,
    create_workspace,
    init_control_plane_db,
    update_repo_allocation_status,
    update_workspace_pr_comments_setting,
    upsert_entitlement,
    upsert_github_identity,
    upsert_github_installation,
)
from services.entitlements import derive_entitlement_payload
from services.queue import LocalSQLiteQueue, QueueMessage, close_queue_backend
from services.token_cache import clear_local_token_cache, get_installation_token, set_installation_token
from services.webhook_deliveries import claim_webhook_delivery, init_webhook_delivery_db
from services.webhook_service import create_webhook_app
from services.api_service import create_api_app


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



def test_webhook_redelivery_retries_after_enqueue_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "webhook-retry.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    _reset_settings_cache()

    class FlakyQueue:
        def __init__(self):
            self.messages = []
            self.failures = 1

        async def enqueue(self, message):
            if self.failures > 0:
                self.failures -= 1
                raise RuntimeError("temporary enqueue failure")
            self.messages.append(message)
            return "message-1"

    queue = FlakyQueue()
    app = create_webhook_app(queue)
    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {"number": 7, "base": {"sha": "base"}, "head": {"sha": "head"}},
    }
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    with TestClient(app, raise_server_exceptions=False) as client:
        headers = {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-retry",
            "Content-Type": "application/json",
        }
        first = client.post("/webhook", content=body, headers=headers)
        second = client.post("/webhook", content=body, headers=headers)

    assert first.status_code == 500
    assert second.status_code == 202
    assert len(queue.messages) == 1
    assert queue.messages[0]["delivery_id"] == "delivery-retry"


def test_webhook_delivery_dedupe_survives_restart_for_postgres_locator_simulation(tmp_path, monkeypatch):
    backing_db_path = tmp_path / "webhook-postgres-sim.db"
    locator = "postgresql://user:pass@db.example.com/driftguard"
    monkeypatch.setenv("DATABASE_URL", locator)
    monkeypatch.setenv("AUDIT_DB_PATH", "ignored.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_ROLE", "webhook")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://redis.example.com:6379/0")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "")
    _reset_settings_cache()

    class RecordingQueue:
        def __init__(self):
            self.messages = []
            self.closed = False

        async def enqueue(self, message):
            self.messages.append(message)
            return f"message-{len(self.messages)}"

        async def dequeue(self, _batch_size):
            return []

        async def ack(self, _receipt_handle):
            return None

        async def nack(self, _receipt_handle, _delay_seconds):
            return None

        async def move_to_dlq(self, _receipt_handle):
            return None

        async def depth(self):
            return len(self.messages)

        async def aclose(self):
            self.closed = True

    def fake_connect_sqlite(_db_path: str, *, foreign_keys: bool = False):
        connection = sqlite3.connect(backing_db_path)
        connection.row_factory = sqlite3.Row
        if foreign_keys:
            connection.execute("PRAGMA foreign_keys = ON")
        return connection

    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {"number": 7, "base": {"sha": "base"}, "head": {"sha": "head"}},
    }
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-postgres-restart",
        "Content-Type": "application/json",
    }

    with patch("services.webhook_deliveries.connect_sqlite", side_effect=fake_connect_sqlite), patch(
        "services.webhook_service.init_db"
    ):
        first_queue = RecordingQueue()
        with TestClient(create_webhook_app(first_queue)) as client:
            first = client.post("/webhook", content=body, headers=headers)

        second_queue = RecordingQueue()
        with TestClient(create_webhook_app(second_queue)) as client:
            second = client.post("/webhook", content=body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(first_queue.messages) == 1
    assert second.json() == {"message": "duplicate ignored"}
    assert second_queue.messages == []
    assert first_queue.closed is True
    assert second_queue.closed is True


def test_webhook_lifespan_closes_queue_backend(tmp_path, monkeypatch):
    db_path = str(tmp_path / "webhook-close.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    _reset_settings_cache()

    class ClosableQueue:
        def __init__(self):
            self.closed = False

        async def enqueue(self, _message):
            return "message-1"

        async def dequeue(self, _batch_size):
            return []

        async def ack(self, _receipt_handle):
            return None

        async def nack(self, _receipt_handle, _delay_seconds):
            return None

        async def move_to_dlq(self, _receipt_handle):
            return None

        async def depth(self):
            return 0

        async def aclose(self):
            self.closed = True

    queue = ClosableQueue()
    app = create_webhook_app(queue)

    with TestClient(app):
        pass

    assert queue.closed is True


def test_run_api_uses_configured_api_port(monkeypatch):
    monkeypatch.setenv("API_PORT", "9012")
    _reset_settings_cache()
    import run_api

    with patch.object(run_api.uvicorn, "run") as run_server:
        run_api.main()

    run_server.assert_called_once_with("run_api:app", host="0.0.0.0", port=9012)


def test_run_webhook_uses_configured_webhook_port(monkeypatch):
    monkeypatch.setenv("WEBHOOK_PORT", "9011")
    _reset_settings_cache()
    import run_webhook

    with patch.object(run_webhook.uvicorn, "run") as run_server:
        run_webhook.main()

    run_server.assert_called_once_with("run_webhook:app", host="0.0.0.0", port=9011)


def test_run_api_falls_back_to_railway_port(monkeypatch):
    monkeypatch.delenv("API_PORT", raising=False)
    monkeypatch.setenv("PORT", "7810")
    _reset_settings_cache()
    import run_api

    with patch.object(run_api.uvicorn, "run") as run_server:
        run_api.main()

    run_server.assert_called_once_with("run_api:app", host="0.0.0.0", port=7810)


def test_run_webhook_falls_back_to_railway_port(monkeypatch):
    monkeypatch.delenv("WEBHOOK_PORT", raising=False)
    monkeypatch.setenv("PORT", "7811")
    _reset_settings_cache()
    import run_webhook

    with patch.object(run_webhook.uvicorn, "run") as run_server:
        run_webhook.main()

    run_server.assert_called_once_with("run_webhook:app", host="0.0.0.0", port=7811)


def test_api_service_health_and_readiness_endpoints(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api-health.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("API_ADMIN_TOKEN", "admin-token")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_ENV", "local")
    _reset_settings_cache()

    with TestClient(create_api_app()) as client:
        health_response = client.get("/health")
        ready_response = client.get("/health/ready")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "service_role": "api"}
    assert ready_response.status_code == 200
    ready_payload = ready_response.json()
    assert ready_payload["status"] == "ok"
    assert ready_payload["service_role"] == "api"
    assert any(check["name"] == "config" and check["status"] == "ok" for check in ready_payload["checks"])


def test_api_service_health_and_readiness_support_postgres_locator(monkeypatch):
    locator = "postgresql://user:pass@db.example.com/driftguard"
    monkeypatch.setenv("DATABASE_URL", locator)
    monkeypatch.setenv("AUDIT_DB_PATH", "ignored.db")
    monkeypatch.setenv("API_ADMIN_TOKEN", "admin-token")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setenv("INTERNAL_JWT_SECRET", "test-internal-jwt-secret-32-bytes!")
    _reset_settings_cache()

    _all_versions = [
        "0001_bootstrap_relational_schema",
        "0002_add_pull_request_audits_fused_confidence",
        "0003_add_onboarding_approval_columns",
        "0004_add_machine_principals",
        "0005_add_session_flash",
        "0006_add_audit_feedback_and_triage_tables",
        "0007_add_high_risk_proposal_tables",
    ]
    applied_migrations = [type("AppliedMigration", (), {"version": v})() for v in _all_versions]
    with patch("services.api_service.init_db") as init_db_mock, patch(
        "services.runtime_guardrails.connect_sqlite"
    ) as connect, patch(
        "services.runtime_guardrails.list_applied_migrations", return_value=applied_migrations
    ):
        with TestClient(create_api_app()) as client:
            health_response = client.get("/health")
            ready_response = client.get("/health/ready")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "service_role": "api"}
    assert ready_response.status_code == 200
    ready_payload = ready_response.json()
    assert ready_payload["status"] == "ok"
    assert any(
        check["name"] == "persistence" and check["status"] == "ok" and "PostgreSQL connectivity verified." in check["detail"]
        for check in ready_payload["checks"]
    )
    assert any(check["name"] == "migrations" and check["status"] == "ok" for check in ready_payload["checks"])
    init_db_mock.assert_called_once_with(locator)
    connect.assert_called_once_with(locator)


def test_api_service_persistence_status_redacts_database_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api-persistence.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("API_ADMIN_TOKEN", "admin-token")
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("APP_ENV", "local")
    _reset_settings_cache()

    with TestClient(create_api_app()) as client:
        response = client.get("/api/persistence", headers={"Authorization": "Bearer admin-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"] == "sqlite"
    assert "database_path" not in payload


def test_webhook_service_health_and_readiness_endpoints(tmp_path, monkeypatch):
    db_path = str(tmp_path / "webhook-health.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("SERVICE_ROLE", "webhook")
    monkeypatch.setenv("APP_ENV", "local")
    _reset_settings_cache()

    with TestClient(create_webhook_app()) as client:
        health_response = client.get("/health")
        ready_response = client.get("/health/ready")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "service_role": "webhook"}
    assert ready_response.status_code == 200
    ready_payload = ready_response.json()
    assert ready_payload["status"] == "ok"
    assert ready_payload["service_role"] == "webhook"
    assert any(check["name"] == "config" and check["status"] == "ok" for check in ready_payload["checks"])


def test_webhook_service_health_and_readiness_support_postgres_locator(monkeypatch):
    class _FakeQueue:
        def __init__(self):
            self.closed = False

        async def depth(self):
            return 0

        async def aclose(self):
            self.closed = True

    locator = "postgresql://user:pass@db.example.com/driftguard"
    monkeypatch.setenv("DATABASE_URL", locator)
    monkeypatch.setenv("AUDIT_DB_PATH", "ignored.db")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("SERVICE_ROLE", "webhook")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://redis.example.com:6379/0")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    _reset_settings_cache()

    queue = _FakeQueue()
    _all_versions = [
        "0001_bootstrap_relational_schema",
        "0002_add_pull_request_audits_fused_confidence",
        "0003_add_onboarding_approval_columns",
        "0004_add_machine_principals",
        "0005_add_session_flash",
        "0006_add_audit_feedback_and_triage_tables",
        "0007_add_high_risk_proposal_tables",
    ]
    applied_migrations = [type("AppliedMigration", (), {"version": v})() for v in _all_versions]
    with patch("services.webhook_service.init_db") as init_db_mock, patch(
        "services.webhook_service.init_webhook_delivery_db"
    ) as init_delivery_db_mock, patch(
        "services.webhook_service.cleanup_webhook_deliveries"
    ) as cleanup_deliveries_mock, patch(
        "services.runtime_guardrails.connect_sqlite"
    ) as connect, patch(
        "services.runtime_guardrails.list_applied_migrations", return_value=applied_migrations
    ):
        with TestClient(create_webhook_app(queue)) as client:
            health_response = client.get("/health")
            ready_response = client.get("/health/ready")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "service_role": "webhook"}
    assert ready_response.status_code == 200
    ready_payload = ready_response.json()
    assert ready_payload["status"] == "ok"
    assert any(
        check["name"] == "persistence" and check["status"] == "ok" and "PostgreSQL connectivity verified." in check["detail"]
        for check in ready_payload["checks"]
    )
    assert any(check["name"] == "migrations" and check["status"] == "ok" for check in ready_payload["checks"])
    assert any(check["name"] == "queue" and check["status"] == "ok" for check in ready_payload["checks"])
    init_db_mock.assert_called_once_with(locator)
    init_delivery_db_mock.assert_called_once_with(locator)
    cleanup_deliveries_mock.assert_called_once_with(locator)
    connect.assert_called_once_with(locator)
    assert queue.closed is True


def test_message_authorization_respects_workspace_pr_comments_setting(tmp_path, monkeypatch):
    db_path = str(tmp_path / "worker-settings.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    _reset_settings_cache()

    init_control_plane_db(db_path)
    user, _identity = upsert_github_identity(
        db_path,
        github_user_id="1500",
        github_login="worker-settings-owner",
        display_name="Worker Settings Owner",
        primary_email="worker-settings@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        db_path,
        slug="worker-settings-workspace",
        display_name="Worker Settings Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_entitlement(
        db_path,
        workspace_id=workspace.id,
        payload=derive_entitlement_payload("team", "active"),
    )
    upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=888,
        account_id="888",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=888,
        repo_github_id="dummyAI",
        repo_full="doria90/dummyAI",
        baseline_mode="default_branch",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(db_path, allocation.id, "active")

    settings = get_settings()
    payload = {
        "installation_id": 888,
        "repo_full": "doria90/dummyAI",
        "event_type": "pull_request",
    }
    assert _message_still_authorized(payload, settings) is True

    update_workspace_pr_comments_setting(db_path, workspace.id, enabled=False)
    assert _message_still_authorized(payload, settings) is False


def test_webhook_push_enqueues_default_branch_scan_delivery(tmp_path, monkeypatch):
    db_path = str(tmp_path / "webhook-push.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    _reset_settings_cache()

    queue = LocalSQLiteQueue(db_path)
    app = create_webhook_app(queue)
    payload = {
        "ref": "refs/heads/main",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI", "default_branch": "main"},
        "head_commit": {"id": "pushsha123"},
    }
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    with TestClient(app) as client:
        headers = {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "delivery-push-1",
            "Content-Type": "application/json",
        }
        response = client.post("/webhook", content=body, headers=headers)

    assert response.status_code == 202
    messages = asyncio.run(queue.dequeue(10))
    assert len(messages) == 1
    assert messages[0].payload["event_type"] == "push"
    assert messages[0].payload["commit_sha"] == "pushsha123"
    assert messages[0].payload["branch_ref"] == "refs/heads/main"


def test_worker_turns_push_message_into_branch_scan_job(tmp_path, monkeypatch):
    db_path = str(tmp_path / "worker-push.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_APP_ID", "app-id")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "/tmp/test-key.pem")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _reset_settings_cache()

    init_db(db_path)
    queue = LocalSQLiteQueue(db_path)

    async def exercise_worker():
        await queue.enqueue(
            {
                "event_type": "push",
                "installation_id": 123,
                "repo_full": "doria90/dummyAI",
                "commit_sha": "pushsha123",
                "branch_ref": "refs/heads/main",
                "triggered_by": "push_webhook",
            }
        )
        message = (await queue.dequeue(1))[0]
        await _process_message(queue, message, get_settings(), configure_logging("worker-test"), Mock())
        assert await queue.dequeue(1) == []

    asyncio.run(exercise_worker())
    created_job = claim_next_branch_scan_job(db_path)
    assert created_job is not None
    assert created_job.repo_full == "doria90/dummyAI"
    assert created_job.commit_sha == "pushsha123"


def test_worker_push_job_persists_across_restart_for_postgres_locator_simulation(tmp_path, monkeypatch):
    backing_db_path = tmp_path / "worker-postgres-sim.db"
    locator = "postgresql://user:pass@db.example.com/driftguard"
    monkeypatch.setenv("DATABASE_URL", locator)
    monkeypatch.setenv("AUDIT_DB_PATH", "ignored.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://redis.example.com:6379/0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FOUNDRY_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "")
    _reset_settings_cache()

    class AckQueue:
        def __init__(self):
            self.acked = []

        async def enqueue(self, _message):
            return "message-1"

        async def dequeue(self, _batch_size):
            return []

        async def ack(self, receipt_handle):
            self.acked.append(receipt_handle)

        async def nack(self, _receipt_handle, _delay_seconds):
            return None

        async def move_to_dlq(self, _receipt_handle):
            return None

        async def depth(self):
            return 0

        async def aclose(self):
            return None

    def fake_connect_sqlite(_db_path: str, *, foreign_keys: bool = False):
        connection = sqlite3.connect(backing_db_path)
        connection.row_factory = sqlite3.Row
        if foreign_keys:
            connection.execute("PRAGMA foreign_keys = ON")
        return connection

    queue = AckQueue()
    message = QueueMessage(
        message_id="msg-1",
        receipt_handle="receipt-1",
        payload={
            "event_type": "push",
            "installation_id": 123,
            "repo_full": "doria90/dummyAI",
            "commit_sha": "persisted-push-sha",
            "branch_ref": "refs/heads/main",
            "triggered_by": "push_webhook",
        },
        attempt_count=1,
    )

    with patch("services.branch_scan_jobs.connect_sqlite", side_effect=fake_connect_sqlite):
        asyncio.run(_process_message(queue, message, get_settings(), configure_logging("worker-test"), Mock()))

        # Simulate a worker restart by loading the queued job through the normal claim path.
        claimed_job = claim_next_branch_scan_job(locator)

    assert queue.acked == ["receipt-1"]
    assert claimed_job is not None
    assert claimed_job.repo_full == "doria90/dummyAI"
    assert claimed_job.commit_sha == "persisted-push-sha"
    assert claimed_job.branch_ref == "refs/heads/main"
    assert claimed_job.status == "processing"


def test_stale_processing_delivery_can_be_reclaimed(tmp_path):
    db_path = str(tmp_path / "webhook-stale.db")
    init_webhook_delivery_db(db_path)

    queue = LocalSQLiteQueue(db_path)
    with queue._connect() as conn:
        conn.execute(
            """
            INSERT INTO webhook_deliveries (delivery_id, received_at, event_type, enqueued, status)
            VALUES (?, ?, ?, 0, 'processing')
            """,
            ("delivery-stale", time.time() - 601, "pull_request"),
        )

    assert claim_webhook_delivery(db_path, "delivery-stale", "pull_request") is True


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


def test_worker_completed_pr_audit_survives_restart_for_postgres_locator_simulation(tmp_path, monkeypatch):
    backing_db_path = tmp_path / "worker-audit-postgres-sim.db"
    locator = "postgresql://user:pass@db.example.com/driftguard"
    monkeypatch.setenv("DATABASE_URL", locator)
    monkeypatch.setenv("AUDIT_DB_PATH", "ignored.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://redis.example.com:6379/0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FOUNDRY_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "")
    _reset_settings_cache()

    init_db(str(backing_db_path))

    class AckQueue:
        def __init__(self):
            self.acked = []

        async def enqueue(self, _message):
            return "message-1"

        async def dequeue(self, _batch_size):
            return []

        async def ack(self, receipt_handle):
            self.acked.append(receipt_handle)

        async def nack(self, _receipt_handle, _delay_seconds):
            return None

        async def move_to_dlq(self, _receipt_handle):
            return None

        async def depth(self):
            return 0

        async def aclose(self):
            return None

    def fake_connect_sqlite(_db_path: str, *, foreign_keys: bool = False):
        connection = sqlite3.connect(backing_db_path)
        connection.row_factory = sqlite3.Row
        if foreign_keys:
            connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def fake_process_job(job, _settings):
        analysis = analyze_diff(job.diff_text)
        from services.audit_jobs import mark_job_completed

        mark_job_completed(locator, job.id, comment_body="done")
        record_audit_result(
            locator,
            job_id=job.id,
            repo_full=job.repo_full,
            pr_number=job.pr_number,
            installation_id=job.installation_id,
            head_sha=job.head_sha,
            deterministic_analysis=analysis,
            status="completed",
            completion_mode="completed",
            output_mode="full_review",
            comment_body="done",
            comment_mode="full_review",
            semantic_review_completed=True,
        )
        return "completed"

    queue = AckQueue()
    initial_message = QueueMessage(
        message_id="msg-pr-1",
        receipt_handle="receipt-pr-1",
        payload={
            "action": "opened",
            "installation_id": 123,
            "repo_full": "doria90/dummyAI",
            "pr_number": 11,
            "head_sha": "sha-pr-11",
        },
        attempt_count=1,
    )
    restarted_message = QueueMessage(
        message_id="msg-pr-2",
        receipt_handle="receipt-pr-2",
        payload={
            "action": "opened",
            "installation_id": 123,
            "repo_full": "doria90/dummyAI",
            "pr_number": 11,
            "head_sha": "sha-pr-11",
        },
        attempt_count=1,
    )

    with patch("services.audit_jobs.connect_sqlite", side_effect=fake_connect_sqlite), patch(
        "services.audit_records.connect_sqlite", side_effect=fake_connect_sqlite
    ), patch(
        "services.cloud_worker._get_installation_token_for_worker",
        return_value="installation-token",
    ), patch(
        "services.cloud_worker.fetch_diff_with_retry",
        return_value="diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n+system prompt\n",
    ) as fetch_diff, patch(
        "services.cloud_worker.process_job", side_effect=fake_process_job
    ):
        asyncio.run(_process_message(queue, initial_message, get_settings(), configure_logging("worker-test"), Mock()))

        assert has_completed_audit(locator, repo_full="doria90/dummyAI", pr_number=11, head_sha="sha-pr-11") is True

        asyncio.run(_process_message(queue, restarted_message, get_settings(), configure_logging("worker-test"), Mock()))

    assert queue.acked == ["receipt-pr-1", "receipt-pr-2"]
    assert fetch_diff.call_count == 1


def test_audit_comment_episode_survives_restart_for_postgres_locator_simulation(tmp_path, monkeypatch):
    backing_db_path = tmp_path / "worker-comment-postgres-sim.db"
    locator = "postgresql://user:pass@db.example.com/driftguard"
    monkeypatch.setenv("DATABASE_URL", locator)
    monkeypatch.setenv("AUDIT_DB_PATH", "ignored.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://redis.example.com:6379/0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FOUNDRY_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "")
    _reset_settings_cache()

    init_db(str(backing_db_path))

    class AckQueue:
        def __init__(self):
            self.acked = []

        async def enqueue(self, _message):
            return "message-1"

        async def dequeue(self, _batch_size):
            return []

        async def ack(self, receipt_handle):
            self.acked.append(receipt_handle)

        async def nack(self, _receipt_handle, _delay_seconds):
            return None

        async def move_to_dlq(self, _receipt_handle):
            return None

        async def depth(self):
            return 0

        async def aclose(self):
            return None

    def fake_connect_sqlite(_db_path: str, *, foreign_keys: bool = False):
        connection = sqlite3.connect(backing_db_path)
        connection.row_factory = sqlite3.Row
        if foreign_keys:
            connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def fake_process_job(job, _settings):
        analysis = analyze_diff(job.diff_text)
        from services.audit_jobs import mark_job_completed

        mark_job_completed(locator, job.id, comment_body="review comment")
        record_audit_result(
            locator,
            job_id=job.id,
            repo_full=job.repo_full,
            pr_number=job.pr_number,
            installation_id=job.installation_id,
            head_sha=job.head_sha,
            deterministic_analysis=analysis,
            status="completed",
            completion_mode="completed",
            output_mode="full_review",
            comment_body="review comment",
            comment_mode="full_review",
            semantic_review_completed=True,
            github_comment_id=321,
        )
        return "completed"

    queue = AckQueue()
    message = QueueMessage(
        message_id="msg-comment-1",
        receipt_handle="receipt-comment-1",
        payload={
            "action": "opened",
            "installation_id": 123,
            "repo_full": "doria90/dummyAI",
            "pr_number": 12,
            "head_sha": "sha-pr-12",
        },
        attempt_count=1,
    )

    with patch("services.audit_jobs.connect_sqlite", side_effect=fake_connect_sqlite), patch(
        "services.audit_records.connect_sqlite", side_effect=fake_connect_sqlite
    ), patch(
        "services.cloud_worker._get_installation_token_for_worker",
        return_value="installation-token",
    ), patch(
        "services.cloud_worker.fetch_diff_with_retry",
        return_value="diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n+system prompt\n",
    ), patch(
        "services.cloud_worker.process_job", side_effect=fake_process_job
    ):
        asyncio.run(_process_message(queue, message, get_settings(), configure_logging("worker-test"), Mock()))

    with patch("services.audit_records.connect_sqlite", side_effect=fake_connect_sqlite):
        comment_episode = get_audit_comment_episode_for_pr_head_sha(locator, "doria90/dummyAI", 12, "sha-pr-12")

    assert queue.acked == ["receipt-comment-1"]
    assert comment_episode is not None
    assert comment_episode.audit_comment.github_comment_id == 321
    assert comment_episode.audit_comment.comment_mode == "full_review"
    assert comment_episode.audit_comment.comment_body == "review comment"
    assert comment_episode.audit_status == "completed"
    assert comment_episode.audit_output_mode == "full_review"


def test_audit_job_retry_wait_survives_restart_for_postgres_locator_simulation(tmp_path, monkeypatch):
    backing_db_path = tmp_path / "worker-retry-postgres-sim.db"
    locator = "postgresql://user:pass@db.example.com/driftguard"
    monkeypatch.setenv("DATABASE_URL", locator)
    monkeypatch.setenv("AUDIT_DB_PATH", "ignored.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://redis.example.com:6379/0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FOUNDRY_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "")
    _reset_settings_cache()

    init_db(str(backing_db_path))

    class RetryQueue:
        def __init__(self):
            self.acked = []
            self.nacks = []

        async def enqueue(self, _message):
            return "message-1"

        async def dequeue(self, _batch_size):
            return []

        async def ack(self, receipt_handle):
            self.acked.append(receipt_handle)

        async def nack(self, receipt_handle, delay_seconds):
            self.nacks.append((receipt_handle, delay_seconds))

        async def move_to_dlq(self, _receipt_handle):
            return None

        async def depth(self):
            return 0

        async def aclose(self):
            return None

    def fake_connect_sqlite(_db_path: str, *, foreign_keys: bool = False):
        connection = sqlite3.connect(backing_db_path)
        connection.row_factory = sqlite3.Row
        if foreign_keys:
            connection.execute("PRAGMA foreign_keys = ON")
        return connection

    retry_at = time.time() + 5

    def fake_process_job(job, _settings):
        from services.audit_jobs import mark_job_retry

        mark_job_retry(locator, job.id, error_message="temporary llm issue", retry_at=retry_at)
        return "retry_wait"

    queue = RetryQueue()
    message = QueueMessage(
        message_id="msg-retry-1",
        receipt_handle="receipt-retry-1",
        payload={
            "action": "opened",
            "installation_id": 123,
            "repo_full": "doria90/dummyAI",
            "pr_number": 13,
            "head_sha": "sha-pr-13",
        },
        attempt_count=1,
    )

    with patch("services.audit_jobs.connect_sqlite", side_effect=fake_connect_sqlite), patch(
        "services.audit_records.connect_sqlite", side_effect=fake_connect_sqlite
    ), patch(
        "services.cloud_worker._get_installation_token_for_worker",
        return_value="installation-token",
    ), patch(
        "services.cloud_worker.fetch_diff_with_retry",
        return_value="diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n+system prompt\n",
    ), patch(
        "services.cloud_worker.process_job", side_effect=fake_process_job
    ):
        asyncio.run(_process_message(queue, message, get_settings(), configure_logging("worker-test"), Mock()))

        persisted_job = get_job(locator, 1)
        reclaimed_job = claim_next_job(locator, now=retry_at + 1)

        analysis = analyze_diff(reclaimed_job.diff_text)
        from services.audit_jobs import mark_job_completed

        mark_job_completed(locator, reclaimed_job.id, comment_body="retry recovered")
        record_audit_result(
            locator,
            job_id=reclaimed_job.id,
            repo_full=reclaimed_job.repo_full,
            pr_number=reclaimed_job.pr_number,
            installation_id=reclaimed_job.installation_id,
            head_sha=reclaimed_job.head_sha,
            deterministic_analysis=analysis,
            status="completed",
            completion_mode="completed",
            output_mode="full_review",
            comment_body="retry recovered",
            comment_mode="full_review",
            semantic_review_completed=True,
        )

    assert queue.acked == []
    assert len(queue.nacks) == 1
    assert queue.nacks[0][0] == "receipt-retry-1"
    assert persisted_job is not None
    assert persisted_job.status == "retry_wait"
    assert persisted_job.last_error == "temporary llm issue"
    assert reclaimed_job is not None
    assert reclaimed_job.status == "processing"
    assert reclaimed_job.attempt_count == 2
    with patch("services.audit_records.connect_sqlite", side_effect=fake_connect_sqlite):
        assert has_completed_audit(locator, repo_full="doria90/dummyAI", pr_number=13, head_sha="sha-pr-13") is True


def test_worker_skips_message_for_inactive_allocation(tmp_path, monkeypatch):
    db_path = str(tmp_path / "worker-control-plane.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_APP_ID", "app-id")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "/tmp/test-key.pem")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _reset_settings_cache()
    init_control_plane_db(db_path)

    user, _ = upsert_github_identity(
        db_path,
        github_user_id="123",
        github_login="reviewer",
        display_name="Reviewer",
        primary_email="reviewer@example.com",
        avatar_url=None,
        granted_scopes=["repo"],
        access_token_encrypted="token",
    )
    workspace = create_workspace(
        db_path,
        slug="secure-workspace",
        display_name="Secure Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=derive_entitlement_payload("team", "active"))
    upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=123,
        account_id="acct-1",
        account_login="reviewer",
        account_type="User",
        target_type="User",
    )
    allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=123,
        repo_github_id="repo-1",
        repo_full="doria90/dummyAI",
        baseline_mode="default_branch",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(db_path, allocation.id, "inactive")

    queue = LocalSQLiteQueue(db_path)

    async def exercise_worker():
        await queue.enqueue(
            {
                "action": "opened",
                "installation_id": 123,
                "repo_full": "doria90/dummyAI",
                "pr_number": 10,
                "head_sha": "sha-10",
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


def test_run_worker_supports_postgres_locator_with_provided_queue(monkeypatch):
    class ClosableQueue:
        def __init__(self):
            self.closed = False

        async def dequeue(self, _batch_size):
            return []

        async def ack(self, _receipt_handle):
            return None

        async def nack(self, _receipt_handle, _delay_seconds):
            return None

        async def move_to_dlq(self, _receipt_handle):
            return None

        async def depth(self):
            return 0

        async def aclose(self):
            self.closed = True

    class FakeTask:
        def cancel(self):
            return None

    locator = "postgresql://user:pass@db.example.com/driftguard"
    monkeypatch.setenv("DATABASE_URL", locator)
    monkeypatch.setenv("AUDIT_DB_PATH", "ignored.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://redis.example.com:6379/0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FOUNDRY_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setenv("GITHUB_APP_ID", "app-id")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "inline-test-key")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "")
    _reset_settings_cache()

    queue = ClosableQueue()
    created_tasks = []

    def fake_create_task(coro):
        coro.close()
        task = FakeTask()
        created_tasks.append(task)
        return task

    async def fake_gather(*_tasks):
        return None

    with patch("services.runtime_guardrails._validate_github_app_private_key"), patch(
        "services.cloud_worker.init_db"
    ) as init_db_mock, patch(
        "services.cloud_worker.cleanup_webhook_deliveries"
    ) as cleanup_mock, patch(
        "services.cloud_worker.OpenAI"
    ) as openai_mock, patch(
        "services.cloud_worker.asyncio.create_task", side_effect=fake_create_task
    ), patch(
        "services.cloud_worker.asyncio.gather", side_effect=fake_gather
    ):
        asyncio.run(run_worker(queue))

    init_db_mock.assert_called_once_with(locator)
    cleanup_mock.assert_called_once_with(locator)
    openai_mock.assert_called_once_with(api_key="test-key", base_url=None)
    assert len(created_tasks) == max(1, get_settings().worker_concurrency) + 2
    assert queue.closed is True


def test_close_queue_backend_ignores_none_and_closes_when_supported():
    class ClosableQueue:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    queue = ClosableQueue()

    async def exercise_cleanup():
        await close_queue_backend(None)
        await close_queue_backend(queue)

    asyncio.run(exercise_cleanup())

    assert queue.closed is True


def test_api_service_initializes_schema_for_fresh_database(tmp_path, monkeypatch):
    db_path = str(tmp_path / "fresh-api.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ignored.db'}")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("API_ADMIN_TOKEN", "read-token")
    monkeypatch.delenv("ENABLE_METRICS", raising=False)
    _reset_settings_cache()

    app = create_api_app()

    with TestClient(app) as client:
        response = client.get("/api/repos", headers={"Authorization": "Bearer read-token"})
        metrics_response = client.get("/metrics")

    assert response.status_code == 200
    assert response.json() == {"repos": []}
    assert metrics_response.status_code == 404


def test_explicit_audit_db_path_wins_for_shared_sqlite_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./driftguard.db")
    shared_path = str(tmp_path / "shared" / "driftguard.db")
    monkeypatch.setenv("AUDIT_DB_PATH", shared_path)
    _reset_settings_cache()

    settings = get_settings()

    assert settings.resolved_db_path == shared_path


def test_postgres_database_url_becomes_runtime_locator(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.com/driftguard")
    monkeypatch.setenv("AUDIT_DB_PATH", "ignored.db")
    _reset_settings_cache()

    settings = get_settings()

    assert settings.resolved_db_path == "postgresql://user:pass@db.example.com/driftguard"


def test_api_write_routes_require_admin_token(tmp_path, monkeypatch):
    db_path = str(tmp_path / "secured-api.db")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("API_ADMIN_TOKEN", "super-secret-token")
    monkeypatch.delenv("ENABLE_METRICS", raising=False)
    _reset_settings_cache()

    app = create_api_app()

    with patch("services.api_service.generate_jwt", return_value="jwt"), patch(
        "services.api_service.get_installation_token", return_value="installation-token"
    ), patch("services.api_service.execute_repository_history_backfill", return_value=[]):
        with TestClient(app) as client:
            unauthorized = client.post(
                "/api/repos/doria90/dummyAI/backfill",
                json={"installation_id": 123},
            )
            authorized = client.post(
                "/api/repos/doria90/dummyAI/backfill",
                headers={"Authorization": "Bearer super-secret-token"},
                json={"installation_id": 123},
            )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_api_read_routes_require_admin_token(tmp_path, monkeypatch):
    db_path = str(tmp_path / "secured-read-api.db")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("API_ADMIN_TOKEN", "read-secret-token")
    monkeypatch.delenv("ENABLE_METRICS", raising=False)
    _reset_settings_cache()

    app = create_api_app()

    with TestClient(app) as client:
        unauthorized_json = client.get("/api/repos")
        authorized_json = client.get("/api/repos", headers={"X-Admin-Token": "read-secret-token"})
        unauthorized_html = client.get("/dashboard")
        authorized_html = client.get("/dashboard", headers={"Authorization": "Bearer read-secret-token"})

    assert unauthorized_json.status_code == 401
    assert authorized_json.status_code == 200
    assert unauthorized_html.status_code == 401
    assert authorized_html.status_code == 200


def test_metrics_can_be_enabled_explicitly(tmp_path, monkeypatch):
    db_path = str(tmp_path / "metrics-api.db")
    monkeypatch.setenv("AUDIT_DB_PATH", db_path)
    monkeypatch.setenv("ENABLE_METRICS", "true")
    _reset_settings_cache()

    app = create_api_app()

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
