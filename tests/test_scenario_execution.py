import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.scenario_evaluation import ScenarioEvalPlan
from services.scenario_execution import execute_scenario_eval_plan


def test_execute_scenario_eval_plan_returns_plan_reason_when_disabled():
    plan = ScenarioEvalPlan(
        rollout_mode="off",
        should_run=False,
        reason="Scenario eval rollout is disabled for this worker.",
        artifact_paths=(),
    )

    summary = execute_scenario_eval_plan(
        plan,
        db_path="jobs.db",
        workspace_id=None,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        output_root="artifacts/eval-runs",
        branch_name="feature/test",
        run_label="audit-job-1",
        verifier_rollout_mode="off",
        verifier_max_requests_per_review=3,
    )

    assert summary.executed is False
    assert summary.execution_count == 0
    assert summary.reason == "Scenario eval rollout is disabled for this worker."


def test_execute_scenario_eval_plan_skips_when_repo_has_no_seeded_scenario():
    plan = ScenarioEvalPlan(
        rollout_mode="shadow",
        should_run=True,
        reason="Shadow-mode scenario eval would review 1 artifact on this PR.",
        artifact_paths=("prompts/policy.md",),
    )

    summary = execute_scenario_eval_plan(
        plan,
        db_path="jobs.db",
        workspace_id=None,
        repo_full="example/unknown-repo",
        installation_id=123,
        token="token",
        output_root="artifacts/eval-runs",
        branch_name="feature/test",
        run_label="audit-job-2",
        verifier_rollout_mode="shadow",
        verifier_max_requests_per_review=3,
    )

    assert summary.executed is False
    assert summary.execution_count == 0
    assert "No seeded scenario" in summary.reason


def test_execute_scenario_eval_plan_runs_seeded_scenario_for_repo():
    plan = ScenarioEvalPlan(
        rollout_mode="shadow",
        should_run=True,
        reason="Shadow-mode scenario eval would review 1 artifact on this PR.",
        artifact_paths=("prompts/policy.md",),
    )
    captured = {}

    def fake_run_evaluation(db_path: str, **kwargs):
        captured["db_path"] = db_path
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            package={
                "assertion_summary": {"all_passed": True, "failed_count": 0},
                "candidate_source": "seeded",
            },
            package_path="artifacts/eval-runs/feature-test/doria90-dummyai/audit-job-3/run-package.json",
            comparison_path="artifacts/eval-runs/feature-test/doria90-dummyai/audit-job-3/comparison-summary.json",
        )

    summary = execute_scenario_eval_plan(
        plan,
        db_path="jobs.db",
        workspace_id=42,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        output_root="artifacts/eval-runs",
        branch_name="feature/test",
        run_label="audit-job-3",
        verifier_rollout_mode="shadow",
        verifier_max_requests_per_review=3,
        run_evaluation_fn=fake_run_evaluation,
    )

    assert summary.executed is True
    assert summary.execution_count == 1
    assert summary.executions[0].scenario_key == "dummyai-review-target"
    assert summary.executions[0].artifact_paths == ("prompts/policy.md",)
    assert summary.executions[0].assertion_summary["all_passed"] is True
    assert captured["db_path"] == "jobs.db"
    assert captured["kwargs"]["workspace_id"] == 42
    assert captured["kwargs"]["scenario_key"] == "dummyai-review-target"
    assert captured["kwargs"]["run_label"] == "audit-job-3"