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
DEFAULT_EVAL_CANDIDATE_SOURCE = "oss"
DEFAULT_EVAL_SCENARIO_SOURCE = "seeded"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class OssEvalCandidate:
    key: str
    repo_full: str
    recommended_mode: str = "baseline_plus_backfill"
    commit_limit_per_artifact: int = 10
    notes: str | None = None
    expected_control_surfaces: list[str] = field(default_factory=list)
    candidate_source: str = DEFAULT_EVAL_CANDIDATE_SOURCE


@dataclass(frozen=True)
class EvalScenario:
    key: str
    repo_full: str
    notes: str | None = None
    expected_control_surfaces: list[str] = field(default_factory=list)
    expected_high_confidence_baseline_coverage_min: float | None = None
    expected_top_review_target_present: bool | None = None
    expected_top_review_target_path: str | None = None
    expected_lower_confidence_queue_max: int | None = None
    reference_package_path: str | None = None
    scenario_source: str = DEFAULT_EVAL_SCENARIO_SOURCE


@dataclass(frozen=True)
class OssEvalRunResult:
    package: dict[str, Any]
    package_path: str
    repo_dashboard_path: str
    overview_dashboard_path: str
    comparison_path: str | None = None


EvalCandidate = OssEvalCandidate
EvalRunResult = OssEvalRunResult


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


