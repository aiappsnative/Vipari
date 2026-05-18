from __future__ import annotations

import asyncio
import time
from urllib.error import URLError

from github.GithubException import GithubException
from openai import OpenAI
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from config import Settings, get_settings
from engine.analysis import analyze_diff
from .audit_jobs import claim_job_by_id, create_audit_job, get_job, init_db, update_job_pr_state
from .branch_scan_jobs import create_branch_scan_job
from .branch_scan_worker import BranchScanWorkerSettings, process_next_branch_scan_job_once
from .audit_records import (
    has_completed_audit,
    list_pull_request_audits_for_repo,
    record_audit_result,
    record_pr_outcome_feedback_events,
    update_pull_request_audit_state,
)
from .audit_worker import WorkerSettings, process_job
from .cloud_common import evaluate_and_persist_audit_decision, fetch_diff_with_retry, is_transient_error
from .control_plane_records import (
    count_workspaces,
    get_github_installation_by_installation_id,
    get_repo_allocation_for_installation,
    get_workspace_by_id,
    get_workspace_entitlement,
)
from .github_integration import (
    fetch_pull_request_lifecycle,
    generate_jwt,
    get_installation_token as request_installation_token,
)
from .onboarding_records import get_latest_repository_onboarding, list_latest_repository_onboardings
from .observability import configure_logging
from .queue import LocalSQLiteQueue, QueueBackend, QueueMessage, RedisQueue, SQSQueue, close_queue_backend
from .runtime_guardrails import validate_runtime_configuration
from .token_cache import get_installation_token, set_installation_token
from .webhook_deliveries import cleanup_webhook_deliveries


JOBS_PROCESSED = Counter("driftguard_jobs_processed_total", "Processed worker jobs", ["status"])
JOB_DURATION = Histogram("driftguard_job_duration_seconds", "Worker phase duration", ["phase"])
QUEUE_DEPTH = Gauge("driftguard_queue_depth", "Current queue depth")
OPENAI_TOKENS = Counter("driftguard_openai_tokens_used_total", "Estimated OpenAI tokens used")
BASE_RETRY_DELAY_SECONDS = 5
MAX_RETRY_DELAY_SECONDS = 300
CHARS_PER_TOKEN_ESTIMATE = 4
PULL_REQUEST_LIFECYCLE_RECONCILE_SECONDS = 300


def build_queue_backend(settings: Settings) -> QueueBackend:
    if settings.queue_backend == "sqs":
        return SQSQueue(settings.sqs_queue_url, settings.sqs_dlq_url)
    if settings.queue_backend == "redis":
        return RedisQueue(settings.redis_url)
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


def _get_latest_onboarding_if_available(db_path: str, repo_full: str):
    try:
        return get_latest_repository_onboarding(db_path, repo_full)
    except Exception:
        return None


def _ensure_lifecycle_only_audit_for_repo(payload: dict[str, object], settings: Settings) -> None:
    repo_full = str(payload["repo_full"])
    pr_number = int(payload["pr_number"])
    normalized_head_sha = str(payload.get("head_sha") or "").strip()
    if not normalized_head_sha:
        return

    existing = [
        audit
        for audit in list_pull_request_audits_for_repo(settings.resolved_db_path, repo_full)
        if audit.pr_number == pr_number and audit.head_sha == normalized_head_sha
    ]
    if existing:
        return

    lifecycle_job = create_audit_job(
        settings.resolved_db_path,
        repo_full=repo_full,
        pr_number=pr_number,
        pr_title=str(payload.get("pr_title") or "") or None,
        installation_id=int(payload["installation_id"]),
        head_sha=normalized_head_sha,
        diff_text="",
        pr_state=str(payload.get("pr_state") or "") or None,
        pr_merged=bool(payload.get("pr_merged")) if payload.get("pr_merged") is not None else None,
        pr_closed_at=payload.get("pr_closed_at"),
        pr_merged_at=payload.get("pr_merged_at"),
        pr_merge_commit_sha=str(payload.get("pr_merge_commit_sha") or "") or None,
        pr_updated_at=payload.get("pr_updated_at"),
    )
    record_audit_result(
        settings.resolved_db_path,
        job_id=lifecycle_job.id,
        repo_full=repo_full,
        pr_number=pr_number,
        pr_title=str(payload.get("pr_title") or "") or None,
        installation_id=int(payload["installation_id"]),
        head_sha=normalized_head_sha,
        pr_state=str(payload.get("pr_state") or "") or None,
        pr_merged=bool(payload.get("pr_merged")) if payload.get("pr_merged") is not None else None,
        pr_closed_at=payload.get("pr_closed_at"),
        pr_merged_at=payload.get("pr_merged_at"),
        pr_merge_commit_sha=str(payload.get("pr_merge_commit_sha") or "") or None,
        pr_updated_at=payload.get("pr_updated_at"),
        deterministic_analysis=analyze_diff(""),
        status="completed",
        completion_mode="lifecycle_only",
        output_mode="lifecycle_tracking",
        comment_body=None,
        comment_mode=None,
        semantic_review_completed=False,
        suggested_risk_level="unknown",
        fused_confidence=None,
    )


