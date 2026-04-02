from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from config import get_settings
from .cloud_common import build_webhook_envelope, verify_signature
from .observability import configure_logging, instrument_fastapi
from .queue import LocalSQLiteQueue, QueueBackend, SQSQueue
from .webhook_deliveries import (
    cleanup_webhook_deliveries,
    init_webhook_delivery_db,
    mark_webhook_delivery_enqueued,
    register_webhook_delivery,
)


def build_queue_backend(settings) -> QueueBackend:
    if settings.queue_backend == "sqs":
        return SQSQueue(settings.sqs_queue_url, settings.sqs_dlq_url)
    return LocalSQLiteQueue(settings.resolved_db_path)


def create_webhook_app(queue_backend: QueueBackend | None = None) -> FastAPI:
    settings = get_settings()
    db_path = settings.resolved_db_path
    queue = queue_backend or build_queue_backend(settings)
    logger = configure_logging("webhook-ingress")

    app = FastAPI()
    instrument_fastapi(app)

    @app.on_event("startup")
    async def startup() -> None:
        init_webhook_delivery_db(db_path)
        cleanup_webhook_deliveries(db_path)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(request: Request):
        body = await request.body()
        if not verify_signature(settings.github_webhook_secret, body, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=400, detail="Invalid signature")

        event = request.headers.get("X-GitHub-Event", "")
        if event != "pull_request":
            return JSONResponse({"message": "ignored"}, status_code=202)

        delivery_id = request.headers.get("X-GitHub-Delivery")
        if not delivery_id:
            raise HTTPException(status_code=400, detail="Missing delivery id")

        if not register_webhook_delivery(db_path, delivery_id, event):
            logger.info("Ignored duplicate webhook delivery", extra={"delivery_id": delivery_id})
            return JSONResponse({"message": "duplicate ignored"}, status_code=202)

        payload = json.loads(body.decode("utf-8"))
        envelope = build_webhook_envelope(payload, delivery_id=delivery_id)
        if envelope is None:
            return JSONResponse({"message": "ignored"}, status_code=202)

        message_id = await queue.enqueue(envelope)
        mark_webhook_delivery_enqueued(db_path, delivery_id)
        logger.info("Enqueued webhook delivery", extra={"delivery_id": delivery_id, "job_id": message_id})
        return JSONResponse({"message": "queued", "message_id": message_id}, status_code=202)

    return app
