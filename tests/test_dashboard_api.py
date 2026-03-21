import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

import main
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


def test_dashboard_api_returns_repo_view_for_seeded_repo(tmp_path):
    db_path = str(tmp_path / "api-dashboard.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False

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

    with TestClient(main.app) as client:
        overview_response = client.get("/api/dashboard/overview")
        index_response = client.get("/api/repos")
        repo_response = client.get("/api/repos/doria90/dummyAI/dashboard")

    assert overview_response.status_code == 200
    overview_payload = overview_response.json()
    assert overview_payload["risk_state"]["headline"]
    assert overview_payload["highest_risk_items"][0]["repo_full"] == "doria90/dummyAI"
    assert overview_payload["control_surface_risk"][0]["group_key"] == "prompts"
    assert overview_payload["metrics"][0]["label"] == "Onboarded repositories"
    assert overview_payload["attention_repos"][0]["repo_full"] == "doria90/dummyAI"

    assert index_response.status_code == 200
    assert index_response.json()["repos"][0]["repo_full"] == "doria90/dummyAI"

    assert repo_response.status_code == 200
    payload = repo_response.json()
    assert payload["repo_full"] == "doria90/dummyAI"
    assert payload["onboarding"]["default_branch"] == "main"
    assert payload["backfill"]["completed_job_count"] == 1
    assert payload["insights"][0]["artifact_path"] == "prompts/refund.txt"
    assert payload["control_surface_groups"][0]["group_key"] == "prompts"
    assert payload["history_timelines"][0]["artifact_path"] == "prompts/refund.txt"
    assert payload["history_timelines"][0]["point_count"] == 1
    assert payload["design_profiles"][0]["artifact_path"] == "prompts/refund.txt"
    assert payload["design_profiles"][0]["baseline_profile"]["guardrail_robustness"] >= 0
    assert payload["artifacts"][0]["artifact_path"] == "prompts/refund.txt"