def _message_still_authorized(payload: dict[str, object], settings: Settings) -> bool:
    if not _control_plane_active(settings.resolved_db_path):
        return False

    event_type = str(payload.get("event_type") or "")
    action = str(payload.get("action") or "").lower()
    allocation = get_repo_allocation_for_installation(
        settings.resolved_db_path,
        int(payload["installation_id"]),
        str(payload["repo_full"]),
    )
    onboarding = _get_latest_onboarding_if_available(settings.resolved_db_path, str(payload["repo_full"]))
    installation = get_github_installation_by_installation_id(settings.resolved_db_path, int(payload["installation_id"]))

    if event_type == "push":
        if allocation is None:
            return False
        entitlement = get_workspace_entitlement(settings.resolved_db_path, allocation.workspace_id)
        return entitlement is not None and bool(entitlement.dashboard_enabled)

    if action in {"closed", "reopened"}:
        workspace_id = allocation.workspace_id if allocation is not None else installation.workspace_id if installation is not None else None
        if workspace_id is None or (allocation is None and onboarding is None):
            return False
        entitlement = get_workspace_entitlement(settings.resolved_db_path, workspace_id)
        return entitlement is not None and bool(entitlement.dashboard_enabled)

    if allocation is None:
        return False

    entitlement = get_workspace_entitlement(settings.resolved_db_path, allocation.workspace_id)
    workspace = get_workspace_by_id(settings.resolved_db_path, allocation.workspace_id)
    if workspace is None or not workspace.pr_comments_setting_enabled:
        return False
    return entitlement is not None and bool(entitlement.pr_comments_enabled)


def _queue_merge_branch_scan(
    *,
    settings: Settings,
    repo_full: str,
    installation_id: int,
    commit_sha: str | None,
    base_ref: str | None,
    default_branch: str,
    triggered_by: str,
) -> None:
    normalized_commit_sha = str(commit_sha or "").strip()
    if not normalized_commit_sha:
        return
    branch_name = str(base_ref or "").strip() or default_branch
    create_branch_scan_job(
        settings.resolved_db_path,
        repo_full=repo_full,
        installation_id=installation_id,
        commit_sha=normalized_commit_sha,
        branch_ref=f"refs/heads/{branch_name}",
        triggered_by=triggered_by,
    )


