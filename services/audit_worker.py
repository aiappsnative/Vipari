from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from engine.analysis import DiffAnalysis, analyze_diff
from engine.semantic_review import build_semantic_review_packages, format_semantic_review_packages
from .audit_jobs import (
    AuditJob,
    claim_next_job,
    mark_job_completed,
    mark_job_failed,
    mark_job_fallback_posted,
    mark_job_retry,
)
from .audit_records import get_latest_audit_comment_for_pr, record_audit_result
from .github_integration import fetch_file_content, generate_jwt, get_installation_token, upsert_pr_comment


@dataclass(frozen=True)
class WorkerSettings:
    db_path: str
    github_app_id: str
    github_private_key_path: str
    llm_client: object
    model: str
    llm_timeout_seconds: float = 30.0
    max_attempts: int = 5
    max_retry_window_seconds: float = 5400.0
    poll_interval_seconds: float = 2.0


RISK_BADGES = {
    "Low": "✅ Risk: Low",
    "Medium": "⚠️ Risk: Medium",
    "High": "❌ Risk: High",
}


def build_llm_comment(diff_text: str, deterministic_analysis: DiffAnalysis, *, llm_client: object, model: str, timeout_seconds: float) -> str:
    semantic_packages = build_semantic_review_packages(deterministic_analysis)
    system_prompt = (
        "You are an AI Security Auditor. Analyze this code diff. "
        "You will receive deterministic pre-analysis findings, structured semantic review packages, and the raw diff. "
        "Use the semantic review packages as the primary review frame, use deterministic findings as grounding evidence, and use the raw diff as reference detail. "
        "Return concise reviewer notes in Markdown. "
        "Include a one-sentence line in the form 'Summary: ...' describing what changed and why the risk level fits. "
        "Include an explicit line in the form 'Risk Level: Low|Medium|High'. "
        "Include a short 'Recommendation:' line. "
        "Keep the detailed section compact and do not use code fences."
    )
    response = llm_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"{deterministic_analysis.format_for_prompt()}\n\n"
                    f"{format_semantic_review_packages(semantic_packages)}\n\n"
                    f"Raw diff:\n{diff_text}"
                ),
            },
        ],
        temperature=0.0,
        timeout=timeout_seconds,
    )
    raw_comment = response.choices[0].message.content or "Audit failed: empty response from AI model."
    risk_level = _extract_risk_level(raw_comment, default=deterministic_analysis.suggested_risk_level.value)
    summary = _extract_summary(
        raw_comment,
        default=_build_fallback_summary(deterministic_analysis),
    )
    return _format_comment_body(raw_comment, risk_level=risk_level, review_mode="Full semantic review", summary=summary)


def build_fallback_comment(deterministic_analysis: DiffAnalysis, *, error_message: str) -> str:
    summary = _build_fallback_summary(deterministic_analysis)
    lines = [
        "## PromptDrift Preliminary Audit",
        "",
        f"Risk Level: **{deterministic_analysis.suggested_risk_level.value}**",
        "",
        "This review is based on deterministic risk signals while semantic review is still pending or unavailable.",
        "",
        "### Deterministic findings",
    ]

    if not deterministic_analysis.findings:
        lines.append("- AI-relevant files were detected, but no deterministic rule findings were triggered.")
    else:
        for finding in deterministic_analysis.findings:
            evidence = "; ".join(finding.evidence[:2]) if finding.evidence else "no evidence excerpt"
            lines.append(f"- **{finding.severity.value}** `{finding.rule_id}`: {finding.title} — {evidence}")

    lines.extend(
        [
            "",
            "### Suggested reviewer action",
            "Review the changed AI artifacts directly. Further semantic review may refine this assessment when model capacity is available.",
        ]
    )
    return _format_comment_body(
        "\n".join(lines),
        risk_level=deterministic_analysis.suggested_risk_level.value,
        review_mode="Deterministic fallback review",
        summary=summary,
    )


