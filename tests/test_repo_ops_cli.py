import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

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