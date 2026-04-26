from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, is_dataclass, asdict
from pathlib import Path
from typing import Any

from services.dashboard_views import build_dashboard_overview_view, build_repo_dashboard_view
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill


HIGH_CONFIDENCE_THRESHOLD = 0.85
DEFAULT_TOP_REVIEW_TARGET_LIMIT = 5


@dataclass(frozen=True)
class OssEvalCandidate:
    key: str
    repo_full: str
    recommended_mode: str = "baseline_plus_backfill"
    commit_limit_per_artifact: int = 10
    notes: str | None = None
    expected_control_surfaces: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OssEvalRunResult:
    package: dict[str, Any]
    package_path: str
    repo_dashboard_path: str
    overview_dashboard_path: str
    comparison_path: str | None = None


DEFAULT_OSS_EVAL_CANDIDATES = (
    OssEvalCandidate(
        key="openfang",
        repo_full="doria90/openfang",
        recommended_mode="baseline_plus_backfill",
        commit_limit_per_artifact=12,
        notes="Previously validated real-repository onboarding candidate with prompt-heavy assets.",
        expected_control_surfaces=["prompts", "guardrails", "model configuration"],
    ),
    OssEvalCandidate(
        key="hermes-agent",
        repo_full="doria90/hermes-agent",
        recommended_mode="baseline_plus_backfill",
        commit_limit_per_artifact=12,
        notes="Previously validated larger OSS candidate with noisier structure and agent wiring.",
        expected_control_surfaces=["prompts", "agent wiring", "tool definitions", "model configuration"],
    ),
)


def list_oss_eval_candidates() -> list[OssEvalCandidate]:
    return list(DEFAULT_OSS_EVAL_CANDIDATES)


def resolve_oss_eval_target(target: str) -> OssEvalCandidate:
    normalized = target.strip().lower()
    for candidate in DEFAULT_OSS_EVAL_CANDIDATES:
        if normalized in {candidate.key.lower(), candidate.repo_full.lower()}:
            return candidate
    return OssEvalCandidate(key=target.replace("/", "-"), repo_full=target)


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def _read_field(value: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field_name, default)
    return getattr(value, field_name, default)


def _slug(value: str) -> str:
    slug = []
    for char in value.lower():
        slug.append(char if char.isalnum() else "-")
    collapsed = "".join(slug)
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    return collapsed.strip("-") or "unknown"


def _timestamp_label(now: float) -> str:
    utc = time.gmtime(now)
    return time.strftime("%Y%m%d-%H%M%S", utc)


def _build_evaluator_rubric(*, expected_control_surfaces: list[str], manual_notes: str | None) -> list[dict[str, Any]]:
    expected_text = ", ".join(expected_control_surfaces) if expected_control_surfaces else "No explicit expectations recorded"
    return [
        {
            "dimension": "discovery",
            "status": "pending_human_review",
            "question": "Did DriftGuard discover the major AI control surfaces without letting false positives dominate?",
            "notes": expected_text,
        },
        {
            "dimension": "baseline",
            "status": "pending_human_review",
            "question": "Did the most authoritative discovered artifacts receive usable baselines?",
            "notes": None,
        },
        {
            "dimension": "history",
            "status": "pending_human_review",
            "question": "Did optional backfill produce coherent, bounded lineage on the important artifacts?",
            "notes": None,
        },
        {
            "dimension": "reviewer_output",
            "status": "pending_human_review",
            "question": "Do the top-ranked review targets feel plausible and actionable to a human reviewer?",
            "notes": None,
        },
        {
            "dimension": "dashboard",
            "status": "pending_human_review",
            "question": "Do the overview and repo case-file outputs help decide what to inspect next?",
            "notes": manual_notes,
        },
    ]


