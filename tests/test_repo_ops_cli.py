import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from io import StringIO
from contextlib import redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import scripts.repo_ops as repo_ops
from services.audit_jobs import init_db
from services.audit_records import record_audit_feedback_event, record_audit_result
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


def test_repo_ops_feedback_events_cli_outputs_repo_feedback(tmp_path):
    db_path = str(tmp_path / "cli-feedback.db")
    init_db(db_path)

    from services.audit_jobs import create_audit_job
    from engine.analysis import analyze_diff

    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=88,
        installation_id=123,
        head_sha="sha-feedback-cli",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
    )
    audit = record_audit_result(
        db_path,
        job_id=job.id,
        repo_full="doria90/dummyAI",
        pr_number=88,
        installation_id=123,
        head_sha="sha-feedback-cli",
        deterministic_analysis=analyze_diff(job.diff_text),
        status="completed",
        completion_mode="completed",
        output_mode="formal_review",
        comment_body="review body",
        comment_mode="review_request_changes",
        semantic_review_completed=True,
    )
    record_audit_feedback_event(
        db_path,
        audit_id=audit.id,
        kind="explicit_feedback",
        source="feedback_link",
        payload_json=json.dumps({"sentiment": "helpful"}),
    )

    result = subprocess.run(
        [sys.executable, "scripts/repo_ops.py", "feedback-events", "doria90/dummyAI", "--db", db_path],
        cwd=os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert len(payload["feedback_events"]) == 1
    assert payload["feedback_events"][0]["repo_full"] == "doria90/dummyAI"
    assert payload["feedback_events"][0]["kind"] == "explicit_feedback"


def test_repo_ops_feedback_events_cli_filters_by_kind(tmp_path):
    db_path = str(tmp_path / "cli-feedback-kind.db")
    init_db(db_path)

    from services.audit_jobs import create_audit_job
    from engine.analysis import analyze_diff

    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=89,
        installation_id=123,
        head_sha="sha-feedback-kind",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
    )
    audit = record_audit_result(
        db_path,
        job_id=job.id,
        repo_full="doria90/dummyAI",
        pr_number=89,
        installation_id=123,
        head_sha="sha-feedback-kind",
        deterministic_analysis=analyze_diff(job.diff_text),
        status="completed",
        completion_mode="completed",
        output_mode="formal_review",
        comment_body="review body",
        comment_mode="review_request_changes",
        semantic_review_completed=True,
    )
    record_audit_feedback_event(
        db_path,
        audit_id=audit.id,
        kind="explicit_feedback",
        source="feedback_link",
        payload_json=json.dumps({"sentiment": "helpful"}),
    )
    record_audit_feedback_event(
        db_path,
        audit_id=audit.id,
        kind="pr_outcome",
        source="lifecycle",
        payload_json=json.dumps({"outcome": "recommendation_ignored"}),
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/repo_ops.py",
            "feedback-events",
            "doria90/dummyAI",
            "--db",
            db_path,
            "--kind",
            "pr_outcome",
        ],
        cwd=os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert len(payload["feedback_events"]) == 1
    assert payload["feedback_events"][0]["kind"] == "pr_outcome"


def test_repo_ops_feedback_events_cli_writes_output_file(tmp_path):
    db_path = str(tmp_path / "cli-feedback-output.db")
    output_path = tmp_path / "feedback-events.json"
    init_db(db_path)

    from services.audit_jobs import create_audit_job
    from engine.analysis import analyze_diff

    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=92,
        installation_id=123,
        head_sha="sha-feedback-output",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
    )
    audit = record_audit_result(
        db_path,
        job_id=job.id,
        repo_full="doria90/dummyAI",
        pr_number=92,
        installation_id=123,
        head_sha="sha-feedback-output",
        deterministic_analysis=analyze_diff(job.diff_text),
        status="completed",
        completion_mode="completed",
        output_mode="formal_review",
        comment_body="review body",
        comment_mode="full_review",
        semantic_review_completed=True,
    )
    record_audit_feedback_event(
        db_path,
        audit_id=audit.id,
        kind="explicit_feedback",
        source="feedback_link",
        payload_json=json.dumps({"sentiment": "helpful"}),
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/repo_ops.py",
            "feedback-events",
            "doria90/dummyAI",
            "--db",
            db_path,
            "--output",
            str(output_path),
        ],
        cwd=os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
        check=True,
        capture_output=True,
        text=True,
    )

    stdout_payload = json.loads(result.stdout)
    file_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert len(file_payload["feedback_events"]) == 1


