import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.oss_eval_harness import (
    compare_eval_packages,
    compare_oss_eval_packages,
    list_eval_candidates,
    list_eval_scenarios,
    load_eval_reference_package,
    resolve_eval_target,
    resolve_eval_reference_package_path,
    resolve_oss_eval_target,
    run_evaluation,
    run_oss_evaluation,
)


def _onboarding_result():
    return SimpleNamespace(
        onboarding={
            "id": 1,
            "repo_full": "doria90/dummyAI",
            "default_branch": "main",
            "status": "completed",
            "discovered_artifact_count": 2,
        },
        artifacts=[
            {
                "artifact_path": "config/model.yaml",
                "artifact_type": "model_config",
                "discovery_reason": "Path indicates a model configuration artifact.",
                "confidence": 0.95,
            },
            {
                "artifact_path": "docs/prompts/system.txt",
                "artifact_type": "prompt",
                "discovery_reason": "Path indicates a prompt artifact.",
                "confidence": 0.72,
            },
        ],
        baseline_versions=[
            {
                "artifact_path": "config/model.yaml",
                "artifact_type": "model_config",
            }
        ],
    )


def _repo_dashboard(lower_confidence_count: int = 1):
    return {
        "repo_full": "doria90/dummyAI",
        "backfill": {
            "completed_job_count": 1,
            "total_historical_versions": 3,
            "total_historical_profiles": 3,
        },
        "insights": [
            {
                "title": "Review the production refund prompt",
                "artifact_path": "prompts/refund.txt",
                "artifact_type": "prompt",
                "priority": "review_now",
                "recommended_action": "Inspect the latest drift and escalation posture.",
                "review_target": "commit sha-2",
                "review_url": "https://github.com/doria90/dummyAI/commit/sha-2",
                "evidence_summary": "Only merged-history evidence is available right now; start with commit sha-2.",
                "confidence_label": "high confidence",
                "queue_lane": "primary",
            }
        ],
        "lower_confidence_insights": [{"artifact_path": f"misc/{index}.txt"} for index in range(lower_confidence_count)],
    }


def _overview_dashboard():
    return {
        "risk_state": {"headline": "1 repo needs attention"},
        "attention_repos": [{"repo_full": "doria90/dummyAI"}],
    }


def test_run_oss_evaluation_writes_repeatable_package_and_snapshots(tmp_path):
    result = run_oss_evaluation(
        str(tmp_path / "eval.db"),
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        mode="baseline_plus_backfill",
        commit_limit_per_artifact=5,
        output_root=str(tmp_path / "artifacts"),
        branch_name="feature/oss-eval-harness-v1",
        candidate_key="dummy-ai",
        expected_control_surfaces=["prompts", "model configuration"],
        manual_notes="Looks directionally good.",
        run_label="run-001",
        onboard_repository_fn=lambda *args, **kwargs: _onboarding_result(),
        plan_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(status="planned")],
        execute_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(job=SimpleNamespace(status="completed"))],
        build_repo_dashboard_view_fn=lambda *args, **kwargs: _repo_dashboard(),
        build_dashboard_overview_view_fn=lambda *args, **kwargs: _overview_dashboard(),
    )

    assert os.path.exists(result.package_path)
    assert os.path.exists(result.repo_dashboard_path)
    assert os.path.exists(result.overview_dashboard_path)
    assert result.package["baseline_coverage_summary"]["high_confidence_artifact_count"] == 1
    assert result.package["baseline_coverage_summary"]["high_confidence_baseline_coverage"] == 1.0
    assert result.package["backfill_execution_summary"]["completed_job_count"] == 1
    assert result.package["top_artifacts_requiring_review"][0]["artifact_path"] == "prompts/refund.txt"
    assert result.package["evaluator_rubric"][0]["dimension"] == "discovery"
    assert result.package["package_type"] == "evaluation_run"
    assert result.package["candidate_source"] == "oss"

    persisted = json.loads((tmp_path / "artifacts" / "feature-oss-eval-harness-v1" / "doria90-dummyai" / "run-001" / "run-package.json").read_text(encoding="utf-8"))
    assert persisted["repo_full"] == "doria90/dummyAI"
    assert persisted["manual_notes"] == "Looks directionally good."


