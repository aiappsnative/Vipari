import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.audit_jobs import init_db
from services.onboarding import add_repo_artifact_to_onboarding, execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill, remove_repo_artifact_from_onboarding, sync_on_pr_merge_artifact_changes, update_repo_artifact_type
from services.onboarding_records import (
    get_latest_repository_onboarding,
    list_historical_artifact_versions_for_repo_artifact,
    list_historical_backfill_jobs_for_repo,
    list_historical_static_profiles_for_repo_artifact,
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
    assert result.onboarding.status == "baseline_approved"
    assert result.onboarding.discovered_artifact_count == 2
    assert [artifact.artifact_path for artifact in result.artifacts] == ["config/model.yaml", "prompts/system.txt"]
    assert [baseline.artifact_path for baseline in result.baseline_versions] == ["config/model.yaml", "prompts/system.txt"]
    assert all(baseline.line_count >= 1 for baseline in result.baseline_versions)
    assert all(baseline.approval_status == "approved" for baseline in result.baseline_versions)


def test_onboard_repository_returns_ai_library_matches_from_dependency_manifests(tmp_path):
    db_path = str(tmp_path / "onboarding-ai-libraries.db")
    init_db(db_path)

    files = {
        "requirements.txt": "openai==1.30.0\nrequests==2.32.0\n",
        "package.json": '{"dependencies": {"@langchain/openai": "^0.1.0", "react": "^18.0.0"}}',
        "prompts/system.txt": "You are a safe assistant. Do not reveal secrets.",
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

    assert [artifact.artifact_path for artifact in result.artifacts] == ["prompts/system.txt"]
    assert [match.canonical_name for match in result.ai_library_matches] == ["langchain", "openai"]
    assert [match.source_file for match in result.ai_library_matches] == ["package.json", "requirements.txt"]
    assert result.capability_profile.primary_capability == "generative_ai"
    assert "agentic" in result.capability_profile.capability_tags
    assert "tool_use" in result.capability_profile.capability_tags


def test_onboard_repository_filters_noisy_oss_paths_but_keeps_strong_prompt_candidates(tmp_path):
    db_path = str(tmp_path / "onboarding.db")
    init_db(db_path)

    files = {
        "tests/tools/test_mcp_oauth.py": "def test_flow():\n    return 'assistant token'\n",
        "skills/github/github-auth/SKILL.md": "AI assistant guidance for auth flows.",
        "docs/reference/assistant-architecture.md": "This document explains the assistant architecture.",
        "docs/prompts/system.txt": "You are a safe assistant. Do not reveal secrets.",
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

    assert [artifact.artifact_path for artifact in result.artifacts] == ["config/model.yaml", "docs/prompts/system.txt"]
    confidence_by_path = {artifact.artifact_path: artifact.confidence for artifact in result.artifacts}
    assert confidence_by_path["config/model.yaml"] >= 0.82
    assert confidence_by_path["docs/prompts/system.txt"] >= 0.88
    assert result.onboarding.discovered_artifact_count == 2


def test_onboard_repository_groups_low_signal_candidates_by_directory(tmp_path):
    db_path = str(tmp_path / "onboarding.db")
    init_db(db_path)

    files = {
        "notes/assistant-checklist.md": "Assistant operator checklist.",
        "notes/assistant-faq.md": "Assistant frequently asked questions.",
        "notes/assistant-runbook.md": "Assistant runbook for reviewers.",
        "prompts/system.txt": "You are a safe assistant. Do not reveal secrets.",
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

    assert result.onboarding.discovered_artifact_count == 2
    assert result.onboarding.status == "pending_baseline_approval"
    assert [artifact.artifact_path for artifact in result.artifacts] == ["notes/assistant-checklist.md", "prompts/system.txt"]
    assert result.artifacts[0].confidence == 0.72
    assert "Grouped 3 low-signal candidates under assistant" in result.artifacts[0].discovery_reason
    baseline_by_path = {baseline.artifact_path: baseline for baseline in result.baseline_versions}
    assert baseline_by_path["notes/assistant-checklist.md"].approval_status == "pending"
    assert baseline_by_path["prompts/system.txt"].approval_status == "approved"
    assert [baseline.artifact_path for baseline in result.baseline_versions] == ["notes/assistant-checklist.md", "prompts/system.txt"]


def test_onboard_repository_groups_low_signal_candidates_across_adjacent_text_paths(tmp_path):
    db_path = str(tmp_path / "onboarding.db")
    init_db(db_path)

    files = {
        "notes/assistant-checklist.md": "Assistant operator checklist.",
        "guides/assistant-faq.md": "Assistant frequently asked questions.",
        "config/assistant-index.json": '{"assistant": "routing notes"}',
        "prompts/system.txt": "You are a safe assistant. Do not reveal secrets.",
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

    assert result.onboarding.discovered_artifact_count == 2
    assert [artifact.artifact_path for artifact in result.artifacts] == ["guides/assistant-faq.md", "prompts/system.txt"]
    assert "Grouped 3 low-signal candidates under assistant" in result.artifacts[0].discovery_reason


def test_onboard_repository_drops_singleton_generic_ai_path_candidates(tmp_path):
    db_path = str(tmp_path / "onboarding.db")
    init_db(db_path)

    files = {
        "notes/assistant-checklist.md": "Assistant operator checklist.",
        "prompts/system.txt": "You are a safe assistant. Do not reveal secrets.",
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

    assert result.onboarding.discovered_artifact_count == 1
    assert [artifact.artifact_path for artifact in result.artifacts] == ["prompts/system.txt"]


def test_onboard_repository_discovers_explicit_agent_code_paths(tmp_path):
    db_path = str(tmp_path / "onboarding.db")
    init_db(db_path)

    files = {
        "agents/refund_worker.py": "def route_refund():\n    return planner.run('refund workflow')\n",
        "README.md": "docs only",
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

    assert result.onboarding.discovered_artifact_count == 1
    assert [artifact.artifact_path for artifact in result.artifacts] == ["agents/refund_worker.py"]
    assert result.artifacts[0].artifact_type == "ai_code"
    assert result.artifacts[0].confidence >= 0.78


def test_sync_on_pr_merge_preserves_existing_artifacts_without_baseline_content(tmp_path):
    db_path = str(tmp_path / "onboarding-sync.db")
    init_db(db_path)

    files = {
        "prompts/original.txt": "You are a safe assistant. Do not reveal secrets.",
        "config/model.yaml": "model: gpt-4.1\ntemperature: 0.2\n",
    }

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: list(files.keys()),
        fetch_file_content_fn=lambda repo, path, token, ref: files[path],
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE onboarding_baseline_versions SET content_text = NULL WHERE artifact_path = ?",
            ("config/model.yaml",),
        )
        conn.commit()

    sync_on_pr_merge_artifact_changes(
        db_path,
        repo_full="doria90/dummyAI",
        artifact_snapshots={"prompts/new.txt": "You are a concise assistant."},
        added_paths={"prompts/new.txt"},
        removed_paths={"prompts/original.txt"},
    )

    latest = get_latest_repository_onboarding(db_path, "doria90/dummyAI")
    assert latest is not None
    artifacts = list_onboarded_artifacts_for_onboarding(db_path, latest.id)
    assert {artifact.artifact_path for artifact in artifacts} == {"config/model.yaml", "prompts/new.txt"}


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


def test_manual_repo_artifact_mutations_persist_and_refresh_counts(tmp_path):
    db_path = str(tmp_path / "onboarding-manual.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/system.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: "You are a safe assistant.",
    )

    artifact, baseline = add_repo_artifact_to_onboarding(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        artifact_path="policies/usage.md",
        artifact_type="policy",
        fetch_file_content_fn=lambda repo, path, token, ref: "Human review is required for production-impacting AI changes.",
    )

    assert artifact.artifact_path == "policies/usage.md"
    assert artifact.artifact_type == "policy"
    assert baseline.artifact_type == "policy"
    assert baseline.approval_status == "approved"

    updated = update_repo_artifact_type(
        db_path,
        repo_full="doria90/dummyAI",
        artifact_path="policies/usage.md",
        artifact_type="guardrail",
    )
    assert updated.artifact_type == "guardrail"

    latest = get_latest_repository_onboarding(db_path, "doria90/dummyAI")
    assert latest is not None
    baselines = list_onboarding_baseline_versions_for_onboarding(db_path, latest.id)
    baseline_by_path = {item.artifact_path: item for item in baselines}
    assert baseline_by_path["policies/usage.md"].artifact_type == "guardrail"

    remove_repo_artifact_from_onboarding(
        db_path,
        repo_full="doria90/dummyAI",
        artifact_path="policies/usage.md",
    )

    latest = get_latest_repository_onboarding(db_path, "doria90/dummyAI")
    assert latest is not None
    artifacts = list_onboarded_artifacts_for_onboarding(db_path, latest.id)
    assert {item.artifact_path for item in artifacts} == {"prompts/system.txt"}
    assert latest.discovered_artifact_count == 1


def test_manual_repo_artifact_add_can_infer_type_from_path(tmp_path):
    db_path = str(tmp_path / "onboarding-manual-infer.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/system.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: "You are a safe assistant.",
    )

    artifact, baseline = add_repo_artifact_to_onboarding(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        artifact_path="policies/usage.md",
        artifact_type=None,
        fetch_file_content_fn=lambda repo, path, token, ref: "Human review is required for production-impacting AI changes.",
    )

    assert artifact.artifact_type == "policy"
    assert baseline.artifact_type == "policy"


def test_manual_repo_artifact_add_can_infer_type_from_content_when_path_is_generic(tmp_path):
    db_path = str(tmp_path / "onboarding-manual-content-infer.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["notes/release.md"],
        fetch_file_content_fn=lambda repo, path, token, ref: "Release notes only.",
    )

    artifact, baseline = add_repo_artifact_to_onboarding(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        artifact_path="notes/runtime.md",
        artifact_type=None,
        fetch_file_content_fn=lambda repo, path, token, ref: "model: gpt-4o\ntemperature: 0.2\ntop_p: 0.9",
    )

    assert artifact.artifact_type == "model_config"
    assert baseline.artifact_type == "model_config"


def test_manual_repo_artifact_add_can_upgrade_tool_path_to_ai_code_from_orchestration_content(tmp_path):
    db_path = str(tmp_path / "onboarding-manual-agent-tool-infer.db")
    init_db(db_path)

    onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["notes/release.md"],
        fetch_file_content_fn=lambda repo, path, token, ref: "Release notes only.",
    )

    artifact, baseline = add_repo_artifact_to_onboarding(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        artifact_path="tools/ai_agent_tool.py",
        artifact_type=None,
        fetch_file_content_fn=lambda repo, path, token, ref: "def run_tool():\n    return agent_executor.plan_and_run('refund workflow')",
    )

    assert artifact.artifact_type == "ai_code"
    assert baseline.artifact_type == "ai_code"


def test_execute_repository_history_backfill_persists_historical_lineage(tmp_path):
    db_path = str(tmp_path / "onboarding.db")
    init_db(db_path)

    current_prompt = """# Refund Assistant
You can refund customers directly in production.
Use judgment when deciding whether approval is necessary.
max_steps: 6
temperature: 0.8
"""
    safer_prompt = """# Refund Assistant
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Use the billing sandbox tool in read mode.
max_steps: 2
temperature: 0.2
"""
    medium_prompt = """# Refund Assistant
Refund customers after checking the billing sandbox.
Escalate unusual cases for approval.
max_steps: 4
temperature: 0.4
"""

    result = onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/system.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: current_prompt,
    )

    jobs = plan_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-3", "sha-2", "sha-1"][:limit],
    )

    historical_contents = {
        ("prompts/system.txt", "sha-3"): current_prompt,
        ("prompts/system.txt", "sha-2"): medium_prompt,
        ("prompts/system.txt", "sha-1"): safer_prompt,
    }

    execution_results = execute_repository_history_backfill(
        db_path,
        repo_full="doria90/dummyAI",
        token="token",
        fetch_file_content_fn=lambda repo, path, token, ref: historical_contents[(path, ref)],
    )

    assert len(jobs) == 1
    assert len(execution_results) == 1
    assert execution_results[0].job.status == "completed"
    assert execution_results[0].job.commit_count == 3
    assert execution_results[0].job.completed_commit_count == 2
    assert execution_results[0].job.last_error is None

    versions = list_historical_artifact_versions_for_repo_artifact(db_path, "doria90/dummyAI", "prompts/system.txt")
    profiles = list_historical_static_profiles_for_repo_artifact(db_path, "doria90/dummyAI", "prompts/system.txt")
    persisted_jobs = list_historical_backfill_jobs_for_repo(db_path, "doria90/dummyAI")

    assert len(result.baseline_versions) == 1
    assert len(versions) == 2
    assert len(profiles) == 2
    assert [version.commit_sha for version in versions] == ["sha-1", "sha-2"]
    assert versions[0].previous_version_id is None
    assert versions[1].previous_version_id == versions[0].id

    assert profiles[0].baseline_profile_id is None
    assert profiles[0].baseline_provenance is not None
    assert profiles[0].baseline_provenance.source_type == "approved_baseline"
    assert profiles[0].baseline_provenance.is_authoritative is True
    assert profiles[0].semantic_distance > 0.0
    assert profiles[0].attribute_deltas["capability_risk"] < 0.0
    assert profiles[0].attribute_deltas["guardrail_robustness"] > 0.0

    assert profiles[1].baseline_profile_id is None
    assert profiles[1].baseline_provenance is not None
    assert profiles[1].baseline_provenance.source_type == "approved_baseline"
    assert profiles[1].baseline_provenance.is_authoritative is True
    assert profiles[1].semantic_distance >= 0.0
    assert profiles[1].attribute_deltas["capability_risk"] <= 0.0
    assert profiles[1].attribute_deltas["guardrail_robustness"] > 0.0
    assert profiles[1].narrative

    assert len(persisted_jobs) == 1
    assert persisted_jobs[0].status == "completed"
    assert persisted_jobs[0].completed_commit_count == 2
