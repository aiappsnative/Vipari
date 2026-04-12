import tempfile
import time

import pytest

from services.export_jobs import (
    ExportJob,
    create_export_job,
    get_export_job,
    list_export_jobs_for_repo,
    update_export_job_status,
)


class TestExportJobs:
    def test_create_and_get_export_job(self, tmp_path):
        """Test creating and retrieving an export job."""
        import sqlite3

        db_path = str(tmp_path / "test.db")

        # Init DB
        from services.audit_jobs import init_db
        init_db(db_path)

        repo_full = "test/repo"
        from_ts = time.time() - 86400
        to_ts = time.time()

        job = create_export_job(
            db_path=db_path,
            repo_full=repo_full,
            from_ts=from_ts,
            to_ts=to_ts,
            export_mode="compliance",
            include_artifact_content=False,
        )

        assert job.id > 0
        assert job.repo_full == repo_full
        assert job.from_ts == from_ts
        assert job.to_ts == to_ts
        assert job.export_mode == "compliance"
        assert not job.include_artifact_content
        assert job.status == "queued"
        assert job.download_token

        # Retrieve
        retrieved = get_export_job(db_path, job.id)
        assert retrieved == job

    def test_update_export_job_status(self, tmp_path):
        """Test updating job status."""
        import sqlite3

        db_path = str(tmp_path / "test.db")

        from services.audit_jobs import init_db
        init_db(db_path)

        job = create_export_job(
            db_path=db_path,
            repo_full="test/repo",
            from_ts=time.time() - 86400,
            to_ts=time.time(),
            export_mode="compliance",
            include_artifact_content=False,
        )

        update_export_job_status(
            db_path=db_path,
            job_id=job.id,
            status="completed",
            result_size_bytes=1024,
        )

        updated = get_export_job(db_path, job.id)
        assert updated.status == "completed"
        assert updated.result_size_bytes == 1024
        assert updated.completed_at is not None

    def test_list_export_jobs_for_repo(self, tmp_path):
        """Test listing jobs for a repo."""
        import sqlite3

        db_path = str(tmp_path / "test.db")

        from services.audit_jobs import init_db
        init_db(db_path)

        # Create multiple jobs
        job1 = create_export_job(
            db_path=db_path,
            repo_full="test/repo",
            from_ts=time.time() - 86400,
            to_ts=time.time(),
            export_mode="compliance",
            include_artifact_content=False,
        )

        job2 = create_export_job(
            db_path=db_path,
            repo_full="test/repo",
            from_ts=time.time() - 172800,
            to_ts=time.time() - 86400,
            export_mode="compliance_plus_drift",
            include_artifact_content=True,
        )

        jobs = list_export_jobs_for_repo(db_path, "test/repo")
        assert len(jobs) == 2
        # Should be ordered by created_at desc
        assert jobs[0].id == job2.id  # newer first? Wait, created later
        assert jobs[1].id == job1.id

    def test_list_export_jobs_limit(self, tmp_path):
        """Test listing with limit."""
        import sqlite3

        db_path = str(tmp_path / "test.db")

        from services.audit_jobs import init_db
        init_db(db_path)

        for i in range(5):
            create_export_job(
                db_path=db_path,
                repo_full="test/repo",
                from_ts=time.time() - 86400 * (i + 1),
                to_ts=time.time() - 86400 * i,
                export_mode="compliance",
                include_artifact_content=False,
            )

        jobs = list_export_jobs_for_repo(db_path, "test/repo", limit=3)
        assert len(jobs) == 3