def _format_comment_body(detail_markdown: str, *, risk_level: str, review_mode: str, summary: str) -> str:
    normalized_risk = _normalize_risk_level(risk_level)
    badge = RISK_BADGES[normalized_risk]
    cleaned_details = _sanitize_detail_markdown(detail_markdown)
    return "\n".join(
        [
            f"{badge} — {summary}",
            "",
            "<details>",
            f"<summary>{review_mode} details</summary>",
            "",
            cleaned_details,
            "",
            "</details>",
        ]
    )


def _sanitize_detail_markdown(detail_markdown: str) -> str:
    cleaned = detail_markdown.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            cleaned = "\n".join(lines[1:-1]).strip()
    cleaned = _remove_duplicate_summary_lines(cleaned)
    return _remove_duplicate_risk_level_lines(cleaned)


def _remove_duplicate_summary_lines(detail_markdown: str) -> str:
    lines = detail_markdown.splitlines()
    cleaned_lines: list[str] = []

    for line in lines:
        if _is_standalone_summary_line(line):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def _remove_duplicate_risk_level_lines(detail_markdown: str) -> str:
    lines = detail_markdown.splitlines()
    seen_risk_level = False
    cleaned_lines: list[str] = []

    for line in lines:
        if _is_standalone_risk_level_line(line):
            if seen_risk_level:
                continue
            seen_risk_level = True
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def _is_standalone_risk_level_line(line: str) -> bool:
    normalized = line.strip()
    return bool(
        re.match(r"^(\*\*)?risk level(\*\*)?\s*[:\-]\s*(\*\*)?(low|medium|high)(\*\*)?\.?$", normalized, re.IGNORECASE)
    )


def _is_standalone_summary_line(line: str) -> bool:
    normalized = line.strip()
    return bool(re.match(r"^(\*\*)?summary(\*\*)?\s*[:\-]\s*.+$", normalized, re.IGNORECASE))


def _extract_summary(comment_body: str, *, default: str) -> str:
    summary_patterns = [
        r"^#{0,6}\s*summary\s*[:\-]\s*(.+)$",
        r"^\*\*summary\*\*\s*[:\-]\s*(.+)$",
        r"^\*\*summary\s*[:\-]\*\*\s*(.+)$",
    ]
    for raw_line in comment_body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for pattern in summary_patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                return _normalize_summary(match.group(1), default=default)

    skip_patterns = (
        r"^#{1,6}\s*summary\s*$",
        r"^#{1,6}\s*reviewer notes\s*$",
        r"^summary\s*$",
        r"^reviewer notes\s*$",
        r"^risk level\s*[:\-]",
        r"^\*\*risk level\*\*\s*[:\-]",
        r"^recommendation\s*[:\-]",
        r"^\*\*recommendation\*\*\s*[:\-]",
        r"^detailed analysis\s*[:\-]?$",
        r"^\*\*detailed analysis\*\*\s*[:\-]?$",
    )
    for raw_line in comment_body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        normalized = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if not normalized:
            continue
        if any(re.match(pattern, normalized, re.IGNORECASE) for pattern in skip_patterns):
            continue
        return _normalize_summary(normalized, default=default)

    return _normalize_summary(default, default=default)


def _normalize_summary(summary: str, *, default: str) -> str:
    cleaned = re.sub(r"\s+", " ", summary).strip(" -*_`\t\r\n")
    cleaned = re.sub(r"^\*{0,2}summary\*{0,2}\s*[:\-]?\*{0,2}\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\*\*(.+?)\*\*$", r"\1", cleaned)
    cleaned = cleaned.rstrip(".")
    if not cleaned:
        cleaned = default.rstrip(".")
    return cleaned + "."


def _build_fallback_summary(deterministic_analysis: DiffAnalysis) -> str:
    risk_level = _normalize_risk_level(deterministic_analysis.suggested_risk_level.value).lower()
    if deterministic_analysis.findings:
        primary_finding = deterministic_analysis.findings[0]
        return (
            f"{primary_finding.title} was detected, driving a {risk_level} risk assessment"
        )

    artifact_count = len(deterministic_analysis.artifacts)
    if artifact_count == 0:
        return f"No AI-relevant artifacts were identified, so the change remains {risk_level} risk"
    if artifact_count == 1:
        artifact = deterministic_analysis.artifacts[0].relevance.path
        return f"AI-relevant changes were found in {artifact}, so the change remains {risk_level} risk"
    return f"AI-relevant changes were found across {artifact_count} artifacts, so the change remains {risk_level} risk"


