from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .analysis_budget import consume_analysis_budget, estimate_feature_units, release_analysis_budget, reserve_analysis_budget
from .oss_eval_harness import list_eval_scenarios, run_evaluation
from .scenario_evaluation import ScenarioEvalPlan


@dataclass(frozen=True)
class ScenarioEvalExecution:
    scenario_key: str
    artifact_paths: tuple[str, ...]
    package_path: str
    comparison_path: str | None
    assertion_summary: dict[str, Any]
    candidate_source: str | None


@dataclass(frozen=True)
class ScenarioEvalExecutionSummary:
    rollout_mode: str
    attempted: bool
    executed: bool
    reason: str
    executions: tuple[ScenarioEvalExecution, ...]

    @property
    def execution_count(self) -> int:
        return len(self.executions)


def _select_seeded_scenario_key_for_repo(
    repo_full: str,
    *,
    list_eval_scenarios_fn=list_eval_scenarios,
) -> str | None:
    normalized_repo = repo_full.strip().lower()
    for scenario in list_eval_scenarios_fn():
        if scenario.repo_full.strip().lower() == normalized_repo:
            return scenario.key
    return None


def execute_scenario_eval_plan(
    plan: ScenarioEvalPlan,
    *,
    db_path: str,
    workspace_id: int | None,
    audit_job_id: int | None = None,
    audit_job_attempt_count: int | None = None,
    repo_full: str,
    installation_id: int,
    token: str,
    output_root: str,
    branch_name: str,
    run_label: str,
    verifier_rollout_mode: str,
    verifier_max_requests_per_review: int,
    run_evaluation_fn=run_evaluation,
    list_eval_scenarios_fn=list_eval_scenarios,
) -> ScenarioEvalExecutionSummary:
    if not plan.should_run:
        return ScenarioEvalExecutionSummary(
            rollout_mode=plan.rollout_mode,
            attempted=False,
            executed=False,
            reason=plan.reason,
            executions=(),
        )

    scenario_key = _select_seeded_scenario_key_for_repo(
        repo_full,
        list_eval_scenarios_fn=list_eval_scenarios_fn,
    )
    if scenario_key is None:
        return ScenarioEvalExecutionSummary(
            rollout_mode=plan.rollout_mode,
            attempted=False,
            executed=False,
            reason="No seeded scenario is registered for this repository.",
            executions=(),
        )

    reservation_key = None
    if workspace_id is not None and audit_job_id is not None:
        attempt_count = max(1, int(audit_job_attempt_count or 1))
        budget = reserve_analysis_budget(
            db_path,
            workspace_id=workspace_id,
            feature_key="scenario",
            reservation_key=f"audit-job:{audit_job_id}:attempt:{attempt_count}:scenario-eval",
            estimated_units=estimate_feature_units("scenario", request_count=max(1, len(plan.artifact_paths))),
            audit_job_id=audit_job_id,
        )
        if not budget.allowed:
            return ScenarioEvalExecutionSummary(
                rollout_mode=plan.rollout_mode,
                attempted=False,
                executed=False,
                reason=f"Scenario eval execution skipped because {budget.reason}.",
                executions=(),
            )
        reservation_key = budget.reservation_key

    try:
        result = run_evaluation_fn(
            db_path,
            workspace_id=workspace_id,
            repo_full=repo_full,
            installation_id=installation_id,
            token=token,
            mode="baseline_plus_backfill",
            commit_limit_per_artifact=5,
            output_root=output_root,
            branch_name=branch_name,
            candidate_key=scenario_key,
            expected_control_surfaces=[],
            manual_notes=(
                f"Shadow-mode scenario eval triggered from audit job context for selected artifacts: "
                f"{', '.join(plan.artifact_paths)}"
            ),
            run_label=run_label,
            scenario_key=scenario_key,
            verifier_rollout_mode=verifier_rollout_mode,
            verifier_max_requests_per_review=verifier_max_requests_per_review,
        )
    except Exception:
        release_analysis_budget(
            db_path,
            reservation_key=reservation_key,
            note="scenario eval failed before completion",
        )
        raise

    execution = ScenarioEvalExecution(
        scenario_key=scenario_key,
        artifact_paths=plan.artifact_paths,
        package_path=result.package_path,
        comparison_path=result.comparison_path,
        assertion_summary=dict(result.package.get("assertion_summary") or {}),
        candidate_source=result.package.get("candidate_source"),
    )
    consume_analysis_budget(
        db_path,
        reservation_key=reservation_key,
        consumed_units=estimate_feature_units("scenario", request_count=max(1, len(plan.artifact_paths))),
        note="scenario eval completed",
    )
    return ScenarioEvalExecutionSummary(
        rollout_mode=plan.rollout_mode,
        attempted=True,
        executed=True,
        reason=f"Shadow-mode scenario eval executed seeded scenario '{scenario_key}'.",
        executions=(execution,),
    )