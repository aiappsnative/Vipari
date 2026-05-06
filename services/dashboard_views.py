from __future__ import annotations

import difflib
import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any
from urllib.parse import quote

from engine.drift_profile import AgentAttributeProfile, StaticSignals
from .baseline_provenance import (
    BaselineProvenance,
    approved_onboarding_provenance,
    baseline_provenance_from_json,
    historical_fallback_provenance,
    no_baseline_provenance,
    previous_pr_fallback_provenance,
)
from .baseline_approval_service import RepoBaselineReviewPanel, build_repo_baseline_review_panel_from_records

from .audit_records import (
    ArtifactDriftLeaderboardEntry,
    RepoStaticDriftSummary,
    get_repo_static_drift_summary,
    list_pull_request_audits_for_repo,
    list_top_drifting_artifacts_for_repo,
)
from .governance_signals import GovernanceAttentionSummary, RepoGovernancePosture, build_overview_governance_attention, build_repo_governance_posture
from .signal_fusion import priority_from_fused_signals, priority_sort_rank, priority_weighted_risk
from .onboarding_records import (
    OnboardingBaselineVersionRecord,
    RepositoryOnboardingRecord,
    get_latest_baseline_snapshot_id_for_onboarding,
    get_latest_repository_onboarding,
    list_baseline_audit_log_for_onboarding,
    list_onboarding_baseline_version_summaries_for_onboarding,
    list_historical_backfill_jobs_for_repo,
    list_latest_repository_onboardings,
    list_onboarded_artifacts_for_onboarding,
    list_onboarding_baseline_versions_for_onboarding,
    select_effective_onboarding_baseline_versions,
    select_latest_approved_onboarding_baseline_versions,
    select_latest_onboarding_baseline_versions,
)
from .persistence import connect_sqlite
from .provenance_labels import artifact_provenance_label


_DASHBOARD_CACHE_LOCK = threading.RLock()
_DASHBOARD_CACHE_MAX_ENTRIES = 128
_OVERVIEW_VIEW_CACHE: dict[tuple[Any, ...], DashboardOverviewView] = {}
_REPO_VIEW_CACHE: dict[tuple[Any, ...], RepoDashboardView] = {}


def _db_cache_signature(db_path: str) -> tuple[int, int]:
    try:
        stat = os.stat(db_path)
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _freeze_mapping(value: dict[str, str] | None) -> tuple[tuple[str, str], ...] | None:
    if not value:
        return None
    return tuple(sorted((str(key), str(item)) for key, item in value.items()))


def _freeze_repo_scope(value: dict[str, str] | None) -> tuple[tuple[str, str], ...] | None:
    return _freeze_mapping(value)


def _freeze_allowed_repo_fulls(value: set[str] | None) -> tuple[str, ...] | None:
    if not value:
        return None
    return tuple(sorted(str(item) for item in value))


def _cache_get(cache: dict[tuple[Any, ...], Any], key: tuple[Any, ...]) -> Any | None:
    with _DASHBOARD_CACHE_LOCK:
        cached = cache.get(key)
        if cached is not None:
            cache.pop(key, None)
            cache[key] = cached
        return cached


def _cache_set(cache: dict[tuple[Any, ...], Any], key: tuple[Any, ...], value: Any) -> Any:
    with _DASHBOARD_CACHE_LOCK:
        cache[key] = value
        while len(cache) > _DASHBOARD_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(cache))
            cache.pop(oldest_key, None)
    return value


def invalidate_dashboard_caches() -> None:
    with _DASHBOARD_CACHE_LOCK:
        _OVERVIEW_VIEW_CACHE.clear()
        _REPO_VIEW_CACHE.clear()


@dataclass(frozen=True)
class RepoDashboardIndexEntry:
    repo_full: str
    default_branch: str
    onboarding_status: str
    discovered_artifact_count: int
    last_onboarded_at: float
    historical_version_count: int = 0
    dashboard_scope: str = "allocated"
    allocation_status: str | None = None


@dataclass(frozen=True)
class DashboardOverviewMetric:
    label: str
    value: int | float
    detail: str


@dataclass(frozen=True)
class DashboardOverviewRiskState:
    status: str
    headline: str
    summary: str
    review_now_repo_count: int
    watch_repo_count: int
    baseline_review_repo_count: int
    highest_risk_repo_full: str | None
    highest_risk_artifact_path: str | None
    highest_risk_title: str | None
    highest_drift_magnitude: float


@dataclass(frozen=True)
class DashboardOverviewAttentionRepo:
    repo_full: str
    highest_priority: str
    highest_insight_title: str | None
    highest_insight_artifact_path: str | None
    highest_evidence_label: str | None
    highest_evidence_summary: str | None
    highest_change_summary: str | None
    highest_flag_summary: str | None
    highest_rationale: str | None
    highest_recommended_action: str | None
    highest_baseline_label: str | None
    highest_review_target: str | None
    highest_review_url: str | None
    insight_count: int
    lower_confidence_count: int
    review_now_count: int
    watch_count: int
    baseline_review_count: int
    top_drift_magnitude: float
    avg_semantic_distance: float
    discovered_artifact_count: int
    highest_updated_at: float | None = None


@dataclass(frozen=True)
class DashboardOverviewControlSurface:
    group_key: str
    label: str
    repo_count: int
    artifact_count: int
    high_confidence_count: int


@dataclass(frozen=True)
class DashboardOverviewRiskDistribution:
    group_key: str
    label: str
    repo_count: int
    artifact_count: int
    weighted_risk: float
    review_now_artifact_count: int


@dataclass(frozen=True)
class DashboardOverviewRegressionPattern:
    pattern_key: str
    label: str
    repo_count: int
    artifact_count: int
    review_now_artifact_count: int
    max_drift_magnitude: float
    example_repo_full: str | None
    example_artifact_path: str | None
    example_title: str | None
    summary: str




@dataclass(frozen=True)
class DashboardOverviewRegressionEntry:
    repo_full: str
    artifact_path: str
    artifact_type: str
    title: str
    priority: str
    confidence_label: str
    evidence_label: str
    evidence_summary: str
    baseline_label: str
    provenance_summary: str
    review_target: str | None
    review_url: str | None
    change_summary: str
    flag_summary: str
    rationale: str
    recommended_action: str
    drift_magnitude: float
    capability_shift: float
    guardrail_shift: float
    attribute_profile: list["AttributeProfileDimension"] | None = None


@dataclass(frozen=True)
class DashboardOverviewRepoCard:
    repo_full: str
    default_branch: str
    onboarding_status: str
    discovered_artifact_count: int
    last_onboarded_at: float
    historical_version_count: int = 0
    dashboard_scope: str = "allocated"
    allocation_status: str | None = None
    highest_priority: str = "baseline_review"
    highest_insight_title: str | None = None
    highest_insight_artifact_path: str | None = None
    highest_evidence_label: str | None = None
    highest_evidence_summary: str | None = None
    highest_change_summary: str | None = None
    highest_flag_summary: str | None = None
    highest_rationale: str | None = None
    highest_recommended_action: str | None = None
    highest_baseline_label: str | None = None
    highest_review_target: str | None = None
    highest_review_url: str | None = None
    insight_count: int = 0
    lower_confidence_count: int = 0
    review_now_count: int = 0
    watch_count: int = 0
    baseline_review_count: int = 0
    top_drift_magnitude: float = 0.0
    avg_semantic_distance: float = 0.0
    recent_activity_at: float = 0.0
    matched_risk_item: DashboardOverviewRegressionEntry | None = None


@dataclass(frozen=True)
class DashboardOverviewUrgentQueueSection:
    repos: list[DashboardOverviewRepoCard] = field(default_factory=list)
    repo_count: int = 0
    review_now_count: int = 0
    watch_count: int = 0


@dataclass(frozen=True)
class DashboardOverviewRecentChangesSection:
    repos: list[DashboardOverviewRepoCard] = field(default_factory=list)
    repo_count: int = 0


@dataclass(frozen=True)
class DashboardOverviewPostureSnapshotSection:
    metrics: list[DashboardOverviewMetric] = field(default_factory=list)
    risk_state: DashboardOverviewRiskState | None = None
    control_surface_coverage: list[DashboardOverviewControlSurface] = field(default_factory=list)
    control_surface_risk: list[DashboardOverviewRiskDistribution] = field(default_factory=list)


@dataclass(frozen=True)
class DashboardOverviewSections:
    urgent_queue: DashboardOverviewUrgentQueueSection = field(default_factory=DashboardOverviewUrgentQueueSection)
    recent_changes: DashboardOverviewRecentChangesSection = field(default_factory=DashboardOverviewRecentChangesSection)
    posture_snapshot: DashboardOverviewPostureSnapshotSection = field(default_factory=DashboardOverviewPostureSnapshotSection)
    governance_attention: GovernanceAttentionSummary = field(default_factory=lambda: GovernanceAttentionSummary(0, 0, (), ()))


@dataclass(frozen=True)
class DashboardOverviewView:
    risk_state: DashboardOverviewRiskState
    metrics: list[DashboardOverviewMetric]
    regression_patterns: list[DashboardOverviewRegressionPattern]
    highest_risk_items: list[DashboardOverviewRegressionEntry]
    control_surface_risk: list[DashboardOverviewRiskDistribution]
    attention_repos: list[DashboardOverviewAttentionRepo]
    control_surface_coverage: list[DashboardOverviewControlSurface]
    repos: list[RepoDashboardIndexEntry]
    overview_sections: DashboardOverviewSections = field(default_factory=DashboardOverviewSections)


@dataclass(frozen=True)
class RepoDashboardBackfillSummary:
    job_count: int
    planned_job_count: int
    processing_job_count: int
    completed_job_count: int
    failed_job_count: int
    total_historical_versions: int
    total_historical_profiles: int


@dataclass(frozen=True)
class RepoDashboardArtifactEntry:
    artifact_path: str
    artifact_type: str
    discovery_reason: str
    discovery_confidence: float
    baseline_line_count: int
    historical_version_count: int
    historical_profile_count: int
    latest_historical_semantic_distance: float
    latest_historical_drift_magnitude: float
    latest_historical_capability_shift: float
    latest_historical_guardrail_shift: float
    latest_historical_governance_shift: float
    latest_historical_autonomy_shift: float
    pr_profile_count: int
    latest_pr_semantic_distance: float
    latest_pr_capability_shift: float
    latest_pr_guardrail_shift: float
    latest_pr_governance_shift: float = 0.0
    latest_pr_autonomy_shift: float = 0.0
    leaderboard_drift_magnitude: float = 0.0
    latest_activity_at: float = 0.0
    provenance_kind: str = "supporting_repository_artifact"
    provenance_label: str = "Supporting repository artifact"


@dataclass(frozen=True)
class RepoDashboardInsightEntry:
    title: str
    artifact_path: str
    artifact_type: str
    priority: str
    queue_lane: str = "primary"
    score: float = 0.0
    confidence_label: str = "lower confidence"
    evidence_label: str = "baseline only"
    evidence_summary: str = ""
    baseline_label: str = "Baseline: none"
    provenance_summary: str = ""
    review_target: str | None = None
    review_url: str | None = None
    review_head_sha: str | None = None
    supporting_review_target: str | None = None
    supporting_review_url: str | None = None
    change_summary: str = ""
    flag_summary: str = ""
    updated_at: float | None = None
    risk_reasons: list[str] = None
    rationale: str = ""
    recommended_action: str = ""
    attribute_profile: list["AttributeProfileDimension"] | None = None


@dataclass(frozen=True)
class RepoDashboardControlSurfaceGroup:
    group_key: str
    label: str
    artifact_count: int
    high_confidence_count: int
    top_artifact_paths: list[str]


@dataclass(frozen=True)
class RepoArtifactTimelinePoint:
    source: str
    label: str
    source_ref: str | None = None
    source_url: str | None = None
    review_context: str | None = None
    created_at: float = 0.0
    baseline_provenance: BaselineProvenance | None = None
    semantic_distance: float = 0.0
    capability_shift: float = 0.0
    guardrail_shift: float = 0.0
    autonomy_shift: float = 0.0
    drift_magnitude: float = 0.0


@dataclass(frozen=True)
class RepoArtifactHistoryTimeline:
    artifact_path: str
    artifact_type: str
    point_count: int
    max_drift_magnitude: float
    points: list[RepoArtifactTimelinePoint]


@dataclass(frozen=True)
class DriftEpisode:
    episode_timestamp: float
    source_type: str
    source_label: str
    source_ref: str | None = None
    source_url: str | None = None
    episode_type: str = "mixed"
    top_attributes: list[str] = None
    episode_summary: str = ""
    severity: str = "low"
    confidence: str = "medium confidence"
    is_milestone: bool = False


@dataclass(frozen=True)
class RepoArtifactStoryline:
    artifact_path: str
    artifact_type: str
    summary: str
    baseline_label: str
    current_posture_label: str
    limited_history_note: str | None = None
    episodes: list[DriftEpisode] = None


@dataclass(frozen=True)
class RepoHistoryCue:
    cue_key: str
    label: str
    summary: str
    artifact_paths: list[str]


@dataclass(frozen=True)
class DashboardProfileVector:
    guardrail_robustness: float
    capability_risk: float
    autonomy_level: float
    stability_vs_creativity: float
    governance_strength: float


@dataclass(frozen=True)
class AttributeProfileDimension:
    attribute_key: str
    label: str
    baseline_value: str
    current_value: str
    direction: str
    state: str
    confidence_label: str
    confidence_score: float
    reason: str
    evidence: list[str] = None
    remediation: str = ""
    baseline_score: float | None = None
    current_score: float | None = None
    delta: float | None = None


@dataclass(frozen=True)
class ArtifactAttributeProfile:
    artifact_path: str
    artifact_type: str
    control_surface_label: str
    dimensions: list[AttributeProfileDimension]
    baseline_reference: str = "none-yet"
    has_authoritative_baseline: bool = False


@dataclass(frozen=True)
class RepoArtifactProvenance:
    source_type: str
    label: str
    source_ref: str | None = None
    source_url: str | None = None
    review_context: str | None = None
    created_at: float | None = None


@dataclass(frozen=True)
class RepoArtifactAttributeFinding:
    attribute_key: str
    label: str
    direction: str
    delta: float
    reason: str
    evidence: list[str]
    remediation: str


@dataclass(frozen=True)
class RepoArtifactDesignProfile:
    artifact_path: str
    artifact_type: str
    drift_from_baseline: float
    drift_label: str = "small drift"
    drift_tone: str = "low"
    baseline_profile: DashboardProfileVector | None = None
    current_profile: DashboardProfileVector | None = None
    baseline_provenance: BaselineProvenance | None = None
    headline_summary: str = ""
    risk_tags: list[str] = None
    narrative: list[str] = None
    attribute_findings: list[RepoArtifactAttributeFinding] = None
    attribute_profile: list[AttributeProfileDimension] = None
    can_promote_source_to_baseline: bool = False
    provenance: RepoArtifactProvenance | None = None


@dataclass(frozen=True)
class RepoAuditBriefFinding:
    title: str
    artifact_path: str
    summary: str
    evidence_label: str
    review_target: str | None = None
    review_url: str | None = None
    affected_dimensions: list[str] = None


@dataclass(frozen=True)
class RepoAuditBriefAction:
    label: str
    href: str
    style: str = "secondary"


@dataclass(frozen=True)
class RepoAuditBrief:
    trigger_source: str
    recommendation_key: str
    recommendation_label: str
    severity_label: str
    severity_tone: str
    confidence_label: str
    changed_artifact_count: int
    review_now_count: int
    watch_count: int
    lower_confidence_count: int
    baseline_status: str
    baseline_reference: str
    why_now: str
    summary: str
    affected_dimensions: list[str] = None
    findings: list[RepoAuditBriefFinding] = None
    actions: list[RepoAuditBriefAction] = None


