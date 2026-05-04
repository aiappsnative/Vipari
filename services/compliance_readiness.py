from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .dashboard_views import RepoDashboardIndexEntry
from .export_jobs import ExportJob
from .onboarding_records import get_latest_repository_onboarding, list_onboarded_artifacts_for_onboarding
from .provenance_labels import artifact_family


_EXPORT_PENDING_STATUSES = {"queued", "processing", "retrying"}
_EXPORT_COMPLETED_STATUSES = {"completed"}
_GAP_PRIORITY = {
    "needs_setup": 0,
    "baseline_review": 1,
    "missing_governance": 2,
    "stale_evidence": 3,
    "aging_evidence": 4,
}


def _gap_cta_href(gap_key: str) -> str:
    return f"/app/compliance/evidence?gap={gap_key}"


@dataclass(frozen=True)
class ComplianceMetric:
    label: str
    value: str
    detail: str


@dataclass(frozen=True)
class ComplianceVerdict:
    tone: str
    headline: str
    detail: str
    cta_label: str
    cta_href: str


@dataclass(frozen=True)
class ComplianceGapItem:
    key: str
    title: str
    detail: str
    affected_count: int
    cta_label: str
    cta_href: str
    repo_fulls: tuple[str, ...]


@dataclass(frozen=True)
class ComplianceRepoReadinessRow:
    repo_full: str
    repo_href: str
    default_branch: str
    connection_status: str
    overall_label: str
    overall_tone: str
    baseline_label: str
    baseline_tone: str
    governance_label: str
    governance_tone: str
    freshness_label: str
    freshness_tone: str
    export_ready: bool
    action_label: str
    action_href: str
    action_detail: str
    gap_keys: tuple[str, ...]
    artifact_families: tuple[str, ...]
    has_ai_surface: bool
    has_model_surface: bool
    last_onboarded_at: float | None


@dataclass(frozen=True)
class ComplianceFrameworkCard:
    title: str
    status_label: str
    detail: str
    bullets: tuple[str, ...]


@dataclass(frozen=True)
class ComplianceEvidenceRow:
    repo_full: str
    repo_href: str
    freshness_label: str
    freshness_tone: str
    summary: str
    next_step: str
    gaps: tuple[str, ...]


@dataclass(frozen=True)
class ComplianceExportSummary:
    ready_repo_count: int
    completed_count: int
    pending_count: int
    failed_count: int
    latest_status_label: str
    latest_detail: str
    latest_download_href: str | None


@dataclass(frozen=True)
class ComplianceWorkspaceView:
    metrics: tuple[ComplianceMetric, ...]
    verdict: ComplianceVerdict
    top_gaps: tuple[ComplianceGapItem, ...]
    repo_rows: tuple[ComplianceRepoReadinessRow, ...]
    framework_cards: tuple[ComplianceFrameworkCard, ...]
    evidence_rows: tuple[ComplianceEvidenceRow, ...]
    export_summary: ComplianceExportSummary


def _freshness_payload(last_onboarded_at: float | None) -> tuple[str, str, int | None]:
    if not last_onboarded_at:
        return ("No evidence yet", "muted", None)
    age_days = max(int((__import__("time").time() - last_onboarded_at) / 86400), 0)
    if age_days >= 30:
        return (f"Stale ({age_days}d)", "danger", age_days)
    if age_days >= 7:
        return (f"Aging ({age_days}d)", "warning", age_days)
    return (f"Fresh ({age_days}d)", "success", age_days)


def _collect_artifact_families(db_path: str, repo_full: str) -> tuple[tuple[str, ...], float | None, str | None]:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        return tuple(), None, None
    families = sorted(
        {
            artifact_family(artifact.artifact_type)
            for artifact in list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
            if artifact.artifact_type
        }
    )
    return tuple(families), onboarding.updated_at, onboarding.status


def _repo_action(row_href: str, gap_keys: Sequence[str]) -> tuple[str, str, str]:
    if "needs_setup" in gap_keys:
        return ("Complete onboarding", row_href, "No stored baseline or evidence package yet.")
    if "baseline_review" in gap_keys:
        return ("Review baseline", row_href, "Approve the pending baseline before exporting evidence.")
    if "missing_governance" in gap_keys:
        return ("Add governance evidence", row_href, "Capture policy or approval artifacts for this repo.")
    if "stale_evidence" in gap_keys:
        return ("Refresh evidence", row_href, "Run a fresh onboarding pass so the evidence pack is current.")
    if "aging_evidence" in gap_keys:
        return ("Monitor freshness", row_href, "Evidence is still usable, but it is moving out of the fresh window.")
    return ("Open repo dashboard", row_href, "Review the current drift and evidence trail.")


