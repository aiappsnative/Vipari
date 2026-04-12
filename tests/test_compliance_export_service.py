import io
import json
import tempfile
import zipfile
from unittest.mock import patch

import pytest

from services.compliance_export_service import (
    ComplianceExportRequest,
    build_compliance_export,
)


class TestComplianceExportService:
    def test_build_compliance_export_basic(self, tmp_path):
        """Test basic export generation with minimal data."""
        import sqlite3

        db_path = str(tmp_path / "test.db")

        # Create a minimal DB
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE repository_onboardings (id INTEGER, repo_full TEXT, installation_id INTEGER, default_branch TEXT, status TEXT, discovered_artifact_count INTEGER, approved_by TEXT, approved_at REAL, created_at REAL, updated_at REAL)")
        conn.execute("INSERT INTO repository_onboardings VALUES (1, 'test/repo', 123, 'main', 'completed', 5, 'user', 1000000, 1000000, 1000000)")
        conn.execute("CREATE TABLE onboarding_baseline_versions (id INTEGER, onboarding_id INTEGER, artifact_path TEXT, artifact_type TEXT, version_hash TEXT, approved_by TEXT, approved_at REAL, approval_note TEXT, approval_source TEXT, approval_status TEXT)")
        conn.execute("CREATE TABLE baseline_audit_log (id INTEGER, onboarding_id INTEGER, artifact_path TEXT, action TEXT, actor_login TEXT, note TEXT, created_at REAL)")
        conn.execute("CREATE TABLE pull_request_audits (id INTEGER, repo_full TEXT, pr_number INTEGER, head_sha TEXT, status TEXT, completion_mode TEXT, deterministic_score INTEGER, suggested_risk_level TEXT, semantic_review_completed INTEGER, error_message TEXT, created_at REAL, updated_at REAL)")
        conn.execute("CREATE TABLE findings (id INTEGER, audit_id INTEGER, source TEXT, rule_id TEXT, title TEXT, severity TEXT, rationale TEXT, created_at REAL)")
        conn.execute("CREATE TABLE repo_posture_snapshots (id INTEGER, snapshot_key TEXT, repo_full TEXT, commit_sha TEXT, pr_number INTEGER, author TEXT, created_at REAL, snapshot_type TEXT, baseline_reference TEXT, default_branch TEXT, source_ref TEXT, source_url TEXT, attribute_vector_json TEXT, artifact_coverage_json TEXT, artifact_state_json TEXT, change_summary_json TEXT, change_breakdown_json TEXT, drift_summary_json TEXT, risk_summary_json TEXT, change_labels_json TEXT, baseline_authority_json TEXT, input_summary_json TEXT, distance_from_baseline REAL, distance_from_previous REAL, materializer_version INTEGER, updated_at REAL)")
        conn.commit()
        conn.close()

        request = ComplianceExportRequest(
            repo_full="test/repo",
            from_ts=0,
            to_ts=2000000000,  # far future
            export_mode="compliance",
            include_artifact_content=False,
        )

        result = build_compliance_export(db_path, request)

        assert result.zip_bytes
        assert result.file_count > 0
        assert result.total_size_bytes > 0

        # Extract and check files
        with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as zf:
            files = zf.namelist()
            assert "README.txt" in files
            assert "manifest.json" in files
            assert "01-baseline-registry.csv" in files
            assert "02-baseline-audit-log.csv" in files
            assert "03-version-history.csv" in files
            assert "04-pr-scan-history.csv" in files
            assert "05-findings.csv" in files
            assert "06-risk-events.csv" in files
            assert "08-control-mapping.md" in files

            # Check manifest
            manifest_data = json.loads(zf.read("manifest.json"))
            assert manifest_data["export_mode"] == "compliance"
            assert manifest_data["repo_full"] == "test/repo"
            assert len(manifest_data["file_hashes"]) == len(files)

    def test_build_compliance_plus_drift_export(self, tmp_path):
        """Test export with drift mode."""
        import sqlite3

        db_path = str(tmp_path / "test.db")

        # Similar setup
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE repository_onboardings (id INTEGER, repo_full TEXT, installation_id INTEGER, default_branch TEXT, status TEXT, discovered_artifact_count INTEGER, approved_by TEXT, approved_at REAL, created_at REAL, updated_at REAL)")
        conn.execute("INSERT INTO repository_onboardings VALUES (1, 'test/repo', 123, 'main', 'completed', 5, 'user', 1000000, 1000000, 1000000)")
        conn.execute("CREATE TABLE onboarding_baseline_versions (id INTEGER, onboarding_id INTEGER, artifact_path TEXT, artifact_type TEXT, version_hash TEXT, approved_by TEXT, approved_at REAL, approval_note TEXT, approval_source TEXT, approval_status TEXT)")
        conn.execute("CREATE TABLE baseline_audit_log (id INTEGER, onboarding_id INTEGER, artifact_path TEXT, action TEXT, actor_login TEXT, note TEXT, created_at REAL)")
        conn.execute("CREATE TABLE pull_request_audits (id INTEGER, repo_full TEXT, pr_number INTEGER, head_sha TEXT, status TEXT, completion_mode TEXT, deterministic_score INTEGER, suggested_risk_level TEXT, semantic_review_completed INTEGER, error_message TEXT, created_at REAL, updated_at REAL)")
        conn.execute("CREATE TABLE findings (id INTEGER, audit_id INTEGER, source TEXT, rule_id TEXT, title TEXT, severity TEXT, rationale TEXT, created_at REAL)")
        conn.execute("CREATE TABLE repo_posture_snapshots (id INTEGER, snapshot_key TEXT, repo_full TEXT, commit_sha TEXT, pr_number INTEGER, author TEXT, created_at REAL, snapshot_type TEXT, baseline_reference TEXT, default_branch TEXT, source_ref TEXT, source_url TEXT, attribute_vector_json TEXT, artifact_coverage_json TEXT, artifact_state_json TEXT, change_summary_json TEXT, change_breakdown_json TEXT, drift_summary_json TEXT, risk_summary_json TEXT, change_labels_json TEXT, baseline_authority_json TEXT, input_summary_json TEXT, distance_from_baseline REAL, distance_from_previous REAL, materializer_version INTEGER, updated_at REAL)")
        conn.commit()
        conn.close()

        request = ComplianceExportRequest(
            repo_full="test/repo",
            from_ts=0,
            to_ts=2000000000,
            export_mode="compliance_plus_drift",
            include_artifact_content=False,
        )

        result = build_compliance_export(db_path, request)

        # Check for drift files
        with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as zf:
            files = zf.namelist()
            assert "07-drift/repo-posture-snapshots.json" in files
            assert "07-drift/artifact-drift-history.csv" in files
            assert "07-drift/drift-leaderboard.csv" in files
            assert "07-drift/posture-summary.json" in files

    def test_empty_date_range(self, tmp_path):
        """Test export with no data in range."""
        import sqlite3

        db_path = str(tmp_path / "test.db")

        # Setup with data outside range
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE repository_onboardings (id INTEGER, repo_full TEXT, installation_id INTEGER, default_branch TEXT, status TEXT, discovered_artifact_count INTEGER, approved_by TEXT, approved_at REAL, created_at REAL, updated_at REAL)")
        conn.execute("INSERT INTO repository_onboardings VALUES (1, 'test/repo', 123, 'main', 'completed', 5, 'user', 1000000, 1000000, 1000000)")
        conn.execute("CREATE TABLE onboarding_baseline_versions (id INTEGER, onboarding_id INTEGER, artifact_path TEXT, artifact_type TEXT, version_hash TEXT, approved_by TEXT, approved_at REAL, approval_note TEXT, approval_source TEXT, approval_status TEXT)")
        conn.execute("CREATE TABLE baseline_audit_log (id INTEGER, onboarding_id INTEGER, artifact_path TEXT, action TEXT, actor_login TEXT, note TEXT, created_at REAL)")
        conn.execute("CREATE TABLE pull_request_audits (id INTEGER, job_id INTEGER, repo_full TEXT, pr_number INTEGER, installation_id INTEGER, head_sha TEXT, pr_state TEXT, pr_merged INTEGER, pr_closed_at REAL, pr_merged_at REAL, pr_merge_commit_sha TEXT, pr_updated_at REAL, status TEXT, completion_mode TEXT, output_mode TEXT, deterministic_score INTEGER, suggested_risk_level TEXT, semantic_review_completed INTEGER, error_message TEXT, created_at REAL, updated_at REAL)")
        conn.execute("INSERT INTO pull_request_audits VALUES (1, 100, 'test/repo', 1, 123, 'abc123', 'closed', 1, 1000000000, 1000000000, 'def456', 1000000000, 'completed', 'full', 'json', 50, 'low', 1, NULL, 1000000000, 1000000000)")  # old date
        conn.execute("CREATE TABLE findings (id INTEGER, audit_id INTEGER, source TEXT, rule_id TEXT, title TEXT, severity TEXT, rationale TEXT, created_at REAL)")
        conn.execute("CREATE TABLE repo_posture_snapshots (id INTEGER, snapshot_key TEXT, repo_full TEXT, commit_sha TEXT, pr_number INTEGER, author TEXT, created_at REAL, snapshot_type TEXT, baseline_reference TEXT, default_branch TEXT, source_ref TEXT, source_url TEXT, attribute_vector_json TEXT, artifact_coverage_json TEXT, artifact_state_json TEXT, change_summary_json TEXT, change_breakdown_json TEXT, drift_summary_json TEXT, risk_summary_json TEXT, change_labels_json TEXT, baseline_authority_json TEXT, input_summary_json TEXT, distance_from_baseline REAL, distance_from_previous REAL, materializer_version INTEGER, updated_at REAL)")
        conn.commit()
        conn.close()

        request = ComplianceExportRequest(
            repo_full="test/repo",
            from_ts=1600000000,  # recent
            to_ts=1700000000,
            export_mode="compliance",
            include_artifact_content=False,
        )

        result = build_compliance_export(db_path, request)

        # Should still generate ZIP with headers
        assert result.zip_bytes
        with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as zf:
            csv_content = zf.read("04-pr-scan-history.csv").decode()
            assert "pr_number" in csv_content  # headers present