async def _reconcile_pull_request_lifecycle_for_repo(
    repo_full: str,
    installation_id: int,
    default_branch: str,
    settings: Settings,
    logger=None,
) -> int:
    audits = list_pull_request_audits_for_repo(settings.resolved_db_path, repo_full)
    if not audits:
        return 0

    latest_by_pr_number = {}
    for audit in audits:
        existing = latest_by_pr_number.get(audit.pr_number)
        current_timestamp = audit.pr_updated_at or audit.updated_at
        existing_timestamp = (existing.pr_updated_at or existing.updated_at) if existing is not None else None
        if existing is None or current_timestamp >= existing_timestamp:
            latest_by_pr_number[audit.pr_number] = audit

    installation_token = await _get_installation_token_for_worker(installation_id, settings)
    reconciled_count = 0
    for audit in latest_by_pr_number.values():
        step = "resume-merged-scan"
        try:
            if audit.pr_merged and audit.pr_merge_commit_sha:
                _queue_merge_branch_scan(
                    settings=settings,
                    repo_full=repo_full,
                    installation_id=installation_id,
                    commit_sha=audit.pr_merge_commit_sha,
                    base_ref=None,
                    default_branch=default_branch,
                    triggered_by="pr_lifecycle_reconcile",
                )
                if audit.pr_state == "closed":
                    continue

            step = "fetch-pr-lifecycle"
            lifecycle = await asyncio.to_thread(fetch_pull_request_lifecycle, repo_full, audit.pr_number, installation_token)
            step = "compare-lifecycle-state"
            if lifecycle.pr_state == audit.pr_state and lifecycle.pr_merged == audit.pr_merged and lifecycle.pr_merge_commit_sha == audit.pr_merge_commit_sha:
                if lifecycle.pr_merged and lifecycle.pr_merge_commit_sha:
                    step = "queue-merge-scan-noop-refresh"
                    _queue_merge_branch_scan(
                        settings=settings,
                        repo_full=repo_full,
                        installation_id=installation_id,
                        commit_sha=lifecycle.pr_merge_commit_sha,
                        base_ref=lifecycle.base_ref,
                        default_branch=default_branch,
                        triggered_by="pr_lifecycle_reconcile",
                    )
                continue

            payload = {
                "repo_full": repo_full,
                "pr_number": audit.pr_number,
                "pr_title": lifecycle.pr_title,
                "installation_id": installation_id,
                "head_sha": lifecycle.head_sha,
                "pr_state": lifecycle.pr_state,
                "pr_merged": lifecycle.pr_merged,
                "pr_closed_at": lifecycle.pr_closed_at,
                "pr_merged_at": lifecycle.pr_merged_at,
                "pr_merge_commit_sha": lifecycle.pr_merge_commit_sha,
                "pr_updated_at": lifecycle.pr_updated_at,
            }
            if lifecycle.pr_merged:
                step = "ensure-lifecycle-only-audit"
                _ensure_lifecycle_only_audit_for_repo(payload, settings)
            step = "update-job-pr-state"
            update_job_pr_state(
                settings.resolved_db_path,
                repo_full=repo_full,
                pr_number=audit.pr_number,
                head_sha=lifecycle.head_sha,
                pr_title=lifecycle.pr_title,
                pr_state=lifecycle.pr_state,
                pr_merged=lifecycle.pr_merged,
                pr_closed_at=lifecycle.pr_closed_at,
                pr_merged_at=lifecycle.pr_merged_at,
                pr_merge_commit_sha=lifecycle.pr_merge_commit_sha,
                pr_updated_at=lifecycle.pr_updated_at,
            )
            step = "update-pull-request-audit-state"
            update_pull_request_audit_state(
                settings.resolved_db_path,
                repo_full=repo_full,
                pr_number=audit.pr_number,
                head_sha=lifecycle.head_sha,
                pr_title=lifecycle.pr_title,
                pr_state=lifecycle.pr_state,
                pr_merged=lifecycle.pr_merged,
                pr_closed_at=lifecycle.pr_closed_at,
                pr_merged_at=lifecycle.pr_merged_at,
                pr_merge_commit_sha=lifecycle.pr_merge_commit_sha,
                pr_updated_at=lifecycle.pr_updated_at,
            )
            step = "record-pr-outcome-feedback"
            record_pr_outcome_feedback_events(
                settings.resolved_db_path,
                repo_full=repo_full,
                pr_number=audit.pr_number,
                head_sha=lifecycle.head_sha,
                pr_state=lifecycle.pr_state,
                pr_merged=lifecycle.pr_merged,
            )
            if lifecycle.pr_merged and lifecycle.pr_merge_commit_sha:
                step = "queue-merge-branch-scan"
                _queue_merge_branch_scan(
                    settings=settings,
                    repo_full=repo_full,
                    installation_id=installation_id,
                    commit_sha=lifecycle.pr_merge_commit_sha,
                    base_ref=lifecycle.base_ref,
                    default_branch=default_branch,
                    triggered_by="pr_lifecycle_reconcile",
                )
            reconciled_count += 1
        except Exception:
            if logger is not None:
                logger.exception(
                    "Failed to reconcile PR lifecycle audit",
                    extra={
                        "repo": repo_full,
                        "pr_number": audit.pr_number,
                        "head_sha": audit.head_sha,
                        "installation_id": installation_id,
                        "step": step,
                    },
                )
            raise
    return reconciled_count


async def _reconcile_pull_request_lifecycle(settings: Settings, logger) -> int:
    total_reconciled = 0
    for onboarding in list_latest_repository_onboardings(settings.resolved_db_path):
        try:
            total_reconciled += await _reconcile_pull_request_lifecycle_for_repo(
                onboarding.repo_full,
                onboarding.installation_id,
                onboarding.default_branch,
                settings,
                logger=logger,
            )
        except Exception:
            logger.exception("Failed to reconcile PR lifecycle state", extra={"repo": onboarding.repo_full})
    return total_reconciled