def test_compare_oss_eval_packages_reports_precision_and_coverage_changes():
    baseline = {
        "repo_full": "doria90/dummyAI",
        "run_id": "baseline-run",
        "branch_name": "main",
        "baseline_coverage_summary": {
            "high_confidence_baseline_coverage": 0.5,
            "high_confidence_artifact_count": 2,
        },
        "repo_dashboard_snapshot": {
            "lower_confidence_insights": [{"artifact_path": "one"}, {"artifact_path": "two"}],
        },
        "top_artifacts_requiring_review": [{"artifact_path": "prompts/system.txt"}],
    }
    current = {
        "repo_full": "doria90/dummyAI",
        "run_id": "current-run",
        "branch_name": "feature/oss-eval-harness-v1",
        "baseline_coverage_summary": {
            "high_confidence_baseline_coverage": 1.0,
            "high_confidence_artifact_count": 3,
        },
        "repo_dashboard_snapshot": {
            "lower_confidence_insights": [{"artifact_path": "one"}],
        },
        "top_artifacts_requiring_review": [{"artifact_path": "prompts/refund.txt"}],
    }

    summary = compare_oss_eval_packages(current, baseline)

    assert summary["repo_full"] == "doria90/dummyAI"
    assert any("baseline coverage improved" in item.lower() for item in summary["improvements"])
    assert any("lower-confidence queue size improved" in item.lower() for item in summary["improvements"])
    assert any("Top review target changed" in item for item in summary["improvements"])


def test_resolve_oss_eval_target_supports_registry_key_and_repo_full():
    by_key = resolve_oss_eval_target("openfang")
    by_repo = resolve_oss_eval_target("doria90/hermes-agent")
    ad_hoc = resolve_oss_eval_target("someone/custom-repo")

    assert by_key.repo_full == "doria90/openfang"
    assert by_repo.key == "hermes-agent"
    assert ad_hoc.repo_full == "someone/custom-repo"


def test_generic_eval_aliases_match_oss_compatibility_surface(tmp_path):
    candidate = resolve_eval_target("openfang")
    assert candidate.repo_full == "doria90/openfang"
    assert list_eval_candidates()[0].candidate_source == "oss"

    result = run_evaluation(
        str(tmp_path / "eval.db"),
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        mode="baseline_only",
        commit_limit_per_artifact=5,
        output_root=str(tmp_path / "artifacts"),
        branch_name="feature/eval-harness-v1",
        candidate_key="dummy-ai",
        expected_control_surfaces=["prompts"],
        manual_notes=None,
        run_label="run-generic",
        onboard_repository_fn=lambda *args, **kwargs: _onboarding_result(),
        plan_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(status="planned")],
        execute_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(job=SimpleNamespace(status="completed"))],
        build_repo_dashboard_view_fn=lambda *args, **kwargs: _repo_dashboard(),
        build_dashboard_overview_view_fn=lambda *args, **kwargs: _overview_dashboard(),
    )

    comparison = compare_eval_packages(result.package, result.package)
    assert result.package["package_type"] == "evaluation_run"
    assert comparison["repo_full"] == "doria90/dummyAI"


def test_seeded_scenario_registry_and_assertions_are_written_into_package(tmp_path):
    result = run_evaluation(
        str(tmp_path / "eval.db"),
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        mode="baseline_plus_backfill",
        commit_limit_per_artifact=5,
        output_root=str(tmp_path / "artifacts"),
        branch_name="feature/eval-harness-v1",
        candidate_key="dummy-ai",
        expected_control_surfaces=["prompts", "model configuration"],
        manual_notes="Scenario check.",
        run_label="run-scenario",
        scenario_key="dummyai-review-target",
        onboard_repository_fn=lambda *args, **kwargs: _onboarding_result(),
        plan_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(status="planned")],
        execute_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(job=SimpleNamespace(status="completed"))],
        build_repo_dashboard_view_fn=lambda *args, **kwargs: _repo_dashboard(),
        build_dashboard_overview_view_fn=lambda *args, **kwargs: _overview_dashboard(),
    )

    assert any(scenario.key == "dummyai-review-target" for scenario in list_eval_scenarios())
    assert result.package["scenario_key"] == "dummyai-review-target"
    assert result.package["candidate_source"] == "seeded"
    assert result.package["assertion_summary"]["all_passed"] is True
    assert {item["assertion_id"] for item in result.package["assertions"]} == {
        "high_confidence_baseline_coverage_min",
        "top_review_target_present",
        "top_review_target_path",
        "lower_confidence_queue_max",
    }


