import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import Settings
from services.audit_jobs import init_db
from services.entitlements import derive_entitlement_payload, get_plan_definition, resolve_price_id


def test_entitlement_catalog_supports_enterprise_and_business_alias():
    enterprise = get_plan_definition("enterprise")
    business_alias = get_plan_definition("business")

    assert enterprise.code == "enterprise"
    assert business_alias.code == "enterprise"
    assert enterprise.repo_limit == 100


def test_entitlement_payload_disables_dashboard_for_failed_billing():
    payload = derive_entitlement_payload("team", "payment_failed")

    assert payload["plan_code"] == "team"
    assert payload["dashboard_enabled"] is False
    assert payload["repo_limit"] == 20


def test_price_resolution_prefers_enterprise_key_but_supports_legacy_business_key():
    settings = Settings(
        stripe_price_enterprise="price_enterprise_live",
        stripe_price_business="price_business_legacy",
    )

    assert resolve_price_id(settings, "enterprise") == "price_enterprise_live"

    legacy_only = Settings(stripe_price_business="price_business_legacy")
    assert resolve_price_id(legacy_only, "business") == "price_business_legacy"


def test_init_db_creates_control_plane_tables(tmp_path):
    db_path = str(tmp_path / "promptdrift.db")

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "users" in tables
    assert "workspaces" in tables
    assert "subscriptions" in tables
    assert "repo_allocations" in tables
    assert "webhook_event_receipts" in tables


def test_init_db_adds_missing_control_plane_columns_for_existing_tables(tmp_path):
    db_path = str(tmp_path / "promptdrift-legacy.db")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE github_installations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id INTEGER NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                account_login TEXT NOT NULL,
                account_type TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE repo_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id INTEGER NOT NULL,
                repo_github_id TEXT NOT NULL,
                repo_full TEXT NOT NULL,
                default_branch TEXT,
                is_private INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(installation_id, repo_github_id)
            )
            """
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        github_installation_columns = {row[1] for row in conn.execute("PRAGMA table_info(github_installations)").fetchall()}
        repo_connection_columns = {row[1] for row in conn.execute("PRAGMA table_info(repo_connections)").fetchall()}

    assert "workspace_id" in github_installation_columns
    assert "target_type" in github_installation_columns
    assert "status" in github_installation_columns
    assert "workspace_id" in repo_connection_columns
    assert "status" in repo_connection_columns


def test_init_db_rebuilds_legacy_repo_connections_foreign_key(tmp_path):
    db_path = str(tmp_path / "promptdrift-legacy-fk.db")

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TABLE workspaces (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE)")
        conn.execute(
            """
            CREATE TABLE github_installations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER,
                installation_id INTEGER NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                account_login TEXT NOT NULL,
                account_type TEXT NOT NULL,
                target_type TEXT NOT NULL,
                status TEXT NOT NULL,
                last_synced_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE repo_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id INTEGER NOT NULL,
                repo_github_id TEXT NOT NULL,
                repo_full TEXT NOT NULL,
                default_branch TEXT,
                is_private INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'available',
                last_synced_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                workspace_id INTEGER,
                UNIQUE(installation_id, repo_github_id),
                FOREIGN KEY(installation_id) REFERENCES github_installations(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("INSERT INTO workspaces (id, slug) VALUES (1, 'workspace')")
        conn.execute(
            "INSERT INTO github_installations (id, workspace_id, installation_id, account_id, account_login, account_type, target_type, status, last_synced_at, created_at, updated_at) VALUES (1, 1, 12345, 'acct', 'login', 'User', 'User', 'active', 1.0, 1.0, 1.0)"
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO repo_connections (installation_id, workspace_id, repo_github_id, repo_full, default_branch, is_private, status, last_synced_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (12345, 1, 'repo-id', 'owner/repo', 'main', 1, 'available', 1.0, 1.0, 1.0),
        )
        conn.commit()

        foreign_keys = conn.execute("PRAGMA foreign_key_list(repo_connections)").fetchall()

    assert any(foreign_key[2] == 'github_installations' and foreign_key[3] == 'installation_id' and foreign_key[4] == 'installation_id' for foreign_key in foreign_keys)


def test_init_db_rebuilds_legacy_repo_allocations_foreign_key(tmp_path):
    db_path = str(tmp_path / "promptdrift-legacy-allocation-fk.db")

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, github_user_id TEXT NOT NULL UNIQUE, github_login TEXT NOT NULL, display_name TEXT NOT NULL, primary_email TEXT, avatar_url TEXT, granted_scopes_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        conn.execute("CREATE TABLE workspaces (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE)")
        conn.execute(
            """
            CREATE TABLE github_installations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER,
                installation_id INTEGER NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                account_login TEXT NOT NULL,
                account_type TEXT NOT NULL,
                target_type TEXT NOT NULL,
                status TEXT NOT NULL,
                last_synced_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE repo_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                installation_id INTEGER NOT NULL,
                repo_github_id TEXT NOT NULL,
                repo_full TEXT NOT NULL,
                allocation_status TEXT NOT NULL,
                baseline_mode TEXT NOT NULL,
                activated_by_user_id INTEGER,
                activated_at REAL,
                deactivated_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(workspace_id, repo_github_id),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                FOREIGN KEY(installation_id) REFERENCES github_installations(id) ON DELETE CASCADE,
                FOREIGN KEY(activated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute("INSERT INTO users (id, github_user_id, github_login, display_name, primary_email, avatar_url, granted_scopes_json, created_at, updated_at) VALUES (1, 'user-1', 'doria90', 'Doria', NULL, NULL, '[]', 1.0, 1.0)")
        conn.execute("INSERT INTO workspaces (id, slug) VALUES (1, 'workspace')")
        conn.execute(
            "INSERT INTO github_installations (id, workspace_id, installation_id, account_id, account_login, account_type, target_type, status, last_synced_at, created_at, updated_at) VALUES (1, 1, 12345, 'acct', 'login', 'User', 'User', 'active', 1.0, 1.0, 1.0)"
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO repo_allocations (workspace_id, installation_id, repo_github_id, repo_full, allocation_status, baseline_mode, activated_by_user_id, activated_at, deactivated_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 12345, 'repo-id', 'owner/repo', 'active', 'onboarding', 1, 1.0, None, 1.0, 1.0),
        )
        conn.commit()

        foreign_keys = conn.execute("PRAGMA foreign_key_list(repo_allocations)").fetchall()

    assert any(foreign_key[2] == 'github_installations' and foreign_key[3] == 'installation_id' and foreign_key[4] == 'installation_id' for foreign_key in foreign_keys)