def _extract_risk_level(comment_body: str, *, default: str) -> str:
    match = re.search(r"risk level\s*[:\-]\s*\**(low|medium|high)\**", comment_body, re.IGNORECASE)
    if not match:
        return _normalize_risk_level(default)
    return _normalize_risk_level(match.group(1))


def _normalize_risk_level(risk_level: str) -> str:
    lowered = risk_level.strip().lower()
    if lowered == "low":
        return "Low"
    if lowered == "medium":
        return "Medium"
    return "High"


def _is_retryable_llm_error(exc: Exception) -> bool:
    return isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError))


def _retry_delay_seconds(attempt_count: int) -> int:
    schedule = {1: 120, 2: 600, 3: 1800, 4: 3600}
    return schedule.get(attempt_count, 3600)


def _extract_retry_after_seconds(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None

    retry_after_ms = headers.get("retry-after-ms") if hasattr(headers, "get") else None
    if retry_after_ms:
        try:
            return max(1, int(float(retry_after_ms) / 1000))
        except (TypeError, ValueError):
            return None

    retry_after = headers.get("retry-after") if hasattr(headers, "get") else None
    if retry_after:
        try:
            return max(1, int(float(retry_after)))
        except (TypeError, ValueError):
            return None

    return None


def _should_retry(job: AuditJob, settings: WorkerSettings) -> bool:
    if job.attempt_count >= settings.max_attempts:
        return False
    job_age_seconds = max(0.0, time.time() - job.created_at)
    return job_age_seconds < settings.max_retry_window_seconds


def _get_installation_token_for_job(job: AuditJob, settings: WorkerSettings) -> str:
    jwt_token = generate_jwt(settings.github_app_id, settings.github_private_key_path)
    return get_installation_token(jwt_token, job.installation_id)


def _post_comment_for_job(job: AuditJob, body: str, settings: WorkerSettings, *, installation_token: str | None = None) -> int:
    token = installation_token or _get_installation_token_for_job(job, settings)
    existing_comment = get_latest_audit_comment_for_pr(settings.db_path, job.repo_full, job.pr_number)
    return upsert_pr_comment(
        job.repo_full,
        job.pr_number,
        token,
        body,
        existing_comment_id=existing_comment.github_comment_id if existing_comment is not None else None,
    )


def _fetch_artifact_snapshots(job: AuditJob, deterministic_analysis: DiffAnalysis, settings: WorkerSettings) -> dict[str, str]:
    if not deterministic_analysis.artifacts:
        return {}

    try:
        installation_token = _get_installation_token_for_job(job, settings)
    except Exception:
        return {}

    snapshots: dict[str, str] = {}
    for artifact in deterministic_analysis.artifacts:
        try:
            snapshots[artifact.relevance.path] = fetch_file_content(
                job.repo_full,
                artifact.relevance.path,
                installation_token,
                ref=job.head_sha,
            )
        except Exception:
            continue
    return snapshots


def _persist_audit_result(
    job: AuditJob,
    deterministic_analysis: DiffAnalysis,
    settings: WorkerSettings,
    *,
    status: str,
    completion_mode: str,
    output_mode: str,
    comment_body: str | None,
    comment_mode: str | None,
    semantic_review_completed: bool,
    error_message: str | None = None,
    github_comment_id: int | None = None,
) -> None:
    record_audit_result(
        settings.db_path,
        job_id=job.id,
        repo_full=job.repo_full,
        pr_number=job.pr_number,
        installation_id=job.installation_id,
        head_sha=job.head_sha,
        deterministic_analysis=deterministic_analysis,
        status=status,
        completion_mode=completion_mode,
        output_mode=output_mode,
        comment_body=comment_body,
        comment_mode=comment_mode,
        semantic_review_completed=semantic_review_completed,
        error_message=error_message,
        artifact_snapshots=_fetch_artifact_snapshots(job, deterministic_analysis, settings),
        github_comment_id=github_comment_id,
    )


def _handle_fallback(job: AuditJob, settings: WorkerSettings, deterministic_analysis: DiffAnalysis, *, error_message: str) -> str:
    fallback_comment = build_fallback_comment(deterministic_analysis, error_message=error_message)
    try:
        github_comment_id = _post_comment_for_job(job, fallback_comment, settings)
    except Exception as fallback_exc:
        combined_error = f"{error_message}; fallback post failed: {type(fallback_exc).__name__}: {fallback_exc}"
        try:
            _persist_audit_result(
                job,
                deterministic_analysis,
                settings,
                status="failed",
                completion_mode="failed",
                output_mode="no_comment",
                comment_body=None,
                comment_mode=None,
                semantic_review_completed=False,
                error_message=combined_error,
            )
        except Exception as persist_exc:
            combined_error = (
                f"{combined_error}; persistence failed: {type(persist_exc).__name__}: {persist_exc}"
            )
        mark_job_failed(settings.db_path, job.id, error_message=combined_error)
        return "failed"

    try:
        _persist_audit_result(
            job,
            deterministic_analysis,
            settings,
            status="fallback_posted",
            completion_mode="fallback_posted",
            output_mode="preliminary_fallback",
            comment_body=fallback_comment,
            comment_mode="preliminary_fallback",
            semantic_review_completed=False,
            error_message=error_message,
            github_comment_id=github_comment_id,
        )
    except Exception as persist_exc:
        combined_error = (
            f"{error_message}; persistence failed after fallback comment post: {type(persist_exc).__name__}: {persist_exc}"
        )
        mark_job_failed(settings.db_path, job.id, error_message=combined_error)
        return "failed"

    mark_job_fallback_posted(
        settings.db_path,
        job.id,
        comment_body=fallback_comment,
        error_message=error_message,
    )
    return "fallback_posted"


def process_job(job: AuditJob, settings: WorkerSettings) -> str:
    deterministic_analysis = analyze_diff(job.diff_text)
    try:
        comment_body = build_llm_comment(
            job.diff_text,
            deterministic_analysis,
            llm_client=settings.llm_client,
            model=settings.model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        if _is_retryable_llm_error(exc) and _should_retry(job, settings):
            retry_delay_seconds = _extract_retry_after_seconds(exc) or _retry_delay_seconds(job.attempt_count)
            retry_at = time.time() + retry_delay_seconds
            mark_job_retry(settings.db_path, job.id, error_message=error_message, retry_at=retry_at)
            return "retry_wait"

        return _handle_fallback(job, settings, deterministic_analysis, error_message=error_message)

    try:
        github_comment_id = _post_comment_for_job(job, comment_body, settings)
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        return _handle_fallback(job, settings, deterministic_analysis, error_message=error_message)

    try:
        _persist_audit_result(
            job,
            deterministic_analysis,
            settings,
            status="completed",
            completion_mode="completed",
            output_mode="full_review",
            comment_body=comment_body,
            comment_mode="full_review",
            semantic_review_completed=True,
            github_comment_id=github_comment_id,
        )
    except Exception as persist_exc:
        error_message = f"Persistence failure after comment post: {type(persist_exc).__name__}: {persist_exc}"
        mark_job_failed(settings.db_path, job.id, error_message=error_message)
        return "failed"

    mark_job_completed(settings.db_path, job.id, comment_body=comment_body)
    return "completed"


def process_next_job_once(settings: WorkerSettings) -> bool:
    job = claim_next_job(settings.db_path)
    if job is None:
        return False
    process_job(job, settings)
    return True


class AuditWorker:
    def __init__(self, settings: WorkerSettings):
        self.settings = settings
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="promptdrift-audit-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            processed = process_next_job_once(self.settings)
            if not processed:
                self._stop_event.wait(self.settings.poll_interval_seconds)
