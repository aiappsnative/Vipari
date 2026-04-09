from __future__ import annotations

import asyncio
import time
from urllib.error import URLError

from github.GithubException import GithubException
from openai import OpenAI
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from config import Settings, get_settings
from .audit_jobs import claim_job_by_id, create_audit_job, get_job, init_db
from .audit_records import has_completed_audit
from .audit_worker import WorkerSettings, process_job
from .cloud_common import fetch_diff_with_retry, is_transient_error, needs_audit
from .control_plane_records import count_workspaces, get_repo_allocation_for_installation, get_workspace_entitlement
from .github_integration import generate_jwt, get_installation_token as request_installation_token
from .observability import configure_logging
from .queue import LocalSQLiteQueue, QueueBackend, QueueMessage, SQSQueue
from .token_cache import get_installation_token, set_installation_token
from .webhook_deliveries import cleanup_webhook_deliveries


JOBS_PROCESSED = Counter("driftguard_jobs_processed_total", "Processed worker jobs", ["status"])
JOB_DURATION = Histogram("driftguard_job_duration_seconds", "Worker phase duration", ["phase"])
QUEUE_DEPTH = Gauge("driftguard_queue_depth", "Current queue depth")
OPENAI_TOKENS = Counter("driftguard_openai_tokens_used_total", "Estimated OpenAI tokens used")
BASE_RETRY_DELAY_SECONDS = 5
MAX_RETRY_DELAY_SECONDS = 300
CHARS_PER_TOKEN_ESTIMATE = 4


def build_queue_backend(settings: Settings) -> QueueBackend:
    if settings.queue_backend == "sqs":
        return SQSQueue(settings.sqs_queue_url, settings.sqs_dlq_url)
    return LocalSQLiteQueue(settings.resolved_db_path)


async def _get_installation_token_for_worker(installation_id: int, settings: Settings) -> str:
    cached = await get_installation_token(installation_id)
    if cached:
        return cached
    jwt_token = generate_jwt(
        settings.github_app_id,
        settings.github_private_key_path,
        settings.resolved_github_private_key,
    )
    token = request_installation_token(jwt_token, installation_id)
    await set_installation_token(installation_id, token, 60 * 60)
    return token


def _retry_delay_seconds(attempt_count: int) -> int:
    return min(MAX_RETRY_DELAY_SECONDS, BASE_RETRY_DELAY_SECONDS * (2 ** max(0, attempt_count - 1)))


def _estimate_token_count(text: str) -> int:
    # Intentional coarse heuristic for metrics only; DriftGuard does not currently ship a tokenizer dependency here.
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


async def _update_queue_depth(queue: QueueBackend) -> None:
    if hasattr(queue, "depth"):
        QUEUE_DEPTH.set(await queue.depth())  # type: ignore[attr-defined]


def _control_plane_active(db_path: str) -> bool:
    try:
        return count_workspaces(db_path) > 0
    except Exception:
        return False


def _message_still_authorized(payload: dict[str, object], settings: Settings) -> bool:
    if not _control_plane_active(settings.resolved_db_path):
        return True

    allocation = get_repo_allocation_for_installation(
        settings.resolved_db_path,
        int(payload["installation_id"]),
        str(payload["repo_full"]),
    )
    if allocation is None:
        return False

    entitlement = get_workspace_entitlement(settings.resolved_db_path, allocation.workspace_id)
    return entitlement is not None and bool(entitlement.pr_comments_enabled)


