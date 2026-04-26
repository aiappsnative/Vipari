from __future__ import annotations

import difflib
import json
import sqlite3
from dataclasses import dataclass

from engine.drift_profile import AgentAttributeProfile, StaticSignals
from .baseline_provenance import (
    BaselineProvenance,
    approved_onboarding_provenance,
    baseline_provenance_from_json,
    historical_fallback_provenance,
    no_baseline_provenance,
    previous_pr_fallback_provenance,
)

from .audit_records import (
    ArtifactDriftLeaderboardEntry,
    RepoStaticDriftSummary,
    get_repo_static_drift_summary,
    list_pull_request_audits_for_repo,
    list_top_drifting_artifacts_for_repo,
)
from .signal_fusion import priority_from_fused_signals, priority_sort_rank, priority_weighted_risk
from .onboarding_records import (
    RepositoryOnboardingRecord,
    get_latest_repository_onboarding,
    list_historical_backfill_jobs_for_repo,
    list_latest_repository_onboardings,
    list_onboarded_artifacts_for_onboarding,
    list_onboarding_baseline_versions_for_onboarding,
)


@dataclass(frozen=True)
class RepoDashboardIndexEntry:
    repo_full: str
    default_branch: str
    onboarding_status: str
    discovered_artifact_count: int
    last_onboarded_at: float


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
    pr_profile_count: int
    latest_pr_semantic_distance: float
    latest_pr_risk_level: str | None
    latest_pr_capability_shift: float
    latest_pr_guardrail_shift: float
    latest_pr_governance_shift: float = 0.0
    latest_pr_autonomy_shift: float = 0.0
    leaderboard_drift_magnitude: float = 0.0
    latest_activity_at: float = 0.0


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
    supporting_review_target: str | None = None
    supporting_review_url: str | None = None
    change_summary: str = ""
    flag_summary: str = ""
    updated_at: float | None = None
    risk_reasons: list[str] = None
    rationale: str = ""
    recommended_action: str = ""


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
class DashboardProfileVector:
    guardrail_robustness: float
    capability_risk: float
    autonomy_level: float
    stability_vs_creativity: float
    governance_strength: float


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
    can_promote_source_to_baseline: bool = False
    provenance: RepoArtifactProvenance | None = None


@dataclass(frozen=True)
class RepoDashboardView:
    repo_full: str
    onboarding: RepositoryOnboardingRecord | None
    backfill: RepoDashboardBackfillSummary
    pull_request_audit_count: int
    baseline_version_count: int
    drift_summary: RepoStaticDriftSummary
    top_drifting_artifacts: list[ArtifactDriftLeaderboardEntry] = None
    insights: list[RepoDashboardInsightEntry] = None
    lower_confidence_insights: list[RepoDashboardInsightEntry] = None
    control_surface_groups: list[RepoDashboardControlSurfaceGroup] = None
    history_timelines: list[RepoArtifactHistoryTimeline] = None
    design_profiles: list[RepoArtifactDesignProfile] = None
    artifacts: list[RepoDashboardArtifactEntry] = None


def list_repo_dashboard_index(db_path: str) -> list[RepoDashboardIndexEntry]:
    onboardings = list_latest_repository_onboardings(db_path)
    return [
        RepoDashboardIndexEntry(
            repo_full=onboarding.repo_full,
            default_branch=onboarding.default_branch,
            onboarding_status=onboarding.status,
            discovered_artifact_count=onboarding.discovered_artifact_count,
            last_onboarded_at=onboarding.updated_at,
        )
        for onboarding in onboardings
    ]


