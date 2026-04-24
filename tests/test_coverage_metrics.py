import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.onboarding import onboard_repository
from services.repo_journey import build_repo_journey
from services.audit_jobs import init_db


def test_dashboard_coverage_fields_present(tmp_path):
    db_path = str(tmp_path / "coverage.db")
    init_db(db_path)

    # create a simple onboarding with one artifact
    files = {"prompts/refund.txt": "baseline content"}
    onboarding = onboard_repository(
        db_path,
        repo_full="doria90/dummyAI",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: list(files.keys()),
        fetch_file_content_fn=lambda repo, path, token, ref: files[path],
    )

    snapshots = build_repo_journey(db_path, "doria90/dummyAI")
    assert snapshots, "Expected snapshots to be materialized"
    for snapshot in snapshots:
        assert "tracked_count" in snapshot.input_summary
        assert "coverage_percent" in snapshot.input_summary
        assert "critical_artifact_count" in snapshot.input_summary
        assert "critical_coverage_percent" in snapshot.input_summary
        # coverage should be 0..100
        cov = snapshot.input_summary["coverage_percent"]
        assert isinstance(cov, float)
        assert 0.0 <= cov <= 100.0
