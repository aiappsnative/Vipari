import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import scripts.repo_ops as repo_ops
from services.audit_jobs import init_db
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill


PROMPT_BASELINE = """# Refund Copilot
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Use the billing sandbox tool in read mode.
max_steps: 2
temperature: 0.2
"""

PROMPT_CURRENT = """# Refund Copilot
You can refund customers directly in production using the billing tool.
Use judgment when deciding whether approval is necessary.
Update billing records and send confirmations.
parallel plan with multi-step execution
max_steps: 6
temperature: 0.8
"""


def _seed_repo(db_path: str) -> None:
    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_CURRENT,
    )
    plan_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-2", "sha-1"][:limit],
    )
    execute_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        fetch_file_content_fn=lambda repo, path, token, ref: {
            "sha-1": PROMPT_BASELINE,
            "sha-2": PROMPT_CURRENT,
        }[ref],
    )


def test_repo_ops_list_repos_cli_outputs_seeded_repository(tmp_path):
    db_path = str(tmp_path / "cli.db")
    init_db(db_path)
    _seed_repo(db_path)

    result = subprocess.run(
        [sys.executable, "scripts/repo_ops.py", "list-repos", "--db", db_path],
        cwd=os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["repos"][0]["repo_full"] == "doria90/dummyAI"
    assert payload["repos"][0]["discovered_artifact_count"] == 1


def test_repo_ops_persistence_status_cli_outputs_backend_metadata(tmp_path):
    db_path = str(tmp_path / "cli.db")
    init_db(db_path)

    result = subprocess.run(
        [sys.executable, "scripts/repo_ops.py", "persistence-status", "--db", db_path],
        cwd=os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["backend"] == "sqlite"
    assert payload["production_target"] == "postgresql"
    assert "audit_jobs" in payload["operational_tables"]
    assert "database_path" not in payload
    assert payload["applied_migrations"][0]["version"] == "0001_bootstrap_relational_schema"


def test_repo_ops_default_db_path_stays_runtime_compatible(monkeypatch):
    monkeypatch.delenv("AUDIT_DB_PATH", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    resolved = repo_ops._resolve_db_path(None)

    assert resolved.endswith("promptdrift.db")


def test_repo_ops_default_db_path_uses_postgres_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.com/driftguard")

    resolved = repo_ops._resolve_db_path(None)

    assert resolved == "postgresql://user:pass@db.example.com/driftguard"
def test_repo_ops_dashboard_cli_outputs_unified_repo_payload(tmp_path):
    db_path = str(tmp_path / "cli.db")
    init_db(db_path)
    _seed_repo(db_path)

    result = subprocess.run(
        [sys.executable, "scripts/repo_ops.py", "dashboard", "doria90/dummyAI", "--db", db_path],
        cwd=os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["repo_full"] == "doria90/dummyAI"
    assert payload["backfill"]["completed_job_count"] == 1
    assert payload["artifacts"][0]["artifact_path"] == "prompts/refund.txt"


def test_repo_ops_list_eval_candidates_cli_outputs_curated_candidates(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/repo_ops.py", "list-eval-candidates"],
        cwd=os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["candidates"][0]["repo_full"] == "doria90/openfang"
    assert any(candidate["key"] == "hermes-agent" for candidate in payload["candidates"])


def test_repo_ops_eval_compare_cli_summarizes_saved_packages(tmp_path):
    current_path = tmp_path / "current-run.json"
    baseline_path = tmp_path / "baseline-run.json"

    current_path.write_text(
        json.dumps(
            {
                "repo_full": "doria90/dummyAI",
                "run_id": "current",
                "branch_name": "feature/oss-eval-harness-v1",
                "baseline_coverage_summary": {
                    "high_confidence_baseline_coverage": 1.0,
                    "high_confidence_artifact_count": 2,
                },
                "repo_dashboard_snapshot": {"lower_confidence_insights": []},
                "top_artifacts_requiring_review": [{"artifact_path": "prompts/refund.txt"}],
            }
        ),
        encoding="utf-8",
    )
    baseline_path.write_text(
        json.dumps(
            {
                "repo_full": "doria90/dummyAI",
                "run_id": "baseline",
                "branch_name": "main",
                "baseline_coverage_summary": {
                    "high_confidence_baseline_coverage": 0.5,
                    "high_confidence_artifact_count": 1,
                },
                "repo_dashboard_snapshot": {"lower_confidence_insights": [{"artifact_path": "noise.txt"}]},
                "top_artifacts_requiring_review": [{"artifact_path": "prompts/system.txt"}],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "scripts/repo_ops.py", "eval-compare", str(current_path), str(baseline_path)],
        cwd=os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["repo_full"] == "doria90/dummyAI"
    assert any("coverage improved" in item.lower() for item in payload["improvements"])


def test_repo_ops_eval_run_uses_candidate_defaults_when_flags_omitted(tmp_path):
    args = SimpleNamespace(
        db=str(tmp_path / "eval.db"),
        target="openfang",
        installation_id=123,
        output_dir=None,
        commit_limit=None,
        mode=None,
        branch=None,
        run_label="run-001",
        notes=None,
        expect_control_surface=None,
        compare_to=None,
    )

    captured: dict[str, object] = {}

    def _capture_run(*call_args, **call_kwargs):
        captured["kwargs"] = call_kwargs
        return SimpleNamespace(
            package={"run_id": "run-001"},
            package_path="package.json",
            repo_dashboard_path="repo-dashboard.json",
            overview_dashboard_path="overview-dashboard.json",
            comparison_path=None,
        )

    with patch("scripts.repo_ops.init_db"), patch(
        "scripts.repo_ops._require_installation_token", return_value="token"
    ), patch(
        "scripts.repo_ops._detect_git_branch", return_value="feature/oss-eval-harness-v1"
    ), patch(
        "scripts.repo_ops.run_oss_evaluation",
        side_effect=_capture_run,
    ):
        repo_ops.cmd_eval_run(args)

    assert captured["kwargs"]["mode"] == "baseline_plus_backfill"
    assert captured["kwargs"]["commit_limit_per_artifact"] == 12
    assert captured["kwargs"]["expected_control_surfaces"] == ["prompts", "guardrails", "model configuration"]