import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.audit_jobs import init_db
from services.persistence import get_persistence_status


def test_init_db_persists_backend_metadata_and_table_groups(tmp_path):
    db_path = str(tmp_path / "promptdrift.db")

    init_db(db_path)
    status = get_persistence_status(db_path)

    assert status.backend == "sqlite"
    assert status.database_exists is True
    assert status.production_target == "postgresql"
    assert "audit_jobs" in status.operational_tables
    assert "pull_request_audits" in status.durable_tables
    assert "historical_static_profiles" in status.durable_tables

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT backend, schema_version FROM persistence_metadata WHERE id = 1").fetchone()

    assert row == ("sqlite", 1)