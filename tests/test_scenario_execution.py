import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.analysis_budget import list_analysis_budget_events
from services.audit_jobs import init_db
from services.control_plane_records import create_user, create_workspace, upsert_entitlement
from services.entitlements import derive_entitlement_payload
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


def test_execute_scenario_eval_plan_skips_when_budget_is_exhausted(tmp_path):
    db_path = str(tmp_path / "scenario-budget.db")
    init_db(db_path)
    owner = create_user(db_path, display_name="Budget Owner", primary_email="budget@example.com")
    workspace = create_workspace(db_path, slug="scenario-budget", display_name="Scenario Budget", billing_owner_user_id=owner.id)
    payload = derive_entitlement_payload("team", "active")
    payload["feature_flags_json"] = '{"advanced_analysis_units_limit": 0, "advanced_analysis_window_seconds": 86400}'
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=payload)
    plan = ScenarioEvalPlan(
        rollout_mode="shadow",
        should_run=True,
        reason="Shadow-mode scenario eval would review 1 artifact on this PR.",
        artifact_paths=("prompts/policy.md",),
    )

    summary = execute_scenario_eval_plan(
        plan,
        db_path=db_path,
        workspace_id=workspace.id,
        audit_job_id=7,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        output_root="artifacts/eval-runs",
        branch_name="feature/test",
        run_label="audit-job-7",
        verifier_rollout_mode="shadow",
        verifier_max_requests_per_review=3,
    )

    assert summary.executed is False
    assert summary.attempted is False
    assert "budget exhausted" in summary.reason.lower()
    assert list_analysis_budget_events(db_path, workspace_id=workspace.id) == []


def test_execute_scenario_eval_plan_records_budget_event_on_success(tmp_path):
    db_path = str(tmp_path / "scenario-budget-success.db")
    init_db(db_path)
    owner = create_user(db_path, display_name="Budget Owner", primary_email="budget@example.com")
    workspace = create_workspace(db_path, slug="scenario-budget-success", display_name="Scenario Budget", billing_owner_user_id=owner.id)
    payload = derive_entitlement_payload("team", "active")
    payload["feature_flags_json"] = '{"advanced_analysis_units_limit": 10, "advanced_analysis_window_seconds": 86400}'
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=payload)

    plan = ScenarioEvalPlan(
        rollout_mode="shadow",
        should_run=True,
        reason="Shadow-mode scenario eval would review 1 artifact on this PR.",
        artifact_paths=("prompts/policy.md",),
    )

    def fake_run_evaluation(db_path: str, **kwargs):
        return SimpleNamespace(
            package={"assertion_summary": {"all_passed": True}, "candidate_source": "seeded"},
            package_path="artifacts/eval-runs/run-package.json",
            comparison_path=None,
        )

    summary = execute_scenario_eval_plan(
        plan,
        db_path=db_path,
        workspace_id=workspace.id,
        audit_job_id=9,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        output_root="artifacts/eval-runs",
        branch_name="feature/test",
        run_label="audit-job-9",
        verifier_rollout_mode="shadow",
        verifier_max_requests_per_review=3,
        run_evaluation_fn=fake_run_evaluation,
    )

    assert summary.executed is True
    budget_events = list_analysis_budget_events(db_path, workspace_id=workspace.id)
    assert len(budget_events) == 1
    assert budget_events[0]["feature_key"] == "scenario"
    assert budget_events[0]["status"] == "consumed"
    assert budget_events[0]["units_consumed"] == 5