def _overall_status(gap_keys: Sequence[str]) -> tuple[str, str]:
    if not gap_keys:
        return ("Ready", "success")
    if "needs_setup" in gap_keys or "baseline_review" in gap_keys:
        return ("Blocked", "danger")
    if "missing_governance" in gap_keys or "stale_evidence" in gap_keys:
        return ("Needs work", "warning")
    return ("Monitor", "muted")


def _build_repo_row(
    db_path: str,
    repo_row: dict[str, object],
    repo_summary: RepoDashboardIndexEntry | None,
) -> ComplianceRepoReadinessRow:
    repo_full = str(repo_row.get("repo_full") or "")
    repo_href = str(repo_row.get("href") or "#")
    default_branch = str(repo_row.get("branch") or "unknown")
    connection_status = str(repo_row.get("status") or "Unknown")
    artifact_families, last_onboarded_at, onboarding_status = _collect_artifact_families(db_path, repo_full)
    freshness_label, freshness_tone, _age_days = _freshness_payload(last_onboarded_at)

    gap_keys: list[str] = []
    if onboarding_status is None:
        baseline_label = "Not onboarded"
        baseline_tone = "danger"
        gap_keys.append("needs_setup")
    elif onboarding_status != "baseline_approved":
        baseline_label = "Baseline review pending"
        baseline_tone = "warning"
        gap_keys.append("baseline_review")
    else:
        baseline_label = "Approved"
        baseline_tone = "success"

    governance_present = "governance" in artifact_families
    if onboarding_status is None:
        governance_label = "No evidence yet"
        governance_tone = "muted"
    elif governance_present:
        governance_label = "Governance evidence present"
        governance_tone = "success"
    else:
        governance_label = "Missing governance evidence"
        governance_tone = "warning"
        gap_keys.append("missing_governance")

    if freshness_tone == "danger":
        gap_keys.append("stale_evidence")
    elif freshness_tone == "warning":
        gap_keys.append("aging_evidence")

    overall_label, overall_tone = _overall_status(gap_keys)
    action_label, action_href, action_detail = _repo_action(repo_href, gap_keys)
    has_ai_surface = "ai" in artifact_families or "runtime" in artifact_families
    has_model_surface = "model_config" in artifact_families or "model" in artifact_families
    export_ready = overall_label == "Ready"

    if repo_summary is not None and repo_summary.onboarding_status == "baseline_approved" and onboarding_status is None:
        baseline_label = "Approved"
        baseline_tone = "success"

    return ComplianceRepoReadinessRow(
        repo_full=repo_full,
        repo_href=repo_href,
        default_branch=default_branch,
        connection_status=connection_status,
        overall_label=overall_label,
        overall_tone=overall_tone,
        baseline_label=baseline_label,
        baseline_tone=baseline_tone,
        governance_label=governance_label,
        governance_tone=governance_tone,
        freshness_label=freshness_label,
        freshness_tone=freshness_tone,
        export_ready=export_ready,
        action_label=action_label,
        action_href=action_href,
        action_detail=action_detail,
        gap_keys=tuple(gap_keys),
        artifact_families=artifact_families,
        has_ai_surface=has_ai_surface,
        has_model_surface=has_model_surface,
        last_onboarded_at=last_onboarded_at,
    )


def _gap_items(repo_rows: Sequence[ComplianceRepoReadinessRow]) -> tuple[ComplianceGapItem, ...]:
    gap_repo_map: dict[str, list[str]] = {}
    for row in repo_rows:
        for gap_key in row.gap_keys:
            gap_repo_map.setdefault(gap_key, []).append(row.repo_full)

    labels = {
        "needs_setup": (
            "Complete onboarding",
            "Some connected repos still have no baseline or evidence history.",
            "Review repos",
            _gap_cta_href("needs_setup"),
        ),
        "baseline_review": (
            "Approve pending baselines",
            "Evidence exists, but the baseline still needs a human decision.",
            "Review baselines",
            _gap_cta_href("baseline_review"),
        ),
        "missing_governance": (
            "Add governance artifacts",
            "Policy or approval evidence is missing from the current review pack.",
            "Open evidence",
            _gap_cta_href("missing_governance"),
        ),
        "stale_evidence": (
            "Refresh stale evidence",
            "Stored evidence is older than the fresh-review window.",
            "Plan refresh",
            _gap_cta_href("stale_evidence"),
        ),
        "aging_evidence": (
            "Watch aging evidence",
            "Evidence is still valid, but it is drifting toward the stale threshold.",
            "Review freshness",
            _gap_cta_href("aging_evidence"),
        ),
    }
    items: list[ComplianceGapItem] = []
    for gap_key, repo_fulls in gap_repo_map.items():
        title, detail, cta_label, cta_href = labels[gap_key]
        items.append(
            ComplianceGapItem(
                key=gap_key,
                title=title,
                detail=detail,
                affected_count=len(repo_fulls),
                cta_label=cta_label,
                cta_href=cta_href,
                repo_fulls=tuple(sorted(repo_fulls)),
            )
        )
    items.sort(key=lambda item: (-item.affected_count, _GAP_PRIORITY[item.key], item.title))
    return tuple(items[:3])