def _build_baseline_coverage_summary(discovered_artifacts: list[dict[str, Any]], baseline_versions: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_paths = {item.get("artifact_path") for item in baseline_versions}
    high_confidence_artifacts = [
        artifact for artifact in discovered_artifacts if float(artifact.get("confidence") or 0.0) >= HIGH_CONFIDENCE_THRESHOLD
    ]
    high_confidence_with_baseline = [
        artifact for artifact in high_confidence_artifacts if artifact.get("artifact_path") in baseline_paths
    ]
    coverage = None
    if high_confidence_artifacts:
        coverage = round(len(high_confidence_with_baseline) / len(high_confidence_artifacts), 4)
    return {
        "discovered_artifact_count": len(discovered_artifacts),
        "baseline_version_count": len(baseline_versions),
        "high_confidence_artifact_count": len(high_confidence_artifacts),
        "high_confidence_baseline_count": len(high_confidence_with_baseline),
        "high_confidence_baseline_coverage": coverage,
    }


def _build_backfill_execution_summary(
    planned_jobs: list[Any],
    execution_results: list[Any],
    repo_dashboard_payload: dict[str, Any],
) -> dict[str, Any]:
    completed = 0
    failed = 0
    for result in execution_results:
        job = _read_field(result, "job")
        status = _read_field(job, "status")
        if status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1

    backfill_payload = repo_dashboard_payload.get("backfill") or {}
    return {
        "planned_job_count": len(planned_jobs),
        "executed_job_count": len(execution_results),
        "completed_job_count": completed,
        "failed_job_count": failed,
        "dashboard_completed_job_count": backfill_payload.get("completed_job_count", 0),
        "dashboard_total_historical_versions": backfill_payload.get("total_historical_versions", 0),
        "dashboard_total_historical_profiles": backfill_payload.get("total_historical_profiles", 0),
    }


def _build_top_review_targets(repo_dashboard_payload: dict[str, Any], limit: int = DEFAULT_TOP_REVIEW_TARGET_LIMIT) -> list[dict[str, Any]]:
    insights = repo_dashboard_payload.get("insights") or []
    top_targets: list[dict[str, Any]] = []
    for insight in insights[:limit]:
        top_targets.append(
            {
                "title": insight.get("title"),
                "artifact_path": insight.get("artifact_path"),
                "artifact_type": insight.get("artifact_type"),
                "priority": insight.get("priority"),
                "recommended_action": insight.get("recommended_action"),
                "review_target": insight.get("review_target"),
                "review_url": insight.get("review_url"),
                "evidence_summary": insight.get("evidence_summary"),
                "confidence_label": insight.get("confidence_label"),
                "queue_lane": insight.get("queue_lane"),
            }
        )
    return top_targets


def _write_json_file(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def load_oss_eval_package(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_oss_eval_packages(current_package: dict[str, Any], baseline_package: dict[str, Any]) -> dict[str, Any]:
    current_coverage = current_package.get("baseline_coverage_summary", {}).get("high_confidence_baseline_coverage")
    baseline_coverage = baseline_package.get("baseline_coverage_summary", {}).get("high_confidence_baseline_coverage")
    current_high_confidence = current_package.get("baseline_coverage_summary", {}).get("high_confidence_artifact_count", 0)
    baseline_high_confidence = baseline_package.get("baseline_coverage_summary", {}).get("high_confidence_artifact_count", 0)
    current_lower_confidence = len((current_package.get("repo_dashboard_snapshot") or {}).get("lower_confidence_insights") or [])
    baseline_lower_confidence = len((baseline_package.get("repo_dashboard_snapshot") or {}).get("lower_confidence_insights") or [])
    current_targets = current_package.get("top_artifacts_requiring_review") or []
    baseline_targets = baseline_package.get("top_artifacts_requiring_review") or []

    improvements: list[str] = []
    regressions: list[str] = []
    unchanged: list[str] = []

    if current_coverage is not None and baseline_coverage is not None:
        if current_coverage > baseline_coverage:
            improvements.append(
                f"High-confidence baseline coverage improved from {baseline_coverage:.0%} to {current_coverage:.0%}."
            )
        elif current_coverage < baseline_coverage:
            regressions.append(
                f"High-confidence baseline coverage regressed from {baseline_coverage:.0%} to {current_coverage:.0%}."
            )
        else:
            unchanged.append(f"High-confidence baseline coverage held at {current_coverage:.0%}.")

    if current_high_confidence > baseline_high_confidence:
        improvements.append(
            f"High-confidence discovered artifacts increased from {baseline_high_confidence} to {current_high_confidence}."
        )
    elif current_high_confidence < baseline_high_confidence:
        regressions.append(
            f"High-confidence discovered artifacts fell from {baseline_high_confidence} to {current_high_confidence}."
        )
    else:
        unchanged.append(f"High-confidence discovered artifacts stayed at {current_high_confidence}.")

    if current_lower_confidence < baseline_lower_confidence:
        improvements.append(
            f"Lower-confidence queue size improved from {baseline_lower_confidence} to {current_lower_confidence}."
        )
    elif current_lower_confidence > baseline_lower_confidence:
        regressions.append(
            f"Lower-confidence queue size regressed from {baseline_lower_confidence} to {current_lower_confidence}."
        )
    else:
        unchanged.append(f"Lower-confidence queue size held at {current_lower_confidence}.")

    if current_targets and not baseline_targets:
        improvements.append("Current run produced explicit top review targets where the baseline run had none.")
    elif not current_targets and baseline_targets:
        regressions.append("Current run lost explicit top review targets that were present in the baseline run.")
    else:
        current_top = current_targets[0].get("artifact_path") if current_targets else None
        baseline_top = baseline_targets[0].get("artifact_path") if baseline_targets else None
        if current_top == baseline_top:
            unchanged.append(f"Top review target remained {current_top or 'unset'}.")
        else:
            improvements.append(
                f"Top review target changed from {baseline_top or 'unset'} to {current_top or 'unset'} for manual reviewer inspection."
            )

    return {
        "repo_full": current_package.get("repo_full"),
        "current_run_id": current_package.get("run_id"),
        "baseline_run_id": baseline_package.get("run_id"),
        "current_branch": current_package.get("branch_name"),
        "baseline_branch": baseline_package.get("branch_name"),
        "improvements": improvements,
        "regressions": regressions,
        "unchanged": unchanged,
    }


def compare_oss_eval_package_files(current_package_path: str, baseline_package_path: str) -> dict[str, Any]:
    return compare_oss_eval_packages(
        load_oss_eval_package(current_package_path),
        load_oss_eval_package(baseline_package_path),
    )


def run_oss_evaluation(
    db_path: str,
    *,
    repo_full: str,
    installation_id: int,
    token: str,
    mode: str = "baseline_plus_backfill",
    commit_limit_per_artifact: int = 10,
    output_root: str,
    branch_name: str | None = None,
    candidate_key: str | None = None,
    expected_control_surfaces: list[str] | None = None,
    manual_notes: str | None = None,
    run_label: str | None = None,
    compare_to_package_path: str | None = None,
    onboard_repository_fn=onboard_repository,
    plan_repository_history_backfill_fn=plan_repository_history_backfill,
    execute_repository_history_backfill_fn=execute_repository_history_backfill,
    build_repo_dashboard_view_fn=build_repo_dashboard_view,
    build_dashboard_overview_view_fn=build_dashboard_overview_view,
) -> OssEvalRunResult:
    now = time.time()
    effective_run_label = run_label or _timestamp_label(now)
    branch_label = branch_name or "unknown"
    expected = list(expected_control_surfaces or [])

    onboarding_result = onboard_repository_fn(
        db_path,
        repo_full=repo_full,
        installation_id=installation_id,
        token=token,
    )

    planned_jobs: list[Any] = []
    execution_results: list[Any] = []
    if mode == "baseline_plus_backfill":
        planned_jobs = plan_repository_history_backfill_fn(
            db_path,
            repo_full=repo_full,
            token=token,
            commit_limit_per_artifact=commit_limit_per_artifact,
        )
        execution_results = execute_repository_history_backfill_fn(
            db_path,
            repo_full=repo_full,
            token=token,
        )

    onboarding_record = _serialize(_read_field(onboarding_result, "onboarding"))
    discovered_artifacts = _serialize(_read_field(onboarding_result, "artifacts", []))
    baseline_versions = _serialize(_read_field(onboarding_result, "baseline_versions", []))
    repo_dashboard_payload = _serialize(build_repo_dashboard_view_fn(db_path, repo_full))
    overview_dashboard_payload = _serialize(build_dashboard_overview_view_fn(db_path))

    baseline_coverage_summary = _build_baseline_coverage_summary(discovered_artifacts, baseline_versions)
    backfill_execution_summary = _build_backfill_execution_summary(planned_jobs, execution_results, repo_dashboard_payload)
    top_review_targets = _build_top_review_targets(repo_dashboard_payload)
    evaluator_rubric = _build_evaluator_rubric(expected_control_surfaces=expected, manual_notes=manual_notes)

    package = {
        "run_id": effective_run_label,
        "repo_full": repo_full,
        "installation_id": installation_id,
        "candidate_key": candidate_key,
        "branch_name": branch_label,
        "mode": mode,
        "generated_at": now,
        "expected_control_surfaces": expected,
        "manual_notes": manual_notes,
        "onboarding_summary": {
            "record": onboarding_record,
            "discovered_artifact_count": len(discovered_artifacts),
            "baseline_version_count": len(baseline_versions),
        },
        "discovered_artifacts": discovered_artifacts,
        "baseline_versions": baseline_versions,
        "baseline_coverage_summary": baseline_coverage_summary,
        "backfill_execution_summary": backfill_execution_summary,
        "top_artifacts_requiring_review": top_review_targets,
        "repo_dashboard_snapshot": repo_dashboard_payload,
        "overview_dashboard_snapshot": overview_dashboard_payload,
        "evaluator_rubric": evaluator_rubric,
    }

    run_directory = Path(output_root) / _slug(branch_label) / _slug(repo_full) / effective_run_label
    package_path = _write_json_file(run_directory / "run-package.json", package)
    repo_dashboard_path = _write_json_file(run_directory / "repo-dashboard.json", repo_dashboard_payload)
    overview_dashboard_path = _write_json_file(run_directory / "overview-dashboard.json", overview_dashboard_payload)

    comparison_path: str | None = None
    if compare_to_package_path:
        comparison = compare_oss_eval_package_files(package_path, compare_to_package_path)
        comparison_path = _write_json_file(run_directory / "comparison-summary.json", comparison)

    return OssEvalRunResult(
        package=package,
        package_path=package_path,
        repo_dashboard_path=repo_dashboard_path,
        overview_dashboard_path=overview_dashboard_path,
        comparison_path=comparison_path,
    )