DEFAULT_EVAL_SCENARIOS = (
    EvalScenario(
        key="dummyai-review-target",
        repo_full="doria90/dummyAI",
        notes="Seeded reviewer-target scenario for checking repeatable queue/actionability output.",
        expected_control_surfaces=["prompts", "model configuration"],
        expected_high_confidence_baseline_coverage_min=0.5,
        expected_top_review_target_present=True,
        expected_top_review_target_path="prompts/refund.txt",
        expected_lower_confidence_queue_max=2,
        reference_package_path="fixtures/eval-harness/dummyai-review-target-baseline.json",
    ),
    EvalScenario(
        key="dummyai-strict-lower-confidence",
        repo_full="doria90/dummyAI",
        notes="Strict seeded scenario that intentionally fails when lower-confidence queue noise appears.",
        expected_control_surfaces=["prompts", "model configuration"],
        expected_high_confidence_baseline_coverage_min=0.5,
        expected_top_review_target_present=True,
        expected_top_review_target_path="prompts/refund.txt",
        expected_lower_confidence_queue_max=0,
        reference_package_path="fixtures/eval-harness/dummyai-strict-lower-confidence-baseline.json",
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


def list_eval_candidates() -> list[EvalCandidate]:
    return list_oss_eval_candidates()


def list_eval_scenarios() -> list[EvalScenario]:
    return list(DEFAULT_EVAL_SCENARIOS)


def resolve_eval_target(target: str) -> EvalCandidate:
    normalized = target.strip().lower()
    for scenario in DEFAULT_EVAL_SCENARIOS:
        if normalized == scenario.key.lower():
            return OssEvalCandidate(
                key=scenario.key,
                repo_full=scenario.repo_full,
                notes=scenario.notes,
                expected_control_surfaces=list(scenario.expected_control_surfaces),
                candidate_source=scenario.scenario_source,
            )
    return resolve_oss_eval_target(target)


def resolve_eval_scenario(target: str) -> EvalScenario | None:
    normalized = target.strip().lower()
    for scenario in DEFAULT_EVAL_SCENARIOS:
        if normalized == scenario.key.lower():
            return scenario
    return None


def resolve_eval_reference_package_path(scenario_key: str) -> str | None:
    scenario = resolve_eval_scenario(scenario_key)
    if scenario is None or not scenario.reference_package_path:
        return None
    reference_path = PROJECT_ROOT / scenario.reference_package_path
    if not reference_path.exists():
        return None
    return str(reference_path)


def load_eval_reference_package(scenario_key: str) -> dict[str, Any] | None:
    reference_path = resolve_eval_reference_package_path(scenario_key)
    if reference_path is None:
        return None
    return load_eval_package(reference_path)


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


def _assertion_result(*, assertion_id: str, passed: bool, expected: Any, actual: Any, message: str) -> dict[str, Any]:
    return {
        "assertion_id": assertion_id,
        "passed": passed,
        "expected": expected,
        "actual": actual,
        "message": message,
    }


def _build_eval_assertions(
    *,
    scenario: EvalScenario | None,
    baseline_coverage_summary: dict[str, Any],
    repo_dashboard_payload: dict[str, Any],
    top_review_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if scenario is None:
        return []

    assertions: list[dict[str, Any]] = []
    coverage = baseline_coverage_summary.get("high_confidence_baseline_coverage")
    if scenario.expected_high_confidence_baseline_coverage_min is not None:
        expected = scenario.expected_high_confidence_baseline_coverage_min
        actual = float(coverage) if coverage is not None else None
        passed = actual is not None and actual >= expected
        assertions.append(
            _assertion_result(
                assertion_id="high_confidence_baseline_coverage_min",
                passed=passed,
                expected={"minimum": expected},
                actual=actual,
                message=(
                    f"High-confidence baseline coverage should be at least {expected:.0%}."
                    if passed
                    else f"High-confidence baseline coverage fell below the expected minimum of {expected:.0%}."
                ),
            )
        )

    if scenario.expected_top_review_target_present is not None:
        actual = bool(top_review_targets)
        expected = scenario.expected_top_review_target_present
        passed = actual == expected
        assertions.append(
            _assertion_result(
                assertion_id="top_review_target_present",
                passed=passed,
                expected=expected,
                actual=actual,
                message=(
                    "Top review target presence matched expectation."
                    if passed
                    else "Top review target presence did not match expectation."
                ),
            )
        )

    if scenario.expected_top_review_target_path is not None:
        actual = top_review_targets[0].get("artifact_path") if top_review_targets else None
        expected = scenario.expected_top_review_target_path
        passed = actual == expected
        assertions.append(
            _assertion_result(
                assertion_id="top_review_target_path",
                passed=passed,
                expected=expected,
                actual=actual,
                message=(
                    f"Top review target matched expected artifact path {expected}."
                    if passed
                    else f"Top review target did not match expected artifact path {expected}."
                ),
            )
        )

    if scenario.expected_lower_confidence_queue_max is not None:
        actual = len((repo_dashboard_payload.get("lower_confidence_insights") or []))
        expected = scenario.expected_lower_confidence_queue_max
        passed = actual <= expected
        assertions.append(
            _assertion_result(
                assertion_id="lower_confidence_queue_max",
                passed=passed,
                expected={"maximum": expected},
                actual=actual,
                message=(
                    f"Lower-confidence queue stayed within the expected max of {expected}."
                    if passed
                    else f"Lower-confidence queue exceeded the expected max of {expected}."
                ),
            )
        )

    return assertions


def _build_assertion_summary(assertions: list[dict[str, Any]]) -> dict[str, Any]:
    passed_count = sum(1 for item in assertions if item.get("passed"))
    failed_count = sum(1 for item in assertions if not item.get("passed"))
    return {
        "total_count": len(assertions),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "all_passed": failed_count == 0,
    }


def _write_json_file(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def load_oss_eval_package(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_eval_package(path: str) -> dict[str, Any]:
    return load_oss_eval_package(path)


def compare_oss_eval_packages(current_package: dict[str, Any], baseline_package: dict[str, Any]) -> dict[str, Any]:
    current_coverage = current_package.get("baseline_coverage_summary", {}).get("high_confidence_baseline_coverage")
    baseline_coverage = baseline_package.get("baseline_coverage_summary", {}).get("high_confidence_baseline_coverage")
    current_high_confidence = current_package.get("baseline_coverage_summary", {}).get("high_confidence_artifact_count", 0)
    baseline_high_confidence = baseline_package.get("baseline_coverage_summary", {}).get("high_confidence_artifact_count", 0)
    current_lower_confidence = len((current_package.get("repo_dashboard_snapshot") or {}).get("lower_confidence_insights") or [])
    baseline_lower_confidence = len((baseline_package.get("repo_dashboard_snapshot") or {}).get("lower_confidence_insights") or [])
    current_targets = current_package.get("top_artifacts_requiring_review") or []
    baseline_targets = baseline_package.get("top_artifacts_requiring_review") or []
    current_assertions = current_package.get("assertions") or []
    baseline_assertions = baseline_package.get("assertions") or []

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

    current_failed_assertions = sum(1 for item in current_assertions if not item.get("passed"))
    baseline_failed_assertions = sum(1 for item in baseline_assertions if not item.get("passed"))
    if current_assertions or baseline_assertions:
        if current_failed_assertions < baseline_failed_assertions:
            improvements.append(
                f"Explicit assertion failures improved from {baseline_failed_assertions} to {current_failed_assertions}."
            )
        elif current_failed_assertions > baseline_failed_assertions:
            regressions.append(
                f"Explicit assertion failures regressed from {baseline_failed_assertions} to {current_failed_assertions}."
            )
        else:
            unchanged.append(f"Explicit assertion failures held at {current_failed_assertions}.")

    return {
        "repo_full": current_package.get("repo_full"),
        "current_run_id": current_package.get("run_id"),
        "baseline_run_id": baseline_package.get("run_id"),
        "current_branch": current_package.get("branch_name"),
        "baseline_branch": baseline_package.get("branch_name"),
        "scenario_key": current_package.get("scenario_key") or baseline_package.get("scenario_key"),
        "improvements": improvements,
        "regressions": regressions,
        "unchanged": unchanged,
        "current_assertion_summary": _build_assertion_summary(current_assertions),
        "baseline_assertion_summary": _build_assertion_summary(baseline_assertions),
    }


def compare_eval_packages(current_package: dict[str, Any], baseline_package: dict[str, Any]) -> dict[str, Any]:
    return compare_oss_eval_packages(current_package, baseline_package)


def compare_oss_eval_package_files(current_package_path: str, baseline_package_path: str) -> dict[str, Any]:
    return compare_oss_eval_packages(
        load_oss_eval_package(current_package_path),
        load_oss_eval_package(baseline_package_path),
    )


def compare_eval_package_files(current_package_path: str, baseline_package_path: str) -> dict[str, Any]:
    return compare_oss_eval_package_files(current_package_path, baseline_package_path)


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
    scenario_key: str | None = None,
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
    scenario = resolve_eval_scenario(scenario_key) if scenario_key else None
    candidate_source = scenario.scenario_source if scenario is not None else DEFAULT_EVAL_CANDIDATE_SOURCE
    effective_compare_to_package_path = compare_to_package_path
    if effective_compare_to_package_path is None and scenario is not None:
        effective_compare_to_package_path = resolve_eval_reference_package_path(scenario.key)

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
    assertions = _build_eval_assertions(
        scenario=scenario,
        baseline_coverage_summary=baseline_coverage_summary,
        repo_dashboard_payload=repo_dashboard_payload,
        top_review_targets=top_review_targets,
    )
    assertion_summary = _build_assertion_summary(assertions)

    package = {
        "run_id": effective_run_label,
        "package_type": "evaluation_run",
        "repo_full": repo_full,
        "installation_id": installation_id,
        "candidate_key": candidate_key,
        "candidate_source": candidate_source,
        "scenario_key": scenario.key if scenario else scenario_key,
        "branch_name": branch_label,
        "mode": mode,
        "generated_at": now,
        "comparison_baseline_package_path": effective_compare_to_package_path,
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
        "assertions": assertions,
        "assertion_summary": assertion_summary,
    }

    run_directory = Path(output_root) / _slug(branch_label) / _slug(repo_full) / effective_run_label
    package_path = _write_json_file(run_directory / "run-package.json", package)
    repo_dashboard_path = _write_json_file(run_directory / "repo-dashboard.json", repo_dashboard_payload)
    overview_dashboard_path = _write_json_file(run_directory / "overview-dashboard.json", overview_dashboard_payload)

    comparison_path: str | None = None
    if effective_compare_to_package_path:
        comparison = compare_oss_eval_package_files(package_path, effective_compare_to_package_path)
        comparison_path = _write_json_file(run_directory / "comparison-summary.json", comparison)

    return OssEvalRunResult(
        package=package,
        package_path=package_path,
        repo_dashboard_path=repo_dashboard_path,
        overview_dashboard_path=overview_dashboard_path,
        comparison_path=comparison_path,
    )


def run_evaluation(
    db_path: str,
    **kwargs: Any,
) -> EvalRunResult:
    return run_oss_evaluation(db_path, **kwargs)