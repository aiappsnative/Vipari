from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .audit_records import (
    ArtifactDriftLeaderboardEntry,
    RepoStaticDriftSummary,
    get_repo_static_drift_summary,
    list_pull_request_audits_for_repo,
    list_top_drifting_artifacts_for_repo,
)
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
    insight_count: int
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
class DashboardOverviewRegressionEntry:
    repo_full: str
    artifact_path: str
    artifact_type: str
    title: str
    priority: str
    drift_magnitude: float
    capability_shift: float
    guardrail_shift: float


@dataclass(frozen=True)
class DashboardOverviewView:
    risk_state: DashboardOverviewRiskState
    metrics: list[DashboardOverviewMetric]
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
    latest_pr_capability_shift: float
    latest_pr_guardrail_shift: float
    leaderboard_drift_magnitude: float


@dataclass(frozen=True)
class RepoDashboardInsightEntry:
    title: str
    artifact_path: str
    artifact_type: str
    priority: str
    score: float
    rationale: str
    recommended_action: str


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
    created_at: float
    semantic_distance: float
    capability_shift: float
    guardrail_shift: float
    autonomy_shift: float
    drift_magnitude: float


@dataclass(frozen=True)
class RepoArtifactHistoryTimeline:
    artifact_path: str
    artifact_type: str
    point_count: int
    max_drift_magnitude: float
    points: list[RepoArtifactTimelinePoint]


