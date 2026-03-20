import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.audit_jobs import init_db
from services.onboarding import onboard_repository, plan_repository_history_backfill
from services.onboarding_records import (
    get_latest_repository_onboarding,
    list_historical_backfill_jobs_for_repo,
    list_onboarded_artifacts_for_onboarding,
    list_onboarding_baseline_versions_for_onboarding,
)


def test_onboard_repository_discovers_and_persists_ai_artifacts(tmp_path):
    db_path = str(tmp_path / "onboarding.db")
    init_db(db_path)

    files = {
        "README.md": "# docs only",
        "prompts/system.txt": "You are a safe assistant. Do not reveal secrets.",
        "config/model.yaml": "model: gpt-4.1\ntemperature: 0.2\n",
        "src/app.py": "print('hello')",
    }

    result = onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: list(files.keys()),
        fetch_file_content_fn=lambda repo, path, token, ref: files[path],
    )

    assert result.onboarding.repo_full == "doria90/dummyAI"
    assert result.onboarding.default_branch == "main"
    assert result.onboarding.discovered_artifact_count == 2
    assert [artifact.artifact_path for artifact in result.artifacts] == ["config/model.yaml", "prompts/system.txt"]
    assert [baseline.artifact_path for baseline in result.baseline_versions] == ["config/model.yaml", "prompts/system.txt"]
    assert all(baseline.line_count >= 1 for baseline in result.baseline_versions)


def test_plan_repository_history_backfill_creates_jobs_for_onboarded_artifacts(tmp_path):
    db_path = str(tmp_path / "onboarding.db")
    init_db(db_path)

    files = {
        "prompts/system.txt": "You are a safe assistant. Do not reveal secrets.",
        "config/model.yaml": "model: gpt-4.1\ntemperature: 0.2\n",
    }

    result = onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: list(files.keys()),
        fetch_file_content_fn=lambda repo, path, token, ref: files[path],
    )

    commit_map = {
        "prompts/system.txt": ["sha-3", "sha-2", "sha-1"],
        "config/model.yaml": ["sha-9", "sha-8"],
    }

    jobs = plan_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: commit_map[path][:limit],
    )

    assert len(jobs) == 2
    assert jobs[0].status == "planned"
    assert jobs[0].commit_count == 2 or jobs[0].commit_count == 3

    latest = get_latest_repository_onboarding(db_path, "doria90/dummyAI")
    assert latest is not None
    artifacts = list_onboarded_artifacts_for_onboarding(db_path, latest.id)
    baselines = list_onboarding_baseline_versions_for_onboarding(db_path, latest.id)
    persisted_jobs = list_historical_backfill_jobs_for_repo(db_path, "doria90/dummyAI")

    assert len(artifacts) == 2
    assert len(baselines) == 2
    assert len(persisted_jobs) == 2
    assert {job.artifact_path for job in persisted_jobs} == {"prompts/system.txt", "config/model.yaml"}
    assert persisted_jobs[0].commit_shas