async def _process_message(queue: QueueBackend, message: QueueMessage, settings: Settings, logger, llm_client) -> None:
    payload = message.payload
    repo_full = payload["repo_full"]
    pr_number = payload["pr_number"]
    head_sha = payload.get("head_sha")

    if not _message_still_authorized(payload, settings):
        JOBS_PROCESSED.labels(status="skipped").inc()
        logger.info(
            "Skipped queued message after allocation or entitlement changed",
            extra={"repo": repo_full, "pr_number": pr_number, "installation_id": payload["installation_id"]},
        )
        await queue.ack(message.receipt_handle)
        return

    if head_sha and has_completed_audit(settings.resolved_db_path, repo_full=repo_full, pr_number=pr_number, head_sha=head_sha):
        JOBS_PROCESSED.labels(status="skipped").inc()
        await queue.ack(message.receipt_handle)
        return

    if payload["action"] in {"closed", "reopened"}:
        JOBS_PROCESSED.labels(status="skipped").inc()
        await queue.ack(message.receipt_handle)
        return

    if not head_sha:
        await queue.move_to_dlq(message.receipt_handle)
        JOBS_PROCESSED.labels(status="failed").inc()
        return

    try:
        with JOB_DURATION.labels(phase="fetch_diff").time():
            installation_token = await _get_installation_token_for_worker(payload["installation_id"], settings)
            diff_text = await fetch_diff_with_retry(
                repo_full,
                pr_number,
                installation_token,
                use_commit_pair=payload["action"] == "synchronize",
                base_sha=payload.get("base_sha"),
                head_sha=head_sha,
                attempts=settings.pr_diff_fetch_attempts,
                retry_seconds=settings.pr_diff_fetch_retry_seconds,
            )
    except Exception as exc:
        if is_transient_error(exc) and message.attempt_count < settings.worker_max_retries:
            await queue.nack(message.receipt_handle, _retry_delay_seconds(message.attempt_count))
            return
        await queue.move_to_dlq(message.receipt_handle)
        JOBS_PROCESSED.labels(status="failed").inc()
        logger.info("Moved job to DLQ after diff fetch failure", extra={"repo": repo_full, "pr_number": pr_number})
        return

    if not needs_audit(diff_text):
        JOBS_PROCESSED.labels(status="skipped").inc()
        await queue.ack(message.receipt_handle)
        return

    created_job = create_audit_job(
        settings.resolved_db_path,
        repo_full=repo_full,
        pr_number=pr_number,
        installation_id=payload["installation_id"],
        head_sha=head_sha,
        diff_text=diff_text,
        pr_state=payload.get("pr_state"),
        pr_merged=payload.get("pr_merged"),
        pr_closed_at=payload.get("pr_closed_at"),
        pr_merged_at=payload.get("pr_merged_at"),
        pr_merge_commit_sha=payload.get("pr_merge_commit_sha"),
        pr_updated_at=payload.get("pr_updated_at"),
    )
    job = claim_job_by_id(settings.resolved_db_path, created_job.id)
    if job is None:
        existing = get_job(settings.resolved_db_path, created_job.id)
        if existing is not None and existing.status == "completed":
            JOBS_PROCESSED.labels(status="skipped").inc()
            await queue.ack(message.receipt_handle)
            return
        await queue.nack(message.receipt_handle, _retry_delay_seconds(message.attempt_count))
        return

    with JOB_DURATION.labels(phase="run_engine").time():
        result = await asyncio.to_thread(
            process_job,
            job,
            WorkerSettings(
                db_path=settings.resolved_db_path,
                github_app_id=settings.github_app_id,
                github_private_key_path=settings.github_private_key_path,
                github_app_private_key=settings.resolved_github_private_key,
                llm_client=llm_client,
                model=settings.ai_model,
                llm_timeout_seconds=settings.llm_timeout_seconds,
                max_attempts=settings.audit_max_attempts,
                max_retry_window_seconds=settings.audit_max_retry_window_seconds,
                poll_interval_seconds=settings.audit_worker_poll_seconds,
            ),
        )

    OPENAI_TOKENS.inc(_estimate_token_count(diff_text))

    if result == "retry_wait":
        saved = get_job(settings.resolved_db_path, job.id)
        delay_seconds = _retry_delay_seconds(message.attempt_count)
        if saved is not None and saved.next_attempt_at > time.time():
            delay_seconds = max(1, int(saved.next_attempt_at - time.time()))
        await queue.nack(message.receipt_handle, delay_seconds)
        return

    status = "success" if result in {"completed", "fallback_posted"} else "failed"
    JOBS_PROCESSED.labels(status=status).inc()
    await queue.ack(message.receipt_handle)


async def run_worker(queue_backend: QueueBackend | None = None) -> None:
    settings = get_settings()
    if not settings.ai_api_key:
        raise RuntimeError("OPENAI_API_KEY or FOUNDRY_API_KEY must be configured for the worker service.")
    logger = configure_logging("worker")
    queue = queue_backend or build_queue_backend(settings)
    llm_client = OpenAI(api_key=settings.ai_api_key, base_url=settings.azure_openai_endpoint or None)

    init_db(settings.resolved_db_path)
    cleanup_webhook_deliveries(settings.resolved_db_path)
    if settings.enable_metrics:
        start_http_server(settings.worker_metrics_port)

    async def queue_depth_poller() -> None:
        while True:
            await _update_queue_depth(queue)
            await asyncio.sleep(30)

    async def worker_loop() -> None:
        while True:
            messages = await queue.dequeue(1)
            if not messages:
                await asyncio.sleep(1)
                continue
            for message in messages:
                try:
                    await _process_message(queue, message, settings, logger, llm_client)
                except (GithubException, URLError) as exc:
                    if is_transient_error(exc):
                        await queue.nack(message.receipt_handle, _retry_delay_seconds(message.attempt_count))
                    else:
                        await queue.move_to_dlq(message.receipt_handle)
                except Exception:
                    await queue.move_to_dlq(message.receipt_handle)
                    JOBS_PROCESSED.labels(status="failed").inc()

    workers = [asyncio.create_task(worker_loop()) for _ in range(max(1, settings.worker_concurrency))]
    workers.append(asyncio.create_task(queue_depth_poller()))
    try:
        await asyncio.gather(*workers)
    finally:
        for worker in workers:
            worker.cancel()