@dataclass(frozen=True)
class RepoDashboardView:
    repo_full: str
    onboarding: RepositoryOnboardingRecord | None
    backfill: RepoDashboardBackfillSummary
    pull_request_audit_count: int
    baseline_version_count: int
    drift_summary: RepoStaticDriftSummary
    top_drifting_artifacts: list[ArtifactDriftLeaderboardEntry]
    insights: list[RepoDashboardInsightEntry]
    control_surface_groups: list[RepoDashboardControlSurfaceGroup]
    history_timelines: list[RepoArtifactHistoryTimeline]
    artifacts: list[RepoDashboardArtifactEntry]


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
            control_surface_groups=[],
            history_timelines=[],
            artifacts=[],
        )

    artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    baseline_versions = list_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    baseline_by_path = {baseline.artifact_path: baseline for baseline in baseline_versions}
    jobs = list_historical_backfill_jobs_for_repo(db_path, repo_full)
    leaderboard_by_path = {entry.artifact_path: entry for entry in top_drifting_artifacts}
    metrics_by_path = _load_repo_artifact_metrics(db_path, repo_full)

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
                latest_pr_capability_shift=metrics["latest_pr_capability_shift"],
                latest_pr_guardrail_shift=metrics["latest_pr_guardrail_shift"],
                leaderboard_drift_magnitude=(leaderboard_entry.drift_magnitude if leaderboard_entry is not None else 0.0),
            )
        )

    artifact_entries.sort(
        key=lambda entry: (
            -max(entry.leaderboard_drift_magnitude, entry.latest_historical_drift_magnitude),
            entry.artifact_path,
        )
    )

    insights = _build_repo_insights(artifact_entries)
    control_surface_groups = _build_control_surface_groups(artifact_entries)
    history_timelines = _build_repo_history_timelines(db_path, repo_full, artifact_entries)

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
        control_surface_groups=control_surface_groups,
        history_timelines=history_timelines,
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
        "latest_pr_capability_shift": 0.0,
        "latest_pr_guardrail_shift": 0.0,
    }


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
            SELECT artifact_path, semantic_distance, attribute_deltas_json
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

        pr_profile_rows = conn.execute(
            """
            SELECT sap.artifact_path, sap.semantic_distance, sap.attribute_deltas_json
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
            metrics["latest_pr_capability_shift"] = attribute_deltas.get("capability_risk", 0.0)
            metrics["latest_pr_guardrail_shift"] = attribute_deltas.get("guardrail_robustness", 0.0)

    return metrics_by_path


def _normalized_id_prefix(repo_full: str) -> str:
    return f"{repo_full.lower()}::%"


def _drift_magnitude(semantic_distance: float, attribute_deltas: dict[str, float]) -> float:
    return round(
        abs(attribute_deltas.get("guardrail_robustness", 0.0))
        + abs(attribute_deltas.get("capability_risk", 0.0))
        + abs(attribute_deltas.get("autonomy_level", 0.0))
        + semantic_distance,
        4,
    )


def _build_repo_insights(artifacts: list[RepoDashboardArtifactEntry]) -> list[RepoDashboardInsightEntry]:
    ranked: list[tuple[float, RepoDashboardInsightEntry]] = []
    for artifact in artifacts:
        score = _insight_score(artifact)
        priority = "review_now" if score >= 1.25 else "watch" if score >= 0.6 else "baseline_review"
        title = _insight_title(artifact, priority)
        rationale = _insight_rationale(artifact, priority)
        recommended_action = _insight_action(artifact, priority)
        ranked.append(
            (
                score,
                RepoDashboardInsightEntry(
                    title=title,
                    artifact_path=artifact.artifact_path,
                    artifact_type=artifact.artifact_type,
                    priority=priority,
                    score=score,
                    rationale=rationale,
                    recommended_action=recommended_action,
                ),
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1].artifact_path))
    return [entry for _, entry in ranked[:8]]


def _insight_score(artifact: RepoDashboardArtifactEntry) -> float:
    type_weight = {
        "guardrail": 0.45,
        "system_prompt": 0.4,
        "prompt": 0.4,
        "tooling": 0.3,
        "model_config": 0.28,
        "retrieval": 0.24,
        "ai_code": 0.22,
        "policy": 0.35,
    }.get(artifact.artifact_type, 0.15)
    drift_signal = max(artifact.leaderboard_drift_magnitude, artifact.latest_historical_drift_magnitude)
    guardrail_regression = abs(min(artifact.latest_pr_guardrail_shift, 0.0))
    capability_expansion = max(artifact.latest_pr_capability_shift, 0.0)
    confidence_bonus = artifact.discovery_confidence * 0.2
    history_bonus = min(artifact.historical_version_count, 5) * 0.04
    return round(type_weight + drift_signal + guardrail_regression + capability_expansion + confidence_bonus + history_bonus, 4)


def _insight_title(artifact: RepoDashboardArtifactEntry, priority: str) -> str:
    if artifact.latest_pr_capability_shift > 0.05:
        return "Capability expansion needs review"
    if artifact.latest_pr_guardrail_shift < -0.05:
        return "Guardrail regression needs review"
    if artifact.latest_historical_drift_magnitude > 0.35:
        return "Historical drift hotspot"
    if priority == "baseline_review":
        return "High-value control surface to baseline"
    return "Potentially important control surface"


def _insight_rationale(artifact: RepoDashboardArtifactEntry, priority: str) -> str:
    if artifact.latest_pr_capability_shift > 0.05 and artifact.latest_pr_guardrail_shift < -0.05:
        return "Recent pull-request drift suggests broader authority while guardrails weakened."
    if artifact.latest_pr_capability_shift > 0.05:
        return "Recent pull-request history increased capability risk relative to baseline."
    if artifact.latest_pr_guardrail_shift < -0.05:
        return "Recent pull-request history weakened guardrail posture relative to baseline."
    if artifact.latest_historical_drift_magnitude > 0.35:
        return "Historical snapshots show meaningful design movement that deserves a human read."
    if priority == "baseline_review":
        return "This artifact looks like a real AI control surface but does not yet have meaningful drift context."
    return "This artifact is likely part of the AI control surface and should stay on the review radar."


def _insight_action(artifact: RepoDashboardArtifactEntry, priority: str) -> str:
    if artifact.latest_pr_capability_shift > 0.05:
        return "Inspect authority, tool access, and production-facing behavior before accepting future changes here."
    if artifact.latest_pr_guardrail_shift < -0.05:
        return "Review missing constraints, escalation paths, and refusal language in this artifact."
    if artifact.latest_historical_drift_magnitude > 0.35:
        return "Open the artifact history and compare the earliest and latest versions to understand the behavior shift."
    if priority == "baseline_review":
        return "Confirm whether this is a true AI control surface and keep it in the monitored baseline set."
    return "Track this artifact, but prioritize stronger capability or guardrail signals first."


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
            SELECT artifact_path, artifact_type, commit_sha, created_at, semantic_distance, attribute_deltas_json
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
            points_by_path[artifact_path].append(
                RepoArtifactTimelinePoint(
                    source="historical",
                    label=f"commit {str(row['commit_sha'])[:7]}",
                    created_at=float(row["created_at"]),
                    semantic_distance=semantic_distance,
                    capability_shift=attribute_deltas.get("capability_risk", 0.0),
                    guardrail_shift=attribute_deltas.get("guardrail_robustness", 0.0),
                    autonomy_shift=attribute_deltas.get("autonomy_level", 0.0),
                    drift_magnitude=_drift_magnitude(semantic_distance, attribute_deltas),
                )
            )

        pr_rows = conn.execute(
            """
            SELECT sap.artifact_path, sap.artifact_type, pra.pr_number, sap.created_at, sap.semantic_distance, sap.attribute_deltas_json
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
            points_by_path[artifact_path].append(
                RepoArtifactTimelinePoint(
                    source="pull_request",
                    label=f"PR #{row['pr_number']}",
                    created_at=float(row["created_at"]),
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


def _build_overview_attention_repos(repo_views: list[RepoDashboardView]) -> list[DashboardOverviewAttentionRepo]:
    priority_rank = {"review_now": 0, "watch": 1, "baseline_review": 2}
    attention_repos: list[DashboardOverviewAttentionRepo] = []
    for view in repo_views:
        sorted_insights = sorted(
            view.insights,
            key=lambda insight: (priority_rank.get(insight.priority, 9), -insight.score, insight.artifact_path),
        )
        top_insight = sorted_insights[0] if sorted_insights else None
        attention_repos.append(
            DashboardOverviewAttentionRepo(
                repo_full=view.repo_full,
                highest_priority=top_insight.priority if top_insight is not None else "baseline_review",
                highest_insight_title=top_insight.title if top_insight is not None else None,
                highest_insight_artifact_path=top_insight.artifact_path if top_insight is not None else None,
                insight_count=len(view.insights),
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
            priority_rank.get(repo.highest_priority, 9),
            -repo.review_now_count,
            -repo.top_drift_magnitude,
            repo.repo_full,
        )
    )
    return attention_repos


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
                    drift_magnitude=drift_magnitude,
                    capability_shift=artifact.latest_pr_capability_shift,
                    guardrail_shift=artifact.latest_pr_guardrail_shift,
                )
            )

    priority_rank = {"review_now": 0, "watch": 1, "baseline_review": 2}
    items.sort(
        key=lambda item: (
            priority_rank.get(item.priority, 9),
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
            group["weighted_risk"] = float(group["weighted_risk"]) + _insight_score(artifact)
            insight = insight_by_path.get(artifact.artifact_path)
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