def build_dashboard_overview_view(db_path: str) -> DashboardOverviewView:
    repos = list_repo_dashboard_index(db_path)
    repo_views = [build_repo_dashboard_view(db_path, repo.repo_full) for repo in repos]

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

    metrics = [
        DashboardOverviewMetric(
            label="Onboarded repositories",
            value=len(repos),
            detail="Repos with a stored onboarding record in the local PromptDrift store.",
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
            label="Pull-request audits",
            value=total_pr_audits,
            detail="Persisted PR audit runs represented across the current repository set.",
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
    )


def build_repo_dashboard_view(db_path: str, repo_full: str) -> RepoDashboardView:
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    drift_summary = get_repo_static_drift_summary(db_path, repo_full)
    top_drifting_artifacts = list_top_drifting_artifacts_for_repo(db_path, repo_full)
    pull_request_audit_count = len(list_pull_request_audits_for_repo(db_path, repo_full))

    if onboarding is None:
        return RepoDashboardView(
            repo_full=repo_full,
            onboarding=None,
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
            design_profiles=[],
            artifacts=[],
        )

    artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    baseline_versions = list_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    baseline_by_path = {baseline.artifact_path: baseline for baseline in baseline_versions}
    jobs = list_historical_backfill_jobs_for_repo(db_path, repo_full)
    leaderboard_by_path = {entry.artifact_path: entry for entry in top_drifting_artifacts}
    metrics_by_path = _load_repo_artifact_metrics(db_path, repo_full)
    profile_context_by_path = _load_repo_artifact_profile_contexts(db_path, repo_full)

    artifact_entries: list[RepoDashboardArtifactEntry] = []
    total_historical_versions = sum(metrics["historical_version_count"] for metrics in metrics_by_path.values())
    total_historical_profiles = sum(metrics["historical_profile_count"] for metrics in metrics_by_path.values())

    for artifact in artifacts:
        baseline = baseline_by_path.get(artifact.artifact_path)
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
                pr_profile_count=metrics["pr_profile_count"],
                latest_pr_semantic_distance=metrics["latest_pr_semantic_distance"],
                latest_pr_risk_level=metrics["latest_pr_risk_level"],
                latest_pr_capability_shift=metrics["latest_pr_capability_shift"],
                latest_pr_guardrail_shift=metrics["latest_pr_guardrail_shift"],
                latest_pr_governance_shift=metrics["latest_pr_governance_shift"],
                latest_pr_autonomy_shift=metrics["latest_pr_autonomy_shift"],
                leaderboard_drift_magnitude=(leaderboard_entry.drift_magnitude if leaderboard_entry is not None else 0.0),
                latest_activity_at=metrics["latest_activity_at"],
            )
        )

    artifact_entries.sort(
        key=lambda entry: (
            priority_sort_rank(
                priority_from_fused_signals(
                    _insight_score(entry, profile_context_by_path.get(entry.artifact_path)),
                    risk_level=entry.latest_pr_risk_level,
                )
            ),
            -_insight_score(entry, profile_context_by_path.get(entry.artifact_path)),
            -max(entry.leaderboard_drift_magnitude, entry.latest_historical_drift_magnitude),
            entry.artifact_path,
        )
    )

    insights, lower_confidence_insights = _build_repo_insights(artifact_entries, baseline_by_path, profile_context_by_path)
    control_surface_groups = _build_control_surface_groups(artifact_entries)
    history_timelines = _build_repo_history_timelines(db_path, repo_full, artifact_entries)
    design_profiles = _build_repo_design_profiles(artifact_entries, insights, baseline_by_path, profile_context_by_path)

    return RepoDashboardView(
        repo_full=repo_full,
        onboarding=onboarding,
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
        design_profiles=design_profiles,
        artifacts=artifact_entries,
    )