async def _process_message(queue: QueueBackend, message: QueueMessage, settings: Settings, logger, llm_client) -> None:
    payload = message.payload
    if payload.get("event_type") == "push":
        commit_sha = payload.get("commit_sha")
        branch_ref = payload.get("branch_ref")
        if not commit_sha or not branch_ref:
            await queue.move_to_dlq(message.receipt_handle)
            JOBS_PROCESSED.labels(status="failed").inc()
            return
        create_branch_scan_job(
            settings.resolved_db_path,
            repo_full=str(payload["repo_full"]),
            installation_id=int(payload["installation_id"]),
            commit_sha=str(commit_sha),
            branch_ref=str(branch_ref),
            triggered_by=str(payload.get("triggered_by") or "push_webhook"),
        )
        JOBS_PROCESSED.labels(status="success").inc()
        await queue.ack(message.receipt_handle)
        return

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

    if payload["action"] in {"closed", "reopened"}:
        onboarding = _get_latest_onboarding_if_available(settings.resolved_db_path, str(repo_full))
        pr_title = str(payload.get("pr_title") or "") or None
        pr_state = str(payload.get("pr_state") or "") or None
        pr_merged = bool(payload.get("pr_merged")) if payload.get("pr_merged") is not None else None
        pr_merge_commit_sha = str(payload.get("pr_merge_commit_sha") or "") or None

        if onboarding is not None and payload["action"] == "closed" and pr_merged:
            _ensure_lifecycle_only_audit_for_repo(payload, settings)

        update_job_pr_state(
            settings.resolved_db_path,
            repo_full=str(repo_full),
            pr_number=int(pr_number),
            head_sha=str(head_sha) if head_sha else None,
            pr_title=pr_title,
            pr_state=pr_state,
            pr_merged=pr_merged,
            pr_closed_at=payload.get("pr_closed_at"),
            pr_merged_at=payload.get("pr_merged_at"),
            pr_merge_commit_sha=pr_merge_commit_sha,
            pr_updated_at=payload.get("pr_updated_at"),
        )
        update_pull_request_audit_state(
            settings.resolved_db_path,
            repo_full=str(repo_full),
            pr_number=int(pr_number),
            head_sha=str(head_sha) if head_sha else None,
            pr_title=pr_title,
            pr_state=pr_state,
            pr_merged=pr_merged,
            pr_closed_at=payload.get("pr_closed_at"),
            pr_merged_at=payload.get("pr_merged_at"),
            pr_merge_commit_sha=pr_merge_commit_sha,
            pr_updated_at=payload.get("pr_updated_at"),
        )
        record_pr_outcome_feedback_events(
            settings.resolved_db_path,
            repo_full=str(repo_full),
            pr_number=int(pr_number),
            head_sha=str(head_sha) if head_sha else None,
            pr_state=pr_state,
            pr_merged=pr_merged,
        )
        if payload["action"] == "closed" and pr_merged and pr_merge_commit_sha and onboarding is not None:
            _queue_merge_branch_scan(
                settings=settings,
                repo_full=str(repo_full),
                installation_id=int(payload["installation_id"]),
                commit_sha=pr_merge_commit_sha,
                base_ref=None,
                default_branch=onboarding.default_branch,
                triggered_by="pr_merged_webhook",
            )
            JOBS_PROCESSED.labels(status="success").inc()
        else:
            JOBS_PROCESSED.labels(status="skipped").inc()
        await queue.ack(message.receipt_handle)
        return

    if head_sha and has_completed_audit(settings.resolved_db_path, repo_full=repo_full, pr_number=pr_number, head_sha=head_sha):
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

    audit_decision = evaluate_and_persist_audit_decision(
        settings.resolved_db_path,
        repo_full=repo_full,
        pr_number=pr_number,
        head_sha=head_sha,
        diff_text=diff_text,
        llm_client=llm_client,
        model=settings.ai_model,
        timeout_seconds=min(settings.llm_timeout_seconds, 5.0),
        provider=settings.resolved_ai_provider.value,
    )

    if not audit_decision.should_audit:
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
    validate_runtime_configuration(settings)
    if not settings.ai_api_key:
        raise RuntimeError("Configured AI provider credentials are missing for the worker service.")
    logger = configure_logging("worker")
    queue = queue_backend or build_queue_backend(settings)
    llm_client = OpenAI(api_key=settings.ai_api_key, base_url=settings.ai_base_url)

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

    async def branch_scan_loop() -> None:
        branch_scan_settings = BranchScanWorkerSettings(
            db_path=settings.resolved_db_path,
            github_app_id=settings.github_app_id,
            github_private_key_path=settings.github_private_key_path,
            github_app_private_key=settings.resolved_github_private_key,
            max_attempts=settings.audit_max_attempts,
            max_retry_window_seconds=settings.audit_max_retry_window_seconds,
            poll_interval_seconds=settings.audit_worker_poll_seconds,
        )
        while True:
            processed = await asyncio.to_thread(process_next_branch_scan_job_once, branch_scan_settings)
            if processed:
                continue
            await asyncio.sleep(settings.audit_worker_poll_seconds)

    async def pr_lifecycle_reconcile_loop() -> None:
        while True:
            await _reconcile_pull_request_lifecycle(settings, logger)
            await asyncio.sleep(PULL_REQUEST_LIFECYCLE_RECONCILE_SECONDS)

    workers = [asyncio.create_task(worker_loop()) for _ in range(max(1, settings.worker_concurrency))]
    workers.append(asyncio.create_task(queue_depth_poller()))
    workers.append(asyncio.create_task(branch_scan_loop()))
    workers.append(asyncio.create_task(pr_lifecycle_reconcile_loop()))
    try:
        await asyncio.gather(*workers)
    finally:
        for worker in workers:
            worker.cancel()
        await close_queue_backend(queue)
