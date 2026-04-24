import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import get_settings
from engine.analysis import analyze_diff
from services.audit_jobs import create_audit_job, init_db
from services.branch_scan_jobs import claim_next_branch_scan_job
from services.audit_records import record_audit_result
from services.cloud_worker import _message_still_authorized, _process_message
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
from services.queue import LocalSQLiteQueue, close_queue_backend
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
