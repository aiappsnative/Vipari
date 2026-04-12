from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError

from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile

from .branch_scan_jobs import (
    BranchScanJob,
    claim_next_branch_scan_job,
    get_branch_scan_job,
    mark_branch_scan_job_completed,
    mark_branch_scan_job_failed,
    mark_branch_scan_job_retry,
)
from .github_integration import fetch_file_content, generate_jwt, get_installation_token
from .onboarding import sync_on_pr_merge_artifact_changes
from .onboarding_records import (
    HistoricalBackfillJobInput,
    HistoricalArtifactSnapshotInput,
    create_historical_backfill_jobs,
    get_latest_repository_onboarding,
    list_onboarded_artifacts_for_onboarding,
    record_historical_backfill_versions,
    update_historical_backfill_job_status,
)
from .repo_journey import materialize_repo_journey


@dataclass(frozen=True)
class BranchScanWorkerSettings:
    db_path: str
    github_app_id: str
    github_private_key_path: str
    github_app_private_key: str = ""
    max_attempts: int = 5
    max_retry_window_seconds: float = 5400.0
    poll_interval_seconds: float = 2.0


def process_branch_scan_job(job: BranchScanJob, settings: BranchScanWorkerSettings) -> str:
    onboarding = get_latest_repository_onboarding(settings.db_path, job.repo_full)
    if onboarding is None:
        mark_branch_scan_job_completed(settings.db_path, job.id)
        return "completed"

    artifacts = list_onboarded_artifacts_for_onboarding(settings.db_path, onboarding.id)
    if not artifacts:
        mark_branch_scan_job_completed(settings.db_path, job.id)
        materialize_repo_journey(settings.db_path, job.repo_full)
        return "completed"

    jwt_token = generate_jwt(
        settings.github_app_id,
        settings.github_private_key_path,
        settings.github_app_private_key,
    )
    token = get_installation_token(jwt_token, job.installation_id)

    created_any_profile = False
    removed_paths: set[str] = set()
    for artifact in artifacts:
        history_job = create_historical_backfill_jobs(
            settings.db_path,
            onboarding_id=onboarding.id,
            repo_full=job.repo_full,
            jobs=[
                HistoricalBackfillJobInput(
                    onboarded_artifact_id=artifact.id,
                    artifact_path=artifact.artifact_path,
                    artifact_type=artifact.artifact_type,
                    commit_shas=[job.commit_sha],
                )
            ],
            status="processing",
            job_kind="branch_scan",
        )[0]

        try:
            content = fetch_file_content(job.repo_full, artifact.artifact_path, token, ref=job.commit_sha)
        except HTTPError as exc:
            if exc.code == 404:
                removed_paths.add(artifact.artifact_path)
                update_historical_backfill_job_status(
                    settings.db_path,
                    job_id=history_job.id,
                    status="completed",
                    completed_commit_count=0,
                    last_error=None,
                )
                continue
            raise

        _versions, profiles = record_historical_backfill_versions(
            settings.db_path,
            backfill_job_id=history_job.id,
            onboarding_id=onboarding.id,
            onboarded_artifact_id=artifact.id,
            repo_full=job.repo_full,
            artifact_path=artifact.artifact_path,
            artifact_type=artifact.artifact_type,
            snapshots=[HistoricalArtifactSnapshotInput(commit_sha=job.commit_sha, content=content)],
            extract_signal_terms_fn=extract_signal_terms_from_text,
            build_profile_fn=build_attribute_profile,
            branch_ref=job.branch_ref,
            triggered_by=job.triggered_by,
        )
        created_any_profile = created_any_profile or bool(profiles)
        update_historical_backfill_job_status(
            settings.db_path,
            job_id=history_job.id,
            status="completed",
            completed_commit_count=len(profiles),
            last_error=None,
        )

    if removed_paths:
        sync_on_pr_merge_artifact_changes(
            settings.db_path,
            repo_full=job.repo_full,
            removed_paths=removed_paths,
        )
    else:
        materialize_repo_journey(settings.db_path, job.repo_full)
    mark_branch_scan_job_completed(settings.db_path, job.id)
    return "completed_with_updates" if created_any_profile else "completed"


def process_next_branch_scan_job_once(settings: BranchScanWorkerSettings) -> bool:
    job = claim_next_branch_scan_job(settings.db_path)
    if job is None:
        return False

    try:
        process_branch_scan_job(job, settings)
    except Exception as exc:
        saved = get_branch_scan_job(settings.db_path, job.id)
        attempt_count = saved.attempt_count if saved is not None else job.attempt_count
        if attempt_count >= settings.max_attempts:
            mark_branch_scan_job_failed(settings.db_path, job.id, error_message=f"{type(exc).__name__}: {exc}")
        else:
            retry_delay = min(settings.max_retry_window_seconds, float(2 ** max(0, attempt_count - 1)) * 5.0)
            mark_branch_scan_job_retry(
                settings.db_path,
                job.id,
                error_message=f"{type(exc).__name__}: {exc}",
                retry_at=time.time() + retry_delay,
            )
    return True


class BranchScanWorker:
    def __init__(self, settings: BranchScanWorkerSettings):
        self.settings = settings
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="branch-scan-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            processed = process_next_branch_scan_job_once(self.settings)
            if processed:
                continue
            self._stop_event.wait(self.settings.poll_interval_seconds)