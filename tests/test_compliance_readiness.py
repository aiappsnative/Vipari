import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile
from services.audit_jobs import init_db
from services.compliance_readiness import build_compliance_workspace_view
from services.export_jobs import create_export_job, get_export_job, update_export_job_status
from services.onboarding_records import DiscoveredArtifactInput, record_repository_onboarding


def _repo_row(repo_full: str) -> dict[str, object]:
    return {
        "repo_full": repo_full,
        "status": "Onboarded",
        "branch": "main",
        "href": f"/dashboard/{repo_full}",
    }


def _record_onboarding(
    db_path: str,
    *,
    repo_full: str,
    status: str,
    artifact_types: list[str],
) -> None:
    discovered_artifacts = []
    for index, artifact_type in enumerate(artifact_types, start=1):
        discovered_artifacts.append(
            DiscoveredArtifactInput(
                artifact_path=f"artifacts/{index}-{artifact_type}.txt",
                artifact_type=artifact_type,
                discovery_reason=f"seeded {artifact_type} artifact",
                confidence=1.0,
                baseline_content=f"seeded content for {artifact_type}",
            )
        )
    record_repository_onboarding(
        db_path,
        repo_full=repo_full,
        installation_id=100,
        default_branch="main",
        status=status,
        discovered_artifacts=discovered_artifacts,
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )


def test_build_compliance_workspace_view_marks_ready_repo_and_success_verdict(tmp_path):
    db_path = str(tmp_path / "compliance-ready.db")
    init_db(db_path)

    _record_onboarding(
        db_path,
        repo_full="acme/ready-repo",
        status="baseline_approved",
        artifact_types=["prompt", "policy"],
    )

    view = build_compliance_workspace_view(db_path, [_repo_row("acme/ready-repo")], (), ())

    assert len(view.repo_rows) == 1
    repo_row = view.repo_rows[0]
    assert repo_row.overall_label == "Ready"
    assert repo_row.export_ready is True
    assert repo_row.baseline_label == "Approved"
    assert repo_row.governance_label == "Governance evidence present"
    assert repo_row.freshness_label.startswith("Fresh")
    assert view.verdict.tone == "success"
    assert view.verdict.headline == "The monitored repos are ready for export."
    assert view.top_gaps == ()
    assert view.export_summary.ready_repo_count == 1


def test_build_compliance_workspace_view_ranks_top_gaps_by_count_then_priority(tmp_path):
    db_path = str(tmp_path / "compliance-gaps.db")
    init_db(db_path)

    _record_onboarding(
        db_path,
        repo_full="acme/missing-governance-a",
        status="baseline_approved",
        artifact_types=["prompt"],
    )
    _record_onboarding(
        db_path,
        repo_full="acme/missing-governance-b",
        status="baseline_approved",
        artifact_types=["tool"],
    )
    _record_onboarding(
        db_path,
        repo_full="acme/pending-baseline",
        status="pending_baseline_approval",
        artifact_types=["prompt", "policy"],
    )

    repo_rows = [
        _repo_row("acme/missing-governance-a"),
        _repo_row("acme/missing-governance-b"),
        _repo_row("acme/pending-baseline"),
        _repo_row("acme/no-onboarding"),
    ]
    view = build_compliance_workspace_view(db_path, repo_rows, (), ())

    assert [item.key for item in view.top_gaps] == ["missing_governance", "needs_setup", "baseline_review"]
    assert view.top_gaps[0].affected_count == 2
    assert view.verdict.tone == "danger"
    assert view.verdict.headline == "The workspace is not export-ready yet."
    assert view.repo_rows[0].overall_label == "Needs work"
    assert any(row.repo_full == "acme/no-onboarding" and row.overall_label == "Blocked" for row in view.repo_rows)


def test_build_compliance_workspace_view_marks_stale_evidence_and_surfaces_evidence_rows(tmp_path):
    db_path = str(tmp_path / "compliance-stale.db")
    init_db(db_path)

    _record_onboarding(
        db_path,
        repo_full="acme/stale-repo",
        status="baseline_approved",
        artifact_types=["prompt", "policy"],
    )
    stale_timestamp = time.time() - (45 * 86400)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE repository_onboardings SET updated_at = ? WHERE repo_full = ?",
            (stale_timestamp, "acme/stale-repo"),
        )

    view = build_compliance_workspace_view(db_path, [_repo_row("acme/stale-repo")], (), ())

    repo_row = view.repo_rows[0]
    assert repo_row.freshness_label == "Stale (45d)"
    assert repo_row.overall_label == "Needs work"
    assert "stale_evidence" in repo_row.gap_keys
    assert view.evidence_rows[0].summary == "Stored evidence is outside the fresh-review window."
    assert view.top_gaps[0].key == "stale_evidence"


def test_build_compliance_workspace_view_summarizes_export_counts_and_latest_download(tmp_path):
    db_path = str(tmp_path / "compliance-exports.db")
    init_db(db_path)

    _record_onboarding(
        db_path,
        repo_full="acme/ready-repo",
        status="baseline_approved",
        artifact_types=["prompt", "policy"],
    )
    _record_onboarding(
        db_path,
        repo_full="acme/aging-repo",
        status="baseline_approved",
        artifact_types=["prompt", "policy"],
    )
    aging_timestamp = time.time() - (10 * 86400)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE repository_onboardings SET updated_at = ? WHERE repo_full = ?",
            (aging_timestamp, "acme/aging-repo"),
        )

    pending_job = create_export_job(
        db_path=db_path,
        repo_full="acme/ready-repo",
        from_ts=1700000000,
        to_ts=1700086400,
        workspace_id=1,
        requested_by_user_id=10,
        requested_by_github_login="alice",
        export_mode="compliance",
        include_artifact_content=False,
    )
    failed_job = create_export_job(
        db_path=db_path,
        repo_full="acme/aging-repo",
        from_ts=1700000000,
        to_ts=1700086400,
        workspace_id=1,
        requested_by_user_id=10,
        requested_by_github_login="alice",
        export_mode="compliance_plus_drift",
        include_artifact_content=False,
    )
    update_export_job_status(db_path, failed_job.id, "failed", last_error="zip failed")
    completed_job = create_export_job(
        db_path=db_path,
        repo_full="acme/ready-repo",
        from_ts=1700000000,
        to_ts=1700086400,
        workspace_id=1,
        requested_by_user_id=10,
        requested_by_github_login="alice",
        export_mode="compliance",
        include_artifact_content=False,
    )
    update_export_job_status(
        db_path,
        completed_job.id,
        "completed",
        result_size_bytes=18,
        result_sha256="abc123",
        result_blob=b"stored-export-bytes",
    )

    completed_record = get_export_job(db_path, completed_job.id)
    failed_record = get_export_job(db_path, failed_job.id)
    assert completed_record is not None
    assert failed_record is not None

    view = build_compliance_workspace_view(
        db_path,
        [_repo_row("acme/ready-repo"), _repo_row("acme/aging-repo")],
        (),
        (pending_job, failed_record, completed_record),
    )

    assert view.verdict.tone == "warning"
    assert view.verdict.headline == "1 of 2 monitored repos are ready right now."
    assert view.export_summary.ready_repo_count == 1
    assert view.export_summary.completed_count == 1
    assert view.export_summary.pending_count == 1
    assert view.export_summary.failed_count == 1
    assert view.export_summary.latest_status_label == "Completed"
    assert view.export_summary.latest_download_href == f"/api/export/{completed_job.id}/download?token={completed_record.download_token}"