def _build_verdict(repo_rows: Sequence[ComplianceRepoReadinessRow], top_gaps: Sequence[ComplianceGapItem]) -> ComplianceVerdict:
    repo_count = len(repo_rows)
    ready_count = sum(1 for row in repo_rows if row.export_ready)
    if repo_count == 0:
        return ComplianceVerdict(
            tone="muted",
            headline="No repositories are connected yet.",
            detail="Link GitHub repos to start building a compliance readiness view.",
            cta_label="Connect repositories",
            cta_href="/app/install",
        )
    if ready_count == repo_count:
        return ComplianceVerdict(
            tone="success",
            headline="The monitored repos are ready for export.",
            detail="Baseline approval, governance evidence, and freshness are all in the green window.",
            cta_label="Generate export",
            cta_href="/app/compliance/exports#new-export",
        )
    if ready_count == 0:
        primary_gap = top_gaps[0] if top_gaps else None
        return ComplianceVerdict(
            tone="danger",
            headline="The workspace is not export-ready yet.",
            detail=(primary_gap.detail if primary_gap is not None else "Resolve the blockers below before packaging evidence."),
            cta_label=(primary_gap.cta_label if primary_gap is not None else "Review evidence"),
            cta_href=(primary_gap.cta_href if primary_gap is not None else "/app/compliance/evidence"),
        )
    primary_gap = top_gaps[0] if top_gaps else None
    return ComplianceVerdict(
        tone="warning",
        headline=f"{ready_count} of {repo_count} monitored repos are ready right now.",
        detail=(primary_gap.detail if primary_gap is not None else "The workspace is partially ready. Clear the remaining blockers to stabilize export packs."),
        cta_label=(primary_gap.cta_label if primary_gap is not None else "Review readiness"),
        cta_href=(primary_gap.cta_href if primary_gap is not None else "/app/compliance/evidence"),
    )


def _framework_cards(repo_rows: Sequence[ComplianceRepoReadinessRow]) -> tuple[ComplianceFrameworkCard, ...]:
    repo_count = len(repo_rows)
    baseline_ready = sum(1 for row in repo_rows if row.baseline_tone == "success")
    governance_ready = sum(1 for row in repo_rows if row.governance_tone == "success")
    fresh_ready = sum(1 for row in repo_rows if row.freshness_tone == "success")
    ai_surface = sum(1 for row in repo_rows if row.has_ai_surface)
    model_surface = sum(1 for row in repo_rows if row.has_model_surface)
    return (
        ComplianceFrameworkCard(
            title="EU AI Act",
            status_label=("Ready to review" if ai_surface and governance_ready else "Needs evidence"),
            detail="Focuses on AI system scope, governance evidence, and baseline accountability.",
            bullets=(
                f"{ai_surface} repos expose AI or runtime control surfaces.",
                f"{governance_ready} repos already carry governance artifacts.",
                f"{baseline_ready} repos have an approved baseline trail.",
            ),
        ),
        ComplianceFrameworkCard(
            title="SOC 2",
            status_label=("Operational" if baseline_ready and fresh_ready else "Attention needed"),
            detail="Highlights baseline approvals, evidence freshness, and repeatable export readiness.",
            bullets=(
                f"{baseline_ready} of {repo_count} repos have approved baselines.",
                f"{fresh_ready} repos are still inside the fresh evidence window.",
                f"{sum(1 for row in repo_rows if row.export_ready)} repos can be exported immediately.",
            ),
        ),
        ComplianceFrameworkCard(
            title="ISO 27001",
            status_label=("Coverage visible" if repo_count else "No scope yet"),
            detail="Tracks governance, model configuration evidence, and operational review cadence.",
            bullets=(
                f"{governance_ready} repos include governance or policy artifacts.",
                f"{model_surface} repos include model or configuration evidence.",
                f"{sum(1 for row in repo_rows if row.freshness_tone == 'danger')} repos have stale evidence to refresh.",
            ),
        ),
    )