@dataclass(frozen=True)
class RepoDashboardView:
    repo_full: str
    onboarding: RepositoryOnboardingRecord | None
    baseline_review: RepoBaselineReviewPanel | None
    backfill: RepoDashboardBackfillSummary
    pull_request_audit_count: int
    baseline_version_count: int
    drift_summary: RepoStaticDriftSummary
    top_drifting_artifacts: list[ArtifactDriftLeaderboardEntry] = None
    insights: list[RepoDashboardInsightEntry] = None
    lower_confidence_insights: list[RepoDashboardInsightEntry] = None
    control_surface_groups: list[RepoDashboardControlSurfaceGroup] = None
    history_timelines: list[RepoArtifactHistoryTimeline] = None
    featured_storyline: RepoArtifactStoryline | None = None
    history_cues: list[RepoHistoryCue] = None
    design_profiles: list[RepoArtifactDesignProfile] = None
    audit_brief: RepoAuditBrief | None = None
    governance_posture: RepoGovernancePosture = field(default_factory=lambda: RepoGovernancePosture("low confidence", "mixed", 0, "current", ()))
    artifacts: list[RepoDashboardArtifactEntry] = None
    journey_snapshots: list[dict[str, Any]] = None
    journey_comparison: dict[str, Any] | None = None
    selected_baseline_source_snapshot_id: int | None = None
    export_jobs: list[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Escalation queue — workspace-wide flat ranked list
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EscalationQueueItem:
    """A single item in the workspace escalation queue."""
    repo_full: str
    artifact_path: str
    artifact_type: str
    priority: str          # "review_now" | "watch"
    score: float
    title: str
    rationale: str
    recommended_action: str
    evidence_label: str
    provenance_summary: str
    baseline_label: str
    review_target: str | None
    review_url: str | None
    attribute_deltas: list[dict[str, Any]]  # top-2 changed dimensions
    updated_at: float | None


def build_workspace_escalation_queue(
    db_path: str,
    allowed_repo_fulls: set[str] | None = None,
    *,
    include_watch: bool = False,
) -> dict[str, Any]:
    """Return a ranked flat list of escalation items across all repos.

    Returns a dict with:
        workspace_posture: "risk" | "watch" | "healthy"
        workspace_posture_reasons: list[str]  – top-3 deduplicated risk reasons
        escalation_count: int  – review_now items
        watch_count: int       – watch-priority items
        items: list[EscalationQueueItem as dict]
    """
    repos = list_repo_dashboard_index(db_path, allowed_repo_fulls=allowed_repo_fulls)
    items: list[EscalationQueueItem] = []
    for repo_entry in repos:
        view = build_repo_dashboard_view(
            db_path,
            repo_entry.repo_full,
            include_journey=False,
            include_detail_sections=True,
        )
        for insight in (view.insights or []):
            if insight.priority == "review_now" or (include_watch and insight.priority == "watch"):
                # Collect top-2 changed attribute dimensions
                deltas: list[dict[str, Any]] = []
                for dim in (insight.attribute_profile or []):
                    if dim.state not in ("no_change", "unknown") and len(deltas) < 2:
                        deltas.append({
                            "attribute_key": dim.attribute_key,
                            "label": dim.label,
                            "baseline_value": dim.baseline_value,
                            "current_value": dim.current_value,
                            "direction": dim.direction,
                            "delta": dim.delta,
                        })
                items.append(EscalationQueueItem(
                    repo_full=repo_entry.repo_full,
                    artifact_path=insight.artifact_path,
                    artifact_type=insight.artifact_type,
                    priority=insight.priority,
                    score=insight.score,
                    title=insight.title,
                    rationale=insight.rationale,
                    recommended_action=insight.recommended_action,
                    evidence_label=insight.evidence_label,
                    provenance_summary=insight.provenance_summary,
                    baseline_label=insight.baseline_label,
                    review_target=insight.review_target,
                    review_url=insight.review_url,
                    attribute_deltas=deltas,
                    updated_at=insight.updated_at,
                ))

    # Sort: review_now before watch, then by score descending within each tier
    def _sort_key(item: EscalationQueueItem) -> tuple[int, float]:
        tier = 0 if item.priority == "review_now" else 1
        return (tier, -item.score)

    items.sort(key=_sort_key)

    review_now_items = [i for i in items if i.priority == "review_now"]
    watch_items = [i for i in items if i.priority == "watch"]
    escalation_count = len(review_now_items)
    watch_count = len(watch_items)

    # Workspace posture
    if any(i.score > 2.0 for i in review_now_items):
        workspace_posture = "risk"
    elif review_now_items:
        workspace_posture = "watch"
    elif watch_items:
        workspace_posture = "watch"
    else:
        workspace_posture = "healthy"

    # Top-3 deduplicated reasons from the highest-scoring review_now items
    seen_reasons: set[str] = set()
    workspace_posture_reasons: list[str] = []
    # Collect risk_reasons from top-scoring review_now insights
    for item in items:
        if item.priority != "review_now":
            break
        # Re-fetch the insight's risk_reasons from the view (already cached)
        view = build_repo_dashboard_view(
            db_path,
            item.repo_full,
            include_journey=False,
            include_detail_sections=True,
        )
        for insight in (view.insights or []):
            if insight.artifact_path == item.artifact_path and insight.priority == "review_now":
                for reason in (insight.risk_reasons or []):
                    if reason not in seen_reasons:
                        seen_reasons.add(reason)
                        workspace_posture_reasons.append(reason)
                break
        if len(workspace_posture_reasons) >= 3:
            break

    return {
        "workspace_posture": workspace_posture,
        "workspace_posture_reasons": workspace_posture_reasons[:3],
        "escalation_count": escalation_count,
        "watch_count": watch_count,
        "items": [
            {
                "repo_full": item.repo_full,
                "artifact_path": item.artifact_path,
                "artifact_type": item.artifact_type,
                "priority": item.priority,
                "score": item.score,
                "title": item.title,
                "rationale": item.rationale,
                "recommended_action": item.recommended_action,
                "evidence_label": item.evidence_label,
                "provenance_summary": item.provenance_summary,
                "baseline_label": item.baseline_label,
                "review_target": item.review_target,
                "review_url": item.review_url,
                "attribute_deltas": item.attribute_deltas,
                "updated_at": item.updated_at,
            }
            for item in items
        ],
    }


# ---------------------------------------------------------------------------
# Repo index
# ---------------------------------------------------------------------------


def list_repo_dashboard_index(
    db_path: str,
    allowed_repo_fulls: set[str] | None = None,
    repo_scope_by_full: dict[str, str] | None = None,
    allocation_status_by_full: dict[str, str] | None = None,
) -> list[RepoDashboardIndexEntry]:
    onboardings = list_latest_repository_onboardings(db_path)
    if allowed_repo_fulls is not None:
        onboardings = [onboarding for onboarding in onboardings if onboarding.repo_full in allowed_repo_fulls]
    return [
        RepoDashboardIndexEntry(
            repo_full=onboarding.repo_full,
            default_branch=onboarding.default_branch,
            onboarding_status=onboarding.status,
            discovered_artifact_count=onboarding.discovered_artifact_count,
            last_onboarded_at=onboarding.updated_at,
            dashboard_scope=(repo_scope_by_full or {}).get(onboarding.repo_full, "allocated"),
            allocation_status=(allocation_status_by_full or {}).get(onboarding.repo_full),
        )
        for onboarding in onboardings
    ]


def build_dashboard_overview_view(
    db_path: str,
    allowed_repo_fulls: set[str] | None = None,
    repo_scope_by_full: dict[str, str] | None = None,
    allocation_status_by_full: dict[str, str] | None = None,
) -> DashboardOverviewView:
    cache_key = (
        "overview",
        db_path,
        _db_cache_signature(db_path),
        _freeze_allowed_repo_fulls(allowed_repo_fulls),
        _freeze_repo_scope(repo_scope_by_full),
        _freeze_mapping(allocation_status_by_full),
    )
    cached = _cache_get(_OVERVIEW_VIEW_CACHE, cache_key)
    if cached is not None:
        return cached

    view = _build_dashboard_overview_view_uncached(
        db_path,
        allowed_repo_fulls=allowed_repo_fulls,
        repo_scope_by_full=repo_scope_by_full,
        allocation_status_by_full=allocation_status_by_full,
    )
    return _cache_set(_OVERVIEW_VIEW_CACHE, cache_key, view)


def filter_dashboard_overview_view(
    view: DashboardOverviewView,
    overview_filter: str | None,
    *,
    overview_range: str | None = None,
    now: float | None = None,
    allowed_repo_fulls: set[str] | None = None,
) -> DashboardOverviewView:
    filtered_view = view
    if allowed_repo_fulls is not None:
        filtered_view = _filter_dashboard_overview_view_by_repo_fulls(filtered_view, allowed_repo_fulls)
    normalized_range = str(overview_range or "7d").strip().lower()
    if normalized_range in {"24h", "7d", "30d"}:
        filtered_view = _filter_dashboard_overview_view_by_range(filtered_view, normalized_range, now=now)

    normalized_filter = str(overview_filter or "all").strip().lower()
    if normalized_filter != "critical":
        return filtered_view

    filtered_repos = [repo for repo in filtered_view.repos if repo.repo_full in {item.repo_full for item in filtered_view.highest_risk_items}]
    filtered_attention_repos = [repo for repo in filtered_view.attention_repos if repo.highest_priority == "review_now"]
    filtered_risk_items = [item for item in filtered_view.highest_risk_items if item.priority == "review_now"]
    filtered_recent_cards = [repo for repo in filtered_view.overview_sections.recent_changes.repos if repo.highest_priority == "review_now"]

    return replace(
        filtered_view,
        repos=filtered_repos,
        attention_repos=filtered_attention_repos,
        highest_risk_items=filtered_risk_items,
        overview_sections=replace(
            filtered_view.overview_sections,
            urgent_queue=replace(
                filtered_view.overview_sections.urgent_queue,
                repos=[repo for repo in filtered_view.overview_sections.urgent_queue.repos if repo.highest_priority == "review_now"],
                repo_count=sum(1 for repo in filtered_view.overview_sections.urgent_queue.repos if repo.highest_priority == "review_now"),
                review_now_count=sum(1 for repo in filtered_view.overview_sections.urgent_queue.repos if repo.highest_priority == "review_now"),
                watch_count=0,
            ),
            recent_changes=replace(
                filtered_view.overview_sections.recent_changes,
                repos=filtered_recent_cards,
                repo_count=len(filtered_recent_cards),
            ),
        ),
    )


def _filter_dashboard_overview_view_by_range(
    view: DashboardOverviewView,
    overview_range: str,
    *,
    now: float | None = None,
) -> DashboardOverviewView:
    cutoff = _dashboard_overview_range_cutoff(overview_range, now=now)
    if cutoff is None:
        return view

    recent_repo_fulls = {
        repo.repo_full
        for repo in view.overview_sections.recent_changes.repos
        if _dashboard_overview_repo_activity_at(repo) >= cutoff
    }
    filtered_repos = [repo for repo in view.repos if repo.repo_full in recent_repo_fulls]
    filtered_attention_repos = [repo for repo in view.attention_repos if repo.repo_full in recent_repo_fulls]
    filtered_risk_items = [item for item in view.highest_risk_items if item.repo_full in recent_repo_fulls]
    filtered_recent_cards = [repo for repo in view.overview_sections.recent_changes.repos if repo.repo_full in recent_repo_fulls]
    filtered_urgent_cards = [repo for repo in view.overview_sections.urgent_queue.repos if repo.repo_full in recent_repo_fulls]

    filtered_risk_state = replace(
        view.risk_state,
        review_now_repo_count=sum(1 for repo in filtered_attention_repos if repo.highest_priority == "review_now"),
        watch_repo_count=sum(1 for repo in filtered_attention_repos if repo.highest_priority == "watch"),
        baseline_review_repo_count=sum(1 for repo in filtered_attention_repos if repo.highest_priority == "baseline_review"),
        highest_risk_repo_full=(filtered_risk_items[0].repo_full if filtered_risk_items else None),
        highest_risk_artifact_path=(filtered_risk_items[0].artifact_path if filtered_risk_items else None),
        highest_risk_title=(filtered_risk_items[0].title if filtered_risk_items else None),
        highest_drift_magnitude=max((item.drift_magnitude for item in filtered_risk_items), default=0.0),
    )

    return replace(
        view,
        risk_state=filtered_risk_state,
        repos=filtered_repos,
        attention_repos=filtered_attention_repos,
        highest_risk_items=filtered_risk_items,
        overview_sections=replace(
            view.overview_sections,
            urgent_queue=replace(
                view.overview_sections.urgent_queue,
                repos=filtered_urgent_cards,
                repo_count=len(filtered_urgent_cards),
                review_now_count=sum(1 for repo in filtered_urgent_cards if repo.highest_priority == "review_now"),
                watch_count=sum(1 for repo in filtered_urgent_cards if repo.highest_priority == "watch"),
            ),
            recent_changes=replace(
                view.overview_sections.recent_changes,
                repos=filtered_recent_cards,
                repo_count=len(filtered_recent_cards),
            ),
            posture_snapshot=replace(
                view.overview_sections.posture_snapshot,
                risk_state=filtered_risk_state,
            ),
        ),
    )


def _filter_dashboard_overview_view_by_repo_fulls(
    view: DashboardOverviewView,
    allowed_repo_fulls: set[str],
) -> DashboardOverviewView:
    normalized_repo_fulls = {str(repo_full) for repo_full in allowed_repo_fulls}
    filtered_repos = [repo for repo in view.repos if repo.repo_full in normalized_repo_fulls]
    filtered_attention_repos = [repo for repo in view.attention_repos if repo.repo_full in normalized_repo_fulls]
    filtered_risk_items = [item for item in view.highest_risk_items if item.repo_full in normalized_repo_fulls]
    filtered_recent_cards = [repo for repo in view.overview_sections.recent_changes.repos if repo.repo_full in normalized_repo_fulls]
    filtered_urgent_cards = [repo for repo in view.overview_sections.urgent_queue.repos if repo.repo_full in normalized_repo_fulls]

    filtered_risk_state = replace(
        view.risk_state,
        review_now_repo_count=sum(1 for repo in filtered_attention_repos if repo.highest_priority == "review_now"),
        watch_repo_count=sum(1 for repo in filtered_attention_repos if repo.highest_priority == "watch"),
        baseline_review_repo_count=sum(1 for repo in filtered_attention_repos if repo.highest_priority == "baseline_review"),
        highest_risk_repo_full=(filtered_risk_items[0].repo_full if filtered_risk_items else None),
        highest_risk_artifact_path=(filtered_risk_items[0].artifact_path if filtered_risk_items else None),
        highest_risk_title=(filtered_risk_items[0].title if filtered_risk_items else None),
        highest_drift_magnitude=max((item.drift_magnitude for item in filtered_risk_items), default=0.0),
    )

    return replace(
        view,
        risk_state=filtered_risk_state,
        repos=filtered_repos,
        attention_repos=filtered_attention_repos,
        highest_risk_items=filtered_risk_items,
        overview_sections=replace(
            view.overview_sections,
            urgent_queue=replace(
                view.overview_sections.urgent_queue,
                repos=filtered_urgent_cards,
                repo_count=len(filtered_urgent_cards),
                review_now_count=sum(1 for repo in filtered_urgent_cards if repo.highest_priority == "review_now"),
                watch_count=sum(1 for repo in filtered_urgent_cards if repo.highest_priority == "watch"),
            ),
            recent_changes=replace(
                view.overview_sections.recent_changes,
                repos=filtered_recent_cards,
                repo_count=len(filtered_recent_cards),
            ),
            posture_snapshot=replace(
                view.overview_sections.posture_snapshot,
                risk_state=filtered_risk_state,
            ),
        ),
    )


def _dashboard_overview_range_cutoff(overview_range: str, *, now: float | None = None) -> float | None:
    normalized_range = str(overview_range or "7d").strip().lower()
    now_timestamp = time.time() if now is None else float(now)
    if normalized_range == "24h":
        return now_timestamp - 86400.0
    if normalized_range == "7d":
        return now_timestamp - (7 * 86400.0)
    if normalized_range == "30d":
        return now_timestamp - (30 * 86400.0)
    return None


def _dashboard_overview_repo_activity_at(repo: DashboardOverviewRepoCard) -> float:
    return max(float(repo.recent_activity_at or 0.0), float(repo.last_onboarded_at or 0.0))


def _build_dashboard_overview_view_uncached(
    db_path: str,
    allowed_repo_fulls: set[str] | None = None,
    repo_scope_by_full: dict[str, str] | None = None,
    allocation_status_by_full: dict[str, str] | None = None,
) -> DashboardOverviewView:
    repos = list_repo_dashboard_index(
        db_path,
        allowed_repo_fulls=allowed_repo_fulls,
        repo_scope_by_full=repo_scope_by_full,
        allocation_status_by_full=allocation_status_by_full,
    )
    repo_views = _build_overview_repo_views(db_path, repos)
    repo_view_by_full = {view.repo_full: view for view in repo_views}
    repos = [
        RepoDashboardIndexEntry(
            repo_full=repo.repo_full,
            default_branch=repo.default_branch,
            onboarding_status=repo.onboarding_status,
            discovered_artifact_count=repo.discovered_artifact_count,
            last_onboarded_at=repo.last_onboarded_at,
            historical_version_count=(repo_view_by_full[repo.repo_full].backfill.total_historical_versions if repo.repo_full in repo_view_by_full else 0),
            dashboard_scope=repo.dashboard_scope,
            allocation_status=repo.allocation_status,
        )
        for repo in repos
    ]

    total_artifacts = sum(repo.discovered_artifact_count for repo in repos)
    total_backfill_jobs = sum(view.backfill.job_count for view in repo_views)
    review_now_repo_count = sum(1 for view in repo_views if any(insight.priority == "review_now" for insight in view.insights))
    total_pr_audits = sum(view.pull_request_audit_count for view in repo_views)

    attention_repos = _build_overview_attention_repos(repo_views)
    control_surface_coverage = _build_overview_control_surface_coverage(repo_views)
    control_surface_risk = _build_overview_control_surface_risk(repo_views)
    regression_patterns = _build_overview_regression_patterns(repo_views)
    highest_risk_items = _build_overview_regressions(repo_views)
    risk_state = _build_overview_risk_state(attention_repos)
    repo_cards = _build_overview_repo_cards(repos, attention_repos, highest_risk_items)
    governance_attention = build_overview_governance_attention(repo_views)

    metrics = [
        DashboardOverviewMetric(
            label="Onboarded repositories",
            value=len(repos),
            detail="Repos with a stored onboarding record in the local Vipari store.",
        ),
        DashboardOverviewMetric(
            label="Tracked artifacts",
            value=total_artifacts,
            detail="Discovered AI control surfaces currently included in the local baseline inventory.",
        ),
        DashboardOverviewMetric(
            label="Needs review now",
            value=review_now_repo_count,
            detail="Repositories that currently contain at least one high-priority `review now` insight.",
        ),
        DashboardOverviewMetric(
            label="PR audits (proposal evidence)",
            value=total_pr_audits,
            detail="Before-merge audit runs recorded for reviewer workflow; not counted as landed drift evidence.",
        ),
        DashboardOverviewMetric(
            label="Backfill jobs",
            value=total_backfill_jobs,
            detail="Historical backfill jobs planned or executed across onboarded repositories.",
        ),
        DashboardOverviewMetric(
            label="Control surface groups",
            value=len(control_surface_coverage),
            detail="Distinct control surface categories currently represented across onboarded repositories.",
        ),
    ]

    return DashboardOverviewView(
        risk_state=risk_state,
        metrics=metrics,
        regression_patterns=regression_patterns,
        highest_risk_items=highest_risk_items,
        control_surface_risk=control_surface_risk,
        attention_repos=attention_repos,
        control_surface_coverage=control_surface_coverage,
        repos=repos,
        overview_sections=DashboardOverviewSections(
            urgent_queue=DashboardOverviewUrgentQueueSection(
                repos=[repo for repo in repo_cards if repo.highest_priority in {"review_now", "watch"}],
                repo_count=sum(1 for repo in repo_cards if repo.highest_priority in {"review_now", "watch"}),
                review_now_count=sum(1 for repo in repo_cards if repo.highest_priority == "review_now"),
                watch_count=sum(1 for repo in repo_cards if repo.highest_priority == "watch"),
            ),
            recent_changes=DashboardOverviewRecentChangesSection(
                repos=repo_cards,
                repo_count=len(repo_cards),
            ),
            posture_snapshot=DashboardOverviewPostureSnapshotSection(
                metrics=metrics,
                risk_state=risk_state,
                control_surface_coverage=control_surface_coverage,
                control_surface_risk=control_surface_risk,
            ),
            governance_attention=governance_attention,
        ),
    )


def _overview_priority_rank(priority: str | None) -> int:
    normalized = str(priority or "baseline_review").strip().lower()
    if normalized == "review_now":
        return 0
    if normalized == "watch":
        return 1
    return 2


def _build_overview_repo_cards(
    repos: list[RepoDashboardIndexEntry],
    attention_repos: list[DashboardOverviewAttentionRepo],
    highest_risk_items: list[DashboardOverviewRegressionEntry],
) -> list[DashboardOverviewRepoCard]:
    attention_by_repo = {repo.repo_full: repo for repo in attention_repos}
    risk_by_repo = {item.repo_full: item for item in highest_risk_items}
    cards: list[DashboardOverviewRepoCard] = []
    for repo in repos:
        attention = attention_by_repo.get(repo.repo_full)
        matched_risk_item = risk_by_repo.get(repo.repo_full)
        cards.append(
            DashboardOverviewRepoCard(
                repo_full=repo.repo_full,
                default_branch=repo.default_branch,
                onboarding_status=repo.onboarding_status,
                discovered_artifact_count=(attention.discovered_artifact_count if attention is not None else repo.discovered_artifact_count),
                last_onboarded_at=repo.last_onboarded_at,
                historical_version_count=repo.historical_version_count,
                dashboard_scope=repo.dashboard_scope,
                allocation_status=repo.allocation_status,
                highest_priority=(attention.highest_priority if attention is not None else "baseline_review"),
                highest_insight_title=(attention.highest_insight_title if attention is not None else (matched_risk_item.title if matched_risk_item is not None else None)),
                highest_insight_artifact_path=(attention.highest_insight_artifact_path if attention is not None else (matched_risk_item.artifact_path if matched_risk_item is not None else None)),
                highest_evidence_label=(attention.highest_evidence_label if attention is not None else (matched_risk_item.evidence_label if matched_risk_item is not None else None)),
                highest_evidence_summary=(attention.highest_evidence_summary if attention is not None else (matched_risk_item.evidence_summary if matched_risk_item is not None else None)),
                highest_change_summary=(attention.highest_change_summary if attention is not None else (matched_risk_item.change_summary if matched_risk_item is not None else None)),
                highest_flag_summary=(attention.highest_flag_summary if attention is not None else (matched_risk_item.flag_summary if matched_risk_item is not None else None)),
                highest_rationale=(attention.highest_rationale if attention is not None else (matched_risk_item.rationale if matched_risk_item is not None else None)),
                highest_recommended_action=(attention.highest_recommended_action if attention is not None else (matched_risk_item.recommended_action if matched_risk_item is not None else None)),
                highest_baseline_label=(attention.highest_baseline_label if attention is not None else (matched_risk_item.baseline_label if matched_risk_item is not None else "Baseline: none yet")),
                highest_review_target=(attention.highest_review_target if attention is not None else (matched_risk_item.review_target if matched_risk_item is not None else None)),
                highest_review_url=(attention.highest_review_url if attention is not None else (matched_risk_item.review_url if matched_risk_item is not None else None)),
                insight_count=(attention.insight_count if attention is not None else 0),
                lower_confidence_count=(attention.lower_confidence_count if attention is not None else 0),
                review_now_count=(attention.review_now_count if attention is not None else 0),
                watch_count=(attention.watch_count if attention is not None else 0),
                baseline_review_count=(attention.baseline_review_count if attention is not None else 0),
                top_drift_magnitude=(attention.top_drift_magnitude if attention is not None else 0.0),
                avg_semantic_distance=(attention.avg_semantic_distance if attention is not None else 0.0),
                recent_activity_at=(attention.highest_updated_at if attention is not None and attention.highest_updated_at is not None else repo.last_onboarded_at),
                matched_risk_item=matched_risk_item,
            )
        )
    cards.sort(
        key=lambda card: (
            _overview_priority_rank(card.highest_priority),
            -card.top_drift_magnitude,
            card.repo_full,
        )
    )
    return cards


def build_repo_dashboard_view(
    db_path: str,
    repo_full: str,
    *,
    include_journey: bool = True,
    include_detail_sections: bool = True,
) -> RepoDashboardView:
    cache_key = (
        "repo",
        db_path,
        _db_cache_signature(db_path),
        repo_full,
        include_journey,
        include_detail_sections,
    )
    cached = _cache_get(_REPO_VIEW_CACHE, cache_key)
    if cached is not None:
        return cached

    view = _build_repo_dashboard_view_uncached(
        db_path,
        repo_full,
        include_journey=include_journey,
        include_detail_sections=include_detail_sections,
    )
    return _cache_set(_REPO_VIEW_CACHE, cache_key, view)


def build_repo_dashboard_view_with_timings(
    db_path: str,
    repo_full: str,
    *,
    include_journey: bool = True,
    include_detail_sections: bool = True,
) -> tuple[RepoDashboardView, list[tuple[str, float]]]:
    cache_key = (
        "repo",
        db_path,
        _db_cache_signature(db_path),
        repo_full,
        include_journey,
        include_detail_sections,
    )
    cached = _cache_get(_REPO_VIEW_CACHE, cache_key)
    if cached is not None:
        return cached, [("repo-cache-hit", 0.0)]

    stage_timings: list[tuple[str, float]] = []
    view = _build_repo_dashboard_view_uncached(
        db_path,
        repo_full,
        include_journey=include_journey,
        include_detail_sections=include_detail_sections,
        stage_timings=stage_timings,
    )
    return _cache_set(_REPO_VIEW_CACHE, cache_key, view), stage_timings


def _build_repo_dashboard_view_uncached(
    db_path: str,
    repo_full: str,
    *,
    include_journey: bool = True,
    include_detail_sections: bool = True,
    stage_timings: list[tuple[str, float]] | None = None,
) -> RepoDashboardView:
    def timed_stage(stage_name: str, factory):
        started_at = time.perf_counter()
        result = factory()
        if stage_timings is not None:
            stage_timings.append((stage_name, (time.perf_counter() - started_at) * 1000.0))
        return result

    onboarding = timed_stage("repo-onboarding", lambda: get_latest_repository_onboarding(db_path, repo_full))
    baseline_review = None
    drift_summary = timed_stage("repo-drift-summary", lambda: get_repo_static_drift_summary(db_path, repo_full))
    top_drifting_artifacts = timed_stage("repo-top-drifting", lambda: list_top_drifting_artifacts_for_repo(db_path, repo_full))
    pull_request_audit_count = timed_stage("repo-pr-audits", lambda: len(list_pull_request_audits_for_repo(db_path, repo_full)))
    journey_snapshots: list[dict[str, Any]] = []
    journey_comparison: dict[str, Any] | None = None
    selected_baseline_source_snapshot_id: int | None = None
    if onboarding is not None:
        selected_baseline_source_snapshot_id = timed_stage(
            "repo-selected-baseline",
            lambda: get_latest_baseline_snapshot_id_for_onboarding(db_path, onboarding.id),
        )
    if include_journey:
        journey_snapshots, journey_comparison = timed_stage(
            "repo-journey-panel",
            lambda: _build_repo_journey_panel(
                db_path,
                repo_full,
                selected_baseline_source_snapshot_id=selected_baseline_source_snapshot_id,
            ),
        )

    if onboarding is None:
        return RepoDashboardView(
            repo_full=repo_full,
            onboarding=None,
            baseline_review=None,
            backfill=RepoDashboardBackfillSummary(
                job_count=0,
                planned_job_count=0,
                processing_job_count=0,
                completed_job_count=0,
                failed_job_count=0,
                total_historical_versions=0,
                total_historical_profiles=0,
            ),
            pull_request_audit_count=pull_request_audit_count,
            baseline_version_count=0,
            drift_summary=drift_summary,
            top_drifting_artifacts=top_drifting_artifacts,
            insights=[],
            lower_confidence_insights=[],
            control_surface_groups=[],
            history_timelines=[],
            featured_storyline=None,
            history_cues=[],
            design_profiles=[],
            audit_brief=None,
            governance_posture=RepoGovernancePosture("low confidence", "mixed", 0, "current", ()),
            artifacts=[],
            journey_snapshots=journey_snapshots,
            journey_comparison=journey_comparison,
            selected_baseline_source_snapshot_id=selected_baseline_source_snapshot_id,
            export_jobs=[],
        )

    artifacts = timed_stage("repo-onboarded-artifacts", lambda: list_onboarded_artifacts_for_onboarding(db_path, onboarding.id))
    baseline_versions = timed_stage(
        "repo-baseline-versions",
        lambda: list_onboarding_baseline_version_summaries_for_onboarding(db_path, onboarding.id),
    )
    latest_baseline_versions = select_latest_onboarding_baseline_versions(baseline_versions)
    latest_approved_baseline_versions = select_latest_approved_onboarding_baseline_versions(baseline_versions)
    baseline_review = timed_stage(
        "repo-baseline-review",
        lambda: build_repo_baseline_review_panel_from_records(
            repo_full=repo_full,
            onboarding=onboarding,
            latest_baselines=latest_baseline_versions,
            authoritative_artifact_count=len(latest_approved_baseline_versions),
            baseline_audit_logs=list_baseline_audit_log_for_onboarding(db_path, onboarding.id),
        ),
    )
    baseline_by_path = timed_stage(
        "repo-effective-baselines",
        lambda: {
            baseline.artifact_path: baseline
            for baseline in select_effective_onboarding_baseline_versions(baseline_versions)
        },
    )
    jobs = timed_stage("repo-backfill-jobs", lambda: list_historical_backfill_jobs_for_repo(db_path, repo_full))
    leaderboard_by_path = {entry.artifact_path: entry for entry in top_drifting_artifacts}
    metrics_by_path = timed_stage("repo-artifact-metrics", lambda: _load_repo_artifact_metrics(db_path, repo_full))
    profile_context_by_path = timed_stage(
        "repo-profile-contexts",
        lambda: _load_repo_artifact_profile_contexts(db_path, repo_full),
    )

    artifact_entries: list[RepoDashboardArtifactEntry] = []
    total_historical_versions = sum(metrics["historical_version_count"] for metrics in metrics_by_path.values())
    total_historical_profiles = sum(metrics["historical_profile_count"] for metrics in metrics_by_path.values())

    def build_artifact_entries() -> list[RepoDashboardArtifactEntry]:
        built_entries: list[RepoDashboardArtifactEntry] = []
        for artifact in artifacts:
            baseline = baseline_by_path.get(artifact.artifact_path)
            provenance = artifact_provenance_label(artifact.artifact_type)
            metrics = metrics_by_path.get(artifact.artifact_path, _empty_artifact_metrics())
            leaderboard_entry = leaderboard_by_path.get(artifact.artifact_path)

            built_entries.append(
                RepoDashboardArtifactEntry(
                    artifact_path=artifact.artifact_path,
                    artifact_type=artifact.artifact_type,
                    discovery_reason=artifact.discovery_reason,
                    discovery_confidence=artifact.confidence,
                    baseline_line_count=baseline.line_count if baseline is not None else 0,
                    historical_version_count=metrics["historical_version_count"],
                    historical_profile_count=metrics["historical_profile_count"],
                    latest_historical_semantic_distance=metrics["latest_historical_semantic_distance"],
                    latest_historical_drift_magnitude=metrics["latest_historical_drift_magnitude"],
                    latest_historical_capability_shift=metrics["latest_historical_capability_shift"],
                    latest_historical_guardrail_shift=metrics["latest_historical_guardrail_shift"],
                    latest_historical_governance_shift=metrics["latest_historical_governance_shift"],
                    latest_historical_autonomy_shift=metrics["latest_historical_autonomy_shift"],
                    pr_profile_count=metrics["pr_profile_count"],
                    latest_pr_semantic_distance=metrics["latest_pr_semantic_distance"],
                    latest_pr_capability_shift=metrics["latest_pr_capability_shift"],
                    latest_pr_guardrail_shift=metrics["latest_pr_guardrail_shift"],
                    latest_pr_governance_shift=metrics["latest_pr_governance_shift"],
                    latest_pr_autonomy_shift=metrics["latest_pr_autonomy_shift"],
                    leaderboard_drift_magnitude=(leaderboard_entry.drift_magnitude if leaderboard_entry is not None else 0.0),
                    latest_activity_at=metrics["latest_activity_at"],
                    provenance_kind=provenance.kind,
                    provenance_label=provenance.label,
                )
            )
        built_entries.sort(
            key=lambda entry: (
                priority_sort_rank(priority_from_fused_signals(_insight_score(entry, profile_context_by_path.get(entry.artifact_path)))),
                -_insight_score(entry, profile_context_by_path.get(entry.artifact_path)),
                -max(entry.leaderboard_drift_magnitude, entry.latest_historical_drift_magnitude),
                entry.artifact_path,
            )
        )
        return built_entries

    artifact_entries = timed_stage("repo-artifact-entries", build_artifact_entries)

    insights, lower_confidence_insights = timed_stage(
        "repo-insights",
        lambda: _build_repo_insights(
            artifact_entries,
            baseline_by_path,
            profile_context_by_path,
            attribute_profile_mode=("all" if include_detail_sections else "ranked"),
        ),
    )
    control_surface_groups = timed_stage("repo-control-surfaces", lambda: _build_control_surface_groups(artifact_entries))
    history_timelines: list[RepoArtifactHistoryTimeline] = []
    featured_storyline: RepoArtifactStoryline | None = None
    history_cues: list[RepoHistoryCue] = []
    design_profiles: list[RepoArtifactDesignProfile] = []
    if include_detail_sections:
        history_timelines = timed_stage(
            "repo-history-timelines",
            lambda: _build_repo_history_timelines(db_path, repo_full, artifact_entries),
        )
        featured_storyline = timed_stage(
            "repo-featured-storyline",
            lambda: _build_featured_storyline(
                db_path,
                repo_full,
                artifact_entries,
                insights,
                baseline_by_path,
                profile_context_by_path,
            ),
        )
        history_cues = timed_stage(
            "repo-history-cues",
            lambda: _build_repo_history_cues(artifact_entries, baseline_by_path, profile_context_by_path),
        )
        design_profiles = timed_stage(
            "repo-design-profiles",
            lambda: _build_repo_design_profiles(artifact_entries, insights, baseline_by_path, profile_context_by_path),
        )
    governance_posture = timed_stage(
        "repo-governance-posture",
        lambda: build_repo_governance_posture(
            repo_full,
            design_profiles=design_profiles,
            artifacts=artifact_entries,
            history_cues=history_cues,
            insights=insights,
        ),
    )
    audit_brief = timed_stage(
        "repo-audit-brief",
        lambda: _build_repo_audit_brief(
            repo_full,
            insights,
            lower_confidence_insights,
            baseline_review,
            artifact_entries,
            governance_posture,
        ),
    )

    return RepoDashboardView(
        repo_full=repo_full,
        onboarding=onboarding,
        baseline_review=baseline_review,
        backfill=RepoDashboardBackfillSummary(
            job_count=len(jobs),
            planned_job_count=sum(1 for job in jobs if job.status == "planned"),
            processing_job_count=sum(1 for job in jobs if job.status == "processing"),
            completed_job_count=sum(1 for job in jobs if job.status == "completed"),
            failed_job_count=sum(1 for job in jobs if job.status == "failed"),
            total_historical_versions=total_historical_versions,
            total_historical_profiles=total_historical_profiles,
        ),
        pull_request_audit_count=pull_request_audit_count,
        baseline_version_count=len(baseline_versions),
        drift_summary=drift_summary,
        top_drifting_artifacts=top_drifting_artifacts,
        insights=insights,
        lower_confidence_insights=lower_confidence_insights,
        control_surface_groups=control_surface_groups,
        history_timelines=history_timelines,
        featured_storyline=featured_storyline,
        history_cues=history_cues,
        design_profiles=design_profiles,
        audit_brief=audit_brief,
        governance_posture=governance_posture,
        artifacts=artifact_entries,
        journey_snapshots=journey_snapshots,
        journey_comparison=journey_comparison,
        selected_baseline_source_snapshot_id=selected_baseline_source_snapshot_id,
        export_jobs=[],
    )


def _build_repo_audit_brief(
    repo_full: str,
    insights: list[RepoDashboardInsightEntry] | None,
    lower_confidence_insights: list[RepoDashboardInsightEntry] | None,
    baseline_review: RepoBaselineReviewPanel | None,
    artifacts: list[RepoDashboardArtifactEntry] | None,
    governance_posture: RepoGovernancePosture | None,
) -> RepoAuditBrief:
    primary_insight = (insights or [None])[0]
    review_now_count = sum(1 for insight in (insights or []) if insight.priority == "review_now")
    watch_count = sum(1 for insight in (insights or []) if insight.priority == "watch")
    lower_confidence_count = len(lower_confidence_insights or [])
    changed_artifact_count = len({insight.artifact_path for insight in (insights or []) if insight.artifact_path})
    if changed_artifact_count == 0:
        changed_artifact_count = sum(
            1
            for artifact in (artifacts or [])
            if max(
                float(artifact.latest_historical_drift_magnitude or 0.0),
                float(artifact.latest_pr_capability_shift or 0.0),
                float(artifact.latest_pr_guardrail_shift or 0.0),
                float(artifact.latest_pr_governance_shift or 0.0),
                float(artifact.latest_pr_autonomy_shift or 0.0),
            )
            > 0
        )

    if primary_insight is not None and primary_insight.priority == "review_now":
        recommendation_key = "review_before_merge"
        recommendation_label = "Review before merge"
        severity_label = "High"
        severity_tone = "high"
    elif primary_insight is not None and primary_insight.priority == "watch":
        recommendation_key = "review_before_merge"
        recommendation_label = "Review before merge"
        severity_label = "Medium"
        severity_tone = "medium"
    elif baseline_review is not None and baseline_review.is_pending_review:
        recommendation_key = "needs_baseline_decision"
        recommendation_label = "Needs baseline decision"
        severity_label = "Medium"
        severity_tone = "medium"
    else:
        recommendation_key = "safe_to_merge"
        recommendation_label = "Safe to merge"
        severity_label = "Low"
        severity_tone = "low"

    baseline_status = "pending review" if baseline_review is not None and baseline_review.is_pending_review else "approved"
    baseline_reference = (
        primary_insight.baseline_label
        if primary_insight is not None and primary_insight.baseline_label
        else (
            f"Baseline: Approved #{baseline_review.approved_count}"
            if baseline_review is not None and baseline_review.approved_count
            else "Baseline: none yet"
        )
    )

    trigger_source = "repo_posture"
    if primary_insight is not None and primary_insight.review_target:
        review_target = str(primary_insight.review_target).lower()
        if review_target.startswith("pr #") or primary_insight.review_head_sha:
            trigger_source = "pull_request"
        elif "commit " in review_target:
            trigger_source = "history_review"
    elif baseline_review is not None and baseline_review.is_pending_review:
        trigger_source = "baseline_review"

    affected_dimensions = [
        dimension.label or dimension.attribute_key.replace("_", " ")
        for dimension in (primary_insight.attribute_profile or [])
        if dimension.state not in {"no_change", "unknown"}
    ] if primary_insight is not None else []

    findings = [
        RepoAuditBriefFinding(
            title=insight.title,
            artifact_path=insight.artifact_path,
            summary=insight.rationale or insight.change_summary or insight.flag_summary or "Review this artifact change.",
            evidence_label=insight.evidence_label,
            review_target=insight.review_target,
            review_url=insight.review_url,
            affected_dimensions=[
                dimension.label or dimension.attribute_key.replace("_", " ")
                for dimension in (insight.attribute_profile or [])
                if dimension.state not in {"no_change", "unknown"}
            ],
        )
        for insight in (insights or [])[:5]
    ]

    encoded_repo_full = quote(repo_full, safe="")
    actions = [
        RepoAuditBriefAction(
            label="Inspect audit evidence",
            href=f"/dashboard/{encoded_repo_full}/audit#repo-audit-brief-section",
            style="primary",
        )
    ]
    if primary_insight is not None and primary_insight.review_url:
        actions.append(
            RepoAuditBriefAction(
                label="Open source review",
                href=primary_insight.review_url,
            )
        )
    if baseline_review is not None and baseline_review.is_pending_review:
        actions.append(
            RepoAuditBriefAction(
                label="Open baseline review",
                href=f"/dashboard/{encoded_repo_full}?tab=baseline#baseline-review-panel",
            )
        )
    else:
        actions.append(
            RepoAuditBriefAction(
                label="Open repo posture",
                href=f"/dashboard/{encoded_repo_full}?tab=drift#repo-triage-section",
            )
        )

    why_now = (
        primary_insight.rationale
        if primary_insight is not None and primary_insight.rationale
        else (
            "Baseline review is still pending for repository evidence that should become the approved reference."
            if baseline_review is not None and baseline_review.is_pending_review
            else "No high-priority repo findings are currently demanding immediate action."
        )
    )
    summary = (
        primary_insight.change_summary
        if primary_insight is not None and primary_insight.change_summary
        else (
            primary_insight.recommended_action
            if primary_insight is not None and primary_insight.recommended_action
            else "Use the audit view to confirm the trigger, evidence, and recommended next step before switching to broader repo posture context."
        )
    )
    confidence_label = (
        primary_insight.confidence_label
        if primary_insight is not None and primary_insight.confidence_label
        else str(getattr(governance_posture, "review_quality", "mixed") or "mixed")
    )

    return RepoAuditBrief(
        trigger_source=trigger_source,
        recommendation_key=recommendation_key,
        recommendation_label=recommendation_label,
        severity_label=severity_label,
        severity_tone=severity_tone,
        confidence_label=confidence_label,
        changed_artifact_count=changed_artifact_count,
        review_now_count=review_now_count,
        watch_count=watch_count,
        lower_confidence_count=lower_confidence_count,
        baseline_status=baseline_status,
        baseline_reference=baseline_reference,
        why_now=why_now,
        summary=summary,
        affected_dimensions=affected_dimensions,
        findings=findings,
        actions=actions,
    )


def _build_repo_journey_panel(
    db_path: str,
    repo_full: str,
    *,
    selected_baseline_source_snapshot_id: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    from .repo_journey import _compare_snapshot_records, build_repo_journey, snapshot_to_public_payload

    snapshot_records = build_repo_journey(db_path, repo_full)
    snapshots = [snapshot_to_public_payload(snapshot) for snapshot in snapshot_records]
    snapshot_record_by_id = {snapshot.id: snapshot for snapshot in snapshot_records}
    baseline_snapshot = None
    if selected_baseline_source_snapshot_id is not None:
        baseline_snapshot = next(
            (snapshot for snapshot in snapshots if int(snapshot["id"]) == int(selected_baseline_source_snapshot_id)),
            None,
        )
    if baseline_snapshot is None:
        baseline_snapshot = next((snapshot for snapshot in snapshots if snapshot["snapshot_type"] == "baseline_approved"), None)
    current_snapshot = next((snapshot for snapshot in snapshots if snapshot["snapshot_type"] == "current"), None)
    if current_snapshot is None:
        current_snapshot = next((snapshot for snapshot in reversed(snapshots) if snapshot["snapshot_type"] == "branch_head"), None)
    comparison = None
    if baseline_snapshot is not None and current_snapshot is not None:
        baseline_record = snapshot_record_by_id.get(int(baseline_snapshot["id"]))
        current_record = snapshot_record_by_id.get(int(current_snapshot["id"]))
        if baseline_record is not None and current_record is not None:
            comparison = asdict(_compare_snapshot_records(repo_full, baseline_record, current_record))
    return snapshots, comparison


def _build_overview_repo_views(db_path: str, repos: list[RepoDashboardIndexEntry]) -> list[RepoDashboardView]:
    if not repos:
        return []

    repo_fulls = [repo.repo_full for repo in repos]
    latest_onboardings = {
        onboarding.repo_full: onboarding
        for onboarding in list_latest_repository_onboardings(db_path)
        if onboarding.repo_full in set(repo_fulls)
    }
    latest_onboarding_ids = [onboarding.id for onboarding in latest_onboardings.values()]

    artifacts_by_repo: dict[str, list[sqlite3.Row]] = {repo_full: [] for repo_full in repo_fulls}
    baselines_by_repo: dict[str, list[OnboardingBaselineVersionRecord]] = {repo_full: [] for repo_full in repo_fulls}

    with _connect(db_path) as conn:
        if latest_onboarding_ids:
            onboarding_placeholders = ", ".join("?" for _ in latest_onboarding_ids)
            artifact_rows = conn.execute(
                f"""
                SELECT ro.repo_full, oa.*
                FROM onboarded_artifacts oa
                INNER JOIN repository_onboardings ro ON ro.id = oa.onboarding_id
                WHERE oa.onboarding_id IN ({onboarding_placeholders})
                ORDER BY ro.repo_full ASC, oa.created_at ASC, oa.id ASC
                """,
                tuple(latest_onboarding_ids),
            ).fetchall()
            for row in artifact_rows:
                artifacts_by_repo.setdefault(str(row["repo_full"]), []).append(row)

            baseline_rows = conn.execute(
                f"""
                SELECT ro.repo_full, obv.*
                FROM onboarding_baseline_versions obv
                INNER JOIN repository_onboardings ro ON ro.id = obv.onboarding_id
                WHERE obv.onboarding_id IN ({onboarding_placeholders})
                ORDER BY ro.repo_full ASC, obv.created_at ASC, obv.id ASC
                """,
                tuple(latest_onboarding_ids),
            ).fetchall()
            for row in baseline_rows:
                baselines_by_repo.setdefault(str(row["repo_full"]), []).append(
                    OnboardingBaselineVersionRecord(
                        id=int(row["id"]),
                        onboarding_id=int(row["onboarding_id"]),
                        onboarded_artifact_id=int(row["onboarded_artifact_id"]),
                        normalized_artifact_id=str(row["normalized_artifact_id"]),
                        artifact_path=str(row["artifact_path"]),
                        artifact_type=str(row["artifact_type"]),
                        version_hash=str(row["version_hash"]),
                        signal_terms=json.loads(row["signal_terms_json"]),
                        line_count=int(row["line_count"]),
                        profile=_profile_from_json(str(row["profile_json"])),
                        approval_status=str(row["approval_status"]) if "approval_status" in row.keys() else "pending",
                        approved_by=(str(row["approved_by"]) if row["approved_by"] is not None else None) if "approved_by" in row.keys() else None,
                        approved_at=float(row["approved_at"]) if "approved_at" in row.keys() and row["approved_at"] is not None else None,
                        approval_note=(str(row["approval_note"]) if row["approval_note"] is not None else None) if "approval_note" in row.keys() else None,
                        created_at=float(row["created_at"]),
                        content_text=row["content_text"],
                    )
                )

        (
            backfill_summaries,
            pull_request_audit_counts,
            metrics_by_repo,
            profile_contexts_by_repo,
            drift_summaries,
            top_drifting_artifacts_by_repo,
        ) = _load_overview_batch_state(conn, repo_fulls)

    views: list[RepoDashboardView] = []
    for repo in repos:
        onboarding = latest_onboardings.get(repo.repo_full)
        if onboarding is None:
            views.append(_empty_repo_dashboard_view(repo.repo_full))
            continue

        artifact_rows = artifacts_by_repo.get(repo.repo_full, [])
        baseline_versions = baselines_by_repo.get(repo.repo_full, [])
        baseline_by_path = {
            baseline.artifact_path: baseline
            for baseline in select_effective_onboarding_baseline_versions(baseline_versions)
        }
        leaderboard_entries = top_drifting_artifacts_by_repo.get(repo.repo_full, [])
        leaderboard_by_path = {entry.artifact_path: entry for entry in leaderboard_entries}
        metrics_by_path = metrics_by_repo.get(repo.repo_full, {})
        profile_context_by_path = profile_contexts_by_repo.get(repo.repo_full, {})

        artifact_entries: list[RepoDashboardArtifactEntry] = []
        for row in artifact_rows:
            artifact_path = str(row["artifact_path"])
            artifact_type = str(row["artifact_type"])
            provenance = artifact_provenance_label(artifact_type)
            metrics = metrics_by_path.get(artifact_path, _empty_artifact_metrics())
            leaderboard_entry = leaderboard_by_path.get(artifact_path)
            baseline = baseline_by_path.get(artifact_path)
            artifact_entries.append(
                RepoDashboardArtifactEntry(
                    artifact_path=artifact_path,
                    artifact_type=artifact_type,
                    discovery_reason=str(row["discovery_reason"]),
                    discovery_confidence=float(row["confidence"]),
                    baseline_line_count=(baseline.line_count if baseline is not None else 0),
                    historical_version_count=int(metrics["historical_version_count"]),
                    historical_profile_count=int(metrics["historical_profile_count"]),
                    latest_historical_semantic_distance=float(metrics["latest_historical_semantic_distance"]),
                    latest_historical_drift_magnitude=float(metrics["latest_historical_drift_magnitude"]),
                    latest_historical_capability_shift=float(metrics["latest_historical_capability_shift"]),
                    latest_historical_guardrail_shift=float(metrics["latest_historical_guardrail_shift"]),
                    latest_historical_governance_shift=float(metrics["latest_historical_governance_shift"]),
                    latest_historical_autonomy_shift=float(metrics["latest_historical_autonomy_shift"]),
                    pr_profile_count=int(metrics["pr_profile_count"]),
                    latest_pr_semantic_distance=float(metrics["latest_pr_semantic_distance"]),
                    latest_pr_capability_shift=float(metrics["latest_pr_capability_shift"]),
                    latest_pr_guardrail_shift=float(metrics["latest_pr_guardrail_shift"]),
                    latest_pr_governance_shift=float(metrics["latest_pr_governance_shift"]),
                    latest_pr_autonomy_shift=float(metrics["latest_pr_autonomy_shift"]),
                    leaderboard_drift_magnitude=(leaderboard_entry.drift_magnitude if leaderboard_entry is not None else 0.0),
                    latest_activity_at=float(metrics["latest_activity_at"]),
                    provenance_kind=provenance.kind,
                    provenance_label=provenance.label,
                )
            )

        artifact_entries.sort(
            key=lambda entry: (
                -_insight_score(entry, profile_context_by_path.get(entry.artifact_path)),
                -max(entry.leaderboard_drift_magnitude, entry.latest_historical_drift_magnitude),
                entry.artifact_path,
            )
        )

        insights, lower_confidence_insights = _build_repo_insights(
            artifact_entries,
            baseline_by_path,
            profile_context_by_path,
            attribute_profile_mode="ranked",
        )

        views.append(
            RepoDashboardView(
                repo_full=repo.repo_full,
                onboarding=onboarding,
                baseline_review=None,
                backfill=backfill_summaries.get(repo.repo_full, _empty_backfill_summary()),
                pull_request_audit_count=pull_request_audit_counts.get(repo.repo_full, 0),
                baseline_version_count=len(baseline_versions),
                drift_summary=drift_summaries.get(repo.repo_full, _empty_repo_static_drift_summary(repo.repo_full)),
                top_drifting_artifacts=leaderboard_entries,
                insights=insights,
                lower_confidence_insights=lower_confidence_insights,
                control_surface_groups=_build_control_surface_groups(artifact_entries),
                history_timelines=[],
                featured_storyline=None,
                history_cues=[],
                design_profiles=[],
                artifacts=artifact_entries,
                journey_snapshots=[],
                journey_comparison=None,
                selected_baseline_source_snapshot_id=get_latest_baseline_snapshot_id_for_onboarding(db_path, onboarding.id),
            )
        )

    return views


def _load_overview_batch_state(
    conn: sqlite3.Connection,
    repo_fulls: list[str],
) -> tuple[
    dict[str, RepoDashboardBackfillSummary],
    dict[str, int],
    dict[str, dict[str, dict[str, float | int]]],
    dict[str, dict[str, _RepoArtifactEvidenceBundle]],
    dict[str, RepoStaticDriftSummary],
    dict[str, list[ArtifactDriftLeaderboardEntry]],
]:
    if not repo_fulls:
        return ({}, {}, {}, {}, {}, {})

    repo_placeholders = ", ".join("?" for _ in repo_fulls)
    repo_params = tuple(repo_fulls)

    backfill_counts_by_repo: dict[str, dict[str, int]] = {repo_full: {} for repo_full in repo_fulls}
    backfill_rows = conn.execute(
        f"""
        SELECT repo_full, status, COUNT(*) AS job_count
        FROM historical_backfill_jobs
        WHERE repo_full IN ({repo_placeholders})
        GROUP BY repo_full, status
        """,
        repo_params,
    ).fetchall()
    for row in backfill_rows:
        repo_full = str(row["repo_full"])
        backfill_counts_by_repo.setdefault(repo_full, {})[str(row["status"])] = int(row["job_count"])

    pull_request_audit_counts = {repo_full: 0 for repo_full in repo_fulls}
    audit_rows = conn.execute(
        f"""
        SELECT repo_full, COUNT(*) AS audit_count
        FROM pull_request_audits
        WHERE repo_full IN ({repo_placeholders})
        GROUP BY repo_full
        """,
        repo_params,
    ).fetchall()
    for row in audit_rows:
        pull_request_audit_counts[str(row["repo_full"])] = int(row["audit_count"])

    metrics_by_repo: dict[str, dict[str, dict[str, float | int]]] = {}
    historical_version_rows = conn.execute(
        f"""
        SELECT ro.repo_full, hav.artifact_path, COUNT(*) AS version_count
        FROM historical_artifact_versions hav
        INNER JOIN repository_onboardings ro ON ro.id = hav.onboarding_id
        WHERE ro.repo_full IN ({repo_placeholders})
        GROUP BY ro.repo_full, hav.artifact_path
        """,
        repo_params,
    ).fetchall()
    for row in historical_version_rows:
        repo_metrics = metrics_by_repo.setdefault(str(row["repo_full"]), {})
        repo_metrics.setdefault(str(row["artifact_path"]), _empty_artifact_metrics())["historical_version_count"] = int(row["version_count"])

    profile_contexts_by_repo: dict[str, dict[str, _RepoArtifactEvidenceBundle]] = {}
    drift_profiles_by_repo_path: dict[str, dict[str, list[dict[str, object]]]] = {}
    pr_profile_rows = conn.execute(
        f"""
        SELECT
            pra.repo_full,
            sap.artifact_path,
            pra.pr_number,
            pra.output_mode,
            pra.suggested_risk_level,
            pra.status,
            pra.semantic_review_completed,
            sap.created_at,
            sap.baseline_profile_id,
            sap.baseline_provenance_json,
            sap.artifact_version_id,
            sap.semantic_distance,
            sap.profile_json,
            sap.attribute_deltas_json,
            sap.narrative_json,
            sap.signal_terms_json,
            av.content_text AS content_text
        FROM static_artifact_profiles sap
        INNER JOIN pull_request_audits pra ON pra.id = sap.audit_id
        INNER JOIN artifact_versions av ON av.id = sap.artifact_version_id
        WHERE pra.repo_full IN ({repo_placeholders})
        ORDER BY pra.repo_full ASC, sap.artifact_path ASC, sap.created_at ASC, sap.id ASC
        """,
        repo_params,
    ).fetchall()
    for row in pr_profile_rows:
        repo_full = str(row["repo_full"])
        artifact_path = str(row["artifact_path"])
        repo_metrics = metrics_by_repo.setdefault(repo_full, {})
        metrics = repo_metrics.setdefault(artifact_path, _empty_artifact_metrics())
        metrics["pr_profile_count"] = int(metrics["pr_profile_count"]) + 1

        attribute_deltas = {key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()}
        semantic_distance = float(row["semantic_distance"])
        created_at = float(row["created_at"])
        metrics["latest_pr_semantic_distance"] = semantic_distance
        metrics["latest_pr_capability_shift"] = attribute_deltas.get("capability_risk", 0.0)
        metrics["latest_pr_guardrail_shift"] = attribute_deltas.get("guardrail_robustness", 0.0)
        metrics["latest_pr_governance_shift"] = attribute_deltas.get("governance_strength", 0.0)
        metrics["latest_pr_autonomy_shift"] = attribute_deltas.get("autonomy_level", 0.0)
        metrics["latest_activity_at"] = max(float(metrics["latest_activity_at"]), created_at)

        baseline_provenance = baseline_provenance_from_json(row["baseline_provenance_json"])
        if baseline_provenance is None and row["baseline_profile_id"] is not None:
            baseline_provenance = previous_pr_fallback_provenance(
                int(row["baseline_profile_id"]),
                int(row["artifact_version_id"]),
            )
        if baseline_provenance is None:
            baseline_provenance = no_baseline_provenance()

        pr_source = _pull_request_source_context(
            repo_full,
            int(row["pr_number"]),
            str(row["output_mode"]),
            str(row["suggested_risk_level"]),
            str(row["status"]),
            bool(row["semantic_review_completed"]),
        )
        context = _RepoArtifactProfileContext(
            profile=_profile_from_json(str(row["profile_json"])),
            source_type=pr_source["source_type"],
            label=pr_source["label"],
            source_ref=pr_source["source_ref"],
            source_url=pr_source["source_url"],
            review_context=pr_source["review_context"],
            created_at=created_at,
            baseline_provenance=baseline_provenance,
            semantic_distance=semantic_distance,
            attribute_deltas=attribute_deltas,
            narrative=json.loads(row["narrative_json"]),
            signal_terms=json.loads(row["signal_terms_json"]),
            content_text=row["content_text"],
        )
        repo_contexts = profile_contexts_by_repo.setdefault(repo_full, {})
        existing_bundle = repo_contexts.get(artifact_path, _RepoArtifactEvidenceBundle())
        repo_contexts[artifact_path] = _RepoArtifactEvidenceBundle(
            latest_pull_request=context,
            latest_historical=existing_bundle.latest_historical,
        )

    profile_rows = conn.execute(
        f"""
        SELECT
            ro.repo_full,
            hsp.id,
            hsp.artifact_path,
            hsp.artifact_type,
            hsp.commit_sha,
            hsp.branch_ref,
            hsp.triggered_by,
            hsp.created_at,
            hsp.baseline_profile_id,
            hsp.baseline_provenance_json,
            hsp.semantic_distance,
            hsp.profile_json,
            hsp.attribute_deltas_json,
            hsp.narrative_json,
            hsp.signal_terms_json,
            hav.content_text AS content_text
        FROM historical_static_profiles hsp
        INNER JOIN repository_onboardings ro ON ro.id = hsp.onboarding_id
        INNER JOIN historical_artifact_versions hav ON hav.id = hsp.historical_artifact_version_id
        WHERE ro.repo_full IN ({repo_placeholders})
        ORDER BY ro.repo_full ASC, hsp.artifact_path ASC, hsp.created_at ASC, hsp.id ASC
        """,
        repo_params,
    ).fetchall()
    for row in profile_rows:
        repo_full = str(row["repo_full"])
        artifact_path = str(row["artifact_path"])
        repo_metrics = metrics_by_repo.setdefault(repo_full, {})
        metrics = repo_metrics.setdefault(artifact_path, _empty_artifact_metrics())
        metrics["historical_profile_count"] = int(metrics["historical_profile_count"]) + 1

        attribute_deltas = {key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()}
        semantic_distance = float(row["semantic_distance"])
        created_at = float(row["created_at"])
        metrics["latest_historical_semantic_distance"] = semantic_distance
        metrics["latest_historical_drift_magnitude"] = _drift_magnitude(semantic_distance, attribute_deltas)
        metrics["latest_historical_capability_shift"] = attribute_deltas.get("capability_risk", 0.0)
        metrics["latest_historical_guardrail_shift"] = attribute_deltas.get("guardrail_robustness", 0.0)
        metrics["latest_historical_governance_shift"] = attribute_deltas.get("governance_strength", 0.0)
        metrics["latest_historical_autonomy_shift"] = attribute_deltas.get("autonomy_level", 0.0)
        metrics["latest_activity_at"] = max(float(metrics["latest_activity_at"]), created_at)

        baseline_provenance = baseline_provenance_from_json(row["baseline_provenance_json"])
        if baseline_provenance is None and row["baseline_profile_id"] is not None:
            baseline_provenance = historical_fallback_provenance(int(row["baseline_profile_id"]))
        if baseline_provenance is None:
            baseline_provenance = no_baseline_provenance()

        historical_source = _historical_source_context(
            repo_full,
            str(row["commit_sha"]),
            row["branch_ref"],
            row["triggered_by"],
        )
        context = _RepoArtifactProfileContext(
            profile=_profile_from_json(str(row["profile_json"])),
            source_type=historical_source["source_type"],
            label=historical_source["label"],
            source_ref=historical_source["source_ref"],
            source_url=historical_source["source_url"],
            review_context=historical_source["review_context"],
            created_at=created_at,
            baseline_provenance=baseline_provenance,
            semantic_distance=semantic_distance,
            attribute_deltas=attribute_deltas,
            narrative=json.loads(row["narrative_json"]),
            signal_terms=json.loads(row["signal_terms_json"]),
            content_text=row["content_text"],
        )
        repo_contexts = profile_contexts_by_repo.setdefault(repo_full, {})
        existing_bundle = repo_contexts.get(artifact_path, _RepoArtifactEvidenceBundle())
        repo_contexts[artifact_path] = _RepoArtifactEvidenceBundle(
            latest_pull_request=existing_bundle.latest_pull_request,
            latest_historical=context,
        )

        drift_profiles_by_repo_path.setdefault(repo_full, {}).setdefault(artifact_path, []).append(
            {
                "id": int(row["id"]),
                "artifact_type": str(row["artifact_type"]),
                "semantic_distance": semantic_distance,
                "attribute_deltas": attribute_deltas,
                "narrative": json.loads(row["narrative_json"]),
                "created_at": created_at,
                "baseline_provenance": baseline_provenance,
            }
        )

    backfill_summaries = {
        repo_full: RepoDashboardBackfillSummary(
            job_count=sum(status_counts.values()),
            planned_job_count=status_counts.get("planned", 0),
            processing_job_count=status_counts.get("processing", 0),
            completed_job_count=status_counts.get("completed", 0),
            failed_job_count=status_counts.get("failed", 0),
            total_historical_versions=sum(
                int(metrics["historical_version_count"])
                for metrics in metrics_by_repo.get(repo_full, {}).values()
            ),
            total_historical_profiles=sum(
                int(metrics["historical_profile_count"])
                for metrics in metrics_by_repo.get(repo_full, {}).values()
            ),
        )
        for repo_full, status_counts in backfill_counts_by_repo.items()
    }

    drift_summaries: dict[str, RepoStaticDriftSummary] = {}
    top_drifting_artifacts_by_repo: dict[str, list[ArtifactDriftLeaderboardEntry]] = {}
    for repo_full in repo_fulls:
        grouped_profiles = drift_profiles_by_repo_path.get(repo_full, {})
        artifact_paths = set(grouped_profiles)
        profile_count = sum(len(profiles) for profiles in grouped_profiles.values())
        baseline_linked_profiles: list[dict[str, object]] = []
        highest_capability_artifact_path: str | None = None
        highest_capability_delta = 0.0
        leaderboard_entries: list[ArtifactDriftLeaderboardEntry] = []

        for artifact_path, profiles in grouped_profiles.items():
            for profile in profiles:
                baseline_provenance = profile["baseline_provenance"]
                if baseline_provenance is not None and baseline_provenance.source_type != "none":
                    baseline_linked_profiles.append(profile)
                    capability_delta = round(float(profile["attribute_deltas"].get("capability_risk", 0.0)), 4)
                    if highest_capability_artifact_path is None or capability_delta >= highest_capability_delta:
                        highest_capability_artifact_path = artifact_path
                        highest_capability_delta = capability_delta

            latest = profiles[-1]
            latest_attribute_deltas = latest["attribute_deltas"]
            latest_semantic_distance = float(latest["semantic_distance"])
            leaderboard_entries.append(
                ArtifactDriftLeaderboardEntry(
                    artifact_path=artifact_path,
                    artifact_type=str(latest["artifact_type"]),
                    latest_profile_id=int(latest["id"]),
                    sample_count=len(profiles),
                    latest_created_at=float(latest["created_at"]),
                    semantic_distance=latest_semantic_distance,
                    guardrail_shift=round(float(latest_attribute_deltas.get("guardrail_robustness", 0.0)), 4),
                    capability_shift=round(float(latest_attribute_deltas.get("capability_risk", 0.0)), 4),
                    autonomy_shift=round(float(latest_attribute_deltas.get("autonomy_level", 0.0)), 4),
                    drift_magnitude=_drift_magnitude(latest_semantic_distance, latest_attribute_deltas),
                    narrative=list(latest["narrative"]),
                )
            )

        leaderboard_entries.sort(key=lambda entry: (-entry.drift_magnitude, entry.artifact_path))
        top_drifting_artifacts_by_repo[repo_full] = leaderboard_entries[:10]

        drift_summaries[repo_full] = RepoStaticDriftSummary(
            repo_full=repo_full,
            artifact_count=len(artifact_paths),
            profile_count=profile_count,
            baseline_linked_profile_count=len(baseline_linked_profiles),
            avg_semantic_distance=_average([float(profile["semantic_distance"]) for profile in baseline_linked_profiles]),
            avg_guardrail_shift=_average(
                [abs(float(profile["attribute_deltas"].get("guardrail_robustness", 0.0))) for profile in baseline_linked_profiles]
            ),
            avg_capability_shift=_average(
                [abs(float(profile["attribute_deltas"].get("capability_risk", 0.0))) for profile in baseline_linked_profiles]
            ),
            avg_autonomy_shift=_average(
                [abs(float(profile["attribute_deltas"].get("autonomy_level", 0.0))) for profile in baseline_linked_profiles]
            ),
            highest_capability_artifact_path=highest_capability_artifact_path,
            highest_capability_delta=highest_capability_delta,
        )

    for repo_full in repo_fulls:
        backfill_summaries.setdefault(repo_full, _empty_backfill_summary())
        pull_request_audit_counts.setdefault(repo_full, 0)
        drift_summaries.setdefault(repo_full, _empty_repo_static_drift_summary(repo_full))
        top_drifting_artifacts_by_repo.setdefault(repo_full, [])

    return (
        backfill_summaries,
        pull_request_audit_counts,
        metrics_by_repo,
        profile_contexts_by_repo,
        drift_summaries,
        top_drifting_artifacts_by_repo,
    )


def _empty_backfill_summary() -> RepoDashboardBackfillSummary:
    return RepoDashboardBackfillSummary(
        job_count=0,
        planned_job_count=0,
        processing_job_count=0,
        completed_job_count=0,
        failed_job_count=0,
        total_historical_versions=0,
        total_historical_profiles=0,
    )


def _empty_repo_static_drift_summary(repo_full: str) -> RepoStaticDriftSummary:
    return RepoStaticDriftSummary(
        repo_full=repo_full,
        artifact_count=0,
        profile_count=0,
        baseline_linked_profile_count=0,
        avg_semantic_distance=0.0,
        avg_guardrail_shift=0.0,
        avg_capability_shift=0.0,
        avg_autonomy_shift=0.0,
        highest_capability_artifact_path=None,
        highest_capability_delta=0.0,
    )


def _empty_repo_dashboard_view(repo_full: str) -> RepoDashboardView:
    return RepoDashboardView(
        repo_full=repo_full,
        onboarding=None,
        baseline_review=None,
        backfill=_empty_backfill_summary(),
        pull_request_audit_count=0,
        baseline_version_count=0,
        drift_summary=_empty_repo_static_drift_summary(repo_full),
        top_drifting_artifacts=[],
        insights=[],
        lower_confidence_insights=[],
        control_surface_groups=[],
        history_timelines=[],
        featured_storyline=None,
        history_cues=[],
        design_profiles=[],
        artifacts=[],
        journey_snapshots=[],
        journey_comparison=None,
    )


def _connect(db_path: str) -> sqlite3.Connection:
    return connect_sqlite(db_path)


def _empty_artifact_metrics() -> dict[str, float | int]:
    return {
        "historical_version_count": 0,
        "historical_profile_count": 0,
        "latest_historical_semantic_distance": 0.0,
        "latest_historical_drift_magnitude": 0.0,
        "latest_historical_capability_shift": 0.0,
        "latest_historical_guardrail_shift": 0.0,
        "latest_historical_governance_shift": 0.0,
        "latest_historical_autonomy_shift": 0.0,
        "pr_profile_count": 0,
        "latest_pr_semantic_distance": 0.0,
        "latest_pr_capability_shift": 0.0,
        "latest_pr_guardrail_shift": 0.0,
        "latest_pr_governance_shift": 0.0,
        "latest_pr_autonomy_shift": 0.0,
        "latest_activity_at": 0.0,
    }


@dataclass(frozen=True)
class _RepoArtifactProfileContext:
    profile: AgentAttributeProfile
    source_type: str
    label: str
    source_ref: str | None
    source_url: str | None
    review_context: str | None
    created_at: float
    baseline_provenance: BaselineProvenance | None
    semantic_distance: float
    attribute_deltas: dict[str, float]
    narrative: list[str]
    signal_terms: list[str]
    content_text: str | None
    review_head_sha: str | None = None


@dataclass(frozen=True)
class _RepoArtifactEvidenceBundle:
    latest_pull_request: _RepoArtifactProfileContext | None = None
    latest_historical: _RepoArtifactProfileContext | None = None


def _load_repo_artifact_metrics(db_path: str, repo_full: str) -> dict[str, dict[str, float | int]]:
    metrics_by_path: dict[str, dict[str, float | int]] = {}

    with _connect(db_path) as conn:
        historical_version_rows = conn.execute(
            """
            SELECT artifact_path, COUNT(*) AS version_count
            FROM historical_artifact_versions
            WHERE normalized_artifact_id LIKE ?
            GROUP BY artifact_path
            """,
            (_normalized_id_prefix(repo_full),),
        ).fetchall()
        for row in historical_version_rows:
            metrics_by_path.setdefault(row["artifact_path"], _empty_artifact_metrics())["historical_version_count"] = row["version_count"]

        historical_profile_rows = conn.execute(
            """
            SELECT artifact_path, semantic_distance, attribute_deltas_json, created_at
            FROM historical_static_profiles
            WHERE normalized_artifact_id LIKE ?
            ORDER BY created_at ASC, id ASC
            """,
            (_normalized_id_prefix(repo_full),),
        ).fetchall()
        for row in historical_profile_rows:
            metrics = metrics_by_path.setdefault(row["artifact_path"], _empty_artifact_metrics())
            metrics["historical_profile_count"] = int(metrics["historical_profile_count"]) + 1
            attribute_deltas = {key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()}
            semantic_distance = float(row["semantic_distance"])
            metrics["latest_historical_semantic_distance"] = semantic_distance
            metrics["latest_historical_drift_magnitude"] = _drift_magnitude(semantic_distance, attribute_deltas)
            metrics["latest_historical_capability_shift"] = attribute_deltas.get("capability_risk", 0.0)
            metrics["latest_historical_guardrail_shift"] = attribute_deltas.get("guardrail_robustness", 0.0)
            metrics["latest_historical_governance_shift"] = attribute_deltas.get("governance_strength", 0.0)
            metrics["latest_historical_autonomy_shift"] = attribute_deltas.get("autonomy_level", 0.0)
            metrics["latest_activity_at"] = max(float(metrics["latest_activity_at"]), float(row["created_at"]))
        pr_profile_rows = conn.execute(
            """
            SELECT sap.artifact_path, sap.semantic_distance, sap.attribute_deltas_json, sap.created_at
            FROM static_artifact_profiles sap
            INNER JOIN pull_request_audits pra ON pra.id = sap.audit_id
            WHERE pra.repo_full = ?
            ORDER BY sap.created_at ASC, sap.id ASC
            """,
            (repo_full,),
        ).fetchall()
        for row in pr_profile_rows:
            metrics = metrics_by_path.setdefault(row["artifact_path"], _empty_artifact_metrics())
            metrics["pr_profile_count"] = int(metrics["pr_profile_count"]) + 1
            attribute_deltas = {key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()}
            semantic_distance = float(row["semantic_distance"])
            metrics["latest_pr_semantic_distance"] = semantic_distance
            metrics["latest_pr_capability_shift"] = attribute_deltas.get("capability_risk", 0.0)
            metrics["latest_pr_guardrail_shift"] = attribute_deltas.get("guardrail_robustness", 0.0)
            metrics["latest_pr_governance_shift"] = attribute_deltas.get("governance_strength", 0.0)
            metrics["latest_pr_autonomy_shift"] = attribute_deltas.get("autonomy_level", 0.0)
            metrics["latest_activity_at"] = max(float(metrics["latest_activity_at"]), float(row["created_at"]))

    return metrics_by_path


def _load_repo_artifact_profile_contexts(db_path: str, repo_full: str) -> dict[str, _RepoArtifactEvidenceBundle]:
    contexts: dict[str, _RepoArtifactEvidenceBundle] = {}
    with _connect(db_path) as conn:
        pr_rows = conn.execute(
            """
            SELECT sap.artifact_path, pra.pr_number, pra.output_mode, pra.suggested_risk_level, pra.status, pra.semantic_review_completed,
                pra.head_sha,
                sap.created_at, sap.baseline_profile_id, sap.baseline_provenance_json, sap.artifact_version_id,
                sap.semantic_distance, sap.profile_json, sap.attribute_deltas_json, sap.narrative_json, sap.signal_terms_json,
                av.content_text AS content_text
            FROM static_artifact_profiles sap
            INNER JOIN pull_request_audits pra ON pra.id = sap.audit_id
            INNER JOIN artifact_versions av ON av.id = sap.artifact_version_id
            WHERE pra.repo_full = ?
            ORDER BY sap.created_at ASC, sap.id ASC
            """,
            (repo_full,),
        ).fetchall()
        for row in pr_rows:
            artifact_path = row["artifact_path"]
            baseline_provenance = baseline_provenance_from_json(row["baseline_provenance_json"])
            if baseline_provenance is None and row["baseline_profile_id"] is not None:
                baseline_provenance = previous_pr_fallback_provenance(
                    int(row["baseline_profile_id"]),
                    int(row["artifact_version_id"]),
                )
            if baseline_provenance is None:
                baseline_provenance = no_baseline_provenance()
            pr_source = _pull_request_source_context(
                repo_full,
                int(row["pr_number"]),
                str(row["output_mode"]),
                str(row["suggested_risk_level"]),
                str(row["status"]),
                bool(row["semantic_review_completed"]),
            )
            context = _RepoArtifactProfileContext(
                profile=_profile_from_json(row["profile_json"]),
                source_type=pr_source["source_type"],
                label=pr_source["label"],
                source_ref=pr_source["source_ref"],
                source_url=pr_source["source_url"],
                review_head_sha=str(row["head_sha"] or "").strip() or None,
                review_context=pr_source["review_context"],
                created_at=float(row["created_at"]),
                baseline_provenance=baseline_provenance,
                semantic_distance=float(row["semantic_distance"]),
                attribute_deltas={key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()},
                narrative=json.loads(row["narrative_json"]),
                signal_terms=json.loads(row["signal_terms_json"]),
                content_text=row["content_text"],
            )
            bundle = contexts.get(artifact_path, _RepoArtifactEvidenceBundle())
            contexts[artifact_path] = _RepoArtifactEvidenceBundle(
                latest_pull_request=context,
                latest_historical=bundle.latest_historical,
            )

        historical_rows = conn.execute(
            """
             SELECT hsp.artifact_path, hsp.commit_sha, hsp.created_at, hsp.baseline_profile_id, hsp.baseline_provenance_json,
                 hsp.branch_ref, hsp.triggered_by, hsp.semantic_distance, hsp.profile_json, hsp.attribute_deltas_json, hsp.narrative_json, hsp.signal_terms_json,
                 hav.content_text AS content_text
             FROM historical_static_profiles hsp
             INNER JOIN historical_artifact_versions hav ON hav.id = hsp.historical_artifact_version_id
             WHERE hsp.normalized_artifact_id LIKE ?
            ORDER BY hsp.created_at ASC, hsp.id ASC
            """,
            (_normalized_id_prefix(repo_full),),
        ).fetchall()
        for row in historical_rows:
            artifact_path = row["artifact_path"]
            baseline_provenance = baseline_provenance_from_json(row["baseline_provenance_json"])
            if baseline_provenance is None and row["baseline_profile_id"] is not None:
                baseline_provenance = historical_fallback_provenance(row["baseline_profile_id"])
            if baseline_provenance is None:
                baseline_provenance = no_baseline_provenance()
            historical_source = _historical_source_context(
                repo_full,
                str(row["commit_sha"]),
                row["branch_ref"],
                row["triggered_by"],
            )
            context = _RepoArtifactProfileContext(
                profile=_profile_from_json(row["profile_json"]),
                source_type=historical_source["source_type"],
                label=historical_source["label"],
                source_ref=historical_source["source_ref"],
                source_url=historical_source["source_url"],
                review_head_sha=None,
                review_context=historical_source["review_context"],
                created_at=float(row["created_at"]),
                baseline_provenance=baseline_provenance,
                semantic_distance=float(row["semantic_distance"]),
                attribute_deltas={key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()},
                narrative=json.loads(row["narrative_json"]),
                signal_terms=json.loads(row["signal_terms_json"]),
                content_text=row["content_text"],
            )
            bundle = contexts.get(artifact_path, _RepoArtifactEvidenceBundle())
            contexts[artifact_path] = _RepoArtifactEvidenceBundle(
                latest_pull_request=bundle.latest_pull_request,
                latest_historical=context,
            )

    return contexts


def _preferred_profile_context(bundle: _RepoArtifactEvidenceBundle | None) -> _RepoArtifactProfileContext | None:
    if bundle is None:
        return None
    if bundle.latest_historical is not None:
        return bundle.latest_historical
    return bundle.latest_pull_request


def _primary_review_context(bundle: _RepoArtifactEvidenceBundle | None) -> _RepoArtifactProfileContext | None:
    if bundle is None:
        return None
    if bundle.latest_pull_request is not None:
        return bundle.latest_pull_request
    return bundle.latest_historical


def _supporting_profile_context(bundle: _RepoArtifactEvidenceBundle | None) -> _RepoArtifactProfileContext | None:
    if bundle is None or bundle.latest_pull_request is None:
        return None
    return bundle.latest_historical


def _normalized_id_prefix(repo_full: str) -> str:
    return f"{repo_full.lower()}::%"


def _github_pull_request_url(repo_full: str, pr_number: int) -> str:
    return f"https://github.com/{repo_full}/pull/{pr_number}"


def _pull_request_source_context(
    repo_full: str,
    pr_number: int,
    output_mode: str,
    risk_level: str,
    status: str,
    semantic_review_completed: bool,
) -> dict[str, str]:
    return {
        "source_type": "pull_request",
        "label": "Pull request proposal",
        "source_ref": f"PR #{pr_number}",
        "source_url": _github_pull_request_url(repo_full, pr_number),
        "review_context": _format_pr_review_context(
            output_mode=output_mode,
            risk_level=risk_level,
            status=status,
            semantic_review_completed=semantic_review_completed,
        ),
    }


def _github_commit_url(repo_full: str, commit_sha: str) -> str:
    return f"https://github.com/{repo_full}/commit/{commit_sha}"


def _historical_source_context(repo_full: str, commit_sha: str, branch_ref: object, triggered_by: object) -> dict[str, str]:
    branch_ref_value = str(branch_ref or "")
    triggered_by_value = str(triggered_by or "historical_backfill")
    if triggered_by_value in {"push_webhook", "scheduled", "manual"} and branch_ref_value.startswith("refs/heads/"):
        branch_name = branch_ref_value.removeprefix("refs/heads/")
        return {
            "source_type": "branch_head",
            "label": "Default branch head",
            "source_ref": f"{branch_name} @ {commit_sha[:7]}",
            "source_url": _github_commit_url(repo_full, commit_sha),
            "review_context": "Live default-branch scan",
        }
    return {
        "source_type": "historical",
        "label": "Historical backfill",
        "source_ref": f"commit {commit_sha[:7]}",
        "source_url": _github_commit_url(repo_full, commit_sha),
        "review_context": "Historical snapshot from backfill",
    }


def _humanize_output_mode(output_mode: str) -> str:
    return output_mode.replace("_", " ")


def _format_pr_review_context(*, output_mode: str, risk_level: str, status: str, semantic_review_completed: bool) -> str:
    review_mode = _humanize_output_mode(output_mode)
    semantic_note = "semantic complete" if semantic_review_completed else status.replace("_", " ")
    return f"{review_mode} · {semantic_note} · risk {risk_level.lower()}"


def _drift_magnitude(semantic_distance: float, attribute_deltas: dict[str, float]) -> float:
    return round(
        abs(attribute_deltas.get("guardrail_robustness", 0.0))
        + abs(attribute_deltas.get("capability_risk", 0.0))
        + abs(attribute_deltas.get("autonomy_level", 0.0))
        + semantic_distance,
        4,
    )


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _build_repo_insights(
    artifacts: list[RepoDashboardArtifactEntry],
    baseline_by_path,
    profile_context_by_path: dict[str, _RepoArtifactEvidenceBundle],
    *,
    attribute_profile_mode: str = "all",
) -> tuple[list[RepoDashboardInsightEntry], list[RepoDashboardInsightEntry]]:
    primary_ranked: list[tuple[float, RepoDashboardInsightEntry]] = []
    lower_confidence_ranked: list[tuple[float, RepoDashboardInsightEntry]] = []
    enrichable_profiles: dict[str, tuple[RepoDashboardArtifactEntry, Any, _RepoArtifactProfileContext | None]] = {}
    for artifact in artifacts:
        evidence_bundle = profile_context_by_path.get(artifact.artifact_path)
        score = _insight_score(artifact, evidence_bundle)
        priority = priority_from_fused_signals(score)
        baseline = baseline_by_path.get(artifact.artifact_path)
        context = _preferred_profile_context(evidence_bundle)
        review_context = _primary_review_context(evidence_bundle)
        attribute_profile = None
        if attribute_profile_mode == "all":
            attribute_profile = _build_insight_attribute_profile(artifact, baseline, context)
        else:
            enrichable_profiles[artifact.artifact_path] = (artifact, baseline, context)
        queue_lane = _insight_queue_lane(artifact, priority, score)
        title = _insight_title(artifact, priority, evidence_bundle)
        rationale = _insight_rationale(artifact, priority, evidence_bundle)
        recommended_action = _insight_action(artifact, priority, evidence_bundle)
        insight = RepoDashboardInsightEntry(
            title=title,
            artifact_path=artifact.artifact_path,
            artifact_type=artifact.artifact_type,
            priority=priority,
            queue_lane=queue_lane,
            score=score,
            confidence_label=_confidence_label(artifact.discovery_confidence),
            evidence_label=_evidence_label(evidence_bundle),
            evidence_summary=_evidence_summary(evidence_bundle),
            baseline_label=_baseline_label(baseline, evidence_bundle),
            provenance_summary=_provenance_summary(evidence_bundle),
            review_target=_review_target(evidence_bundle),
            review_url=_review_url(evidence_bundle),
            review_head_sha=(review_context.review_head_sha if review_context is not None else None),
            supporting_review_target=_supporting_review_target(evidence_bundle),
            supporting_review_url=_supporting_review_url(evidence_bundle),
            change_summary=_change_summary(artifact, evidence_bundle),
            flag_summary=_flag_summary(artifact, priority, evidence_bundle),
            updated_at=(review_context.created_at if review_context is not None else artifact.latest_activity_at or None),
            risk_reasons=_insight_reasons(artifact, evidence_bundle),
            rationale=rationale,
            recommended_action=recommended_action,
            attribute_profile=attribute_profile,
        )
        ranked_list = primary_ranked if queue_lane == "primary" else lower_confidence_ranked
        ranked_list.append(
            (
                score,
                insight,
            )
        )

    sort_key = lambda item: (
        priority_sort_rank(item[1].priority),
        -item[0],
        -(item[1].updated_at or 0.0),
        item[1].artifact_path,
    )
    primary_ranked.sort(key=sort_key)
    lower_confidence_ranked.sort(key=sort_key)
    primary_entries = [entry for _, entry in primary_ranked[:8]]
    lower_confidence_entries = [entry for _, entry in lower_confidence_ranked[:6]]

    if attribute_profile_mode == "ranked":
        primary_entries = _enrich_ranked_insights_with_attribute_profiles(primary_entries, enrichable_profiles)
        lower_confidence_entries = _enrich_ranked_insights_with_attribute_profiles(lower_confidence_entries, enrichable_profiles)

    return primary_entries, lower_confidence_entries


def _build_insight_attribute_profile(
    artifact: RepoDashboardArtifactEntry,
    baseline: Any | None,
    context: _RepoArtifactProfileContext | None,
) -> list[AttributeProfileDimension]:
    baseline_profile = baseline.profile if baseline is not None else None
    current_profile = context.profile if context is not None else None
    baseline_signal_terms = baseline.signal_terms if baseline is not None else []
    current_signal_terms = context.signal_terms if context is not None else []
    baseline_content = baseline.content_text if baseline is not None else None
    current_content = context.content_text if context is not None else None
    return build_artifact_attribute_profile(
        artifact_path=artifact.artifact_path,
        artifact_type=artifact.artifact_type,
        baseline_profile=baseline_profile,
        current_profile=current_profile,
        attribute_deltas=(context.attribute_deltas if context is not None else {}),
        baseline_signal_terms=baseline_signal_terms,
        current_signal_terms=current_signal_terms,
        baseline_content=baseline_content,
        current_content=current_content,
    ).dimensions


def _enrich_ranked_insights_with_attribute_profiles(
    insights: list[RepoDashboardInsightEntry],
    enrichable_profiles: dict[str, tuple[RepoDashboardArtifactEntry, Any | None, _RepoArtifactProfileContext | None]],
) -> list[RepoDashboardInsightEntry]:
    enriched: list[RepoDashboardInsightEntry] = []
    for insight in insights:
        profile_parts = enrichable_profiles.get(insight.artifact_path)
        if profile_parts is None:
            enriched.append(insight)
            continue
        artifact, baseline, context = profile_parts
        enriched.append(replace(insight, attribute_profile=_build_insight_attribute_profile(artifact, baseline, context)))
    return enriched


def _insight_score(
    artifact: RepoDashboardArtifactEntry,
    evidence_bundle: _RepoArtifactEvidenceBundle | None = None,
) -> float:
    primary_context = _primary_review_context(evidence_bundle)
    primary_deltas = _attribute_deltas_for_summary(artifact, primary_context)
    type_weight = {
        "guardrail": 0.5,
        "system_prompt": 0.42,
        "prompt": 0.4,
        "tooling": 0.35,
        "model_config": 0.28,
        "retrieval": 0.24,
        "ai_code": 0.26,
        "policy": 0.4,
    }.get(artifact.artifact_type, 0.15)
    primary_drift_signal = (
        _drift_magnitude(primary_context.semantic_distance, primary_deltas)
        if primary_context is not None
        else artifact.latest_historical_drift_magnitude
    )
    drift_signal = max(artifact.leaderboard_drift_magnitude, artifact.latest_historical_drift_magnitude, primary_drift_signal)
    blast_radius = _blast_radius_weight(artifact)
    guardrail_regression = abs(min(float(primary_deltas.get("guardrail_robustness", artifact.latest_historical_guardrail_shift)), 0.0))
    governance_regression = abs(min(float(primary_deltas.get("governance_strength", artifact.latest_historical_governance_shift)), 0.0))
    capability_expansion = max(float(primary_deltas.get("capability_risk", artifact.latest_historical_capability_shift)), 0.0)
    autonomy_increase = max(float(primary_deltas.get("autonomy_level", artifact.latest_historical_autonomy_shift)), 0.0)
    confidence_bonus = artifact.discovery_confidence * 0.14
    recency_bonus = 0.12 if artifact.latest_activity_at > 0 else 0.0
    history_bonus = min(artifact.historical_version_count, 5) * 0.04
    proposal_bonus = 0.1 if evidence_bundle is not None and evidence_bundle.latest_pull_request is not None else 0.0
    return round(
        type_weight
        + blast_radius
        + drift_signal
        + guardrail_regression
        + governance_regression
        + capability_expansion
        + autonomy_increase
        + confidence_bonus
        + recency_bonus
        + history_bonus
        + proposal_bonus
        ,
        4,
    )


def _insight_title(
    artifact: RepoDashboardArtifactEntry,
    priority: str,
    evidence_bundle: _RepoArtifactEvidenceBundle | None = None,
) -> str:
    primary_context = _primary_review_context(evidence_bundle)
    primary_deltas = _attribute_deltas_for_summary(artifact, primary_context)
    capability_shift = float(primary_deltas.get("capability_risk", artifact.latest_historical_capability_shift))
    guardrail_shift = float(primary_deltas.get("guardrail_robustness", artifact.latest_historical_guardrail_shift))
    governance_shift = float(primary_deltas.get("governance_strength", artifact.latest_historical_governance_shift))
    drift_magnitude = (
        _drift_magnitude(primary_context.semantic_distance, primary_deltas)
        if primary_context is not None
        else artifact.latest_historical_drift_magnitude
    )
    if _blast_radius_weight(artifact) >= 0.45 and capability_shift > 0.05:
        return "Critical control surface expanded authority"
    if capability_shift > 0.05:
        return "Capability expansion needs review"
    if guardrail_shift < -0.05:
        return "Guardrail regression needs review"
    if governance_shift < -0.05:
        return "Governance regression needs review"
    if drift_magnitude > 0.35:
        if evidence_bundle is not None and evidence_bundle.latest_pull_request is not None:
            return "Design drift hotspot needs review"
        return "Historical drift hotspot"
    if priority == "baseline_review":
        return "High-value control surface to baseline"
    return "Potentially important control surface"


def _insight_rationale(
    artifact: RepoDashboardArtifactEntry,
    priority: str,
    evidence_bundle: _RepoArtifactEvidenceBundle | None,
) -> str:
    baseline_context = "relative to the current baseline"
    primary_context = _primary_review_context(evidence_bundle)
    primary_deltas = _attribute_deltas_for_summary(artifact, primary_context)
    capability_shift = float(primary_deltas.get("capability_risk", artifact.latest_historical_capability_shift))
    guardrail_shift = float(primary_deltas.get("guardrail_robustness", artifact.latest_historical_guardrail_shift))
    governance_shift = float(primary_deltas.get("governance_strength", artifact.latest_historical_governance_shift))
    drift_magnitude = (
        _drift_magnitude(primary_context.semantic_distance, primary_deltas)
        if primary_context is not None
        else artifact.latest_historical_drift_magnitude
    )
    has_history = evidence_bundle is not None and evidence_bundle.latest_historical is not None
    has_proposal = evidence_bundle is not None and evidence_bundle.latest_pull_request is not None
    if _blast_radius_weight(artifact) >= 0.45 and capability_shift > 0.05:
        if has_proposal:
            return f"The current PR proposal broadens authority {baseline_context}, increasing blast radius and review urgency."
        return f"A critical control surface broadened authority {baseline_context}, increasing blast radius and review urgency."
    if capability_shift > 0.05 and guardrail_shift < -0.05:
        if has_proposal and has_history:
            return f"The current PR proposal suggests broader authority while merged history shows guardrails weakening {baseline_context}."
        if has_proposal:
            return f"The current PR proposal suggests broader authority while guardrails weaken {baseline_context}."
        return f"Merged history suggests broader authority while guardrails weakened {baseline_context}."
    if capability_shift > 0.05:
        if has_proposal:
            return f"The current PR proposal increases capability risk {baseline_context}."
        return f"Merged history increased capability risk {baseline_context}."
    if guardrail_shift < -0.05:
        if has_proposal:
            return f"The current PR proposal weakens guardrail posture {baseline_context}."
        return f"Merged history weakened guardrail posture {baseline_context}."
    if governance_shift < -0.05:
        if has_proposal:
            return f"The current PR proposal weakens governance or approval posture {baseline_context}."
        return f"Merged history weakened governance or approval posture {baseline_context}."
    if drift_magnitude > 0.35:
        if has_proposal and has_history:
            return "The current PR proposal sits on top of meaningful historical design movement, so the reviewer should inspect both the proposal and the merged history."
        if has_proposal:
            return "The current PR proposal shows meaningful design movement and should be reviewed against the approved baseline before merge."
        if not has_history:
            return "Merged history shows meaningful design movement, but no merged commit snapshot is stored yet, so the latest commit trail should be reviewed first."
        return "Historical snapshots show meaningful design movement that deserves a human read."
    if priority == "baseline_review":
        return "This artifact looks like a real AI control surface but does not yet have meaningful drift context."
    return "This artifact is likely part of the AI control surface and should stay on the review radar."


def _insight_action(
    artifact: RepoDashboardArtifactEntry,
    priority: str,
    evidence_bundle: _RepoArtifactEvidenceBundle | None,
) -> str:
    primary_context = _primary_review_context(evidence_bundle)
    primary_deltas = _attribute_deltas_for_summary(artifact, primary_context)
    capability_shift = float(primary_deltas.get("capability_risk", artifact.latest_historical_capability_shift))
    guardrail_shift = float(primary_deltas.get("guardrail_robustness", artifact.latest_historical_guardrail_shift))
    drift_magnitude = (
        _drift_magnitude(primary_context.semantic_distance, primary_deltas)
        if primary_context is not None
        else artifact.latest_historical_drift_magnitude
    )
    has_history = evidence_bundle is not None and evidence_bundle.latest_historical is not None
    has_proposal = evidence_bundle is not None and evidence_bundle.latest_pull_request is not None
    if _blast_radius_weight(artifact) >= 0.45 and capability_shift > 0.05:
        if has_proposal and has_history:
            return "Escalate this surface to the AI platform owner, inspect the linked PR first, then compare it against the supporting merged history."
        if has_proposal:
            return "Escalate this surface to the AI platform owner and inspect the linked PR before accepting the change."
        return "Escalate this surface to the AI platform owner and inspect the linked merged commit first."
    if capability_shift > 0.05:
        if has_proposal and has_history:
            return "Inspect authority, tool access, and production-facing behavior in the linked PR first, then compare it against the supporting merged history before accepting this change."
        if has_proposal:
            return "Inspect authority, tool access, and production-facing behavior in the linked PR before accepting this change."
        return "Inspect authority, tool access, and production-facing behavior in the linked merged commit before accepting this change."
    if guardrail_shift < -0.05:
        if has_proposal:
            return "Review missing constraints, escalation paths, and refusal language in the linked PR before updating the baseline."
        return "Review missing constraints, escalation paths, and refusal language in the linked merged commit before updating the baseline."
    if artifact.latest_historical_governance_shift < -0.05:
        return "Check whether approvals, review gates, or audit instructions were weakened and route this change for human review."
    if drift_magnitude > 0.35:
        if has_proposal and has_history:
            return "Open the linked PR first, then compare it against the approved baseline and the supporting merged history before escalating."
        if has_proposal:
            return "Open the linked PR first, then compare it against the approved baseline before escalating."
        if not has_history:
            return "Open the linked merged commit first, then compare it against the approved baseline and nearby history before escalating."
        return "Open the artifact history and compare the earliest and latest versions to understand the behavior shift."
    if priority == "baseline_review":
        return "Confirm whether this is a true AI control surface and keep it in the monitored baseline set."
    return "Track this artifact, but prioritize stronger capability or guardrail signals first."


def _insight_queue_lane(artifact: RepoDashboardArtifactEntry, priority: str, score: float) -> str:
    if artifact.discovery_confidence < 0.78 and priority != "review_now" and score < 1.6:
        return "lower_confidence"
    return "primary"


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.9:
        return "high confidence"
    if confidence >= 0.78:
        return "medium confidence"
    return "lower confidence"


def _blast_radius_weight(artifact: RepoDashboardArtifactEntry) -> float:
    haystack = f"{artifact.artifact_path} {artifact.discovery_reason}".lower()
    critical_terms = (
        "refund",
        "billing",
        "payment",
        "invoice",
        "wallet",
        "bank",
        "payroll",
        "auth",
        "identity",
        "token",
        "credential",
        "export",
        "prod",
        "production",
        "pii",
    )
    important_terms = ("customer", "account", "approval", "admin", "tool", "sandbox")
    if any(term in haystack for term in critical_terms):
        return 0.5
    if any(term in haystack for term in important_terms):
        return 0.25
    if artifact.artifact_type in {"tooling", "guardrail", "policy", "system_prompt"}:
        return 0.18
    return 0.0


def _insight_reasons(
    artifact: RepoDashboardArtifactEntry,
    evidence_bundle: _RepoArtifactEvidenceBundle | None,
) -> list[str]:
    reasons: list[str] = []
    if _blast_radius_weight(artifact) >= 0.45:
        reasons.append("critical surface")
    if artifact.latest_historical_capability_shift > 0.05:
        reasons.append("capability expanded")
    if artifact.latest_historical_guardrail_shift < -0.05:
        reasons.append("guardrails weakened")
    if artifact.latest_historical_governance_shift < -0.05:
        reasons.append("governance weakened")
    if artifact.latest_historical_autonomy_shift > 0.05:
        reasons.append("autonomy increased")
    if artifact.latest_historical_drift_magnitude > 0.35:
        reasons.append("historical hotspot")
    if evidence_bundle is not None and evidence_bundle.latest_pull_request is not None and evidence_bundle.latest_historical is not None:
        reasons.append("proposal evidence")
        reasons.append("history-backed")
    elif evidence_bundle is not None and evidence_bundle.latest_pull_request is not None:
        reasons.append("proposal-only evidence")
    elif evidence_bundle is not None and evidence_bundle.latest_historical is not None:
        reasons.append("history-only evidence")
    if not reasons:
        reasons.append(_confidence_label(artifact.discovery_confidence))
    return reasons


def _baseline_label(baseline, evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str:
    context = _preferred_profile_context(evidence_bundle)
    provenance = context.baseline_provenance if context is not None else None
    if provenance is None and baseline is not None:
        provenance = approved_onboarding_provenance(
            baseline.id,
            is_authoritative=baseline.approval_status == "approved",
            approval_status=baseline.approval_status,
            approved_by=baseline.approved_by,
            approved_at=baseline.approved_at,
            approval_note=baseline.approval_note,
        )
    if provenance is None:
        return "Baseline: none yet"
    if provenance.is_authoritative:
        source_id = provenance.source_version_id if provenance.source_version_id is not None else baseline.id if baseline is not None else None
        suffix = f" #{source_id}" if source_id is not None else ""
        return f"Baseline: Approved{suffix}"
    if provenance.source_type == "approved_baseline" and provenance.approval_status == "rejected":
        source_id = provenance.source_version_id if provenance.source_version_id is not None else baseline.id if baseline is not None else None
        suffix = f" #{source_id}" if source_id is not None else ""
        return f"Baseline: Rejected candidate{suffix}"
    if provenance.source_type == "approved_baseline":
        source_id = provenance.source_version_id if provenance.source_version_id is not None else baseline.id if baseline is not None else None
        suffix = f" #{source_id}" if source_id is not None else ""
        return f"Baseline: Pending approval{suffix}"
    if provenance.source_type == "historical_reference":
        return "Baseline: Auto-baseline (historical fallback)"
    if provenance.source_type == "previous_pr_reference":
        return "Baseline: Auto-baseline (previous PR fallback)"
    return "Baseline: none yet"


def _evidence_label(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str:
    if evidence_bundle is None:
        return "baseline only"
    if evidence_bundle.latest_pull_request is not None and evidence_bundle.latest_historical is not None:
        return "proposal + history"
    if evidence_bundle.latest_pull_request is not None:
        return "proposal only"
    if evidence_bundle.latest_historical is not None:
        return "history only"
    return "baseline only"


def _evidence_summary(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str:
    if evidence_bundle is None:
        return "No merged-history evidence yet."
    preferred = _primary_review_context(evidence_bundle)
    if preferred is None:
        return "No merged-history evidence yet."
    supporting = _supporting_profile_context(evidence_bundle)
    if evidence_bundle.latest_pull_request is not None and supporting is not None:
        return (
            f"PR proposal evidence is available right now; start with {preferred.source_ref or preferred.label}, "
            f"then compare against merged history from {supporting.source_ref or supporting.label}."
        )
    if evidence_bundle.latest_pull_request is not None:
        return f"Only PR proposal evidence is available right now; start with {preferred.source_ref or preferred.label}."
    return f"Only merged-history evidence is available right now; start with {preferred.source_ref or preferred.label}."


def _provenance_summary(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str:
    context = _primary_review_context(evidence_bundle)
    if context is None:
        return "No merged-history provenance yet"
    parts = ["From", context.source_ref or context.label, context.review_context]
    supporting = _supporting_profile_context(evidence_bundle)
    if supporting is not None:
        parts.append(f"supporting merged history {supporting.source_ref or supporting.label}")
    return " · ".join(part for part in parts if part)


def _review_target(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str | None:
    context = _primary_review_context(evidence_bundle)
    if context is None:
        return None
    return context.source_ref or context.label


def _review_url(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str | None:
    context = _primary_review_context(evidence_bundle)
    if context is None:
        return None
    return context.source_url


def _supporting_review_target(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str | None:
    context = _supporting_profile_context(evidence_bundle)
    if context is None:
        return None
    return context.source_ref or context.label


def _supporting_review_url(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str | None:
    context = _supporting_profile_context(evidence_bundle)
    if context is None:
        return None
    return context.source_url


def _change_summary(artifact: RepoDashboardArtifactEntry, evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str:
    context = _primary_review_context(evidence_bundle)
    attribute_deltas = _attribute_deltas_for_summary(artifact, context)
    source_label = _sentence_source_label(context)
    changed_labels = _changed_attribute_labels(attribute_deltas)
    if changed_labels:
        return f"{source_label} drift detected in {_human_join(changed_labels)}."
    if artifact.latest_historical_drift_magnitude > 0.35:
        return f"{source_label} drift detected relative to baseline; no single high-risk attribute dominated."
    return "No material baseline-relative shift has been isolated yet."


def _flag_summary(
    artifact: RepoDashboardArtifactEntry,
    priority: str,
    evidence_bundle: _RepoArtifactEvidenceBundle | None,
) -> str:
    has_history = evidence_bundle is not None and evidence_bundle.latest_historical is not None
    if _blast_radius_weight(artifact) >= 0.45 and artifact.latest_historical_capability_shift > 0.05 and artifact.latest_historical_guardrail_shift < -0.05:
        return "Flagged because a critical surface broadened authority while guardrails weakened."
    if _blast_radius_weight(artifact) >= 0.45 and artifact.latest_historical_capability_shift > 0.05:
        return "Flagged because a critical surface gained broader authority than the baseline."
    if artifact.latest_historical_capability_shift > 0.05 and artifact.latest_historical_guardrail_shift < -0.05:
        return "Flagged because authority expanded while safety boundaries weakened."
    if artifact.latest_historical_capability_shift > 0.05:
        return "Flagged because capability expanded on a monitored control surface."
    if artifact.latest_historical_guardrail_shift < -0.05:
        return "Flagged because guardrails look weaker than the approved baseline."
    if artifact.latest_historical_governance_shift < -0.05:
        return "Flagged because governance or approval posture looks weaker than baseline."
    if artifact.latest_historical_autonomy_shift > 0.05:
        return "Flagged because the system appears more autonomous than baseline."
    if artifact.latest_historical_drift_magnitude > 0.35:
        if not has_history:
            return "Flagged because merged history shows repeated design movement, but no merged commit snapshot is stored yet."
        return "Flagged because repeated historical design movement makes this worth human review."
    if priority == "baseline_review":
        return "Flagged because this looks like a high-value AI control surface that still needs stronger evidence."
    return "Flagged because this artifact still contributes meaningful AI-control-surface risk."


def _attribute_deltas_for_summary(
    artifact: RepoDashboardArtifactEntry,
    context: _RepoArtifactProfileContext | None,
) -> dict[str, float]:
    if context is not None:
        return context.attribute_deltas
    return {
        "capability_risk": artifact.latest_historical_capability_shift,
        "guardrail_robustness": artifact.latest_historical_guardrail_shift,
        "governance_strength": artifact.latest_historical_governance_shift,
        "autonomy_level": artifact.latest_historical_autonomy_shift,
        "stability_vs_creativity": 0.0,
    }


def _sentence_source_label(context: _RepoArtifactProfileContext | None) -> str:
    if context is None:
        return "Latest change"
    label = context.source_ref or context.label or "Latest change"
    return f"{label[0].upper()}{label[1:]}" if label else "Latest change"


def _human_join(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _flagged_attribute_labels(attribute_deltas: dict[str, float]) -> list[str]:
    labels: list[str] = []
    if float(attribute_deltas.get("guardrail_robustness", 0.0)) <= -0.03:
        labels.append("guardrails")
    if float(attribute_deltas.get("capability_risk", 0.0)) >= 0.03:
        labels.append("capability")
    if float(attribute_deltas.get("autonomy_level", 0.0)) >= 0.03:
        labels.append("autonomy")
    if float(attribute_deltas.get("governance_strength", 0.0)) <= -0.03:
        labels.append("governance")
    return labels


def _changed_attribute_labels(attribute_deltas: dict[str, float]) -> list[str]:
    labels: list[str] = []
    if abs(float(attribute_deltas.get("guardrail_robustness", 0.0))) >= 0.03:
        labels.append("guardrails")
    if abs(float(attribute_deltas.get("capability_risk", 0.0))) >= 0.03:
        labels.append("capability")
    if abs(float(attribute_deltas.get("autonomy_level", 0.0))) >= 0.03:
        labels.append("autonomy")
    if abs(float(attribute_deltas.get("governance_strength", 0.0))) >= 0.03:
        labels.append("governance")
    if abs(float(attribute_deltas.get("stability_vs_creativity", 0.0))) >= 0.03:
        labels.append("stability")
    return labels


def _classify_drift_magnitude(drift_magnitude: float) -> tuple[str, str]:
    if drift_magnitude >= 0.5:
        return ("large drift", "high")
    if drift_magnitude >= 0.2:
        return ("medium drift", "medium")
    return ("small drift", "low")


def _build_attribute_findings(
    baseline_profile: AgentAttributeProfile,
    current_profile: AgentAttributeProfile,
    attribute_deltas: dict[str, float],
    baseline_signal_terms: list[str],
    current_signal_terms: list[str],
    baseline_content: str | None,
    current_content: str | None,
) -> list[RepoArtifactAttributeFinding]:
    findings: list[RepoArtifactAttributeFinding] = []
    line_evidence = _line_diff_evidence_by_attribute(baseline_content, current_content)

    guardrail_delta = float(attribute_deltas.get("guardrail_robustness", 0.0))
    if abs(guardrail_delta) >= 0.03:
        findings.append(
            RepoArtifactAttributeFinding(
                attribute_key="guardrail_robustness",
                label="Guardrails",
                direction="weakened" if guardrail_delta < 0 else "strengthened",
                delta=round(guardrail_delta, 4),
                reason=_guardrail_reason(baseline_profile.signals, current_profile.signals),
                evidence=(
                    line_evidence.get("guardrail_robustness")
                    or _guardrail_evidence(
                    baseline_profile.signals,
                    current_profile.signals,
                    baseline_signal_terms,
                    current_signal_terms,
                    )
                ),
                remediation=(
                    "Add explicit limits, refusal/escalation language, or approval checks around the risky action before accepting this drift."
                    if guardrail_delta < 0
                    else "If this stronger guardrail posture is intentional, consider approving it into the baseline so future alerts stay focused."
                ),
            )
        )

    capability_delta = float(attribute_deltas.get("capability_risk", 0.0))
    if abs(capability_delta) >= 0.03:
        findings.append(
            RepoArtifactAttributeFinding(
                attribute_key="capability_risk",
                label="Capability",
                direction="expanded" if capability_delta > 0 else "reduced",
                delta=round(capability_delta, 4),
                reason=_capability_reason(baseline_profile.signals, current_profile.signals, capability_delta),
                evidence=(
                    line_evidence.get("capability_risk")
                    or _capability_evidence(
                    baseline_profile.signals,
                    current_profile.signals,
                    baseline_signal_terms,
                    current_signal_terms,
                    )
                ),
                remediation=(
                    "Reduce write or production authority, prefer sandbox/test targets, and require explicit approval for sensitive actions."
                    if capability_delta > 0
                    else "Confirm the reduced authority is intentional and, if correct, consider updating the approved baseline."
                ),
            )
        )

    autonomy_delta = float(attribute_deltas.get("autonomy_level", 0.0))
    if abs(autonomy_delta) >= 0.03:
        findings.append(
            RepoArtifactAttributeFinding(
                attribute_key="autonomy_level",
                label="Autonomy",
                direction="increased" if autonomy_delta > 0 else "decreased",
                delta=round(autonomy_delta, 4),
                reason=_autonomy_reason(baseline_profile.signals, current_profile.signals, autonomy_delta),
                evidence=(
                    line_evidence.get("autonomy_level")
                    or _autonomy_evidence(
                    baseline_profile.signals,
                    current_profile.signals,
                    baseline_signal_terms,
                    current_signal_terms,
                    )
                ),
                remediation=(
                    "Lower step depth, remove parallel execution, or add human checkpoints so the workflow stays reviewable."
                    if autonomy_delta > 0
                    else "Confirm the lower-autonomy posture is intended and consider approving it into the baseline if it reflects the desired design."
                ),
            )
        )

    stability_delta = float(attribute_deltas.get("stability_vs_creativity", 0.0))
    if abs(stability_delta) >= 0.03:
        findings.append(
            RepoArtifactAttributeFinding(
                attribute_key="stability_vs_creativity",
                label="Stability",
                direction="more stable" if stability_delta > 0 else "more creative",
                delta=round(stability_delta, 4),
                reason=_stability_reason(baseline_profile.signals, current_profile.signals, stability_delta),
                evidence=(
                    line_evidence.get("stability_vs_creativity")
                    or _stability_evidence(
                        baseline_profile.signals,
                        current_profile.signals,
                    )
                ),
                remediation=(
                    "If a more deterministic posture is intended, consider approving it into the baseline so future alerts stay focused on new behavior shifts."
                    if stability_delta > 0
                    else "If extra creativity is intentional, bound it with explicit output constraints or tighter sampling settings before approving it into the baseline."
                ),
            )
        )

    governance_delta = float(attribute_deltas.get("governance_strength", 0.0))
    if abs(governance_delta) >= 0.03:
        findings.append(
            RepoArtifactAttributeFinding(
                attribute_key="governance_strength",
                label="Governance",
                direction="weakened" if governance_delta < 0 else "strengthened",
                delta=round(governance_delta, 4),
                reason=(
                    "Review or approval signals appear weaker than the stored baseline posture."
                    if governance_delta < 0
                    else "Review or approval signals appear stronger than the stored baseline posture."
                ),
                evidence=line_evidence.get("governance_strength", []),
                remediation=(
                    "Restore ownership, approval, or audit expectations before treating this version as the intended baseline."
                    if governance_delta < 0
                    else "If the stronger governance posture is intended, consider approving it into the baseline."
                ),
            )
        )

    return findings


def build_artifact_attribute_profile(
    *,
    artifact_path: str,
    artifact_type: str,
    baseline_profile: AgentAttributeProfile | None,
    current_profile: AgentAttributeProfile | None,
    attribute_deltas: dict[str, float] | None,
    baseline_signal_terms: list[str] | None = None,
    current_signal_terms: list[str] | None = None,
    baseline_content: str | None = None,
    current_content: str | None = None,
    baseline_reference: str = "none-yet",
    has_authoritative_baseline: bool = False,
) -> ArtifactAttributeProfile:
    attribute_deltas = attribute_deltas or {}
    baseline_signal_terms = baseline_signal_terms or []
    current_signal_terms = current_signal_terms or []

    findings: list[RepoArtifactAttributeFinding] = []
    if baseline_profile is not None and current_profile is not None:
        findings = _build_attribute_findings(
            baseline_profile,
            current_profile,
            attribute_deltas,
            baseline_signal_terms,
            current_signal_terms,
            baseline_content,
            current_content,
        )
    findings_by_key = {finding.attribute_key: finding for finding in findings}

    dimensions = [
        _build_numeric_attribute_dimension(
            attribute_key="guardrail_robustness",
            label="Guardrail robustness",
            baseline_profile=baseline_profile,
            current_profile=current_profile,
            baseline_value_getter=lambda profile: profile.guardrail_robustness,
            current_value_getter=lambda profile: profile.guardrail_robustness,
            attribute_deltas=attribute_deltas,
            finding=findings_by_key.get("guardrail_robustness"),
        ),
        _build_numeric_attribute_dimension(
            attribute_key="capability_risk",
            label="Capability risk",
            baseline_profile=baseline_profile,
            current_profile=current_profile,
            baseline_value_getter=lambda profile: profile.capability_risk,
            current_value_getter=lambda profile: profile.capability_risk,
            attribute_deltas=attribute_deltas,
            finding=findings_by_key.get("capability_risk"),
        ),
        _build_numeric_attribute_dimension(
            attribute_key="autonomy_level",
            label="Autonomy level",
            baseline_profile=baseline_profile,
            current_profile=current_profile,
            baseline_value_getter=lambda profile: profile.autonomy_level,
            current_value_getter=lambda profile: profile.autonomy_level,
            attribute_deltas=attribute_deltas,
            finding=findings_by_key.get("autonomy_level"),
        ),
        _build_numeric_attribute_dimension(
            attribute_key="governance_strength",
            label="Governance strength",
            baseline_profile=baseline_profile,
            current_profile=current_profile,
            baseline_value_getter=lambda profile: profile.governance_strength,
            current_value_getter=lambda profile: profile.governance_strength,
            attribute_deltas=attribute_deltas,
            finding=findings_by_key.get("governance_strength"),
        ),
        _build_numeric_attribute_dimension(
            attribute_key="model_config_posture",
            label="Model config posture",
            baseline_profile=baseline_profile,
            current_profile=current_profile,
            baseline_value_getter=lambda profile: profile.stability_vs_creativity,
            current_value_getter=lambda profile: profile.stability_vs_creativity,
            attribute_deltas={"model_config_posture": float(attribute_deltas.get("stability_vs_creativity", 0.0))},
            finding=findings_by_key.get("stability_vs_creativity"),
        ),
        _build_control_surface_dimension(artifact_type),
    ]

    return ArtifactAttributeProfile(
        artifact_path=artifact_path,
        artifact_type=artifact_type,
        control_surface_label=_control_surface_label(_artifact_group_key_from_type(artifact_type)),
        dimensions=dimensions,
        baseline_reference=baseline_reference,
        has_authoritative_baseline=has_authoritative_baseline,
    )


def _build_numeric_attribute_dimension(
    *,
    attribute_key: str,
    label: str,
    baseline_profile: AgentAttributeProfile | None,
    current_profile: AgentAttributeProfile | None,
    baseline_value_getter,
    current_value_getter,
    attribute_deltas: dict[str, float],
    finding: RepoArtifactAttributeFinding | None,
) -> AttributeProfileDimension:
    baseline_score = baseline_value_getter(baseline_profile) if baseline_profile is not None else None
    current_score = current_value_getter(current_profile) if current_profile is not None else None
    delta = float(attribute_deltas.get(attribute_key, 0.0)) if baseline_profile is not None and current_profile is not None else None
    state = _attribute_state(delta, baseline_score, current_score)
    confidence_score = _attribute_confidence_score(
        attribute_key=attribute_key,
        delta=delta,
        evidence_count=len(finding.evidence) if finding and finding.evidence else 0,
        has_baseline=baseline_profile is not None,
        has_current=current_profile is not None,
    )
    return AttributeProfileDimension(
        attribute_key=attribute_key,
        label=label,
        baseline_value=_attribute_bucket_label(attribute_key, baseline_score),
        current_value=_attribute_bucket_label(attribute_key, current_score),
        direction=_attribute_direction(attribute_key, delta, state),
        state=state,
        confidence_label=_confidence_band_label(confidence_score),
        confidence_score=confidence_score,
        reason=(finding.reason if finding is not None else _default_attribute_reason(attribute_key, state)),
        evidence=(finding.evidence if finding is not None else []),
        remediation=(finding.remediation if finding is not None else _default_attribute_remediation(attribute_key, state)),
        baseline_score=round(float(baseline_score), 4) if baseline_score is not None else None,
        current_score=round(float(current_score), 4) if current_score is not None else None,
        delta=round(float(delta), 4) if delta is not None else None,
    )


def _build_control_surface_dimension(artifact_type: str) -> AttributeProfileDimension:
    value = _control_surface_value(artifact_type)
    confidence_score = 0.95
    return AttributeProfileDimension(
        attribute_key="control_surface_type",
        label="Control surface type",
        baseline_value=value,
        current_value=value,
        direction="unchanged",
        state="no_change",
        confidence_label=_confidence_band_label(confidence_score),
        confidence_score=confidence_score,
        reason=f"Vipari classifies this artifact as {value.lower()} based on the detected artifact type `{artifact_type}`.",
        evidence=[f"Artifact type: {artifact_type}"],
        remediation="No remediation needed unless this artifact was misclassified.",
    )


def _attribute_state(delta: float | None, baseline_score: float | None, current_score: float | None) -> str:
    if baseline_score is None or current_score is None:
        return "unknown"
    if delta is None or abs(delta) < 0.03:
        return "no_change"
    return "drift_detected"


def _attribute_bucket_label(attribute_key: str, score: float | None) -> str:
    if score is None:
        return "unknown"
    if attribute_key == "guardrail_robustness":
        if score >= 0.7:
            return "strong"
        if score >= 0.4:
            return "moderate"
        return "weak"
    if attribute_key == "capability_risk":
        if score >= 0.7:
            return "high"
        if score >= 0.4:
            return "moderate"
        return "low"
    if attribute_key == "autonomy_level":
        if score >= 0.7:
            return "high"
        if score >= 0.4:
            return "moderate"
        return "low"
    if attribute_key == "governance_strength":
        if score >= 0.7:
            return "strong"
        if score >= 0.4:
            return "moderate"
        return "weak"
    if attribute_key == "model_config_posture":
        if score >= 0.7:
            return "deterministic"
        if score >= 0.4:
            return "balanced"
        return "exploratory"
    return f"{score:.2f}"


def _attribute_direction(attribute_key: str, delta: float | None, state: str) -> str:
    if state == "unknown":
        return "unknown"
    if state == "no_change" or delta is None:
        return "unchanged"
    if attribute_key == "guardrail_robustness":
        return "strengthened" if delta > 0 else "weakened"
    if attribute_key == "capability_risk":
        return "expanded" if delta > 0 else "reduced"
    if attribute_key == "autonomy_level":
        return "increased" if delta > 0 else "decreased"
    if attribute_key == "governance_strength":
        return "strengthened" if delta > 0 else "weakened"
    if attribute_key == "model_config_posture":
        return "more deterministic" if delta > 0 else "more exploratory"
    return "changed"


def _attribute_confidence_score(
    *,
    attribute_key: str,
    delta: float | None,
    evidence_count: int,
    has_baseline: bool,
    has_current: bool,
) -> float:
    if attribute_key == "control_surface_type":
        return 0.95
    score = 0.4
    if has_baseline:
        score += 0.18
    if has_current:
        score += 0.18
    if evidence_count:
        score += min(evidence_count, 2) * 0.08
    if delta is not None and abs(delta) >= 0.1:
        score += 0.08
    return round(min(score, 0.95), 2)


def _confidence_band_label(score: float) -> str:
    if score >= 0.8:
        return "high confidence"
    if score >= 0.6:
        return "medium confidence"
    return "lower confidence"


def _default_attribute_reason(attribute_key: str, state: str) -> str:
    if state == "unknown":
        return "Vipari could not compare this dimension because no approved baseline was available for the changed artifact."
    return {
        "guardrail_robustness": "Vipari did not detect a material guardrail shift relative to the approved baseline.",
        "capability_risk": "Vipari did not detect a material capability or blast-radius shift relative to the approved baseline.",
        "autonomy_level": "Vipari did not detect a material autonomy shift relative to the approved baseline.",
        "governance_strength": "Vipari did not detect a material governance shift relative to the approved baseline.",
        "model_config_posture": "Vipari did not detect a material model sampling posture shift relative to the approved baseline.",
    }.get(attribute_key, "Vipari did not detect a material change for this dimension.")


def _default_attribute_remediation(attribute_key: str, state: str) -> str:
    if state == "unknown":
        return "Approve a stable baseline for this artifact so future reviews can compare posture changes directly."
    return {
        "guardrail_robustness": "Keep explicit limits and refusal or escalation language aligned to the approved design.",
        "capability_risk": "Keep authority scoped to the minimum operational surface needed for the intended behavior.",
        "autonomy_level": "Keep step depth and independent execution aligned with the intended review posture.",
        "governance_strength": "Keep review, approval, and accountability cues aligned with the baseline governance expectations.",
        "model_config_posture": "Keep sampling and determinism settings aligned with the approved model operating mode.",
    }.get(attribute_key, "Keep this dimension aligned with the approved baseline.")


def _control_surface_value(artifact_type: str) -> str:
    return {
        "prompt": "Prompt and instructions",
        "system_prompt": "Prompt and instructions",
        "guardrail": "Guardrails and policy",
        "policy": "Guardrails and policy",
        "model_config": "Model and generation config",
        "tooling": "Tooling and orchestration",
        "retrieval": "Retrieval and knowledge",
        "ai_code": "Agent code and assets",
    }.get(artifact_type, "Other AI-related surface")


def _artifact_group_key_from_type(artifact_type: str) -> str:
    if artifact_type in {"prompt", "system_prompt"}:
        return "prompts"
    if artifact_type in {"guardrail", "policy"}:
        return "guardrails"
    if artifact_type == "model_config":
        return "models"
    if artifact_type == "tooling":
        return "tools"
    if artifact_type == "retrieval":
        return "retrieval"
    if artifact_type == "ai_code":
        return "agents"
    return "other"


def _line_diff_evidence_by_attribute(
    baseline_content: str | None,
    current_content: str | None,
) -> dict[str, list[str]]:
    if not baseline_content or not current_content:
        return {}

    baseline_lines = baseline_content.splitlines()
    current_lines = current_content.splitlines()
    matcher = difflib.SequenceMatcher(a=baseline_lines, b=current_lines)
    evidence: dict[str, list[str]] = {
        "guardrail_robustness": [],
        "capability_risk": [],
        "autonomy_level": [],
        "governance_strength": [],
        "stability_vs_creativity": [],
    }
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed = [(line_no + 1, baseline_lines[line_no]) for line_no in range(i1, i2)]
        added = [(line_no + 1, current_lines[line_no]) for line_no in range(j1, j2)]
        for attribute_key in evidence.keys():
            snippets = _attribute_line_snippets(attribute_key, removed, added)
            for snippet in snippets:
                if snippet not in evidence[attribute_key]:
                    evidence[attribute_key].append(snippet)
    return {key: values[:3] for key, values in evidence.items() if values}


def _attribute_line_snippets(
    attribute_key: str,
    removed: list[tuple[int, str]],
    added: list[tuple[int, str]],
) -> list[str]:
    keyword_map = {
        "guardrail_robustness": ("must", "never", "do not", "only", "required", "approval", "escalate", "refuse"),
        "capability_risk": ("write", "delete", "update", "create", "send", "refund", "production", "prod", "tool", "deploy"),
        "autonomy_level": ("parallel", "multi-step", "planner", "max_steps", "steps", "human review", "approval"),
        "governance_strength": ("approval", "review", "audit", "codeowners", "security", "owner"),
        "stability_vs_creativity": ("temperature", "top_p", "creative", "deterministic", "random", "sampling"),
    }
    keywords = keyword_map[attribute_key]

    def relevant(entries: list[tuple[int, str]]) -> list[tuple[int, str]]:
        return [(line_no, text) for line_no, text in entries if any(keyword in text.lower() for keyword in keywords)]

    removed_relevant = relevant(removed)
    added_relevant = relevant(added)
    snippets: list[str] = []
    for line_no, text in removed_relevant[:2]:
        snippets.append(f"Removed baseline line {line_no}: {text.strip()}")
    for line_no, text in added_relevant[:2]:
        snippets.append(f"Added current line {line_no}: {text.strip()}")
    return snippets[:3]


def _guardrail_reason(baseline: StaticSignals, current: StaticSignals) -> str:
    reasons: list[str] = []
    baseline_guardrail_total = sum(baseline.guardrail_counts.values())
    current_guardrail_total = sum(current.guardrail_counts.values())
    if current.constraint_count < baseline.constraint_count:
        reasons.append(f"constraints dropped from {baseline.constraint_count} to {current.constraint_count}")
    if current.explicit_limit_count < baseline.explicit_limit_count:
        reasons.append(f"explicit limits dropped from {baseline.explicit_limit_count} to {current.explicit_limit_count}")
    if current_guardrail_total < baseline_guardrail_total:
        reasons.append(f"guardrail/refusal cues dropped from {baseline_guardrail_total} to {current_guardrail_total}")
    if current.ambiguity_count > baseline.ambiguity_count:
        reasons.append(f"ambiguous language rose from {baseline.ambiguity_count} to {current.ambiguity_count}")
    if reasons:
        return f"Vipari detected weaker guardrail posture because {'; '.join(reasons[:2])}."
    return "Vipari detected weaker guardrail posture because constraint and refusal signals no longer match the approved baseline."


def _capability_reason(baseline: StaticSignals, current: StaticSignals, delta: float) -> str:
    reasons: list[str] = []
    if current.write_signal_count > baseline.write_signal_count:
        reasons.append(f"write/modify actions rose from {baseline.write_signal_count} to {current.write_signal_count}")
    if current.sensitive_tool_count > baseline.sensitive_tool_count:
        reasons.append(f"sensitive-tool references rose from {baseline.sensitive_tool_count} to {current.sensitive_tool_count}")
    if current.prod_signal_count > baseline.prod_signal_count:
        reasons.append(f"production-facing references rose from {baseline.prod_signal_count} to {current.prod_signal_count}")
    if current.systems_touched_count > baseline.systems_touched_count:
        reasons.append(f"systems touched rose from {baseline.systems_touched_count} to {current.systems_touched_count}")
    if current.sandbox_signal_count < baseline.sandbox_signal_count:
        reasons.append(f"sandbox/test references dropped from {baseline.sandbox_signal_count} to {current.sandbox_signal_count}")
    if current.human_review_count < baseline.human_review_count:
        reasons.append(f"human-review gates dropped from {baseline.human_review_count} to {current.human_review_count}")
    if reasons:
        prefix = "broader authority" if delta > 0 else "reduced authority"
        return f"Vipari detected {prefix} because {'; '.join(reasons[:2])}."
    return (
        "Vipari detected broader authority because the artifact now signals more sensitive or production-facing actions than the baseline."
        if delta > 0
        else "Vipari detected reduced authority because the artifact now signals fewer sensitive or production-facing actions than the baseline."
    )


def _autonomy_reason(baseline: StaticSignals, current: StaticSignals, delta: float) -> str:
    reasons: list[str] = []
    if current.max_steps > baseline.max_steps:
        reasons.append(f"max step depth rose from {baseline.max_steps} to {current.max_steps}")
    if current.parallelism_signal_count > baseline.parallelism_signal_count:
        reasons.append(
            f"parallel or multi-step execution cues rose from {baseline.parallelism_signal_count} to {current.parallelism_signal_count}"
        )
    if current.human_review_count < baseline.human_review_count:
        reasons.append(f"human-review gates dropped from {baseline.human_review_count} to {current.human_review_count}")
    if current.write_signal_count > baseline.write_signal_count:
        reasons.append(f"action-oriented steps rose from {baseline.write_signal_count} to {current.write_signal_count}")
    if reasons:
        prefix = "more independent execution" if delta > 0 else "less independent execution"
        return f"Vipari detected {prefix} because {'; '.join(reasons[:2])}."
    return (
        "Vipari detected more independent execution because the workflow now allows deeper or less supervised action than the baseline."
        if delta > 0
        else "Vipari detected less independent execution because the workflow now allows shallower or more supervised action than the baseline."
    )


def _stability_reason(baseline: StaticSignals, current: StaticSignals, delta: float) -> str:
    reasons: list[str] = []
    if baseline.temperature is not None and current.temperature is not None and current.temperature != baseline.temperature:
        direction = "down" if current.temperature < baseline.temperature else "up"
        reasons.append(f"temperature moved {direction} from {baseline.temperature:g} to {current.temperature:g}")
    if baseline.top_p is not None and current.top_p is not None and current.top_p != baseline.top_p:
        direction = "down" if current.top_p < baseline.top_p else "up"
        reasons.append(f"top_p moved {direction} from {baseline.top_p:g} to {current.top_p:g}")
    if reasons:
        prefix = "more stable and deterministic" if delta > 0 else "more variable and creative"
        return f"Vipari detected {prefix} behavior because {'; '.join(reasons[:2])}."
    return (
        "Vipari detected more stable and deterministic behavior because the current sampling settings are tighter than the baseline."
        if delta > 0
        else "Vipari detected more variable and creative behavior because the current sampling settings are looser than the baseline."
    )


def _added_removed_signal_terms(baseline_terms: list[str], current_terms: list[str]) -> tuple[list[str], list[str]]:
    baseline_set = {term for term in baseline_terms if term}
    current_set = {term for term in current_terms if term}
    added = sorted(current_set - baseline_set)
    removed = sorted(baseline_set - current_set)
    return added, removed


def _guardrail_evidence(
    baseline: StaticSignals,
    current: StaticSignals,
    baseline_terms: list[str],
    current_terms: list[str],
) -> list[str]:
    evidence: list[str] = []
    if current.constraint_count != baseline.constraint_count:
        evidence.append(f"Constraint markers changed from {baseline.constraint_count} to {current.constraint_count}.")
    if current.explicit_limit_count != baseline.explicit_limit_count:
        evidence.append(f"Explicit limits changed from {baseline.explicit_limit_count} to {current.explicit_limit_count}.")
    baseline_total = sum(baseline.guardrail_counts.values())
    current_total = sum(current.guardrail_counts.values())
    if current_total != baseline_total:
        evidence.append(f"Guardrail/refusal cues changed from {baseline_total} to {current_total}.")
    added, removed = _added_removed_signal_terms(baseline_terms, current_terms)
    if removed:
        evidence.append(f"Code no longer signals: {_human_join(removed[:3])}.")
    if added and not evidence:
        evidence.append(f"New code signals include: {_human_join(added[:3])}.")
    return evidence[:3]


def _capability_evidence(
    baseline: StaticSignals,
    current: StaticSignals,
    baseline_terms: list[str],
    current_terms: list[str],
) -> list[str]:
    evidence: list[str] = []
    if current.write_signal_count != baseline.write_signal_count:
        evidence.append(f"Write/modify actions changed from {baseline.write_signal_count} to {current.write_signal_count}.")
    if current.prod_signal_count != baseline.prod_signal_count:
        evidence.append(f"Production/live references changed from {baseline.prod_signal_count} to {current.prod_signal_count}.")
    if current.sandbox_signal_count != baseline.sandbox_signal_count:
        evidence.append(f"Sandbox/test references changed from {baseline.sandbox_signal_count} to {current.sandbox_signal_count}.")
    if current.sensitive_tool_count != baseline.sensitive_tool_count:
        evidence.append(f"Sensitive-tool references changed from {baseline.sensitive_tool_count} to {current.sensitive_tool_count}.")
    added, removed = _added_removed_signal_terms(baseline_terms, current_terms)
    if added:
        evidence.append(f"New code signals include: {_human_join(added[:3])}.")
    elif removed:
        evidence.append(f"Code no longer signals: {_human_join(removed[:3])}.")
    return evidence[:3]


def _autonomy_evidence(
    baseline: StaticSignals,
    current: StaticSignals,
    baseline_terms: list[str],
    current_terms: list[str],
) -> list[str]:
    evidence: list[str] = []
    if current.max_steps != baseline.max_steps:
        evidence.append(f"Max step depth changed from {baseline.max_steps} to {current.max_steps}.")
    if current.parallelism_signal_count != baseline.parallelism_signal_count:
        evidence.append(
            f"Parallel or multi-step execution cues changed from {baseline.parallelism_signal_count} to {current.parallelism_signal_count}."
        )
    if current.human_review_count != baseline.human_review_count:
        evidence.append(f"Human-review cues changed from {baseline.human_review_count} to {current.human_review_count}.")
    added, removed = _added_removed_signal_terms(baseline_terms, current_terms)
    if added and "parallel" in " ".join(added[:3]):
        evidence.append(f"New code signals include: {_human_join(added[:3])}.")
    elif removed and not evidence:
        evidence.append(f"Code no longer signals: {_human_join(removed[:3])}.")
    return evidence[:3]


def _stability_evidence(
    baseline: StaticSignals,
    current: StaticSignals,
) -> list[str]:
    evidence: list[str] = []
    if baseline.temperature is not None and current.temperature is not None and current.temperature != baseline.temperature:
        evidence.append(f"Temperature changed from {baseline.temperature:g} to {current.temperature:g}.")
    if baseline.top_p is not None and current.top_p is not None and current.top_p != baseline.top_p:
        evidence.append(f"top_p changed from {baseline.top_p:g} to {current.top_p:g}.")
    if not evidence:
        evidence.append("Sampling-related settings changed relative to the baseline.")
    return evidence[:3]


def _build_control_surface_groups(artifacts: list[RepoDashboardArtifactEntry]) -> list[RepoDashboardControlSurfaceGroup]:
    label_by_group = {
        "prompts": "Prompts and instructions",
        "guardrails": "Guardrails and policy",
        "models": "Model and generation config",
        "tools": "Tooling and orchestration",
        "agents": "Agent code and assets",
        "retrieval": "Retrieval and knowledge",
        "other": "Other AI-related artifacts",
    }
    grouped: dict[str, list[RepoDashboardArtifactEntry]] = {}
    for artifact in artifacts:
        grouped.setdefault(_artifact_group_key(artifact), []).append(artifact)

    results: list[RepoDashboardControlSurfaceGroup] = []
    for group_key, entries in grouped.items():
        sorted_entries = sorted(entries, key=lambda item: (-_insight_score(item), item.artifact_path))
        results.append(
            RepoDashboardControlSurfaceGroup(
                group_key=group_key,
                label=label_by_group[group_key],
                artifact_count=len(entries),
                high_confidence_count=sum(1 for entry in entries if entry.discovery_confidence >= 0.85),
                top_artifact_paths=[entry.artifact_path for entry in sorted_entries[:3]],
            )
        )

    results.sort(key=lambda item: (-item.artifact_count, item.label))
    return results


def _artifact_group_key(artifact: RepoDashboardArtifactEntry) -> str:
    return _artifact_group_key_from_type(artifact.artifact_type)


def _build_repo_history_timelines(
    db_path: str,
    repo_full: str,
    artifacts: list[RepoDashboardArtifactEntry],
) -> list[RepoArtifactHistoryTimeline]:
    artifacts_by_path = {artifact.artifact_path: artifact for artifact in artifacts}
    if not artifacts_by_path:
        return []

    points_by_path: dict[str, list[RepoArtifactTimelinePoint]] = {path: [] for path in artifacts_by_path}
    with _connect(db_path) as conn:
        historical_rows = conn.execute(
            """
                 SELECT artifact_path, artifact_type, commit_sha, branch_ref, triggered_by, created_at, baseline_profile_id, baseline_provenance_json,
                   semantic_distance, attribute_deltas_json
            FROM historical_static_profiles
            WHERE normalized_artifact_id LIKE ?
            ORDER BY created_at ASC, id ASC
            """,
            (_normalized_id_prefix(repo_full),),
        ).fetchall()
        for row in historical_rows:
            artifact_path = row["artifact_path"]
            if artifact_path not in points_by_path:
                continue
            attribute_deltas = {key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()}
            semantic_distance = float(row["semantic_distance"])
            baseline_provenance = baseline_provenance_from_json(row["baseline_provenance_json"])
            if baseline_provenance is None and row["baseline_profile_id"] is not None:
                baseline_provenance = historical_fallback_provenance(row["baseline_profile_id"])
            if baseline_provenance is None:
                baseline_provenance = no_baseline_provenance()
            historical_source = _historical_source_context(
                repo_full,
                str(row["commit_sha"]),
                row["branch_ref"],
                row["triggered_by"],
            )
            points_by_path[artifact_path].append(
                RepoArtifactTimelinePoint(
                    source=historical_source["source_type"],
                    label=historical_source["label"],
                    source_ref=historical_source["source_ref"],
                    source_url=historical_source["source_url"],
                    review_context=historical_source["review_context"],
                    created_at=float(row["created_at"]),
                    baseline_provenance=baseline_provenance,
                    semantic_distance=semantic_distance,
                    capability_shift=attribute_deltas.get("capability_risk", 0.0),
                    guardrail_shift=attribute_deltas.get("guardrail_robustness", 0.0),
                    autonomy_shift=attribute_deltas.get("autonomy_level", 0.0),
                    drift_magnitude=_drift_magnitude(semantic_distance, attribute_deltas),
                )
            )

    timelines: list[RepoArtifactHistoryTimeline] = []
    for artifact_path, points in points_by_path.items():
        if not points:
            continue
        points.sort(key=lambda point: (point.created_at, point.label))
        artifact = artifacts_by_path[artifact_path]
        trimmed_points = points[-8:]
        timelines.append(
            RepoArtifactHistoryTimeline(
                artifact_path=artifact_path,
                artifact_type=artifact.artifact_type,
                point_count=len(points),
                max_drift_magnitude=max(point.drift_magnitude for point in points),
                points=trimmed_points,
            )
        )

    timelines.sort(key=lambda item: (-item.max_drift_magnitude, item.artifact_path))
    return timelines[:6]


def _build_featured_storyline(
    db_path: str,
    repo_full: str,
    artifacts: list[RepoDashboardArtifactEntry],
    insights: list[RepoDashboardInsightEntry],
    baseline_by_path,
    profile_context_by_path: dict[str, _RepoArtifactEvidenceBundle],
) -> RepoArtifactStoryline | None:
    if not artifacts:
        return None

    featured_path = insights[0].artifact_path if insights else artifacts[0].artifact_path
    return build_repo_artifact_storyline(
        db_path,
        repo_full,
        featured_path,
        artifacts=artifacts,
        baseline_by_path=baseline_by_path,
        profile_context_by_path=profile_context_by_path,
    )


def build_repo_artifact_storyline(
    db_path: str,
    repo_full: str,
    artifact_path: str,
    *,
    artifacts: list[RepoDashboardArtifactEntry] | None = None,
    baseline_by_path=None,
    profile_context_by_path: dict[str, _RepoArtifactEvidenceBundle] | None = None,
) -> RepoArtifactStoryline | None:
    artifact_entries = artifacts
    local_baseline_by_path = baseline_by_path
    local_profile_context_by_path = profile_context_by_path

    if artifact_entries is None or local_baseline_by_path is None or local_profile_context_by_path is None:
        artifact_entries, local_baseline_by_path, local_profile_context_by_path = _load_storyline_context(
            db_path,
            repo_full,
        )

    artifact = next((item for item in artifact_entries if item.artifact_path == artifact_path), None)
    baseline = local_baseline_by_path.get(artifact_path)
    if artifact is None or baseline is None:
        return None

    episodes: list[DriftEpisode] = [
        DriftEpisode(
            episode_timestamp=float(baseline.created_at),
            source_type="baseline_promotion",
            source_label="Approved baseline",
            source_ref=f"baseline {baseline.id}",
            episode_type="baseline_milestone",
            top_attributes=[],
            episode_summary="This artifact has an approved baseline, which anchors later drift interpretation.",
            severity="low",
            confidence="authoritative baseline",
            is_milestone=True,
        )
    ]

    historical_count = 0
    with _connect(db_path) as conn:
        historical_rows = conn.execute(
            """
            SELECT commit_sha, branch_ref, triggered_by, created_at, semantic_distance, attribute_deltas_json
            FROM historical_static_profiles
            WHERE normalized_artifact_id LIKE ? AND artifact_path = ?
            ORDER BY created_at ASC, id ASC
            """,
            (_normalized_id_prefix(repo_full), artifact_path),
        ).fetchall()
        for row in historical_rows:
            historical_count += 1
            attribute_deltas = {key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()}
            semantic_distance = float(row["semantic_distance"])
            drift_magnitude = _drift_magnitude(semantic_distance, attribute_deltas)
            historical_source = _historical_source_context(
                repo_full,
                str(row["commit_sha"]),
                row["branch_ref"],
                row["triggered_by"],
            )
            episodes.append(
                DriftEpisode(
                    episode_timestamp=float(row["created_at"]),
                    source_type=historical_source["source_type"],
                    source_label=historical_source["label"],
                    source_ref=historical_source["source_ref"],
                    source_url=historical_source["source_url"],
                    episode_type=_episode_type(attribute_deltas),
                    top_attributes=_top_attribute_labels(attribute_deltas),
                    episode_summary=_episode_summary(attribute_deltas, drift_magnitude),
                    severity=_severity_label(drift_magnitude),
                    confidence=_confidence_label(artifact.discovery_confidence),
                )
            )

    context = _preferred_profile_context(local_profile_context_by_path.get(artifact_path))
    current_posture_label = "Baseline only"
    if context is not None:
        current_drift = _drift_magnitude(context.semantic_distance, context.attribute_deltas)
        current_posture_label = _current_posture_label(current_drift)
        episodes.append(
            DriftEpisode(
                episode_timestamp=float(context.created_at),
                source_type=context.source_type,
                source_label="Current posture",
                source_ref=context.source_ref,
                source_url=context.source_url,
                episode_type="current_posture",
                top_attributes=_top_attribute_labels(context.attribute_deltas),
                episode_summary=(context.narrative[0] if context.narrative else "Current posture reflects the latest stored baseline-relative evidence for this artifact."),
                severity=_severity_label(current_drift),
                confidence=_confidence_label(artifact.discovery_confidence),
                is_milestone=True,
            )
        )

    episodes.sort(key=lambda item: (item.episode_timestamp, _storyline_episode_sort_rank(item), item.source_label))
    episodes = _collapse_storyline_episodes(episodes)
    limited_history_note = None
    if historical_count < 2:
        limited_history_note = "Limited history available. Showing the approved baseline and latest known drift evidence only."

    return RepoArtifactStoryline(
        artifact_path=artifact_path,
        artifact_type=artifact.artifact_type,
        summary=_storyline_summary(episodes),
        baseline_label=f"Approved baseline recorded for {artifact_path}.",
        current_posture_label=current_posture_label,
        limited_history_note=limited_history_note,
        episodes=episodes,
    )


def _load_storyline_context(db_path: str, repo_full: str):
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        return [], {}, {}
    artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    baseline_versions = list_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    baseline_by_path = {baseline.artifact_path: baseline for baseline in baseline_versions}
    top_drifting_artifacts = list_top_drifting_artifacts_for_repo(db_path, repo_full)
    leaderboard_by_path = {entry.artifact_path: entry for entry in top_drifting_artifacts}
    metrics_by_path = _load_repo_artifact_metrics(db_path, repo_full)
    profile_context_by_path = _load_repo_artifact_profile_contexts(db_path, repo_full)

    artifact_entries: list[RepoDashboardArtifactEntry] = []
    for artifact in artifacts:
        baseline = baseline_by_path.get(artifact.artifact_path)
        provenance = artifact_provenance_label(artifact.artifact_type)
        metrics = metrics_by_path.get(artifact.artifact_path, _empty_artifact_metrics())
        leaderboard_entry = leaderboard_by_path.get(artifact.artifact_path)
        artifact_entries.append(
            RepoDashboardArtifactEntry(
                artifact_path=artifact.artifact_path,
                artifact_type=artifact.artifact_type,
                discovery_reason=artifact.discovery_reason,
                discovery_confidence=artifact.confidence,
                baseline_line_count=baseline.line_count if baseline is not None else 0,
                historical_version_count=metrics["historical_version_count"],
                historical_profile_count=metrics["historical_profile_count"],
                latest_historical_semantic_distance=metrics["latest_historical_semantic_distance"],
                latest_historical_drift_magnitude=metrics["latest_historical_drift_magnitude"],
                latest_historical_capability_shift=metrics["latest_historical_capability_shift"],
                latest_historical_guardrail_shift=metrics["latest_historical_guardrail_shift"],
                latest_historical_governance_shift=metrics["latest_historical_governance_shift"],
                latest_historical_autonomy_shift=metrics["latest_historical_autonomy_shift"],
                pr_profile_count=metrics["pr_profile_count"],
                latest_pr_semantic_distance=metrics["latest_pr_semantic_distance"],
                latest_pr_capability_shift=metrics["latest_pr_capability_shift"],
                latest_pr_guardrail_shift=metrics["latest_pr_guardrail_shift"],
                latest_pr_governance_shift=metrics["latest_pr_governance_shift"],
                latest_pr_autonomy_shift=metrics["latest_pr_autonomy_shift"],
                leaderboard_drift_magnitude=(leaderboard_entry.drift_magnitude if leaderboard_entry is not None else 0.0),
                latest_activity_at=metrics["latest_activity_at"],
                provenance_kind=provenance.kind,
                provenance_label=provenance.label,
            )
        )
    return artifact_entries, baseline_by_path, profile_context_by_path


def _build_repo_history_cues(
    artifacts: list[RepoDashboardArtifactEntry],
    baseline_by_path,
    profile_context_by_path: dict[str, _RepoArtifactEvidenceBundle],
) -> list[RepoHistoryCue]:
    if not artifacts:
        return []

    repeated_candidates = sorted(
        [artifact for artifact in artifacts if artifact.historical_profile_count >= 2],
        key=lambda artifact: (-artifact.historical_profile_count, -artifact.latest_historical_drift_magnitude, artifact.artifact_path),
    )
    stale_candidates: list[tuple[float, RepoDashboardArtifactEntry]] = []
    mixed: list[tuple[float, str]] = []
    severe: list[tuple[float, str]] = []
    provenance_gap_candidates: list[tuple[float, str]] = []
    for artifact in artifacts:
        baseline = baseline_by_path.get(artifact.artifact_path)
        context = _preferred_profile_context(profile_context_by_path.get(artifact.artifact_path))
        baseline_age_seconds = _baseline_age_seconds(artifact, baseline)
        if baseline_age_seconds > 0:
            stale_candidates.append((baseline_age_seconds, artifact))
        if context is None:
            if artifact.latest_activity_at > 0:
                provenance_gap_candidates.append((artifact.latest_activity_at, artifact.artifact_path))
            continue
        deltas = context.attribute_deltas
        positives = any(value > 0.05 for value in deltas.values())
        negatives = any(value < -0.05 for value in deltas.values())
        if positives and negatives:
            mixed.append((_drift_magnitude(context.semantic_distance, deltas), artifact.artifact_path))
        current_drift = _drift_magnitude(context.semantic_distance, deltas)
        if _severity_label(current_drift) == "high":
            severe.append((current_drift, artifact.artifact_path))
        if not context.source_url:
            provenance_gap_candidates.append((current_drift or artifact.latest_activity_at, artifact.artifact_path))

    cues: list[RepoHistoryCue] = []
    repeated = [artifact.artifact_path for artifact in repeated_candidates[:3]]
    if repeated:
        cues.append(
            RepoHistoryCue(
                cue_key="repeated_drift",
                label="Repeated drift",
                summary="These artifacts have more than one stored drift episode and should be reviewed for accumulation, not just the latest change.",
                artifact_paths=repeated,
            )
        )
    stale_candidates.sort(key=lambda item: (-item[0], item[1].artifact_path))
    stale = [artifact.artifact_path for _, artifact in stale_candidates[:3]]
    if stale:
        oldest_age = _age_label(stale_candidates[0][0])
        cues.append(
            RepoHistoryCue(
                cue_key="stale_baseline",
                label="Baseline aging",
                summary=f"These artifacts have moved since their approved baseline. The oldest visible gap is about {oldest_age}, so baseline freshness should be reviewed explicitly.",
                artifact_paths=stale,
            )
        )
    mixed.sort(key=lambda item: (-item[0], item[1]))
    if mixed:
        cues.append(
            RepoHistoryCue(
                cue_key="mixed_direction",
                label="Mixed-direction drift",
                summary="These artifacts show both strengthening and weakening moves across dimensions, so the reviewer should inspect the full storyline rather than a single score.",
                artifact_paths=[artifact_path for _, artifact_path in mixed[:3]],
            )
        )
    severe.sort(key=lambda item: (-item[0], item[1]))
    if severe:
        cues.append(
            RepoHistoryCue(
                cue_key="latest_high_severity",
                label="Latest severe episodes",
                summary="These artifacts currently end in a high-severity posture relative to their approved baseline.",
                artifact_paths=[artifact_path for _, artifact_path in severe[:3]],
            )
        )
    provenance_gap_candidates.sort(key=lambda item: (-item[0], item[1]))
    provenance_gaps = [artifact_path for _, artifact_path in provenance_gap_candidates[:3]]
    if provenance_gaps:
        cues.append(
            RepoHistoryCue(
                cue_key="provenance_gaps",
                label="Provenance gaps",
                summary="These artifacts have stored movement but incomplete direct source links, so reviewers may need to confirm the backing change manually.",
                artifact_paths=provenance_gaps,
            )
        )
    return cues


def _baseline_age_seconds(artifact: RepoDashboardArtifactEntry, baseline) -> float:
    if baseline is None:
        return 0.0
    if artifact.latest_activity_at <= 0:
        return 0.0
    return max(0.0, float(artifact.latest_activity_at) - float(baseline.created_at))


def _age_label(seconds: float) -> str:
    if seconds >= 86400:
        return f"{max(1, round(seconds / 86400))} day(s)"
    if seconds >= 3600:
        return f"{max(1, round(seconds / 3600))} hour(s)"
    if seconds >= 60:
        return f"{max(1, round(seconds / 60))} minute(s)"
    return "moments"


def _episode_type(attribute_deltas: dict[str, float]) -> str:
    capability = attribute_deltas.get("capability_risk", 0.0)
    guardrail = attribute_deltas.get("guardrail_robustness", 0.0)
    autonomy = attribute_deltas.get("autonomy_level", 0.0)
    governance = attribute_deltas.get("governance_strength", 0.0)
    ranked = sorted(
        [
            ("capability", capability),
            ("guardrail", guardrail),
            ("autonomy", autonomy),
            ("governance", governance),
        ],
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    if len([item for item in ranked if abs(item[1]) >= 0.05]) > 1:
        return "mixed"
    dominant_key, dominant_value = ranked[0]
    if abs(dominant_value) < 0.05:
        return "mixed"
    if dominant_key == "guardrail":
        return "guardrail_improvement" if dominant_value > 0 else "guardrail_regression"
    if dominant_key == "capability":
        return "capability_expansion" if dominant_value > 0 else "capability_reduction"
    if dominant_key == "autonomy":
        return "autonomy_increase" if dominant_value > 0 else "mixed"
    if dominant_key == "governance":
        return "governance_shift"
    return "mixed"


def _top_attribute_labels(attribute_deltas: dict[str, float]) -> list[str]:
    label_by_key = {
        "guardrail_robustness": "Guardrails",
        "capability_risk": "Capability",
        "autonomy_level": "Autonomy",
        "governance_strength": "Governance",
    }
    ranked = sorted(attribute_deltas.items(), key=lambda item: abs(item[1]), reverse=True)
    return [label_by_key[key] for key, value in ranked if key in label_by_key and abs(value) >= 0.03][:3]


def _episode_summary(attribute_deltas: dict[str, float], drift_magnitude: float) -> str:
    parts: list[str] = []
    if attribute_deltas.get("guardrail_robustness", 0.0) < -0.05:
        parts.append("guardrails weakened")
    elif attribute_deltas.get("guardrail_robustness", 0.0) > 0.05:
        parts.append("guardrails strengthened")
    if attribute_deltas.get("capability_risk", 0.0) > 0.05:
        parts.append("capability expanded")
    elif attribute_deltas.get("capability_risk", 0.0) < -0.05:
        parts.append("capability narrowed")
    if attribute_deltas.get("autonomy_level", 0.0) > 0.05:
        parts.append("autonomy increased")
    if attribute_deltas.get("governance_strength", 0.0) < -0.05:
        parts.append("governance weakened")
    if parts:
        return f"This episode {', '.join(parts[:2])} relative to the approved baseline."
    return f"This episode registered {_severity_label(drift_magnitude)} baseline-relative drift without a single dominant dimension."


def _storyline_summary(episodes: list[DriftEpisode]) -> str:
    drift_episodes = [episode for episode in episodes if episode.episode_type not in {"baseline_milestone", "current_posture"}]
    if not drift_episodes:
        return "This control surface currently has an approved baseline but no meaningful post-baseline storyline yet."
    capability_expansions = sum(1 for episode in drift_episodes if episode.episode_type == "capability_expansion")
    guardrail_regressions = sum(1 for episode in drift_episodes if episode.episode_type == "guardrail_regression")
    latest = drift_episodes[-1]
    return (
        f"Since the approved baseline, this control surface recorded {capability_expansions} capability expansion episode(s) "
        f"and {guardrail_regressions} guardrail regression episode(s). The latest non-baseline episode is {latest.severity} severity."
    )


def _severity_label(drift_magnitude: float) -> str:
    if drift_magnitude >= 0.75:
        return "high"
    if drift_magnitude >= 0.25:
        return "medium"
    return "low"


def _current_posture_label(drift_magnitude: float) -> str:
    label, _ = _classify_drift_magnitude(drift_magnitude)
    return f"Current posture: {label}."


def _collapse_storyline_episodes(episodes: list[DriftEpisode]) -> list[DriftEpisode]:
    collapsed: list[DriftEpisode] = []
    buffer: list[DriftEpisode] = []

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        if len(buffer) == 1:
            collapsed.extend(buffer)
            buffer = []
            return
        collapsed.append(_group_storyline_episode_cluster(buffer))
        buffer = []

    for episode in episodes:
        if episode.is_milestone:
            flush_buffer()
            collapsed.append(episode)
            continue

        if not buffer:
            buffer = [episode]
            continue

        current_key = _storyline_grouping_key(buffer[-1])
        next_key = _storyline_grouping_key(episode)
        if current_key is not None and current_key == next_key:
            buffer.append(episode)
            continue

        flush_buffer()
        buffer = [episode]

    flush_buffer()
    return collapsed


def _storyline_grouping_key(episode: DriftEpisode) -> tuple[str, str, str] | None:
    if episode.is_milestone:
        return None
    if episode.source_type != "historical_backfill":
        return None
    if episode.episode_type in {"mixed", "baseline_milestone", "current_posture"}:
        return None
    if episode.severity == "high":
        return None
    top_attributes = episode.top_attributes or []
    if not top_attributes:
        return None
    return (episode.source_type, episode.episode_type, top_attributes[0])


def _group_storyline_episode_cluster(episodes: list[DriftEpisode]) -> DriftEpisode:
    first = episodes[0]
    last = episodes[-1]
    unique_attributes: list[str] = []
    for episode in episodes:
        for attribute in episode.top_attributes or []:
            if attribute not in unique_attributes:
                unique_attributes.append(attribute)

    severity_rank = {"low": 0, "medium": 1, "high": 2}
    severity = max((episode.severity for episode in episodes), key=lambda value: severity_rank.get(value, 0))
    attribute_phrase = unique_attributes[0].lower() if unique_attributes else "the same dimension"
    episode_type_label = first.episode_type.replace("_", " ")
    return DriftEpisode(
        episode_timestamp=last.episode_timestamp,
        source_type="historical_backfill_cluster",
        source_label="Grouped historical drift",
        source_ref=f"{first.source_ref} -> {last.source_ref}",
        source_url=last.source_url,
        episode_type=first.episode_type,
        top_attributes=unique_attributes[:3],
        episode_summary=(
            f"Review this as one continuing {episode_type_label} pattern: {len(episodes)} nearby historical episodes touched "
            f"{attribute_phrase} between {first.source_ref} and {last.source_ref}."
        ),
        severity=severity,
        confidence=last.confidence,
    )


def _storyline_episode_sort_rank(episode: DriftEpisode) -> int:
    if episode.episode_type == "baseline_milestone":
        return 0
    if episode.episode_type == "current_posture":
        return 2
    return 1


def _build_repo_design_profiles(
    artifacts: list[RepoDashboardArtifactEntry],
    insights: list[RepoDashboardInsightEntry],
    baseline_by_path,
    profile_context_by_path: dict[str, _RepoArtifactEvidenceBundle],
) -> list[RepoArtifactDesignProfile]:
    artifact_by_path = {artifact.artifact_path: artifact for artifact in artifacts}
    ordered_paths: list[str] = []
    for insight in insights:
        if insight.artifact_path not in ordered_paths:
            ordered_paths.append(insight.artifact_path)
    for artifact in artifacts:
        if artifact.artifact_path not in ordered_paths:
            ordered_paths.append(artifact.artifact_path)

    design_profiles: list[RepoArtifactDesignProfile] = []
    for artifact_path in ordered_paths[:4]:
        artifact = artifact_by_path.get(artifact_path)
        baseline = baseline_by_path.get(artifact_path)
        if artifact is None or baseline is None:
            continue

        context = _preferred_profile_context(profile_context_by_path.get(artifact_path))
        baseline_provenance = approved_onboarding_provenance(
            baseline.id,
            is_authoritative=baseline.approval_status == "approved",
            approval_status=baseline.approval_status,
            approved_by=baseline.approved_by,
            approved_at=baseline.approved_at,
            approval_note=baseline.approval_note,
        )
        baseline_profile = _profile_vector(baseline.profile)
        if context is None:
            current_profile = baseline_profile
            drift_from_baseline = 0.0
            drift_label, drift_tone = _classify_drift_magnitude(drift_from_baseline)
            risk_tags = ["baseline only"]
            narrative = ["No drift samples yet. This surface is currently represented only by the approved baseline."]
            headline_summary = "No source change with stored drift evidence yet."
            attribute_findings: list[RepoArtifactAttributeFinding] = []
            attribute_profile = build_artifact_attribute_profile(
                artifact_path=artifact.artifact_path,
                artifact_type=artifact.artifact_type,
                baseline_profile=baseline.profile,
                current_profile=baseline.profile,
                attribute_deltas={},
                baseline_signal_terms=baseline.signal_terms,
                current_signal_terms=baseline.signal_terms,
                baseline_content=baseline.content_text,
                current_content=baseline.content_text,
            ).dimensions
            can_promote_source_to_baseline = False
            provenance = None
        else:
            current_profile = _profile_vector(context.profile)
            drift_from_baseline = _drift_magnitude(context.semantic_distance, context.attribute_deltas)
            drift_label, drift_tone = _classify_drift_magnitude(drift_from_baseline)
            risk_tags = _artifact_risk_tags(artifact, context.attribute_deltas)
            narrative = context.narrative
            baseline_provenance = context.baseline_provenance or baseline_provenance
            attribute_findings = _build_attribute_findings(
                baseline.profile,
                context.profile,
                context.attribute_deltas,
                baseline.signal_terms,
                context.signal_terms,
                baseline.content_text,
                context.content_text,
            )
            attribute_profile = build_artifact_attribute_profile(
                artifact_path=artifact.artifact_path,
                artifact_type=artifact.artifact_type,
                baseline_profile=baseline.profile,
                current_profile=context.profile,
                attribute_deltas=context.attribute_deltas,
                baseline_signal_terms=baseline.signal_terms,
                current_signal_terms=context.signal_terms,
                baseline_content=baseline.content_text,
                current_content=context.content_text,
            ).dimensions
            changed_labels = [finding.label.lower() for finding in attribute_findings]
            if changed_labels:
                headline_summary = f"{_sentence_source_label(context)} drift detected in {_human_join(changed_labels)}."
            else:
                headline_summary = f"{_sentence_source_label(context)} changed posture relative to baseline, but no single high-risk attribute dominated."
            can_promote_source_to_baseline = True
            provenance = RepoArtifactProvenance(
                source_type=context.source_type,
                label=context.label,
                source_ref=context.source_ref,
                source_url=context.source_url,
                review_context=context.review_context,
                created_at=context.created_at,
            )

        design_profiles.append(
            RepoArtifactDesignProfile(
                artifact_path=artifact.artifact_path,
                artifact_type=artifact.artifact_type,
                drift_from_baseline=drift_from_baseline,
                drift_label=drift_label,
                drift_tone=drift_tone,
                baseline_profile=baseline_profile,
                current_profile=current_profile,
                baseline_provenance=baseline_provenance,
                headline_summary=headline_summary,
                risk_tags=risk_tags,
                narrative=narrative,
                attribute_findings=attribute_findings,
                attribute_profile=attribute_profile,
                can_promote_source_to_baseline=can_promote_source_to_baseline,
                provenance=provenance,
            )
        )

    return design_profiles


def _build_overview_attention_repos(repo_views: list[RepoDashboardView]) -> list[DashboardOverviewAttentionRepo]:
    attention_repos: list[DashboardOverviewAttentionRepo] = []
    for view in repo_views:
        sorted_insights = sorted(
            view.insights,
            key=lambda insight: (priority_sort_rank(insight.priority), -insight.score, insight.artifact_path),
        )
        top_insight = sorted_insights[0] if sorted_insights else None
        attention_repos.append(
            DashboardOverviewAttentionRepo(
                repo_full=view.repo_full,
                highest_priority=top_insight.priority if top_insight is not None else "baseline_review",
                highest_insight_title=top_insight.title if top_insight is not None else None,
                highest_insight_artifact_path=top_insight.artifact_path if top_insight is not None else None,
                highest_evidence_label=top_insight.evidence_label if top_insight is not None else None,
                highest_evidence_summary=top_insight.evidence_summary if top_insight is not None else None,
                highest_change_summary=top_insight.change_summary if top_insight is not None else None,
                highest_flag_summary=top_insight.flag_summary if top_insight is not None else None,
                highest_rationale=top_insight.rationale if top_insight is not None else None,
                highest_recommended_action=top_insight.recommended_action if top_insight is not None else None,
                highest_baseline_label=top_insight.baseline_label if top_insight is not None else None,
                highest_review_target=top_insight.review_target if top_insight is not None else None,
                highest_review_url=top_insight.review_url if top_insight is not None else None,
                insight_count=len(view.insights),
                lower_confidence_count=len(view.lower_confidence_insights),
                review_now_count=sum(1 for insight in view.insights if insight.priority == "review_now"),
                watch_count=sum(1 for insight in view.insights if insight.priority == "watch"),
                baseline_review_count=sum(1 for insight in view.insights if insight.priority == "baseline_review"),
                top_drift_magnitude=max(
                    [entry.drift_magnitude for entry in view.top_drifting_artifacts] or [0.0]
                ),
                avg_semantic_distance=view.drift_summary.avg_semantic_distance,
                discovered_artifact_count=(view.onboarding.discovered_artifact_count if view.onboarding is not None else 0),
                highest_updated_at=(top_insight.updated_at if top_insight is not None else None),
            )
        )

    attention_repos.sort(
        key=lambda repo: (
            priority_sort_rank(repo.highest_priority),
            -repo.review_now_count,
            -repo.top_drift_magnitude,
            repo.repo_full,
        )
    )
    return attention_repos


def _artifact_risk_tags(artifact: RepoDashboardArtifactEntry, attribute_deltas: dict[str, float] | None = None) -> list[str]:
    attribute_deltas = attribute_deltas or {}
    tags: list[str] = []
    if attribute_deltas.get("capability_risk", artifact.latest_historical_capability_shift) > 0.05:
        tags.append("capability expanded")
    if attribute_deltas.get("guardrail_robustness", artifact.latest_historical_guardrail_shift) < -0.05:
        tags.append("guardrails weakened")
    if attribute_deltas.get("governance_strength", artifact.latest_historical_governance_shift) < -0.05:
        tags.append("governance weakened")
    if attribute_deltas.get("autonomy_level", artifact.latest_historical_autonomy_shift) > 0.05:
        tags.append("autonomy increased")
    if artifact.latest_historical_drift_magnitude > 0.35:
        tags.append("historical hotspot")
    if not tags:
        tags.append("baseline only")
    return tags


def _build_overview_regression_patterns(repo_views: list[RepoDashboardView]) -> list[DashboardOverviewRegressionPattern]:
    pattern_defs = [
        (
            "capability_expansion",
            "Capability expansion",
            "Baseline-relative changes increased capability or widened operational reach.",
        ),
        (
            "guardrail_weakening",
            "Guardrail weakening",
            "Constraints, approvals, or escalation language weakened relative to the baseline.",
        ),
        (
            "governance_weakening",
            "Governance weakening",
            "Review gates, approval cues, or governance instructions weakened relative to the baseline.",
        ),
        (
            "autonomy_increase",
            "Autonomy increase",
            "Artifacts now signal more independent execution, broader action, or less supervision.",
        ),
        (
            "historical_hotspot",
            "Historical hotspots",
            "Stored history shows repeated design movement even when recent landed signals are limited.",
        ),
        (
            "baseline_candidate",
            "Baseline candidates",
            "Tracked control surfaces still need stronger comparative evidence before they can be classified as active regressions.",
        ),
    ]
    grouped: dict[str, dict[str, object]] = {
        pattern_key: {
            "label": label,
            "summary": summary,
            "repo_set": set(),
            "artifact_count": 0,
            "review_now_artifact_count": 0,
            "max_drift_magnitude": 0.0,
            "example_repo_full": None,
            "example_artifact_path": None,
            "example_title": None,
        }
        for pattern_key, label, summary in pattern_defs
    }

    for view in repo_views:
        insight_by_path = {insight.artifact_path: insight for insight in view.insights}
        for artifact in view.artifacts:
            matches = _artifact_regression_pattern_keys(artifact)
            if not matches:
                continue

            insight = insight_by_path.get(artifact.artifact_path)
            drift_magnitude = max(
                artifact.leaderboard_drift_magnitude,
                artifact.latest_historical_drift_magnitude,
                abs(min(artifact.latest_historical_guardrail_shift, 0.0)),
                max(artifact.latest_historical_capability_shift, 0.0),
                max(artifact.latest_historical_autonomy_shift, 0.0),
            )
            for pattern_key in matches:
                group = grouped[pattern_key]
                repo_set = group["repo_set"]
                assert isinstance(repo_set, set)
                repo_set.add(view.repo_full)
                group["artifact_count"] = int(group["artifact_count"]) + 1
                if insight is not None and insight.priority == "review_now":
                    group["review_now_artifact_count"] = int(group["review_now_artifact_count"]) + 1
                if drift_magnitude >= float(group["max_drift_magnitude"]):
                    group["max_drift_magnitude"] = round(drift_magnitude, 3)
                    group["example_repo_full"] = view.repo_full
                    group["example_artifact_path"] = artifact.artifact_path
                    group["example_title"] = insight.title if insight is not None else _pattern_default_title(pattern_key)

    results = [
        DashboardOverviewRegressionPattern(
            pattern_key=pattern_key,
            label=str(group["label"]),
            repo_count=len(group["repo_set"]),
            artifact_count=int(group["artifact_count"]),
            review_now_artifact_count=int(group["review_now_artifact_count"]),
            max_drift_magnitude=round(float(group["max_drift_magnitude"]), 3),
            example_repo_full=(str(group["example_repo_full"]) if group["example_repo_full"] is not None else None),
            example_artifact_path=(
                str(group["example_artifact_path"]) if group["example_artifact_path"] is not None else None
            ),
            example_title=(str(group["example_title"]) if group["example_title"] is not None else None),
            summary=str(group["summary"]),
        )
        for pattern_key, group in grouped.items()
        if int(group["artifact_count"]) > 0
    ]
    results.sort(key=lambda item: (-item.artifact_count, -item.review_now_artifact_count, item.label))
    return results


def _artifact_regression_pattern_keys(artifact: RepoDashboardArtifactEntry) -> list[str]:
    pattern_keys: list[str] = []
    if artifact.latest_historical_capability_shift > 0.05:
        pattern_keys.append("capability_expansion")
    if artifact.latest_historical_guardrail_shift < -0.05:
        pattern_keys.append("guardrail_weakening")
    if artifact.latest_historical_governance_shift < -0.05:
        pattern_keys.append("governance_weakening")
    if artifact.latest_historical_autonomy_shift > 0.05:
        pattern_keys.append("autonomy_increase")
    if artifact.latest_historical_drift_magnitude > 0.35:
        pattern_keys.append("historical_hotspot")
    if not pattern_keys and artifact.discovery_confidence >= 0.75:
        pattern_keys.append("baseline_candidate")
    return pattern_keys


def _pattern_default_title(pattern_key: str) -> str:
    return {
        "capability_expansion": "Capability expansion needs review",
        "guardrail_weakening": "Guardrail regression needs review",
        "governance_weakening": "Governance regression needs review",
        "autonomy_increase": "Autonomy increase needs review",
        "historical_hotspot": "Historical drift hotspot",
        "baseline_candidate": "High-value control surface to baseline",
    }[pattern_key]


def _build_overview_control_surface_coverage(repo_views: list[RepoDashboardView]) -> list[DashboardOverviewControlSurface]:
    grouped: dict[str, DashboardOverviewControlSurface] = {}
    repo_sets: dict[str, set[str]] = {}
    for view in repo_views:
        for group in view.control_surface_groups:
            repo_sets.setdefault(group.group_key, set()).add(view.repo_full)
            current = grouped.get(group.group_key)
            if current is None:
                grouped[group.group_key] = DashboardOverviewControlSurface(
                    group_key=group.group_key,
                    label=group.label,
                    repo_count=0,
                    artifact_count=group.artifact_count,
                    high_confidence_count=group.high_confidence_count,
                )
            else:
                grouped[group.group_key] = DashboardOverviewControlSurface(
                    group_key=current.group_key,
                    label=current.label,
                    repo_count=0,
                    artifact_count=current.artifact_count + group.artifact_count,
                    high_confidence_count=current.high_confidence_count + group.high_confidence_count,
                )

    results = [
        DashboardOverviewControlSurface(
            group_key=item.group_key,
            label=item.label,
            repo_count=len(repo_sets.get(item.group_key, set())),
            artifact_count=item.artifact_count,
            high_confidence_count=item.high_confidence_count,
        )
        for item in grouped.values()
    ]
    results.sort(key=lambda item: (-item.repo_count, -item.artifact_count, item.label))
    return results


def _build_overview_risk_state(attention_repos: list[DashboardOverviewAttentionRepo]) -> DashboardOverviewRiskState:
    review_now_repo_count = sum(1 for repo in attention_repos if repo.highest_priority == "review_now")
    watch_repo_count = sum(1 for repo in attention_repos if repo.highest_priority == "watch")
    baseline_review_repo_count = sum(1 for repo in attention_repos if repo.highest_priority == "baseline_review")

    top_repo = attention_repos[0] if attention_repos else None
    if review_now_repo_count > 0:
        status = "high_attention"
        headline = "High-attention risk requires review"
        summary = "At least one repository has capability expansion, guardrail regression, or strong drift signals that should be reviewed now."
    elif watch_repo_count > 0:
        status = "watch"
        headline = "Risk posture is stable but worth watching"
        summary = "No repository is in immediate review-now status, but there are active drift signals that deserve follow-up."
    else:
        status = "baseline"
        headline = "Risk posture is baseline-oriented"
        summary = "Current repositories mostly need baseline confirmation and broader coverage rather than urgent investigation."

    return DashboardOverviewRiskState(
        status=status,
        headline=headline,
        summary=summary,
        review_now_repo_count=review_now_repo_count,
        watch_repo_count=watch_repo_count,
        baseline_review_repo_count=baseline_review_repo_count,
        highest_risk_repo_full=top_repo.repo_full if top_repo is not None else None,
        highest_risk_artifact_path=top_repo.highest_insight_artifact_path if top_repo is not None else None,
        highest_risk_title=top_repo.highest_insight_title if top_repo is not None else None,
        highest_drift_magnitude=top_repo.top_drift_magnitude if top_repo is not None else 0.0,
    )


def _build_overview_regressions(repo_views: list[RepoDashboardView]) -> list[DashboardOverviewRegressionEntry]:
    items: list[DashboardOverviewRegressionEntry] = []
    for view in repo_views:
        artifact_by_path = {artifact.artifact_path: artifact for artifact in view.artifacts}
        for insight in view.insights:
            artifact = artifact_by_path.get(insight.artifact_path)
            if artifact is None:
                continue
            drift_magnitude = max(
                artifact.leaderboard_drift_magnitude,
                artifact.latest_historical_drift_magnitude,
                abs(min(artifact.latest_historical_guardrail_shift, 0.0)),
                max(artifact.latest_historical_capability_shift, 0.0),
            )
            items.append(
                DashboardOverviewRegressionEntry(
                    repo_full=view.repo_full,
                    artifact_path=artifact.artifact_path,
                    artifact_type=artifact.artifact_type,
                    title=insight.title,
                    priority=insight.priority,
                    confidence_label=insight.confidence_label,
                    evidence_label=insight.evidence_label,
                    evidence_summary=insight.evidence_summary,
                    baseline_label=insight.baseline_label,
                    provenance_summary=insight.provenance_summary,
                    review_target=insight.review_target,
                    review_url=insight.review_url,
                    change_summary=insight.change_summary,
                    flag_summary=insight.flag_summary,
                    rationale=insight.rationale,
                    recommended_action=insight.recommended_action,
                    drift_magnitude=drift_magnitude,
                    capability_shift=artifact.latest_historical_capability_shift,
                    guardrail_shift=artifact.latest_historical_guardrail_shift,
                    attribute_profile=insight.attribute_profile,
                )
            )

    items.sort(
        key=lambda item: (
            priority_sort_rank(item.priority),
            -max(item.drift_magnitude, item.capability_shift, abs(min(item.guardrail_shift, 0.0))),
            item.repo_full,
            item.artifact_path,
        )
    )
    return items[:8]


def _build_overview_control_surface_risk(repo_views: list[RepoDashboardView]) -> list[DashboardOverviewRiskDistribution]:
    grouped: dict[str, dict[str, float | int | str | set[str]]] = {}
    for view in repo_views:
        insight_by_path = {insight.artifact_path: insight for insight in view.insights}
        for artifact in view.artifacts:
            group_key = _artifact_group_key(artifact)
            group = grouped.setdefault(
                group_key,
                {
                    "label": _control_surface_label(group_key),
                    "repo_set": set(),
                    "artifact_count": 0,
                    "weighted_risk": 0.0,
                    "review_now_artifact_count": 0,
                },
            )
            group["repo_set"].add(view.repo_full)
            group["artifact_count"] = int(group["artifact_count"]) + 1
            insight = insight_by_path.get(artifact.artifact_path)
            if insight is not None:
                group["weighted_risk"] = float(group["weighted_risk"]) + priority_weighted_risk(
                    insight.score,
                    insight.priority,
                )
            else:
                group["weighted_risk"] = float(group["weighted_risk"]) + priority_weighted_risk(_insight_score(artifact))
            if insight is not None and insight.priority == "review_now":
                group["review_now_artifact_count"] = int(group["review_now_artifact_count"]) + 1

    results = [
        DashboardOverviewRiskDistribution(
            group_key=group_key,
            label=str(group["label"]),
            repo_count=len(group["repo_set"]),
            artifact_count=int(group["artifact_count"]),
            weighted_risk=round(float(group["weighted_risk"]), 3),
            review_now_artifact_count=int(group["review_now_artifact_count"]),
        )
        for group_key, group in grouped.items()
    ]
    results.sort(key=lambda item: (-item.weighted_risk, -item.review_now_artifact_count, item.label))
    return results


def _control_surface_label(group_key: str) -> str:
    return {
        "prompts": "Prompts and instructions",
        "guardrails": "Guardrails and policy",
        "models": "Model and generation config",
        "tools": "Tooling and orchestration",
        "agents": "Agent code and assets",
        "retrieval": "Retrieval and knowledge",
        "other": "Other AI-related artifacts",
    }[group_key]


def _profile_vector(profile: AgentAttributeProfile) -> DashboardProfileVector:
    return DashboardProfileVector(
        guardrail_robustness=profile.guardrail_robustness,
        capability_risk=profile.capability_risk,
        autonomy_level=profile.autonomy_level,
        stability_vs_creativity=profile.stability_vs_creativity,
        governance_strength=profile.governance_strength,
    )


def _profile_from_json(profile_json: str) -> AgentAttributeProfile:
    payload = json.loads(profile_json)
    signal_payload = payload["signals"]
    return AgentAttributeProfile(
        guardrail_robustness=float(payload["guardrail_robustness"]),
        capability_risk=float(payload["capability_risk"]),
        autonomy_level=float(payload["autonomy_level"]),
        stability_vs_creativity=float(payload["stability_vs_creativity"]),
        governance_strength=float(payload["governance_strength"]),
        change_frequency=float(payload["change_frequency"]),
        semantic_density=float(payload["semantic_density"]),
        signals=StaticSignals(
            token_count=int(signal_payload["token_count"]),
            char_count=int(signal_payload["char_count"]),
            section_count=int(signal_payload["section_count"]),
            example_count=int(signal_payload["example_count"]),
            instruction_density=float(signal_payload["instruction_density"]),
            constraint_count=int(signal_payload["constraint_count"]),
            explicit_limit_count=int(signal_payload["explicit_limit_count"]),
            ambiguity_count=int(signal_payload["ambiguity_count"]),
            guardrail_counts={key: int(value) for key, value in signal_payload.get("guardrail_counts", {}).items()},
            write_signal_count=int(signal_payload.get("write_signal_count", 0)),
            read_signal_count=int(signal_payload.get("read_signal_count", 0)),
            sensitive_tool_count=int(signal_payload.get("sensitive_tool_count", 0)),
            prod_signal_count=int(signal_payload.get("prod_signal_count", 0)),
            sandbox_signal_count=int(signal_payload.get("sandbox_signal_count", 0)),
            systems_touched_count=int(signal_payload.get("systems_touched_count", 0)),
            human_review_count=int(signal_payload.get("human_review_count", 0)),
            parallelism_signal_count=int(signal_payload.get("parallelism_signal_count", 0)),
            max_steps=int(signal_payload.get("max_steps", 0)),
            temperature=(float(signal_payload["temperature"]) if signal_payload.get("temperature") is not None else None),
            top_p=(float(signal_payload["top_p"]) if signal_payload.get("top_p") is not None else None),
        ),
    )