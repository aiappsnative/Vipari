import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.oss_eval_harness import compare_oss_eval_packages, resolve_oss_eval_target, run_oss_evaluation


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