def _evidence_rows(repo_rows: Sequence[ComplianceRepoReadinessRow]) -> tuple[ComplianceEvidenceRow, ...]:
    rows: list[ComplianceEvidenceRow] = []
    for row in repo_rows:
        if not row.gap_keys and row.freshness_tone == "success":
            summary = "Evidence is current and governance-backed."
            next_step = "Keep the repo inside the fresh review cadence."
        elif "needs_setup" in row.gap_keys:
            summary = "No onboarding record or evidence package is stored yet."
            next_step = "Run onboarding to capture a first baseline and artifact set."
        elif "baseline_review" in row.gap_keys:
            summary = "A baseline exists, but a human approval decision is still pending."
            next_step = "Approve or reject the pending baseline version."
        elif "missing_governance" in row.gap_keys:
            summary = "Evidence exists, but governance or approval artifacts are missing."
            next_step = "Attach policy, decision, or approval artifacts to the repo story."
        elif "stale_evidence" in row.gap_keys:
            summary = "Stored evidence is outside the fresh-review window."
            next_step = "Refresh onboarding output before the next audit export."
        else:
            summary = "Evidence is aging and should be reviewed soon."
            next_step = "Schedule a refresh before the stale threshold is reached."
        rows.append(
            ComplianceEvidenceRow(
                repo_full=row.repo_full,
                repo_href=row.repo_href,
                freshness_label=row.freshness_label,
                freshness_tone=row.freshness_tone,
                summary=summary,
                next_step=next_step,
                gaps=row.gap_keys,
            )
        )
    rows.sort(key=lambda item: (_GAP_PRIORITY.get(item.gaps[0], 99) if item.gaps else 100, item.repo_full.lower()))
    return tuple(rows)


def _export_summary(repo_rows: Sequence[ComplianceRepoReadinessRow], export_jobs: Sequence[ExportJob]) -> ComplianceExportSummary:
    ready_repo_count = sum(1 for row in repo_rows if row.export_ready)
    completed_count = sum(1 for job in export_jobs if job.status in _EXPORT_COMPLETED_STATUSES)
    pending_count = sum(1 for job in export_jobs if job.status in _EXPORT_PENDING_STATUSES)
    failed_count = sum(1 for job in export_jobs if job.status == "failed")
    latest_job = max(export_jobs, key=lambda item: item.created_at, default=None)
    if latest_job is None:
        return ComplianceExportSummary(
            ready_repo_count=ready_repo_count,
            completed_count=0,
            pending_count=0,
            failed_count=0,
            latest_status_label="No exports generated yet",
            latest_detail="Create an export pack once the monitored repos are ready.",
            latest_download_href=None,
        )
    latest_status = latest_job.status.replace("_", " ").strip().title()
    latest_detail = f"Latest export ran for {latest_job.repo_full}."
    latest_download_href = None
    if latest_job.status == "completed" and latest_job.download_token:
        latest_download_href = f"/api/export/{latest_job.id}/download?token={latest_job.download_token}"
    elif latest_job.last_error:
        latest_detail = latest_job.last_error
    return ComplianceExportSummary(
        ready_repo_count=ready_repo_count,
        completed_count=completed_count,
        pending_count=pending_count,
        failed_count=failed_count,
        latest_status_label=latest_status,
        latest_detail=latest_detail,
        latest_download_href=latest_download_href,
    )


def build_compliance_workspace_view(
    db_path: str,
    repo_rows: Sequence[dict[str, object]],
    repo_summaries: Iterable[RepoDashboardIndexEntry],
    export_jobs: Sequence[ExportJob],
) -> ComplianceWorkspaceView:
    repo_summary_by_full = {summary.repo_full: summary for summary in repo_summaries}
    readiness_rows = tuple(
        _build_repo_row(db_path, repo_row, repo_summary_by_full.get(str(repo_row.get("repo_full") or "")))
        for repo_row in repo_rows
    )
    ready_count = sum(1 for row in readiness_rows if row.export_ready)
    needs_attention = sum(1 for row in readiness_rows if row.overall_tone != "success")
    top_gaps = _gap_items(readiness_rows)
    metrics = (
        ComplianceMetric("Repos in scope", str(len(readiness_rows)), "Connected repos with a tracked readiness posture."),
        ComplianceMetric("Ready now", str(ready_count), "Repos that can be packaged into an export immediately."),
        ComplianceMetric("Need attention", str(needs_attention), "Repos blocked by onboarding, governance, or freshness gaps."),
        ComplianceMetric(
            "Pending exports",
            str(sum(1 for job in export_jobs if job.status in _EXPORT_PENDING_STATUSES)),
            "Exports that are queued, running, or waiting for retry.",
        ),
    )
    return ComplianceWorkspaceView(
        metrics=metrics,
        verdict=_build_verdict(readiness_rows, top_gaps),
        top_gaps=top_gaps,
        repo_rows=readiness_rows,
        framework_cards=_framework_cards(readiness_rows),
        evidence_rows=_evidence_rows(readiness_rows),
        export_summary=_export_summary(readiness_rows, export_jobs),
    )