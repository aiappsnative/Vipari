from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from config import get_settings
from .audit_jobs import init_db
from .cloud_common import build_webhook_envelope, verify_signature
from .observability import configure_logging, instrument_fastapi
from .control_plane_records import apply_github_installation_lifecycle_event, apply_github_installation_repository_event
from .queue import LocalSQLiteQueue, QueueBackend, RedisQueue, SQSQueue, close_queue_backend
from .runtime_guardrails import build_runtime_readiness, readiness_json_response, validate_runtime_configuration
from .webhook_deliveries import (
    claim_webhook_delivery,
    cleanup_webhook_deliveries,
    init_webhook_delivery_db,
    mark_webhook_delivery_enqueued,
    mark_webhook_delivery_pending,
)


def build_queue_backend(settings) -> QueueBackend:
    if settings.queue_backend == "sqs":
        return SQSQueue(settings.sqs_queue_url, settings.sqs_dlq_url)
    if settings.queue_backend == "redis":
        return RedisQueue(settings.redis_url)
    return LocalSQLiteQueue(settings.resolved_db_path)


def create_webhook_app(queue_backend: QueueBackend | None = None) -> FastAPI:
    settings = get_settings()
    db_path = settings.resolved_db_path
    queue = queue_backend or build_queue_backend(settings)
    logger = configure_logging("webhook-ingress")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        validate_runtime_configuration(settings)
        if not settings.github_webhook_secret:
            raise RuntimeError("GITHUB_WEBHOOK_SECRET must be configured for the webhook service.")
        init_db(db_path)
        init_webhook_delivery_db(db_path)
        cleanup_webhook_deliveries(db_path)
        try:
            yield
        finally:
            await close_queue_backend(queue)

    app = FastAPI(lifespan=lifespan)
    instrument_fastapi(app)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service_role": settings.service_role}

    @app.get("/health/ready")
    async def ready():
        return readiness_json_response(await build_runtime_readiness(settings, queue_backend=queue))

    @app.post("/webhook")
    async def webhook(request: Request):
        body = await request.body()
        if not verify_signature(settings.github_webhook_secret, body, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=400, detail="Invalid signature")

        event = request.headers.get("X-GitHub-Event", "")
        if event not in {"pull_request", "push", "installation", "installation_repositories"}:
            return JSONResponse({"message": "ignored"}, status_code=202)

        delivery_id = request.headers.get("X-GitHub-Delivery")
        if not delivery_id:
            raise HTTPException(status_code=400, detail="Missing delivery id")

        if not claim_webhook_delivery(db_path, delivery_id, event):
            logger.info("Ignored duplicate webhook delivery", extra={"delivery_id": delivery_id})
            return JSONResponse({"message": "duplicate ignored"}, status_code=202)

        payload = json.loads(body.decode("utf-8"))
        if event == "installation":
            action = str(payload.get("action") or "").strip().lower()
            installation = payload.get("installation") or {}
            installation_id = installation.get("id")
            if not installation_id:
                raise HTTPException(status_code=400, detail="Missing installation id")
            account = installation.get("account") if isinstance(installation, dict) else {}
            updated_installation = apply_github_installation_lifecycle_event(
                db_path,
                installation_id=int(installation_id),
                action=action,
                account_id=str(account.get("id") or "") if isinstance(account, dict) else "",
                account_login=str(account.get("login") or "") if isinstance(account, dict) else "",
                account_type=str(account.get("type") or "Organization") if isinstance(account, dict) else "Organization",
                target_type=str(payload.get("target_type") or "Organization"),
            )
            mark_webhook_delivery_enqueued(db_path, delivery_id)
            if updated_installation is None:
                return JSONResponse({"message": "ignored"}, status_code=202)
            logger.info(
                "Processed installation lifecycle event",
                extra={"delivery_id": delivery_id, "installation_id": installation_id, "status": updated_installation.status},
            )
            return JSONResponse({"message": "installation status updated", "status": updated_installation.status}, status_code=202)
        if event == "installation_repositories":
            installation = payload.get("installation") or {}
            installation_id = installation.get("id")
            if not installation_id:
                raise HTTPException(status_code=400, detail="Missing installation id")
            result = apply_github_installation_repository_event(
                db_path,
                installation_id=int(installation_id),
                repositories_added=payload.get("repositories_added") if isinstance(payload.get("repositories_added"), list) else [],
                repositories_removed=payload.get("repositories_removed") if isinstance(payload.get("repositories_removed"), list) else [],
            )
            mark_webhook_delivery_enqueued(db_path, delivery_id)
            if result is None:
                return JSONResponse({"message": "ignored"}, status_code=202)
            logger.info(
                "Processed installation repository event",
                extra={
                    "delivery_id": delivery_id,
                    "installation_id": installation_id,
                    "connected_repo_count": result["connected_repo_count"],
                    "deactivated_allocation_count": result["deactivated_allocation_count"],
                },
            )
            return JSONResponse({"message": "installation repositories updated", **result}, status_code=202)

        envelope = build_webhook_envelope(payload, delivery_id=delivery_id)
        if envelope is None:
            mark_webhook_delivery_enqueued(db_path, delivery_id)
            return JSONResponse({"message": "ignored"}, status_code=202)

        try:
            message_id = await queue.enqueue(envelope)
        except Exception:
            mark_webhook_delivery_pending(db_path, delivery_id)
            logger.exception("Failed to enqueue webhook delivery", extra={"delivery_id": delivery_id})
            raise
        mark_webhook_delivery_enqueued(db_path, delivery_id)
        logger.info("Enqueued webhook delivery", extra={"delivery_id": delivery_id, "job_id": message_id})
        return JSONResponse({"message": "queued", "message_id": message_id}, status_code=202)

    return app