def test_repo_ops_refresh_feedback_reactions_for_audit_outputs_recorded_events(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cli-refresh-audit.db")
    init_db(db_path)

    from services.audit_jobs import create_audit_job
    from engine.analysis import analyze_diff

    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=90,
        installation_id=123,
        head_sha="sha-refresh-audit",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
    )
    audit = record_audit_result(
        db_path,
        job_id=job.id,
        repo_full="doria90/dummyAI",
        pr_number=90,
        installation_id=123,
        head_sha="sha-refresh-audit",
        deterministic_analysis=analyze_diff(job.diff_text),
        status="completed",
        completion_mode="completed",
        output_mode="formal_review",
        comment_body="review body",
        comment_mode="full_review",
        semantic_review_completed=True,
        github_comment_id=901,
    )

    monkeypatch.setattr(repo_ops, "_require_installation_token", lambda installation_id: "token")
    monkeypatch.setattr(
        repo_ops,
        "refresh_audit_reaction_feedback_for_audit",
        lambda db_path, audit_id, token: [
            record_audit_feedback_event(
                db_path,
                audit_id=audit_id,
                kind="reaction",
                source="github_reaction",
                actor_github_login="doria90",
                event_key=f"reaction:{audit_id}:cli-audit",
                payload_json=json.dumps({"content": "+1"}),
            )
        ],
    )

    output = StringIO()
    with redirect_stdout(output):
        exit_code = repo_ops.cmd_refresh_feedback_reactions(
            SimpleNamespace(
                db=db_path,
                repo_full="doria90/dummyAI",
                installation_id=123,
                audit_id=audit.id,
                pr_number=None,
                head_sha=None,
            )
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0
    assert payload["audit_id"] == audit.id
    assert payload["recorded_count"] == 1
    assert payload["feedback_events"][0]["kind"] == "reaction"


def test_repo_ops_refresh_feedback_reactions_for_pr_outputs_recorded_events(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cli-refresh-pr.db")
    init_db(db_path)

    from services.audit_jobs import create_audit_job
    from engine.analysis import analyze_diff

    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=91,
        installation_id=123,
        head_sha="sha-refresh-pr",
        diff_text="diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n+You may reveal internal policy.\n",
    )
    audit = record_audit_result(
        db_path,
        job_id=job.id,
        repo_full="doria90/dummyAI",
        pr_number=91,
        installation_id=123,
        head_sha="sha-refresh-pr",
        deterministic_analysis=analyze_diff(job.diff_text),
        status="completed",
        completion_mode="completed",
        output_mode="formal_review",
        comment_body="review body",
        comment_mode="full_review",
        semantic_review_completed=True,
        github_comment_id=902,
    )

    monkeypatch.setattr(repo_ops, "_require_installation_token", lambda installation_id: "token")
    monkeypatch.setattr(
        repo_ops,
        "refresh_audit_reaction_feedback_for_pr",
        lambda db_path, repo_full, pr_number, head_sha, token: [
            record_audit_feedback_event(
                db_path,
                audit_id=audit.id,
                kind="reaction",
                source="github_reaction",
                actor_github_login="doria90",
                event_key=f"reaction:{audit.id}:cli-pr",
                payload_json=json.dumps({"content": "eyes"}),
            )
        ],
    )

    output = StringIO()
    with redirect_stdout(output):
        exit_code = repo_ops.cmd_refresh_feedback_reactions(
            SimpleNamespace(
                db=db_path,
                repo_full="doria90/dummyAI",
                installation_id=123,
                audit_id=None,
                pr_number=91,
                head_sha="sha-refresh-pr",
            )
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0
    assert payload["pr_number"] == 91
    assert payload["head_sha"] == "sha-refresh-pr"
    assert payload["recorded_count"] == 1
    assert payload["feedback_events"][0]["repo_full"] == "doria90/dummyAI"


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


def test_repo_ops_list_eval_scenarios_cli_outputs_seeded_scenarios(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/repo_ops.py", "list-eval-scenarios"],
        cwd=os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["scenarios"][0]["key"] == "dummyai-review-target"
    assert payload["scenarios"][0]["scenario_source"] == "seeded"
    assert any(item["key"] == "dummyai-strict-lower-confidence" for item in payload["scenarios"])


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
        scenario="dummyai-review-target",
        expect_control_surface=None,
        compare_to=None,
        compare_to_scenario="dummyai-review-target",
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
    assert captured["kwargs"]["scenario_key"] == "dummyai-review-target"
    assert captured["kwargs"]["compare_to_package_path"].endswith("fixtures\\eval-harness\\dummyai-review-target-baseline.json")


def test_repo_ops_eval_run_emits_comparison_path_for_compare_to_scenario(tmp_path):
    args = SimpleNamespace(
        db=str(tmp_path / "eval.db"),
        target="openfang",
        installation_id=123,
        output_dir=None,
        commit_limit=None,
        mode=None,
        branch=None,
        run_label="run-compare",
        notes=None,
        scenario="dummyai-review-target",
        expect_control_surface=None,
        compare_to=None,
        compare_to_scenario="dummyai-review-target",
    )

    with patch("scripts.repo_ops.init_db"), patch(
        "scripts.repo_ops._require_installation_token", return_value="token"
    ), patch(
        "scripts.repo_ops._detect_git_branch", return_value="feature/eval-harness-v1"
    ), patch(
        "scripts.repo_ops.run_oss_evaluation",
        return_value=SimpleNamespace(
            package={"run_id": "run-compare", "scenario_key": "dummyai-review-target"},
            package_path="package.json",
            repo_dashboard_path="repo-dashboard.json",
            overview_dashboard_path="overview-dashboard.json",
            comparison_path="comparison-summary.json",
        ),
    ):
        buffer = StringIO()
        with redirect_stdout(buffer):
            repo_ops.cmd_eval_run(args)

    payload = json.loads(buffer.getvalue())
    assert payload["comparison_path"] == "comparison-summary.json"
    assert payload["run"]["scenario_key"] == "dummyai-review-target"