def test_strict_seeded_scenario_can_fail_explicit_assertions(tmp_path):
    result = run_evaluation(
        str(tmp_path / "eval.db"),
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        mode="baseline_plus_backfill",
        commit_limit_per_artifact=5,
        output_root=str(tmp_path / "artifacts"),
        branch_name="feature/eval-harness-v1",
        candidate_key="dummy-ai",
        expected_control_surfaces=["prompts", "model configuration"],
        manual_notes="Strict scenario check.",
        run_label="run-strict-scenario",
        scenario_key="dummyai-strict-lower-confidence",
        onboard_repository_fn=lambda *args, **kwargs: _onboarding_result(),
        plan_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(status="planned")],
        execute_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(job=SimpleNamespace(status="completed"))],
        build_repo_dashboard_view_fn=lambda *args, **kwargs: _repo_dashboard(lower_confidence_count=1),
        build_dashboard_overview_view_fn=lambda *args, **kwargs: _overview_dashboard(),
    )

    assert result.package["scenario_key"] == "dummyai-strict-lower-confidence"
    assert result.package["candidate_source"] == "seeded"
    assert result.package["assertion_summary"]["all_passed"] is False
    failed_ids = {item["assertion_id"] for item in result.package["assertions"] if not item["passed"]}
    assert failed_ids == {"lower_confidence_queue_max"}


def test_seeded_scenario_run_writes_real_comparison_summary_against_checked_in_reference(tmp_path):
    result = run_evaluation(
        str(tmp_path / "eval.db"),
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        mode="baseline_plus_backfill",
        commit_limit_per_artifact=5,
        output_root=str(tmp_path / "artifacts"),
        branch_name="feature/eval-harness-v1",
        candidate_key="dummy-ai",
        expected_control_surfaces=["prompts", "model configuration"],
        manual_notes="Scenario compare check.",
        run_label="run-scenario-compare",
        scenario_key="dummyai-review-target",
        onboard_repository_fn=lambda *args, **kwargs: _onboarding_result(),
        plan_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(status="planned")],
        execute_repository_history_backfill_fn=lambda *args, **kwargs: [SimpleNamespace(job=SimpleNamespace(status="completed"))],
        build_repo_dashboard_view_fn=lambda *args, **kwargs: _repo_dashboard(),
        build_dashboard_overview_view_fn=lambda *args, **kwargs: _overview_dashboard(),
    )

    assert result.package["comparison_baseline_package_path"] is not None
    assert result.comparison_path is not None

    comparison = json.loads((tmp_path / "artifacts" / "feature-eval-harness-v1" / "doria90-dummyai" / "run-scenario-compare" / "comparison-summary.json").read_text(encoding="utf-8"))
    assert comparison["scenario_key"] == "dummyai-review-target"
    assert comparison["baseline_assertion_summary"]["all_passed"] is True
    assert comparison["current_assertion_summary"]["all_passed"] is True


def test_seeded_scenario_reference_package_is_resolvable_and_loadable():
    reference_path = resolve_eval_reference_package_path("dummyai-review-target")
    reference_package = load_eval_reference_package("dummyai-review-target")

    assert reference_path is not None
    assert reference_package is not None
    assert reference_package["scenario_key"] == "dummyai-review-target"
    assert reference_package["assertion_summary"]["all_passed"] is True


def test_strict_seeded_scenario_reference_package_is_resolvable_and_loadable():
    reference_path = resolve_eval_reference_package_path("dummyai-strict-lower-confidence")
    reference_package = load_eval_reference_package("dummyai-strict-lower-confidence")

    assert reference_path is not None
    assert reference_package is not None
    assert reference_package["scenario_key"] == "dummyai-strict-lower-confidence"
    assert reference_package["assertion_summary"]["all_passed"] is False


def test_compare_eval_packages_summarizes_assertion_regressions():
    baseline = {
        "repo_full": "doria90/dummyAI",
        "run_id": "baseline-run",
        "branch_name": "main",
        "scenario_key": "dummyai-review-target",
        "baseline_coverage_summary": {
            "high_confidence_baseline_coverage": 1.0,
            "high_confidence_artifact_count": 1,
        },
        "repo_dashboard_snapshot": {"lower_confidence_insights": []},
        "top_artifacts_requiring_review": [{"artifact_path": "prompts/refund.txt"}],
        "assertions": [{"assertion_id": "a", "passed": True}],
    }
    current = {
        "repo_full": "doria90/dummyAI",
        "run_id": "current-run",
        "branch_name": "feature/eval-harness-v1",
        "scenario_key": "dummyai-review-target",
        "baseline_coverage_summary": {
            "high_confidence_baseline_coverage": 0.5,
            "high_confidence_artifact_count": 1,
        },
        "repo_dashboard_snapshot": {"lower_confidence_insights": [{"artifact_path": "one"}]},
        "top_artifacts_requiring_review": [{"artifact_path": "prompts/system.txt"}],
        "assertions": [{"assertion_id": "a", "passed": False}],
    }

    summary = compare_eval_packages(current, baseline)

    assert summary["scenario_key"] == "dummyai-review-target"
    assert summary["current_assertion_summary"]["failed_count"] == 1
    assert any("assertion failures regressed" in item.lower() for item in summary["regressions"])