def _connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _empty_artifact_metrics() -> dict[str, float | int]:
    return {
        "historical_version_count": 0,
        "historical_profile_count": 0,
        "latest_historical_semantic_distance": 0.0,
        "latest_historical_drift_magnitude": 0.0,
        "pr_profile_count": 0,
        "latest_pr_semantic_distance": 0.0,
        "latest_pr_risk_level": None,
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
            metrics["latest_activity_at"] = max(float(metrics["latest_activity_at"]), float(row["created_at"]))

        pr_profile_rows = conn.execute(
            """
            SELECT sap.artifact_path, sap.semantic_distance, sap.attribute_deltas_json, sap.created_at, pra.suggested_risk_level
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
            metrics["latest_pr_semantic_distance"] = float(row["semantic_distance"])
            metrics["latest_pr_risk_level"] = row["suggested_risk_level"]
            metrics["latest_pr_capability_shift"] = attribute_deltas.get("capability_risk", 0.0)
            metrics["latest_pr_guardrail_shift"] = attribute_deltas.get("guardrail_robustness", 0.0)
            metrics["latest_pr_governance_shift"] = attribute_deltas.get("governance_strength", 0.0)
            metrics["latest_pr_autonomy_shift"] = attribute_deltas.get("autonomy_level", 0.0)
            metrics["latest_activity_at"] = max(float(metrics["latest_activity_at"]), float(row["created_at"]))

    return metrics_by_path


def _load_repo_artifact_profile_contexts(db_path: str, repo_full: str) -> dict[str, _RepoArtifactEvidenceBundle]:
    contexts: dict[str, _RepoArtifactEvidenceBundle] = {}
    with _connect(db_path) as conn:
        historical_rows = conn.execute(
            """
             SELECT hsp.artifact_path, hsp.commit_sha, hsp.created_at, hsp.baseline_profile_id, hsp.baseline_provenance_json,
                 hsp.semantic_distance, hsp.profile_json, hsp.attribute_deltas_json, hsp.narrative_json, hsp.signal_terms_json,
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
            context = _RepoArtifactProfileContext(
                profile=_profile_from_json(row["profile_json"]),
                source_type="historical",
                label="Historical backfill",
                source_ref=f"commit {str(row['commit_sha'])[:7]}",
                source_url=_github_commit_url(repo_full, str(row["commit_sha"])),
                review_context="Historical snapshot from backfill",
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

        pr_rows = conn.execute(
            """
             SELECT sap.artifact_path, pra.pr_number, pra.head_sha, pra.status, pra.output_mode,
                 pra.suggested_risk_level, pra.semantic_review_completed, sap.created_at, sap.baseline_profile_id,
                 sap.baseline_provenance_json, sap.artifact_version_id, sap.semantic_distance,
                                     sap.profile_json, sap.attribute_deltas_json, sap.narrative_json, sap.signal_terms_json,
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
                baseline_provenance = previous_pr_fallback_provenance(row["baseline_profile_id"], row["artifact_version_id"])
            if baseline_provenance is None:
                baseline_provenance = no_baseline_provenance()
            context = _RepoArtifactProfileContext(
                profile=_profile_from_json(row["profile_json"]),
                source_type="pull_request",
                label="Pull request audit",
                source_ref=f"PR #{row['pr_number']} · {str(row['head_sha'])[:7]}",
                source_url=_github_pull_request_url(repo_full, int(row["pr_number"])),
                review_context=_format_pr_review_context(
                    output_mode=row["output_mode"],
                    risk_level=row["suggested_risk_level"],
                    status=row["status"],
                    semantic_review_completed=bool(row["semantic_review_completed"]),
                ),
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

    return contexts


def _preferred_profile_context(bundle: _RepoArtifactEvidenceBundle | None) -> _RepoArtifactProfileContext | None:
    if bundle is None:
        return None
    return bundle.latest_pull_request or bundle.latest_historical


def _supporting_profile_context(bundle: _RepoArtifactEvidenceBundle | None) -> _RepoArtifactProfileContext | None:
    if bundle is None:
        return None
    if bundle.latest_pull_request is not None and bundle.latest_historical is not None:
        return bundle.latest_historical
    return None


def _normalized_id_prefix(repo_full: str) -> str:
    return f"{repo_full.lower()}::%"


def _github_pull_request_url(repo_full: str, pr_number: int) -> str:
    return f"https://github.com/{repo_full}/pull/{pr_number}"


def _github_commit_url(repo_full: str, commit_sha: str) -> str:
    return f"https://github.com/{repo_full}/commit/{commit_sha}"


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


def _build_repo_insights(
    artifacts: list[RepoDashboardArtifactEntry],
    baseline_by_path,
    profile_context_by_path: dict[str, _RepoArtifactEvidenceBundle],
) -> tuple[list[RepoDashboardInsightEntry], list[RepoDashboardInsightEntry]]:
    primary_ranked: list[tuple[float, RepoDashboardInsightEntry]] = []
    lower_confidence_ranked: list[tuple[float, RepoDashboardInsightEntry]] = []
    for artifact in artifacts:
        evidence_bundle = profile_context_by_path.get(artifact.artifact_path)
        score = _insight_score(artifact, evidence_bundle)
        priority = priority_from_fused_signals(score, risk_level=artifact.latest_pr_risk_level)
        baseline = baseline_by_path.get(artifact.artifact_path)
        context = _preferred_profile_context(evidence_bundle)
        queue_lane = _insight_queue_lane(artifact, priority, score)
        title = _insight_title(artifact, priority)
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
            supporting_review_target=_supporting_review_target(evidence_bundle),
            supporting_review_url=_supporting_review_url(evidence_bundle),
            change_summary=_change_summary(artifact, evidence_bundle),
            flag_summary=_flag_summary(artifact, priority, evidence_bundle),
            updated_at=(context.created_at if context is not None else artifact.latest_activity_at or None),
            risk_reasons=_insight_reasons(artifact, evidence_bundle),
            rationale=rationale,
            recommended_action=recommended_action,
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
    return [entry for _, entry in primary_ranked[:8]], [entry for _, entry in lower_confidence_ranked[:6]]


def _insight_score(
    artifact: RepoDashboardArtifactEntry,
    evidence_bundle: _RepoArtifactEvidenceBundle | None = None,
) -> float:
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
    drift_signal = max(artifact.leaderboard_drift_magnitude, artifact.latest_historical_drift_magnitude)
    blast_radius = _blast_radius_weight(artifact)
    guardrail_regression = abs(min(artifact.latest_pr_guardrail_shift, 0.0))
    governance_regression = abs(min(artifact.latest_pr_governance_shift, 0.0))
    capability_expansion = max(artifact.latest_pr_capability_shift, 0.0)
    autonomy_increase = max(artifact.latest_pr_autonomy_shift, 0.0)
    confidence_bonus = artifact.discovery_confidence * 0.14
    recency_bonus = 0.12 if artifact.latest_activity_at > 0 else 0.0
    history_bonus = min(artifact.historical_version_count, 5) * 0.04
    pr_evidence_bonus = 0.2 if evidence_bundle is not None and evidence_bundle.latest_pull_request is not None else 0.0
    history_only_penalty = (
        0.12
        if evidence_bundle is not None and evidence_bundle.latest_pull_request is None and evidence_bundle.latest_historical is not None
        else 0.0
    )
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
        + pr_evidence_bonus
        - history_only_penalty,
        4,
    )


def _insight_title(artifact: RepoDashboardArtifactEntry, priority: str) -> str:
    if _blast_radius_weight(artifact) >= 0.45 and artifact.latest_pr_capability_shift > 0.05:
        return "Critical control surface expanded authority"
    if artifact.latest_pr_capability_shift > 0.05:
        return "Capability expansion needs review"
    if artifact.latest_pr_guardrail_shift < -0.05:
        return "Guardrail regression needs review"
    if artifact.latest_pr_governance_shift < -0.05:
        return "Governance regression needs review"
    if artifact.latest_historical_drift_magnitude > 0.35:
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
    has_pr_evidence = evidence_bundle is not None and evidence_bundle.latest_pull_request is not None
    if _blast_radius_weight(artifact) >= 0.45 and artifact.latest_pr_capability_shift > 0.05:
        return f"A critical control surface broadened authority {baseline_context}, increasing blast radius and review urgency."
    if artifact.latest_pr_capability_shift > 0.05 and artifact.latest_pr_guardrail_shift < -0.05:
        return f"Recent pull-request drift suggests broader authority while guardrails weakened {baseline_context}."
    if artifact.latest_pr_capability_shift > 0.05:
        return f"Recent pull-request history increased capability risk {baseline_context}."
    if artifact.latest_pr_guardrail_shift < -0.05:
        return f"Recent pull-request history weakened guardrail posture {baseline_context}."
    if artifact.latest_pr_governance_shift < -0.05:
        return f"Recent pull-request history weakened governance or approval posture {baseline_context}."
    if artifact.latest_historical_drift_magnitude > 0.35:
        if not has_pr_evidence:
            return "Merged history shows meaningful design movement, but no PR-linked evidence is stored yet, so the latest commit trail should be reviewed first."
        return "Historical snapshots show meaningful design movement that deserves a human read."
    if priority == "baseline_review":
        return "This artifact looks like a real AI control surface but does not yet have meaningful drift context."
    return "This artifact is likely part of the AI control surface and should stay on the review radar."


def _insight_action(
    artifact: RepoDashboardArtifactEntry,
    priority: str,
    evidence_bundle: _RepoArtifactEvidenceBundle | None,
) -> str:
    has_pr_evidence = evidence_bundle is not None and evidence_bundle.latest_pull_request is not None
    if _blast_radius_weight(artifact) >= 0.45 and artifact.latest_pr_capability_shift > 0.05:
        return "Escalate this surface to the AI platform owner before merge and inspect the linked PR or commit first."
    if artifact.latest_pr_capability_shift > 0.05:
        return "Inspect authority, tool access, and production-facing behavior in the linked PR before accepting this change."
    if artifact.latest_pr_guardrail_shift < -0.05:
        return "Review missing constraints, escalation paths, and refusal language in the linked PR before merge."
    if artifact.latest_pr_governance_shift < -0.05:
        return "Check whether approvals, review gates, or audit instructions were weakened and route this change for human review."
    if artifact.latest_historical_drift_magnitude > 0.35:
        if not has_pr_evidence:
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
    if artifact.latest_pr_capability_shift > 0.05:
        reasons.append("capability expanded")
    if artifact.latest_pr_guardrail_shift < -0.05:
        reasons.append("guardrails weakened")
    if artifact.latest_pr_governance_shift < -0.05:
        reasons.append("governance weakened")
    if artifact.latest_pr_autonomy_shift > 0.05:
        reasons.append("autonomy increased")
    if artifact.latest_historical_drift_magnitude > 0.35:
        reasons.append("historical hotspot")
    if evidence_bundle is not None and evidence_bundle.latest_pull_request is not None:
        reasons.append("pr-linked evidence")
    elif evidence_bundle is not None and evidence_bundle.latest_historical is not None:
        reasons.append("history-only evidence")
    if not reasons:
        reasons.append(_confidence_label(artifact.discovery_confidence))
    return reasons


def _baseline_label(baseline, evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str:
    context = _preferred_profile_context(evidence_bundle)
    provenance = context.baseline_provenance if context is not None else None
    if provenance is None and baseline is not None:
        provenance = approved_onboarding_provenance(baseline.id)
    if provenance is None:
        return "Baseline: none yet"
    if provenance.is_authoritative:
        source_id = provenance.source_version_id if provenance.source_version_id is not None else baseline.id if baseline is not None else None
        suffix = f" #{source_id}" if source_id is not None else ""
        return f"Baseline: Approved{suffix}"
    if provenance.source_type == "historical_reference":
        return "Baseline: Auto-baseline (historical fallback)"
    if provenance.source_type == "previous_pr_reference":
        return "Baseline: Auto-baseline (previous PR fallback)"
    return "Baseline: none yet"


def _evidence_label(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str:
    if evidence_bundle is None:
        return "baseline only"
    if evidence_bundle.latest_pull_request is not None and evidence_bundle.latest_historical is not None:
        return "PR + history"
    if evidence_bundle.latest_pull_request is not None:
        return "PR-linked"
    if evidence_bundle.latest_historical is not None:
        return "history only"
    return "baseline only"


def _evidence_summary(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str:
    if evidence_bundle is None:
        return "No stored PR or merged-history evidence yet."
    preferred = _preferred_profile_context(evidence_bundle)
    supporting = _supporting_profile_context(evidence_bundle)
    if preferred is None:
        return "No stored PR or merged-history evidence yet."
    if supporting is not None:
        return f"Open {preferred.source_ref or preferred.label} first; supporting merged history is available from {supporting.source_ref or supporting.label}."
    if evidence_bundle.latest_pull_request is not None:
        return f"PR-linked evidence is available from {preferred.source_ref or preferred.label}."
    return f"Only merged-history evidence is available right now; start with {preferred.source_ref or preferred.label}."


def _provenance_summary(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str:
    context = _preferred_profile_context(evidence_bundle)
    if context is None:
        return "No PR or history provenance yet"
    parts = ["From", context.source_ref or context.label, context.review_context]
    supporting = _supporting_profile_context(evidence_bundle)
    if supporting is not None:
        parts.append(f"supporting merged history {supporting.source_ref or supporting.label}")
    return " · ".join(part for part in parts if part)


def _review_target(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str | None:
    context = _preferred_profile_context(evidence_bundle)
    if context is None:
        return None
    return context.source_ref or context.label


def _review_url(evidence_bundle: _RepoArtifactEvidenceBundle | None) -> str | None:
    context = _preferred_profile_context(evidence_bundle)
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
    context = _preferred_profile_context(evidence_bundle)
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
    has_pr_evidence = evidence_bundle is not None and evidence_bundle.latest_pull_request is not None
    if _blast_radius_weight(artifact) >= 0.45 and artifact.latest_pr_capability_shift > 0.05 and artifact.latest_pr_guardrail_shift < -0.05:
        return "Flagged because a critical surface broadened authority while guardrails weakened."
    if _blast_radius_weight(artifact) >= 0.45 and artifact.latest_pr_capability_shift > 0.05:
        return "Flagged because a critical surface gained broader authority than the baseline."
    if artifact.latest_pr_capability_shift > 0.05 and artifact.latest_pr_guardrail_shift < -0.05:
        return "Flagged because authority expanded while safety boundaries weakened."
    if artifact.latest_pr_capability_shift > 0.05:
        return "Flagged because capability expanded on a monitored control surface."
    if artifact.latest_pr_guardrail_shift < -0.05:
        return "Flagged because guardrails look weaker than the approved baseline."
    if artifact.latest_pr_governance_shift < -0.05:
        return "Flagged because governance or approval posture looks weaker than baseline."
    if artifact.latest_pr_autonomy_shift > 0.05:
        return "Flagged because the system appears more autonomous than baseline."
    if artifact.latest_historical_drift_magnitude > 0.35:
        if not has_pr_evidence:
            return "Flagged because merged history shows repeated design movement, but no PR-linked evidence is stored yet."
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
        "capability_risk": artifact.latest_pr_capability_shift,
        "guardrail_robustness": artifact.latest_pr_guardrail_shift,
        "governance_strength": artifact.latest_pr_governance_shift,
        "autonomy_level": artifact.latest_pr_autonomy_shift,
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
        return f"PromptDrift detected weaker guardrail posture because {'; '.join(reasons[:2])}."
    return "PromptDrift detected weaker guardrail posture because constraint and refusal signals no longer match the approved baseline."


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
        return f"PromptDrift detected {prefix} because {'; '.join(reasons[:2])}."
    return (
        "PromptDrift detected broader authority because the artifact now signals more sensitive or production-facing actions than the baseline."
        if delta > 0
        else "PromptDrift detected reduced authority because the artifact now signals fewer sensitive or production-facing actions than the baseline."
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
        return f"PromptDrift detected {prefix} because {'; '.join(reasons[:2])}."
    return (
        "PromptDrift detected more independent execution because the workflow now allows deeper or less supervised action than the baseline."
        if delta > 0
        else "PromptDrift detected less independent execution because the workflow now allows shallower or more supervised action than the baseline."
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
        return f"PromptDrift detected {prefix} behavior because {'; '.join(reasons[:2])}."
    return (
        "PromptDrift detected more stable and deterministic behavior because the current sampling settings are tighter than the baseline."
        if delta > 0
        else "PromptDrift detected more variable and creative behavior because the current sampling settings are looser than the baseline."
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
    if artifact.artifact_type in {"prompt", "system_prompt"}:
        return "prompts"
    if artifact.artifact_type in {"guardrail", "policy"}:
        return "guardrails"
    if artifact.artifact_type == "model_config":
        return "models"
    if artifact.artifact_type == "tooling":
        return "tools"
    if artifact.artifact_type == "retrieval":
        return "retrieval"
    if artifact.artifact_type == "ai_code":
        return "agents"
    return "other"


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
            SELECT artifact_path, artifact_type, commit_sha, created_at, baseline_profile_id, baseline_provenance_json,
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
            points_by_path[artifact_path].append(
                RepoArtifactTimelinePoint(
                    source="historical",
                    label="Historical backfill",
                    source_ref=f"commit {str(row['commit_sha'])[:7]}",
                    source_url=_github_commit_url(repo_full, str(row["commit_sha"])),
                    review_context="Historical snapshot from backfill",
                    created_at=float(row["created_at"]),
                    baseline_provenance=baseline_provenance,
                    semantic_distance=semantic_distance,
                    capability_shift=attribute_deltas.get("capability_risk", 0.0),
                    guardrail_shift=attribute_deltas.get("guardrail_robustness", 0.0),
                    autonomy_shift=attribute_deltas.get("autonomy_level", 0.0),
                    drift_magnitude=_drift_magnitude(semantic_distance, attribute_deltas),
                )
            )

        pr_rows = conn.execute(
            """
             SELECT sap.artifact_path, sap.artifact_type, pra.pr_number, pra.head_sha, pra.status, pra.output_mode,
                 pra.suggested_risk_level, pra.semantic_review_completed, sap.created_at, sap.baseline_profile_id,
                 sap.baseline_provenance_json, sap.artifact_version_id, sap.semantic_distance, sap.attribute_deltas_json
            FROM static_artifact_profiles sap
            INNER JOIN pull_request_audits pra ON pra.id = sap.audit_id
            WHERE pra.repo_full = ?
            ORDER BY sap.created_at ASC, sap.id ASC
            """,
            (repo_full,),
        ).fetchall()
        for row in pr_rows:
            artifact_path = row["artifact_path"]
            if artifact_path not in points_by_path:
                continue
            attribute_deltas = {key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()}
            semantic_distance = float(row["semantic_distance"])
            baseline_provenance = baseline_provenance_from_json(row["baseline_provenance_json"])
            if baseline_provenance is None and row["baseline_profile_id"] is not None:
                baseline_provenance = previous_pr_fallback_provenance(row["baseline_profile_id"], row["artifact_version_id"])
            if baseline_provenance is None:
                baseline_provenance = no_baseline_provenance()
            points_by_path[artifact_path].append(
                RepoArtifactTimelinePoint(
                    source="pull_request",
                    label="Pull request audit",
                    source_ref=f"PR #{row['pr_number']} · {str(row['head_sha'])[:7]}",
                    source_url=_github_pull_request_url(repo_full, int(row["pr_number"])),
                    review_context=_format_pr_review_context(
                        output_mode=row["output_mode"],
                        risk_level=row["suggested_risk_level"],
                        status=row["status"],
                        semantic_review_completed=bool(row["semantic_review_completed"]),
                    ),
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
        baseline_provenance = approved_onboarding_provenance(baseline.id)
        baseline_profile = _profile_vector(baseline.profile)
        if context is None:
            current_profile = baseline_profile
            drift_from_baseline = 0.0
            drift_label, drift_tone = _classify_drift_magnitude(drift_from_baseline)
            risk_tags = ["baseline only"]
            narrative = ["No drift samples yet. This surface is currently represented only by the approved baseline."]
            headline_summary = "No source change with stored drift evidence yet."
            attribute_findings: list[RepoArtifactAttributeFinding] = []
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
    if attribute_deltas.get("capability_risk", artifact.latest_pr_capability_shift) > 0.05:
        tags.append("capability expanded")
    if attribute_deltas.get("guardrail_robustness", artifact.latest_pr_guardrail_shift) < -0.05:
        tags.append("guardrails weakened")
    if attribute_deltas.get("governance_strength", artifact.latest_pr_governance_shift) < -0.05:
        tags.append("governance weakened")
    if attribute_deltas.get("autonomy_level", artifact.latest_pr_autonomy_shift) > 0.05:
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
            "Stored history shows repeated design movement even when recent PR signals are limited.",
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
                abs(min(artifact.latest_pr_guardrail_shift, 0.0)),
                max(artifact.latest_pr_capability_shift, 0.0),
                max(artifact.latest_pr_autonomy_shift, 0.0),
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
    if artifact.latest_pr_capability_shift > 0.05:
        pattern_keys.append("capability_expansion")
    if artifact.latest_pr_guardrail_shift < -0.05:
        pattern_keys.append("guardrail_weakening")
    if artifact.latest_pr_governance_shift < -0.05:
        pattern_keys.append("governance_weakening")
    if artifact.latest_pr_autonomy_shift > 0.05:
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
                abs(min(artifact.latest_pr_guardrail_shift, 0.0)),
                max(artifact.latest_pr_capability_shift, 0.0),
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
                    capability_shift=artifact.latest_pr_capability_shift,
                    guardrail_shift=artifact.latest_pr_guardrail_shift,
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