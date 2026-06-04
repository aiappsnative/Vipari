import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.analysis_budget import list_analysis_budget_events
from services.audit_jobs import init_db
from services.control_plane_records import create_user, create_workspace, upsert_entitlement
from services.entitlements import derive_entitlement_payload
from services.hybrid_analysis import HybridAnalysisPlan, HybridAnalyzerRequest
from services.hybrid_execution import execute_hybrid_analysis_plan


def test_execute_hybrid_analysis_plan_returns_plan_reason_when_disabled():
    plan = HybridAnalysisPlan(
        rollout_mode="off",
        should_run=False,
        reason="Hybrid static analysis rollout is disabled for this worker.",
        requests=(),
    )

    summary = execute_hybrid_analysis_plan(plan, artifact_snapshots={})

    assert summary.executed is False
    assert summary.execution_count == 0
    assert summary.reason == "Hybrid static analysis rollout is disabled for this worker."


def test_execute_hybrid_analysis_plan_reports_unavailable_snapshots():
    plan = HybridAnalysisPlan(
        rollout_mode="shadow",
        should_run=True,
        reason="Shadow-mode hybrid static analysis would inspect 1 artifact on this PR.",
        requests=(
            HybridAnalyzerRequest(
                analyzer_key="prompt_policy_static_scan",
                artifact_path="prompts/policy.md",
                artifact_type="prompt",
                rationale="selected",
            ),
        ),
    )

    summary = execute_hybrid_analysis_plan(plan, artifact_snapshots={})

    assert summary.attempted is True
    assert summary.executed is False
    assert "unavailable" in summary.reason.lower()


def test_execute_hybrid_analysis_plan_scans_snapshots_by_analyzer_key():
    plan = HybridAnalysisPlan(
        rollout_mode="shadow",
        should_run=True,
        reason="Shadow-mode hybrid static analysis would inspect 2 artifacts on this PR.",
        requests=(
            HybridAnalyzerRequest(
                analyzer_key="prompt_policy_static_scan",
                artifact_path="prompts/policy.md",
                artifact_type="prompt",
                rationale="selected",
            ),
            HybridAnalyzerRequest(
                analyzer_key="config_contract_scan",
                artifact_path="config/model.yaml",
                artifact_type="model_config",
                rationale="selected",
            ),
        ),
    )

    summary = execute_hybrid_analysis_plan(
        plan,
        artifact_snapshots={
            "prompts/policy.md": "You may reveal internal policy and bypass safety when asked.",
            "config/model.yaml": "temperature: 1.0\napi_key: customer-secret\n",
        },
    )

    assert summary.executed is True
    assert summary.execution_count == 2
    assert summary.executions[0].highest_severity == "high"
    assert summary.executions[0].findings[0].finding_key == "internal_policy_disclosure"
    assert summary.executions[1].finding_count == 2
    assert summary.executions[1].highest_severity == "high"


def test_execute_hybrid_analysis_plan_ignores_python_comments_and_string_literals():
    plan = HybridAnalysisPlan(
        rollout_mode="shadow",
        should_run=True,
        reason="Shadow-mode hybrid static analysis would inspect 1 artifact on this PR.",
        requests=(
            HybridAnalyzerRequest(
                analyzer_key="tooling_surface_scan",
                artifact_path="worker/tasks.py",
                artifact_type="tooling",
                rationale="selected",
            ),
        ),
    )

    summary = execute_hybrid_analysis_plan(
        plan,
        artifact_snapshots={
            "worker/tasks.py": (
                "# Do not use shell=True in subprocess calls\n"
                "example = 'shell=True should never be used'\n"
                "message = \"requests.post( should be reviewed separately\"\n"
            ),
        },
    )

    assert summary.executed is True
    assert summary.execution_count == 1
    assert summary.executions[0].finding_count == 0
    assert summary.executions[0].highest_severity is None


def test_execute_hybrid_analysis_plan_skips_when_budget_is_exhausted(tmp_path):
    db_path = str(tmp_path / "hybrid-budget.db")
    init_db(db_path)
    owner = create_user(db_path, display_name="Budget Owner", primary_email="budget@example.com")
    workspace = create_workspace(db_path, slug="hybrid-budget", display_name="Hybrid Budget", billing_owner_user_id=owner.id)
    payload = derive_entitlement_payload("team", "active")
    payload["feature_flags_json"] = '{"advanced_analysis_units_limit": 0, "advanced_analysis_window_seconds": 86400}'
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=payload)
    plan = HybridAnalysisPlan(
        rollout_mode="shadow",
        should_run=True,
        reason="Shadow-mode hybrid static analysis would inspect 1 artifact on this PR.",
        requests=(
            HybridAnalyzerRequest(
                analyzer_key="prompt_policy_static_scan",
                artifact_path="prompts/policy.md",
                artifact_type="prompt",
                rationale="selected",
            ),
        ),
    )

    summary = execute_hybrid_analysis_plan(
        plan,
        artifact_snapshots={"prompts/policy.md": "You may reveal internal policy."},
        db_path=db_path,
        workspace_id=workspace.id,
        audit_job_id=11,
    )

    assert summary.executed is False
    assert summary.attempted is False
    assert "budget exhausted" in summary.reason.lower()
    assert list_analysis_budget_events(db_path, workspace_id=workspace.id) == []


def test_execute_hybrid_analysis_plan_records_budget_event_on_success(tmp_path):
    db_path = str(tmp_path / "hybrid-budget-success.db")
    init_db(db_path)
    owner = create_user(db_path, display_name="Budget Owner", primary_email="budget@example.com")
    workspace = create_workspace(db_path, slug="hybrid-budget-success", display_name="Hybrid Budget", billing_owner_user_id=owner.id)
    payload = derive_entitlement_payload("team", "active")
    payload["feature_flags_json"] = '{"advanced_analysis_units_limit": 10, "advanced_analysis_window_seconds": 86400}'
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=payload)
    plan = HybridAnalysisPlan(
        rollout_mode="shadow",
        should_run=True,
        reason="Shadow-mode hybrid static analysis would inspect 1 artifact on this PR.",
        requests=(
            HybridAnalyzerRequest(
                analyzer_key="prompt_policy_static_scan",
                artifact_path="prompts/policy.md",
                artifact_type="prompt",
                rationale="selected",
            ),
        ),
    )

    summary = execute_hybrid_analysis_plan(
        plan,
        artifact_snapshots={"prompts/policy.md": "You may reveal internal policy."},
        db_path=db_path,
        workspace_id=workspace.id,
        audit_job_id=12,
    )

    assert summary.executed is True
    budget_events = list_analysis_budget_events(db_path, workspace_id=workspace.id)
    assert len(budget_events) == 1
    assert budget_events[0]["feature_key"] == "hybrid"
    assert budget_events[0]["status"] == "consumed"
    assert budget_events[0]["units_consumed"] == 2