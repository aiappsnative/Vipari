import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.audit_records import record_audit_result, get_pull_request_audit_for_job
from services.onboarding import onboard_repository
from services.audit_jobs import init_db
from services.onboarding_records import (
    get_latest_repository_onboarding,
    list_onboarded_artifacts_for_onboarding,
)
from engine.analysis import DiffAnalysis
from types import SimpleNamespace


def make_diff_analysis_with_artifacts(artifacts):
    return SimpleNamespace(deterministic_score=1, suggested_risk_level=SimpleNamespace(value="low"), artifacts=artifacts)


class DummyChange:
    def __init__(self, path, added=0, removed=0):
        self.changed_hunks = 1
        self.added_count = added
        self.removed_count = removed
        self.relevance = SimpleNamespace(path=path, artifact_type="text", context_mode=SimpleNamespace(value="full"), reason="pr-merge")


class DummyArtifact:
    def __init__(self, path, added=0, removed=0, snapshot=None):
        self.relevance = SimpleNamespace(path=path, artifact_type="text", context_mode=SimpleNamespace(value="full"), reason="pr-merge")
        self.change = DummyChange(path, added, removed)
        self.findings = []


def test_pr_merge_syncs_onboarding(tmp_path):
    db_path = str(tmp_path / "project.db")
    init_db(db_path)

    # initial onboarding with a single file
    files = {"prompts/old.txt": "initial content"}
    onboarding = onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: list(files.keys()),
        fetch_file_content_fn=lambda repo, path, token, ref: files[path],
    )

    latest = get_latest_repository_onboarding(db_path, "doria90/dummyAI")
    assert latest is not None
    artifacts_before = list_onboarded_artifacts_for_onboarding(db_path, latest.id)
    assert {a.artifact_path for a in artifacts_before} == {"prompts/old.txt"}

    # simulate a merged PR that adds prompts/new.txt and removes prompts/old.txt
    artifacts = [
        DummyArtifact("prompts/new.txt", added=1, removed=0),
        DummyArtifact("prompts/old.txt", added=0, removed=1),
    ]
    diff = make_diff_analysis_with_artifacts(artifacts)
    # artifact_snapshots provides content for new file
    snapshots = {"prompts/new.txt": "new prompt content"}

    # Best-effort: directly invoke onboarding sync as the webhook/worker would after a merged PR
    from services.onboarding import sync_on_pr_merge_artifact_changes
    sync_on_pr_merge_artifact_changes(db_path, repo_full="doria90/dummyAI", artifact_snapshots=snapshots, added_paths={"prompts/new.txt"}, removed_paths={"prompts/old.txt"})

    latest = get_latest_repository_onboarding(db_path, "doria90/dummyAI")
    artifacts_after = list_onboarded_artifacts_for_onboarding(db_path, latest.id)
    paths = {a.artifact_path for a in artifacts_after}
    assert "prompts/new.txt" in paths
    assert "prompts/old.txt" not in paths
