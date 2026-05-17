import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from jwt.exceptions import InvalidKeyError

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient
from fastapi import HTTPException

import main
from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile
from services.billing_service import build_stripe_signature
from services.auth_service import GithubOAuthToken, GithubUserProfile
from services.secure_store import decrypt_text, encrypt_text
from services.persistence import DatabaseRow


class CookieCompatTestClient(TestClient):
    def request(self, method, url, *args, cookies=None, **kwargs):
        if not cookies:
            return super().request(method, url, *args, **kwargs)

        previous = {key: self.cookies.get(key) for key in cookies}
        for key, value in cookies.items():
            self.cookies.set(key, value)
        try:
            return super().request(method, url, *args, **kwargs)
        finally:
            for key, previous_value in previous.items():
                if previous_value is None:
                    self.cookies.pop(key, None)
                else:
                    self.cookies.set(key, previous_value)


client = CookieCompatTestClient(main.app)


@pytest.fixture(autouse=True)
def reset_test_client_cookies():
    client.cookies.clear()
    yield
    client.cookies.clear()


def test_repo_dashboard_mutation_access_rejects_connected_history_repo(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "mutation-access.db")
    main.init_db(main.AUDIT_DB_PATH)

    request = SimpleNamespace()
    workspace = SimpleNamespace(id=7)
    access_context = {"workspace": workspace}
    connection = SimpleNamespace(status="available")
    onboarding = SimpleNamespace(id=11)

    with patch("main._require_dashboard_access", return_value=access_context), patch(
        "main.get_repo_allocation_for_workspace", return_value=None
    ), patch("main.get_repo_connection_for_workspace", return_value=connection), patch(
        "main.get_latest_repository_onboarding", return_value=onboarding
    ):
        with pytest.raises(HTTPException) as exc_info:
            main._require_repo_dashboard_mutation_access(request, "doria90/dummyAI")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Repository is not allocated to this workspace."
    main.AUDIT_DB_PATH = original_db_path


def test_workspace_row_mapper_defaults_feedback_mode_when_column_missing():
    from services.control_plane_records import _row_to_workspace

    row = DatabaseRow(
        ["id", "slug", "display_name", "status", "billing_owner_user_id", "setup_state", "pr_comments_setting_enabled", "created_at", "updated_at"],
        [1, "legacy", "Legacy Workspace", "active", None, "workspace_no_subscription", 1, 1.0, 2.0],
    )

    workspace = _row_to_workspace(row)

    assert workspace.pr_feedback_mode == "comments"


def test_update_workspace_feedback_mode_backfills_legacy_schema_column(tmp_path):
    from services.control_plane_records import _connect, update_workspace_pr_feedback_mode

    db_path = str(tmp_path / "legacy-workspace-feedback.db")
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL,
                billing_owner_user_id INTEGER,
                setup_state TEXT NOT NULL,
                pr_comments_setting_enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO workspaces (slug, display_name, status, billing_owner_user_id, setup_state, pr_comments_setting_enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy", "Legacy Workspace", "active", None, "workspace_no_subscription", 1, 1.0, 1.0),
        )

    workspace = update_workspace_pr_feedback_mode(db_path, 1, pr_feedback_mode="reviews")

    assert workspace.pr_feedback_mode == "reviews"


def test_update_repo_allocation_feedback_mode_backfills_legacy_schema_column(tmp_path):
    from services.control_plane_records import _connect, update_repo_allocation_pr_feedback_mode

    db_path = str(tmp_path / "legacy-allocation-feedback.db")
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL,
                billing_owner_user_id INTEGER,
                setup_state TEXT NOT NULL,
                pr_comments_setting_enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE entitlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                plan_code TEXT NOT NULL,
                subscription_status TEXT NOT NULL,
                dashboard_enabled INTEGER NOT NULL,
                pr_comments_enabled INTEGER NOT NULL,
                repo_limit INTEGER NOT NULL,
                org_limit INTEGER NOT NULL,
                seat_limit INTEGER NOT NULL,
                retention_policy TEXT NOT NULL,
                support_tier TEXT NOT NULL,
                feature_flags_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE github_installations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER,
                installation_id INTEGER NOT NULL,
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
                baseline_mode TEXT NOT NULL DEFAULT 'default_branch',
                activated_by_user_id INTEGER,
                activated_at REAL,
                deactivated_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO workspaces (id, slug, display_name, status, billing_owner_user_id, setup_state, pr_comments_setting_enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "legacy", "Legacy Workspace", "active", None, "workspace_no_subscription", 1, 1.0, 1.0),
        )
        conn.execute(
            "INSERT INTO entitlements (workspace_id, plan_code, subscription_status, dashboard_enabled, pr_comments_enabled, repo_limit, org_limit, seat_limit, retention_policy, support_tier, feature_flags_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "starter", "active", 1, 1, 5, 1, 5, "standard", "email", "{}", 1.0, 1.0),
        )
        conn.execute(
            "INSERT INTO repo_allocations (workspace_id, installation_id, repo_github_id, repo_full, allocation_status, baseline_mode, activated_by_user_id, activated_at, deactivated_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 123, "repo-1", "doria90/dummyAI", "active", "default_branch", None, 1.0, None, 1.0, 1.0),
        )

    allocation = update_repo_allocation_pr_feedback_mode(db_path, 1, pr_feedback_mode="reviews")

    assert allocation.pr_feedback_mode == "reviews"


def test_dashboard_actor_login_uses_authenticated_identity_context():
    request = SimpleNamespace()
    identity = SimpleNamespace(github_login="doria90")

    with patch("main._control_plane_active", return_value=True), patch(
        "main._current_authenticated_identity_context",
        return_value={"identity": identity},
    ):
        actor_login = main._dashboard_actor_login(request)

    assert actor_login == "doria90"


def test_root_redirects_to_login_when_not_signed_in():
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_pricing_page_renders_plan_cards():
    response = client.get("/pricing")

    assert response.status_code == 200
    assert "Starter" in response.text
    assert "Team" in response.text
    assert "Enterprise" in response.text
    assert "Business" in response.text


def test_free_billing_checkout_redirects_active_workspace_to_dashboard(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "billing-active-redirect.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
        update_repo_allocation_status,
    )
    from services.entitlements import derive_entitlement_payload

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1900",
        github_login="billing-active-owner",
        display_name="Billing Active Owner",
        primary_email="billing-active-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="billing-active-workspace",
        display_name="Billing Active Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="billing-active-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="billing-active-csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_active",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload=derive_entitlement_payload("team", "active"),
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="12345",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repo_github_id="doria90/dummyAI",
        repo_full="doria90/dummyAI",
        baseline_mode="default_branch",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")

    response = client.post(
        "/billing/checkout?plan=free",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={"csrf_token": session.csrf_secret},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard")
    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_renders_obscured_shell_when_install_is_missing(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "dashboard-install-missing.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_entitlement, upsert_github_identity, upsert_subscription
    from services.entitlements import derive_entitlement_payload

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1901",
        github_login="billing-install-missing",
        display_name="Billing Install Missing",
        primary_email="billing-install-missing@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="dashboard-install-missing-workspace",
        display_name="Dashboard Install Missing Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="dashboard-install-missing-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="dashboard-install-missing-csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_missing_install",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload=derive_entitlement_payload("team", "active"),
    )

    response = client.get(
        "/dashboard",
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Install Vipari on GitHub" in response.text
    assert "Reinstall or reconnect the GitHub App to continue" in response.text
    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_renders_reconnect_copy_when_installation_was_removed(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "dashboard-install-removed.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_entitlement, upsert_github_identity, upsert_github_installation, upsert_subscription, update_github_installation_status
    from services.entitlements import derive_entitlement_payload

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1902",
        github_login="billing-install-removed",
        display_name="Billing Install Removed",
        primary_email="billing-install-removed@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="dashboard-install-removed-workspace",
        display_name="Dashboard Install Removed Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="dashboard-install-removed-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="dashboard-install-removed-csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_removed_install",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload=derive_entitlement_payload("team", "active"),
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=22222,
        account_id="22222",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    update_github_installation_status(main.AUDIT_DB_PATH, installation_id=22222, status="inactive")

    response = client.get(
        "/dashboard",
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Reconnect Vipari on GitHub" in response.text
    assert "GitHub App access for this workspace was removed" in response.text
    assert "Reconnect GitHub App" in response.text
    main.AUDIT_DB_PATH = original_db_path


def test_install_page_uses_reconnect_copy_for_inactive_installation(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "install-page-reconnect.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_entitlement, upsert_github_identity, upsert_github_installation, upsert_subscription, update_github_installation_status
    from services.entitlements import derive_entitlement_payload

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1903",
        github_login="install-page-reconnect",
        display_name="Install Page Reconnect",
        primary_email="install-page-reconnect@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="install-page-reconnect-workspace",
        display_name="Install Page Reconnect Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="install-page-reconnect-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="install-page-reconnect-csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_install_reconnect",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload=derive_entitlement_payload("team", "active"),
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=33333,
        account_id="33333",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    update_github_installation_status(main.AUDIT_DB_PATH, installation_id=33333, status="inactive")

    response = client.get(
        "/setup/install",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    assert response.status_code == 200
    assert "Reconnect the app to restore dashboard access and automation" in response.text
    assert "Last linked installation doria90 (Organization) is currently inactive." in response.text
    assert "Reconnect GitHub App" in response.text
    main.AUDIT_DB_PATH = original_db_path


def test_repo_setup_page_uses_reconnect_copy_for_inactive_installation(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-setup-reconnect.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_entitlement, upsert_github_identity, upsert_github_installation, upsert_subscription, update_github_installation_status
    from services.entitlements import derive_entitlement_payload

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1904",
        github_login="repo-setup-reconnect",
        display_name="Repo Setup Reconnect",
        primary_email="repo-setup-reconnect@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="repo-setup-reconnect-workspace",
        display_name="Repo Setup Reconnect Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-setup-reconnect-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="repo-setup-reconnect-csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_repo_setup_reconnect",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload=derive_entitlement_payload("team", "active"),
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=44444,
        account_id="44444",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    update_github_installation_status(main.AUDIT_DB_PATH, installation_id=44444, status="inactive")

    response = client.get(
        "/repos",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    assert response.status_code == 200
    assert "Reconnect Vipari to restore repository visibility" in response.text
    assert "Reconnect GitHub App" in response.text
    main.AUDIT_DB_PATH = original_db_path


def test_login_page_renders_github_entry():
    response = client.get("/login")

    assert response.status_code == 200
    assert "Continue with GitHub" in response.text
    assert 'class="sidebar-logo-glyph login-sidebar-logo-glyph"' in response.text


def test_login_page_preserves_handoff_context():
    response = client.get("/login?source=base44&plan=team")

    assert response.status_code == 200
    assert "/auth/github/start?source=base44&amp;plan=team" in response.text
    assert "Resuming the Team plan handoff from base44." in response.text


def test_login_page_explains_missing_oauth_configuration():
    response = client.get("/login?login_error=oauth_not_configured")

    assert response.status_code == 200
    assert "GitHub sign-in is not configured for this deployment yet." in response.text
    assert "/auth/github/start" in response.text
    assert "{{AUTH_ACTION}}" not in response.text


def test_app_page_redirects_to_login_when_unauthenticated():
    response = client.get("/workspace", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_app_page_ignores_preview_state_and_redirects_to_login_when_unauthenticated():
    response = client.get("/workspace?state=payment_failed", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_github_auth_start_redirects_to_provider_when_configured():
    with patch.object(main.settings, "github_oauth_client_id", "client-id"), patch.object(
        main.settings, "github_oauth_client_secret", "client-secret"
    ), patch.object(main.settings, "github_oauth_callback_url", "http://testserver/auth/github/callback"), patch.object(
        main.settings, "app_encryption_key", "very-secret"
    ):
        response = client.get("/auth/github/start", follow_redirects=False)

    assert response.status_code == 302
    assert "github.com/login/oauth/authorize" in response.headers["location"]
    assert "scope=read%3Auser+user%3Aemail+repo+read%3Aorg" in response.headers["location"]
    assert "promptdrift_oauth_state=" in response.headers.get("set-cookie", "")


def test_github_auth_start_short_circuits_when_session_already_exists(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "existing-session.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="125",
        github_login="existing-user",
        display_name="Existing User",
        primary_email="existing@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "user:email", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="existing-session",
        user_id=user.id,
        workspace_id=None,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    response = client.get(
        "/auth/github/start?source=base44&plan=team",
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/workspaces/new?source=base44&plan=team"
    main.AUDIT_DB_PATH = original_db_path


def test_github_auth_start_redirects_to_login_when_oauth_is_not_configured():
    with patch.object(main.settings, "github_oauth_client_id", ""), patch.object(
        main.settings, "github_oauth_client_secret", ""
    ), patch.object(main.settings, "app_encryption_key", "very-secret"):
        response = client.get("/auth/github/start?source=base44&plan=team", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?login_error=oauth_not_configured&source=base44&plan=team"


def test_github_auth_start_redirects_to_login_when_encryption_key_is_missing():
    with patch.object(main.settings, "github_oauth_client_id", "client-id"), patch.object(
        main.settings, "github_oauth_client_secret", "client-secret"
    ), patch.object(main.settings, "app_encryption_key", ""):
        response = client.get("/auth/github/start", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?login_error=encryption_not_configured"


def test_github_auth_callback_creates_session_and_redirects_to_workspace_bootstrap(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "auth.db")
    main.init_db(main.AUDIT_DB_PATH)

    with patch.object(main.settings, "github_oauth_client_id", "client-id"), patch.object(
        main.settings, "github_oauth_client_secret", "client-secret"
    ), patch.object(main.settings, "github_oauth_callback_url", "http://testserver/auth/github/callback"), patch.object(
        main.settings, "app_encryption_key", "very-secret"
    ), patch(
        "main.exchange_code_for_access_token",
        return_value=GithubOAuthToken(access_token="oauth-token", granted_scopes=["read:user"]),
    ), patch(
        "main.fetch_github_user_profile",
        return_value=GithubUserProfile(
            github_user_id="12345",
            login="doria90",
            display_name="Doria",
            email="doria@example.com",
            avatar_url="https://avatars.example.com/u/12345",
        ),
    ):
        start_response = client.get("/auth/github/start?source=base44&plan=team", follow_redirects=False)
        state_cookie = start_response.cookies.get("promptdrift_oauth_state")
        response = client.get(
            f"/auth/github/callback?code=test-code&state={state_cookie}",
            cookies={
                "promptdrift_oauth_state": state_cookie,
                "promptdrift_oauth_context": start_response.cookies.get("promptdrift_oauth_context"),
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/workspaces/new?source=base44&plan=team"
    session_cookie = response.cookies.get(main.settings.session_cookie_name)
    assert session_cookie

    auth_payload = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session_cookie},
    ).json()
    assert auth_payload["authenticated"] is True
    assert auth_payload["session"]["workspace_id"] is None
    assert "session_id" not in auth_payload["session"]
    assert "csrf_secret" not in auth_payload["session"]
    assert "access_token_encrypted" not in auth_payload["identity"]
    assert "refresh_token_encrypted" not in auth_payload["identity"]

    from services.control_plane_records import _connect

    with _connect(main.AUDIT_DB_PATH) as conn:
        identity_row = conn.execute("SELECT access_token_encrypted FROM github_identities WHERE github_user_id = '12345'").fetchone()
    assert identity_row is not None
    assert decrypt_text(identity_row["access_token_encrypted"], "very-secret") == "oauth-token"

    main.AUDIT_DB_PATH = original_db_path


def test_workspace_bootstrap_creates_workspace_and_promotes_session(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "workspace.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="321",
        github_login="owner",
        display_name="Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="session-token",
        user_id=user.id,
        workspace_id=None,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    response = client.post(
        "/workspaces/bootstrap?name=PromptDrift%20Team",
        data={"csrf_token": session.csrf_secret},
        cookies={
            main.settings.session_cookie_name: session.session_id,
            "promptdrift_oauth_context": main._encode_context_cookie(
                {"source": "base44", "plan": "team"},
                binding=main._context_cookie_binding_for_session_id(session.session_id),
            ),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/billing?source=base44&plan=team"

    auth_payload = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session.session_id},
    ).json()
    assert auth_payload["authenticated"] is True
    assert auth_payload["session"]["workspace_id"] is not None
    assert auth_payload["access"]["state"] == "workspace_no_subscription"

    main.AUDIT_DB_PATH = original_db_path


def test_free_checkout_activates_local_entitlement_and_redirects_to_install(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "free-checkout.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        get_workspace_entitlement,
        get_workspace_subscription,
        upsert_github_identity,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="901",
        github_login="free-owner",
        display_name="Free Owner",
        primary_email="free-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="free-workspace",
        display_name="Free Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="free-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    response = client.post(
        "/billing/checkout",
        data={"plan": "free", "csrf_token": session.csrf_secret},
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/setup/install?free_activated=1"

    subscription = get_workspace_subscription(main.AUDIT_DB_PATH, workspace.id)
    entitlement = get_workspace_entitlement(main.AUDIT_DB_PATH, workspace.id)
    assert subscription is not None
    assert subscription.status == "free_active"
    assert entitlement is not None
    assert entitlement.plan_code == "free"
    assert entitlement.pr_comments_enabled is True
    assert entitlement.dashboard_enabled is False

    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_api_rejects_free_tier_workspace(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "free-dashboard.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="902",
        github_login="free-dashboard",
        display_name="Free Dashboard",
        primary_email="free-dashboard@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="free-dashboard-workspace",
        display_name="Free Dashboard Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="free-dashboard-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:free:902",
        stripe_price_id="local:free",
        plan_code="free",
        status="free_active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "free",
            "subscription_status": "free_active",
            "dashboard_enabled": False,
            "pr_comments_enabled": True,
            "repo_limit": 1,
            "org_limit": 1,
            "seat_limit": 1,
            "retention_policy": "basic",
            "support_tier": "community",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9020,
        account_id="9020",
        account_login="free-dashboard-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9020,
        repositories=[
            {
                "repo_github_id": "free-dashboard-org/repo-one",
                "repo_full": "free-dashboard-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9020,
        repo_github_id="free-dashboard-org/repo-one",
        repo_full="free-dashboard-org/repo-one",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")

    response = client.get(
        "/api/dashboard/overview",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    # Free tier now has read-only access to the dashboard
    assert response.status_code == 200
    payload = response.json()
    assert payload is not None

    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_deep_link_renders_shell_for_free_tier_workspace(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "free-dashboard-shell.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="902-shell",
        github_login="free-shell-user",
        display_name="Free Shell User",
        primary_email="free-shell@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="free-shell-workspace",
        display_name="Free Shell Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="free-shell-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf-shell",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:free:shell",
        stripe_price_id="local:free",
        plan_code="free",
        status="free_active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "free",
            "subscription_status": "free_active",
            "dashboard_enabled": False,
            "pr_comments_enabled": True,
            "repo_limit": 1,
            "org_limit": 1,
            "seat_limit": 1,
            "retention_policy": "basic",
            "support_tier": "community",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9021,
        account_id="9021",
        account_login="free-shell-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9021,
        repositories=[
            {
                "repo_github_id": "free-shell-org/repo-one",
                "repo_full": "free-shell-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9021,
        repo_github_id="free-shell-org/repo-one",
        repo_full="free-shell-org/repo-one",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")

    redirect_response = client.get(
        "/dashboard",
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )
    deep_link_response = client.get(
        "/dashboard?pr=42",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    # Free tier can read the main dashboard surface.
    assert redirect_response.status_code == 200
    assert 'class="dashboard-index-page"' in redirect_response.text
    assert 'data-dashboard-shell-state="active"' in redirect_response.text

    # Free tier with deep links also keeps dashboard read access.
    assert deep_link_response.status_code == 200
    assert 'data-dashboard-shell-state="active"' in deep_link_response.text
    assert 'data-dashboard-deep-link-pr="42"' in deep_link_response.text
    assert 'data-dashboard-deep-link-head-sha="abc123456"' in client.get(
        "/dashboard?pr=42&head_sha=abc123456",
        cookies={main.settings.session_cookie_name: session.session_id},
    ).text

    main.AUDIT_DB_PATH = original_db_path


def test_free_tier_blocks_compliance_page_and_repo_reports_tab(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "free-tier-compliance-block.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="9300",
        github_login="free-tier-locked-user",
        display_name="Free Tier Locked User",
        primary_email="free-tier-locked@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="free-tier-locked-workspace",
        display_name="Free Tier Locked Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="free-tier-locked-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf-free-locked",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:free:locked",
        stripe_price_id="local:free",
        plan_code="free",
        status="free_active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "free",
            "subscription_status": "free_active",
            "dashboard_enabled": False,
            "pr_comments_enabled": True,
            "repo_limit": 1,
            "org_limit": 1,
            "seat_limit": 1,
            "retention_policy": "basic",
            "support_tier": "community",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9301,
        account_id="9301",
        account_login="free-tier-locked-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9301,
        repositories=[
            {
                "repo_github_id": "free-tier-locked-org/repo-one",
                "repo_full": "free-tier-locked-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9301,
        repo_github_id="free-tier-locked-org/repo-one",
        repo_full="free-tier-locked-org/repo-one",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")

    cookie = {main.settings.session_cookie_name: session.session_id}
    compliance_response = client.get("/compliance", cookies=cookie)
    repo_reports_response = client.get("/dashboard/free-tier-locked-org/repo-one?tab=reports", cookies=cookie)
    export_submit_response = client.post(
        "/compliance/export",
        cookies=cookie,
        data={
            "export_scope": "all_visible",
            "export_preset": "none",
            "from_date": "2026-01-01",
            "to_date": "2026-01-02",
            "export_mode": "compliance",
            "csrf_token": session.csrf_secret,
        },
        follow_redirects=False,
    )

    assert compliance_response.status_code == 200
    assert 'class="main-content dashboard-shell-blocked"' in compliance_response.text
    assert "the Compliance workspace requires Starter or above" in compliance_response.text
    assert repo_reports_response.status_code == 200
    assert 'data-active-repo-tab="reports"' in repo_reports_response.text
    assert 'class="main-content dashboard-shell-blocked"' in repo_reports_response.text
    assert "the reports tab requires Starter or above" in repo_reports_response.text
    assert export_submit_response.status_code == 303
    assert export_submit_response.headers["location"].startswith("/compliance/exports?status=")

    main.AUDIT_DB_PATH = original_db_path


def test_repo_dashboard_deep_link_renders_onboarding_shell_for_visible_repo(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-onboarding-shell.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="903-shell",
        github_login="paid-shell-user",
        display_name="Paid Shell User",
        primary_email="paid-shell@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="paid-shell-workspace",
        display_name="Paid Shell Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="paid-shell-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf-paid-shell",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:team:shell",
        stripe_price_id="local:team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=time.time() + 86400,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 20,
            "org_limit": 3,
            "seat_limit": 10,
            "retention_policy": "extended",
            "support_tier": "priority",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9031,
        account_id="9031",
        account_login="paid-shell-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9031,
        repositories=[
            {
                "repo_github_id": "doria90/dummyAI",
                "repo_full": "doria90/dummyAI",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9031,
        repo_github_id="doria90/dummyAI",
        repo_full="doria90/dummyAI",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "active")

    response = client.get(
        "/dashboard/doria90/dummyAI?artifact=prompts%2Fpolicy.md&pr=42&head_sha=sha-current",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    assert response.status_code == 200
    assert 'data-dashboard-shell-state="awaiting_repo_onboarding"' in response.text
    assert "doria90/dummyAI has not yet been onboarded" in response.text
    assert 'content="prompts/policy.md"' in response.text
    assert 'content="42"' in response.text
    assert 'content="sha-current"' in response.text
    assert 'class="main-content dashboard-shell-blocked"' in response.text
    assert 'href="/dashboard/doria90%2FdummyAI?tab=drift&artifact=prompts%2Fpolicy.md&pr=42&head_sha=sha-current"' in response.text

    main.AUDIT_DB_PATH = original_db_path


def test_profile_page_requires_dashboard_access(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "profile-free.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity, upsert_entitlement, upsert_subscription

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="930",
        github_login="free-profile-user",
        display_name="Free Profile User",
        primary_email="free-profile@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="free-profile-workspace",
        display_name="Free Profile Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="free-profile-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:free:930",
        stripe_price_id="local:free",
        plan_code="free",
        status="free_active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "free",
            "subscription_status": "free_active",
            "dashboard_enabled": False,
            "pr_comments_enabled": True,
            "repo_limit": 1,
            "org_limit": 1,
            "seat_limit": 1,
            "retention_policy": "basic",
            "support_tier": "community",
            "feature_flags_json": "{}",
        },
    )

    response = client.get("/profile", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 403
    assert response.json()["detail"] == "Profile page is available only for Starter tier and above."

    main.AUDIT_DB_PATH = original_db_path


def test_profile_page_renders_and_updates_display_name(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "profile-paid.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, get_user_by_id, upsert_entitlement, upsert_github_identity, upsert_subscription

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="931",
        github_login="starter-user",
        display_name="Starter User",
        primary_email="starter@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="starter-profile-workspace",
        display_name="Starter Profile Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="starter-profile-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    next_payment = time.time() + 86400
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="base44:subscription:starter-user",
        stripe_price_id="base44:plan:starter",
        plan_code="starter",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=next_payment,
        next_payment_at=next_payment,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "starter",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )

    get_response = client.get("/profile", cookies={main.settings.session_cookie_name: session.session_id})
    assert get_response.status_code == 200
    assert "Starter User" in get_response.text
    assert "starter-user" in get_response.text
    assert "Next payment date" in get_response.text
    assert "Setup checklist" not in get_response.text
    assert "<span class=\"control-page-stat-label\">Plan</span>" in get_response.text
    assert "Permission level" in get_response.text
    assert 'href="/dashboard"' in get_response.text
    assert 'href="/admin"' not in get_response.text
    assert "sidebar" in get_response.text
    assert 'data-theme="dark"' in get_response.text
    assert 'data-theme-toggle' in get_response.text
    assert 'value="dark" checked' in get_response.text

    workspace_response = client.get("/workspace", cookies={main.settings.session_cookie_name: session.session_id}, follow_redirects=False)
    assert workspace_response.status_code == 303
    assert workspace_response.headers["location"] == "/profile"

    post_response = client.post(
        "/profile",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={"display_name": "Updated Starter User", "theme_preference": "light", "csrf_token": session.csrf_secret},
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/profile?updated=1"
    assert get_user_by_id(main.AUDIT_DB_PATH, user.id).display_name == "Updated Starter User"
    assert get_user_by_id(main.AUDIT_DB_PATH, user.id).theme_preference == "light"

    updated_get_response = client.get("/profile", cookies={main.settings.session_cookie_name: session.session_id})
    assert updated_get_response.status_code == 200
    assert 'data-theme="light"' in updated_get_response.text
    assert 'value="light" checked' in updated_get_response.text

    from services.dashboard_frontend import render_dashboard_index_page, render_repo_dashboard_page

    dashboard_html = render_dashboard_index_page(get_user_by_id(main.AUDIT_DB_PATH, user.id).theme_preference)
    assert 'data-theme="light"' in dashboard_html
    assert 'class="dashboard-index-page"' in dashboard_html
    assert "AI Change Overview" in dashboard_html
    assert "Urgent changes to review" in dashboard_html
    assert "Recent changes this week" in dashboard_html
    assert "Change timeline" in dashboard_html
    assert "Posture map" in dashboard_html
    assert "Coverage" in dashboard_html
    assert 'id="governance-attention-headline"' in dashboard_html
    assert 'id="governance-attention-list"' in dashboard_html
    assert 'href="/repos"' in dashboard_html
    assert 'href="/compliance"' in dashboard_html
    assert 'href="/integrations/mcp"' in dashboard_html
    assert 'aria-label="Policies"' in dashboard_html
    assert 'aria-label="Settings"' in dashboard_html
    assert 'aria-label="Audit Logs"' in dashboard_html
    assert 'class="sidebar-profile-link"' in dashboard_html
    assert 'id="journey-repo-name"' in dashboard_html
    assert 'class="journey-stage loading-shell"' in dashboard_html
    assert dashboard_html.index("Urgent changes to review") < dashboard_html.index("Recent changes this week")

    repo_dashboard_html = render_repo_dashboard_page("doria90/hermes-agent", get_user_by_id(main.AUDIT_DB_PATH, user.id).theme_preference)
    assert 'class="repo-audit-page"' in repo_dashboard_html
    assert 'data-theme="light"' in repo_dashboard_html
    assert "Audit Page" in repo_dashboard_html
    assert "Audit Queue" in repo_dashboard_html
    assert "EU AI Act relevance" in repo_dashboard_html
    assert "Governance attention" in repo_dashboard_html
    assert "Loading EU AI Act, SOC 2, and ISO 27001 governance guidance..." in repo_dashboard_html
    assert 'id="repo-governance-attention-details"' in repo_dashboard_html
    assert "Static posture" not in repo_dashboard_html
    assert 'aria-label="Policies"' in repo_dashboard_html
    assert 'aria-label="Settings"' in repo_dashboard_html
    assert 'aria-label="Audit Logs"' in repo_dashboard_html
    assert 'id="audit-logs-toggle"' in repo_dashboard_html
    assert 'id="audit-logs-list" class="sidebar-sublist"' in repo_dashboard_html
    assert 'class="sidebar-nav-item sidebar-nav-item-toggle sidebar-nav-item-active" aria-label="Audit Logs" id="audit-logs-toggle" aria-expanded="true"' in repo_dashboard_html
    assert 'href="/repos"' in repo_dashboard_html
    assert 'href="/compliance"' in repo_dashboard_html
    assert 'href="/integrations/mcp"' in repo_dashboard_html
    assert repo_dashboard_html.index('href="/integrations/mcp" class="sidebar-nav-item" aria-label="Agent Integrations"') < repo_dashboard_html.index('href="/settings" class="sidebar-nav-item" aria-label="Settings"')
    assert "Generate Export Package" not in repo_dashboard_html
    assert "Recent Exports" not in repo_dashboard_html
    assert "Available repositories" not in repo_dashboard_html
    assert "/api/dashboard/overview" not in repo_dashboard_html

    dashboard_css = (Path(__file__).resolve().parent.parent / "static" / "dashboard.css").read_text(encoding="utf-8")
    assert 'body.dashboard-index-page[data-theme="light"]' in dashboard_css
    assert '.dashboard-index-page[data-theme="light"] .dashboard-card' in dashboard_css
    assert '.dashboard-index-page[data-theme="light"] .sidebar' in dashboard_css
    assert '.dashboard-index-page[data-theme="light"] .journey-arrow' in dashboard_css
    assert '.dashboard-index-page[data-theme="light"] .journey-point-baseline .journey-pill' in dashboard_css
    assert 'body.repo-audit-page[data-theme="light"]' in dashboard_css
    assert '.repo-audit-page[data-theme="light"] .detail-panel' in dashboard_css


def test_settings_page_updates_workspace_pr_comments_toggle(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "settings-paid.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        get_workspace_by_id,
        get_repo_allocation_for_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="932",
        github_login="settings-owner",
        display_name="Settings Owner",
        primary_email="settings@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="settings-workspace",
        display_name="Settings Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="settings-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf-settings",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="base44:subscription:settings-owner",
        stripe_price_id="base44:plan:starter",
        plan_code="starter",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "starter",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repositories=[
            {"repo_github_id": "1", "repo_full": "doria90/settings-repo", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )
    allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repo_github_id="1",
        repo_full="doria90/settings-repo",
        baseline_mode="default_branch",
        activated_by_user_id=user.id,
    )

    get_response = client.get("/settings", cookies={main.settings.session_cookie_name: session.session_id})
    assert get_response.status_code == 200
    assert "Workspace settings" in get_response.text
    assert 'value="Settings Workspace"' in get_response.text
    assert "PR feedback mode" in get_response.text
    assert "Effective mode" in get_response.text
    assert "Allowed users and permissions" in get_response.text
    assert 'aria-label="Add user"' in get_response.text
    assert "Onboarded and allocated repositories" in get_response.text
    assert "Inherit workspace default" in get_response.text
    assert "doria90/settings-repo" in get_response.text
    assert 'data-theme-toggle' in get_response.text
    assert 'value="comments" checked' in get_response.text
    assert 'href="/billing"' in get_response.text
    assert "Open billing" in get_response.text
    assert 'aria-label="Settings"' in get_response.text
    assert "Vipari MCP connector" not in get_response.text
    assert "Open Agent Integrations" not in get_response.text
    assert "Open system admin" not in get_response.text
    assert "Setup checklist" not in get_response.text
    assert "{{WORKSPACE_NAME_INPUT}}" not in get_response.text
    assert "{{WORKSPACE_MEMBER_ACTIONS}}" not in get_response.text

    post_response = client.post(
        "/settings",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={
            "workspace_name": "Renamed Settings Workspace",
            "pr_feedback_mode": "off",
            "csrf_token": session.csrf_secret,
        },
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/settings?updated=1"
    updated_workspace = get_workspace_by_id(main.AUDIT_DB_PATH, workspace.id)
    assert updated_workspace.pr_comments_setting_enabled is False
    assert updated_workspace.pr_feedback_mode == "off"
    assert updated_workspace.display_name == "Renamed Settings Workspace"

    allocation = get_repo_allocation_for_workspace(main.AUDIT_DB_PATH, workspace.id, "doria90/settings-repo")
    assert allocation is not None

    repo_override_response = client.post(
        "/settings/repositories/feedback-mode",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={
            "allocation_id": str(allocation.id),
            "pr_feedback_mode": "reviews",
            "csrf_token": session.csrf_secret,
        },
        follow_redirects=False,
    )

    assert repo_override_response.status_code == 303
    assert repo_override_response.headers["location"] == "/settings?updated=1"
    allocation = get_repo_allocation_for_workspace(main.AUDIT_DB_PATH, workspace.id, "doria90/settings-repo")
    assert allocation is not None
    assert allocation.pr_feedback_mode == "reviews"

    updated_get_response = client.get("/settings", cookies={main.settings.session_cookie_name: session.session_id})
    assert updated_get_response.status_code == 200
    assert 'value="off" checked' in updated_get_response.text
    assert 'value="Renamed Settings Workspace"' in updated_get_response.text
    assert "Paused" in updated_get_response.text
    assert 'value="reviews" selected' in updated_get_response.text
    assert "Effective: Reviews." in updated_get_response.text

    reviews_post_response = client.post(
        "/settings",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={
            "workspace_name": "Renamed Settings Workspace",
            "pr_feedback_mode": "reviews",
            "csrf_token": session.csrf_secret,
        },
        follow_redirects=False,
    )

    assert reviews_post_response.status_code == 303
    updated_workspace = get_workspace_by_id(main.AUDIT_DB_PATH, workspace.id)
    assert updated_workspace.pr_feedback_mode == "reviews"
    assert updated_workspace.pr_comments_setting_enabled is True

    reviews_get_response = client.get("/settings", cookies={main.settings.session_cookie_name: session.session_id})
    assert reviews_get_response.status_code == 200
    assert 'value="reviews" checked' in reviews_get_response.text
    assert "Formal reviews" in reviews_get_response.text

    repo_inherit_response = client.post(
        "/settings/repositories/feedback-mode",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={
            "allocation_id": str(allocation.id),
            "pr_feedback_mode": "inherit",
            "csrf_token": session.csrf_secret,
        },
        follow_redirects=False,
    )

    assert repo_inherit_response.status_code == 303
    allocation = get_repo_allocation_for_workspace(main.AUDIT_DB_PATH, workspace.id, "doria90/settings-repo")
    assert allocation is not None
    assert allocation.pr_feedback_mode is None

    main.AUDIT_DB_PATH = original_db_path


def test_settings_page_repo_effective_mode_reflects_plan_gating(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "settings-plan-gating.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
        update_workspace_pr_feedback_mode,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="933",
        github_login="settings-plan-gated-owner",
        display_name="Settings Plan Gated Owner",
        primary_email="settings@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="settings-plan-gated-workspace",
        display_name="Settings Plan Gated Workspace",
        billing_owner_user_id=user.id,
    )
    update_workspace_pr_feedback_mode(main.AUDIT_DB_PATH, workspace.id, pr_feedback_mode="reviews")
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="settings-plan-gated-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf-plan-gated",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="base44:subscription:settings-plan-gated-owner",
        stripe_price_id="base44:plan:starter",
        plan_code="starter",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "starter",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": False,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12346,
        account_id="78",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12346,
        repositories=[
            {"repo_github_id": "2", "repo_full": "doria90/settings-plan-gated-repo", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )
    allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12346,
        repo_github_id="2",
        repo_full="doria90/settings-plan-gated-repo",
        baseline_mode="default_branch",
        activated_by_user_id=user.id,
    )

    response = client.get("/settings", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert "Unavailable" in response.text
    assert "Off (plan gated)" in response.text

    main.AUDIT_DB_PATH = original_db_path


def test_settings_page_can_queue_github_login_invite(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "settings-invite.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        list_workspace_invites_for_workspace,
        upsert_entitlement,
        upsert_github_identity,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="934",
        github_login="invite-owner",
        display_name="Invite Owner",
        primary_email="invite-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="invite-workspace",
        display_name="Invite Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="invite-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf-invite",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="base44:subscription:invite-owner",
        stripe_price_id="base44:plan:starter",
        plan_code="starter",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "starter",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )

    post_response = client.post(
        "/settings/invite",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={"github_login": "@new-teammate", "role": "admin", "csrf_token": session.csrf_secret},
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/settings?invite_added=1"

    invites = list_workspace_invites_for_workspace(main.AUDIT_DB_PATH, workspace.id)
    assert len(invites) == 1
    assert invites[0].invited_github_login == "new-teammate"
    assert invites[0].role == "admin"
    assert invites[0].invitation_state == "pending"

    get_response = client.get("/settings?invite_added=1", cookies={main.settings.session_cookie_name: session.session_id})
    assert get_response.status_code == 200
    assert "Invitation queued." in get_response.text
    assert "new-teammate" in get_response.text
    assert "Pending invite" in get_response.text
    assert 'aria-label="Add user"' in get_response.text
    assert "member-row-pending" in get_response.text

    main.AUDIT_DB_PATH = original_db_path


def test_github_auth_callback_accepts_pending_workspace_invite(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "auth-invite.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        _connect,
        create_workspace,
        list_workspace_memberships_for_user,
        upsert_github_identity,
        upsert_workspace_invite,
    )

    owner, _owner_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="935",
        github_login="invite-workspace-owner",
        display_name="Invite Workspace Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="pending-invite-workspace",
        display_name="Pending Invite Workspace",
        billing_owner_user_id=owner.id,
    )
    upsert_workspace_invite(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        invited_github_login="invited-user",
        role="viewer",
        invited_by_user_id=owner.id,
    )

    with patch.object(main.settings, "github_oauth_client_id", "client-id"), patch.object(
        main.settings, "github_oauth_client_secret", "client-secret"
    ), patch.object(main.settings, "github_oauth_callback_url", "http://testserver/auth/github/callback"), patch.object(
        main.settings, "app_encryption_key", "very-secret"
    ), patch(
        "main.exchange_code_for_access_token",
        return_value=GithubOAuthToken(access_token="oauth-token", granted_scopes=["read:user"]),
    ), patch(
        "main.fetch_github_user_profile",
        return_value=GithubUserProfile(
            github_user_id="936",
            login="invited-user",
            display_name="Invited User",
            email="invited@example.com",
            avatar_url="https://avatars.example.com/u/936",
        ),
    ):
        start_response = client.get("/auth/github/start", follow_redirects=False)
        state_cookie = start_response.cookies.get("promptdrift_oauth_state")
        response = client.get(
            f"/auth/github/callback?code=test-code&state={state_cookie}",
            cookies={
                "promptdrift_oauth_state": state_cookie,
                "promptdrift_oauth_context": start_response.cookies.get("promptdrift_oauth_context"),
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    session_cookie = response.cookies.get(main.settings.session_cookie_name)
    assert session_cookie

    auth_payload = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session_cookie},
    ).json()
    assert auth_payload["authenticated"] is True
    assert auth_payload["session"]["workspace_id"] == workspace.id

    invited_memberships = list_workspace_memberships_for_user(main.AUDIT_DB_PATH, auth_payload["session"]["user_id"])
    assert any(membership.workspace_id == workspace.id and membership.role == "viewer" for membership in invited_memberships)

    with _connect(main.AUDIT_DB_PATH) as conn:
        accepted_invite = conn.execute(
            "SELECT invitation_state, accepted_user_id FROM workspace_invites WHERE workspace_id = ? AND invited_github_login = ?",
            (workspace.id, "invited-user"),
        ).fetchone()

    assert accepted_invite is not None
    assert accepted_invite["invitation_state"] == "accepted"
    assert accepted_invite["accepted_user_id"] == auth_payload["session"]["user_id"]

    main.AUDIT_DB_PATH = original_db_path


def test_github_auth_callback_applies_upgraded_role_for_existing_workspace_member(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "auth-invite-role-upgrade.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        _connect,
        create_workspace,
        get_workspace_membership,
        upsert_github_identity,
        upsert_workspace_invite,
    )

    owner, _owner_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="937",
        github_login="role-upgrade-owner",
        display_name="Role Upgrade Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    invited_user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="938",
        github_login="existing-member",
        display_name="Existing Member",
        primary_email="member@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="role-upgrade-workspace",
        display_name="Role Upgrade Workspace",
        billing_owner_user_id=owner.id,
    )
    with _connect(main.AUDIT_DB_PATH) as conn:
        now = time.time()
        conn.execute(
            "INSERT INTO workspace_memberships (workspace_id, user_id, role, invitation_state, invited_by_user_id, joined_at, created_at, updated_at) VALUES (?, ?, 'viewer', 'accepted', ?, ?, ?, ?)",
            (workspace.id, invited_user.id, owner.id, now, now, now),
        )
    upsert_workspace_invite(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        invited_github_login="existing-member",
        role="admin",
        invited_by_user_id=owner.id,
    )

    with patch.object(main.settings, "github_oauth_client_id", "client-id"), patch.object(
        main.settings, "github_oauth_client_secret", "client-secret"
    ), patch.object(main.settings, "github_oauth_callback_url", "http://testserver/auth/github/callback"), patch.object(
        main.settings, "app_encryption_key", "very-secret"
    ), patch(
        "main.exchange_code_for_access_token",
        return_value=GithubOAuthToken(access_token="oauth-token", granted_scopes=["read:user"]),
    ), patch(
        "main.fetch_github_user_profile",
        return_value=GithubUserProfile(
            github_user_id="938",
            login="existing-member",
            display_name="Existing Member",
            email="member@example.com",
            avatar_url="https://avatars.example.com/u/938",
        ),
    ):
        start_response = client.get("/auth/github/start", follow_redirects=False)
        state_cookie = start_response.cookies.get("promptdrift_oauth_state")
        response = client.get(
            f"/auth/github/callback?code=test-code&state={state_cookie}",
            cookies={
                "promptdrift_oauth_state": state_cookie,
                "promptdrift_oauth_context": start_response.cookies.get("promptdrift_oauth_context"),
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    membership = get_workspace_membership(main.AUDIT_DB_PATH, workspace.id, invited_user.id)
    assert membership is not None
    assert membership.role == "admin"
    assert membership.invitation_state == "accepted"

    main.AUDIT_DB_PATH = original_db_path


def test_help_page_renders_help_center_and_policies_registry_and_classification_flow(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "placeholder-pages.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        get_ai_system_by_id,
        list_ai_systems_for_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.onboarding_records import DiscoveredArtifactInput, record_repository_onboarding

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="933",
        github_login="placeholder-owner",
        display_name="Placeholder Owner",
        primary_email="placeholder@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="placeholder-workspace",
        display_name="Placeholder Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="placeholder-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf-placeholder",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="base44:subscription:placeholder-owner",
        stripe_price_id="base44:plan:starter",
        plan_code="starter",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "starter",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9330,
        account_id="9330",
        account_login="placeholder-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9330,
        repositories=[
            {
                "repo_github_id": "placeholder-org/repo-approved",
                "repo_full": "placeholder-org/repo-approved",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
            {
                "repo_github_id": "placeholder-org/repo-pending",
                "repo_full": "placeholder-org/repo-pending",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="placeholder-org/repo-approved",
        installation_id=9330,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                discovery_reason="Prompt file",
                confidence=0.9,
                baseline_content="Follow the approved flow.",
            )
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="placeholder-org/repo-pending",
        installation_id=9330,
        default_branch="main",
        status="pending_baseline_approval",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="config/model.json",
                artifact_type="model_config",
                discovery_reason="Model config",
                confidence=0.8,
                baseline_content='{"model": "gpt-4o"}',
            )
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9330,
        repo_github_id="placeholder-org/repo-approved",
        repo_full="placeholder-org/repo-approved",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9330,
        repo_github_id="placeholder-org/repo-pending",
        repo_full="placeholder-org/repo-pending",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )

    help_response = client.get("/help", cookies={main.settings.session_cookie_name: session.session_id})
    policies_response = client.get("/policies", cookies={main.settings.session_cookie_name: session.session_id})

    assert help_response.status_code == 200
    assert policies_response.status_code == 200
    assert "Vipari Help Center" in help_response.text
    assert "Visible repos" in help_response.text
    assert "Onboarded repos" in help_response.text
    assert "Baselines approved" in help_response.text
    assert "Review pending baseline" in help_response.text
    assert "placeholder-org/repo-pending" in help_response.text
    assert "Use the platform in this order" in help_response.text
    assert "Setup checklist" in help_response.text
    assert "Workspace readiness" in help_response.text
    assert 'class="checklist-item checklist-item-' in help_response.text
    assert "{{CHECKLIST_ITEMS}}" not in help_response.text
    assert "Connected is not the same as onboarded" in help_response.text
    assert "Submit a support ticket" in help_response.text
    assert "Ticket submission coming soon" in help_response.text
    assert help_response.text.index('href="/integrations/mcp" class="sidebar-nav-item" aria-label="Agent Integrations"') < help_response.text.index('href="/settings" class="sidebar-nav-item" aria-label="Settings"')
    assert 'class="sidebar-nav-item sidebar-nav-item-active" aria-label="Help"' in help_response.text
    assert "Settings" in help_response.text
    assert "AI System Registry" in policies_response.text
    assert 'class="sidebar-nav-item sidebar-nav-item-active" aria-label="Policies"' in policies_response.text
    assert policies_response.text.index('href="/integrations/mcp" class="sidebar-nav-item" aria-label="Agent Integrations"') < policies_response.text.index('href="/settings" class="sidebar-nav-item" aria-label="Settings"')
    assert 'class="sidebar-nav-icon"' in policies_response.text
    assert 'href="/dashboard"' in policies_response.text
    assert 'href="#policies-overview"' in policies_response.text
    assert 'href="#policies-review-queue"' in policies_response.text
    assert 'href="#policies-registry"' in policies_response.text
    assert 'href="#policies-glossary"' in policies_response.text
    assert "Registered systems" in policies_response.text
    assert "Needs review now" in policies_response.text
    assert "2 systems still rely on auto-prefilled registry context and should be confirmed before they are used in compliance decisions." in policies_response.text
    assert "Open repo dashboard" in policies_response.text
    assert "Open compliance workspace view" in policies_response.text
    assert "Baseline evidence is approved, so this system is ready for reviewer confirmation now." in policies_response.text
    assert "EU AI Act risk classification" in policies_response.text
    assert "Minimal risk" in policies_response.text
    assert "High risk" in policies_response.text
    assert "Prohibited" in policies_response.text
    assert 'target="_blank"' in policies_response.text
    assert 'aria-label="Open the official EU AI Act text in a new tab"' in policies_response.text
    assert "Registry entries are derived from repositories already attached to this workspace." not in policies_response.text
    assert "is on the Starter plan" not in policies_response.text
    assert "Reviewer-confirmed" in policies_response.text
    assert "Auto-prefilled" in policies_response.text
    assert "placeholder-org/repo-approved" in policies_response.text
    assert "placeholder-org/repo-pending" in policies_response.text
    assert "Save classification" in policies_response.text
    assert "Auto-prefilled from deterministic repository evidence." in policies_response.text
    assert "We are working on this" not in policies_response.text
    assert "Deterministic evidence first" not in policies_response.text
    assert "LLM assistance stays advisory" not in policies_response.text
    assert 'href="/compliance"' in help_response.text
    assert 'href="/compliance"' in policies_response.text
    assert 'data-theme-toggle' in help_response.text
    assert 'data-theme-toggle' in policies_response.text

    systems = list_ai_systems_for_workspace(main.AUDIT_DB_PATH, workspace.id)
    approved_system = next(system for system in systems if system.repo_full == "placeholder-org/repo-approved")

    update_response = client.post(
        f"/policies/systems/{approved_system.id}",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={
            "csrf_token": session.csrf_secret,
            "risk_level": "high-risk",
            "eu_ai_act_domain": "employment",
            "purpose_summary": "Assists hiring reviewers with prompt-based triage.",
        },
        follow_redirects=False,
    )

    assert update_response.status_code == 303
    assert update_response.headers["location"] == "/policies?classification_saved=1"

    updated_system = get_ai_system_by_id(main.AUDIT_DB_PATH, approved_system.id)
    assert updated_system is not None
    assert updated_system.risk_level == "high-risk"
    assert updated_system.eu_ai_act_domain == "employment"
    assert updated_system.purpose_summary == "Assists hiring reviewers with prompt-based triage."

    refreshed_policies_response = client.get(
        "/policies?classification_saved=1",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    assert refreshed_policies_response.status_code == 200
    assert "AI system classification saved" in refreshed_policies_response.text
    assert "1 system still relies on auto-prefilled registry context and should be confirmed before it is used in compliance decisions." in refreshed_policies_response.text
    assert "High Risk" in refreshed_policies_response.text or "High risk" in refreshed_policies_response.text
    assert "Reviewer-confirmed classification stored in the workspace registry." in refreshed_policies_response.text
    assert "Reviewer-confirmed" in refreshed_policies_response.text
    assert "Auto-prefilled" in refreshed_policies_response.text

    invalid_update_response = client.post(
        f"/policies/systems/{approved_system.id}",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={
            "csrf_token": session.csrf_secret,
            "risk_level": "totally-invalid",
            "eu_ai_act_domain": "employment",
            "purpose_summary": "Assists hiring reviewers with prompt-based triage.",
        },
        follow_redirects=False,
    )

    assert invalid_update_response.status_code == 400
    assert invalid_update_response.json()["detail"] == "Choose a valid risk classification."

    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_requires_explicit_owner_identity(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    main.AUDIT_DB_PATH = str(tmp_path / "admin-guard.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = ""
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""

    from services.control_plane_records import create_user_session, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="940",
        github_login="not-admin",
        display_name="Not Admin",
        primary_email="not-admin@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="not-admin-session",
        user_id=user.id,
        workspace_id=None,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    response = client.get("/admin", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 403
    assert response.json()["detail"] == "System owner access is not enabled for this GitHub identity."

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.AUDIT_DB_PATH = original_db_path


def test_profile_page_shows_admin_link_for_local_billing_owner_without_owner_config(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    original_app_env = main.settings.app_env
    main.AUDIT_DB_PATH = str(tmp_path / "profile-local-owner.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = ""
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""
    main.settings.app_env = "local"

    from services.control_plane_records import create_user_session, create_workspace, upsert_entitlement, upsert_github_identity, upsert_subscription, upsert_workspace_membership

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="975",
        github_login="doria90",
        display_name="Doria",
        primary_email="doria@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "user:email", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        billing_owner_user_id=user.id,
        display_name="Local Owner Workspace",
        slug="local-owner-workspace",
    )
    upsert_workspace_membership(main.AUDIT_DB_PATH, workspace_id=workspace.id, user_id=user.id, role="owner", invitation_state="accepted")
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_local_owner",
        stripe_price_id="price_starter",
        plan_code="starter",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=time.time() + 86400,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "starter",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="local-owner-profile-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    response = client.get("/profile", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert 'href="/admin"' in response.text
    assert "Open system admin" in response.text

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_denies_billing_owner_fallback_outside_local_env(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    original_app_env = main.settings.app_env
    main.AUDIT_DB_PATH = str(tmp_path / "admin-test-owner.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = ""
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""
    main.settings.app_env = "test"

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity, upsert_workspace_membership

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="977",
        github_login="doria90",
        display_name="Doria",
        primary_email="doria@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "user:email", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        billing_owner_user_id=user.id,
        display_name="Test Owner Workspace",
        slug="test-owner-workspace",
    )
    upsert_workspace_membership(main.AUDIT_DB_PATH, workspace_id=workspace.id, user_id=user.id, role="owner", invitation_state="accepted")
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="test-owner-admin-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    response = client.get("/admin", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 403
    assert response.json()["detail"] == "System owner access is not enabled for this GitHub identity."

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_help_shows_admin_link_for_local_billing_owner_without_owner_config(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    original_app_env = main.settings.app_env
    original_app_base_url = main.settings.app_base_url
    main.AUDIT_DB_PATH = str(tmp_path / "local-owner-nav.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = ""
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""
    main.settings.app_env = "local"
    main.settings.app_base_url = "http://127.0.0.1:8011"

    from services.control_plane_records import create_user_session, create_workspace, upsert_entitlement, upsert_github_identity, upsert_subscription, upsert_workspace_membership

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="976",
        github_login="doria90",
        display_name="Doria",
        primary_email="doria@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "user:email", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        billing_owner_user_id=user.id,
        display_name="Local Owner Workspace",
        slug="local-owner-nav-workspace",
    )
    upsert_workspace_membership(main.AUDIT_DB_PATH, workspace_id=workspace.id, user_id=user.id, role="owner", invitation_state="accepted")
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_local_owner_nav",
        stripe_price_id="price_starter",
        plan_code="starter",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=time.time() + 86400,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "starter",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="local-owner-nav-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    cookies = {main.settings.session_cookie_name: session.session_id}
    settings_response = client.get("/settings", cookies=cookies)
    help_response = client.get("/help", cookies=cookies)
    policies_response = client.get("/policies", cookies=cookies)

    assert settings_response.status_code == 200
    assert help_response.status_code == 200
    assert policies_response.status_code == 200
    assert 'href="/admin"' not in settings_response.text
    assert 'href="/admin"' in help_response.text
    assert 'href="/admin"' not in policies_response.text

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.settings.app_env = original_app_env
    main.settings.app_base_url = original_app_base_url
    main.AUDIT_DB_PATH = original_db_path


def test_github_auth_start_reauths_when_existing_session_scopes_are_stale(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "auth-reauth-stale-scopes.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="126",
        github_login="stale-user",
        display_name="Stale User",
        primary_email="stale@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "user:email"],
        access_token_encrypted="encrypted-token",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="stale-scope-session",
        user_id=user.id,
        workspace_id=None,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    with patch.object(main.settings, "github_oauth_client_id", "client-id"), patch.object(
        main.settings, "github_oauth_client_secret", "client-secret"
    ), patch.object(main.settings, "github_oauth_callback_url", "http://testserver/auth/github/callback"), patch.object(
        main.settings, "app_encryption_key", "very-secret"
    ):
        response = client.get(
            "/auth/github/start",
            cookies={main.settings.session_cookie_name: session.session_id},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "github.com/login/oauth/authorize" in response.headers["location"]

    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_renders_registered_and_unclaimed_install_data(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    main.AUDIT_DB_PATH = str(tmp_path / "admin-data.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""

    from services.control_plane_records import (
        create_billing_handoff_claim,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    admin_user, _admin_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="950",
        github_login="admin-user",
        display_name="Admin User",
        primary_email="admin@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    admin_session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="admin-session",
        user_id=admin_user.id,
        workspace_id=None,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    free_user, _free_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="951",
        github_login="free-installed-user",
        display_name="Free Installed User",
        primary_email="free-installed@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    free_workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="free-installed-workspace",
        display_name="Free Installed Workspace",
        billing_owner_user_id=free_user.id,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=free_workspace.id,
        stripe_subscription_id="local:free:951",
        stripe_price_id="local:free",
        plan_code="free",
        status="free_active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=free_workspace.id,
        payload={
            "plan_code": "free",
            "subscription_status": "free_active",
            "dashboard_enabled": False,
            "pr_comments_enabled": True,
            "repo_limit": 1,
            "org_limit": 1,
            "seat_limit": 1,
            "retention_policy": "basic",
            "support_tier": "community",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=free_workspace.id,
        installation_id=9510,
        account_id="9510",
        account_login="free-install-org",
        account_type="Organization",
        target_type="Organization",
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=free_workspace.id,
        installation_id=9511,
        account_id="9511",
        account_login="free-install-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=free_workspace.id,
        installation_id=9510,
        repositories=[
            {
                "repo_github_id": "free-install-org/repo-one",
                "repo_full": "free-install-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
            {
                "repo_github_id": "free-install-org/repo-two",
                "repo_full": "free-install-org/repo-two",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )

    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=None,
        installation_id=9520,
        account_id="9520",
        account_login="marketplace-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=None,
        installation_id=9520,
        repositories=[
            {
                "repo_github_id": "marketplace-org/repo-one",
                "repo_full": "marketplace-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    create_billing_handoff_claim(
        main.AUDIT_DB_PATH,
        claim_token="claim-admin-1",
        provider="base44",
        external_purchase_id="purchase-admin-1",
        plan_code="starter",
        billing_status="active",
        billing_email="buyer@example.com",
        source="base44",
        next_payment_at=time.time() + 604800,
        expires_at=time.time() + 604800,
    )

    response = client.get("/admin", cookies={main.settings.session_cookie_name: admin_session.session_id})

    assert response.status_code == 200
    assert "Control-plane oversight" in response.text
    assert "Aggregated workspace accounts" in response.text
    assert "Add user" in response.text
    assert "Free Installed User" in response.text
    assert "Free Installed Workspace" in response.text
    assert "Free" in response.text
    assert "Installs 1" in response.text
    assert "Connected 2" in response.text
    assert "Onboarded 0" in response.text
    assert "marketplace-org" in response.text
    assert "purchase-admin-1" in response.text

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_renders_github_profile_details(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    main.AUDIT_DB_PATH = str(tmp_path / "admin-profile-data.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""

    from services.control_plane_records import create_user_session, upsert_github_identity

    admin_user, _admin_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="970",
        github_login="admin-user",
        display_name="Admin User",
        primary_email="admin@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "user:email"],
        access_token_encrypted="encrypted-token",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="admin-profile-session",
        user_id=admin_user.id,
        workspace_id=None,
        csrf_secret="csrf-profile",
        expires_at=time.time() + 3600,
    )
    upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="971",
        github_login="profile-user",
        display_name="Profile User",
        primary_email="profile@example.com",
        avatar_url="https://avatars.example.com/u/971",
        profile_url="https://github.com/profile-user",
        company="PromptDrift",
        blog="https://example.com",
        location="Berlin",
        bio="Builds review pipelines.",
        twitter_username="profile_user",
        granted_scopes=["read:user", "user:email"],
        access_token_encrypted="encrypted-token",
    )

    response = client.get("/admin", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert "profile@example.com" in response.text
    assert "PromptDrift" in response.text
    assert "Berlin" in response.text
    assert "Builds review pipelines." in response.text
    assert "profile_user" in response.text
    assert "https://github.com/profile-user" in response.text
    assert "Recent admin activity" in response.text

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.AUDIT_DB_PATH = original_db_path


def test_admin_logs_tab_renders_unified_activity_feed(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    main.AUDIT_DB_PATH = str(tmp_path / "admin-logs.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""

    from services.control_plane_records import create_control_plane_audit_log, create_user_session, create_workspace, record_webhook_event, upsert_github_identity

    admin_user, _admin_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="972",
        github_login="admin-user",
        display_name="Admin User",
        primary_email="admin@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        billing_owner_user_id=admin_user.id,
        display_name="Logs Workspace",
        slug="logs-workspace",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="admin-logs-session",
        user_id=admin_user.id,
        workspace_id=None,
        csrf_secret="csrf-logs",
        expires_at=time.time() + 3600,
    )

    create_control_plane_audit_log(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        actor_user_id=admin_user.id,
        event_type="admin_workspace_created",
        subject_type="workspace",
        subject_id=str(workspace.id),
        payload={"display_name": workspace.display_name},
    )
    record_webhook_event(
        main.AUDIT_DB_PATH,
        provider="github",
        event_id="evt-admin-logs",
        event_type="installation",
        status="processed",
    )

    response = client.get("/admin?tab=logs", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert "Unified logs" in response.text
    assert "All event types" in response.text
    assert "admin_workspace_created" in response.text
    assert "Installation" in response.text or "installation" in response.text
    assert "Logs Workspace" in response.text
    assert "Admin User" in response.text
    assert "matching log rows" in response.text

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.AUDIT_DB_PATH = original_db_path


def test_admin_logs_tab_applies_query_filters(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    main.AUDIT_DB_PATH = str(tmp_path / "admin-logs-filter.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""

    from services.control_plane_records import create_control_plane_audit_log, create_user_session, create_workspace, upsert_github_identity

    admin_user, _admin_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="973",
        github_login="admin-user",
        display_name="Admin User",
        primary_email="admin@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        billing_owner_user_id=admin_user.id,
        display_name="Filter Workspace",
        slug="filter-workspace",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="admin-logs-filter-session",
        user_id=admin_user.id,
        workspace_id=None,
        csrf_secret="csrf-logs-filter",
        expires_at=time.time() + 3600,
    )

    create_control_plane_audit_log(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        actor_user_id=admin_user.id,
        event_type="admin_workspace_created",
        subject_type="workspace",
        subject_id=str(workspace.id),
        payload={"display_name": "Filter Workspace"},
    )
    create_control_plane_audit_log(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        actor_user_id=admin_user.id,
        event_type="admin_user_created",
        subject_type="user",
        subject_id="42",
        payload={"display_name": "Hidden User"},
    )

    response = client.get(
        "/admin?tab=logs&query=Hidden+User",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    assert response.status_code == 200
    assert "Hidden User" in response.text
    assert "admin_user_created" in response.text
    assert "1 matching log rows" in response.text

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.AUDIT_DB_PATH = original_db_path


def test_admin_logs_tab_reads_configured_activity_database(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_activity_db_path = main.settings.activity_db_path
    original_activity_database_url = main.settings.activity_database_url
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    main.AUDIT_DB_PATH = str(tmp_path / "admin-logs-activity-primary.db")
    activity_db_path = str(tmp_path / "admin-logs-activity.db")
    try:
        main.init_db(main.AUDIT_DB_PATH)
        main.settings.activity_database_url = ""
        main.settings.activity_db_path = activity_db_path
        main.settings.owner_github_login = "admin-user"
        main.settings.owner_github_user_id = ""
        main.settings.owner_email = ""

        from services.activity_records import create_activity_event
        from services.activity_schema_migrations import migrate_activity_database
        from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity

        migrate_activity_database(activity_db_path)

        admin_user, _admin_identity = upsert_github_identity(
            main.AUDIT_DB_PATH,
            github_user_id="974",
            github_login="admin-user",
            display_name="Admin User",
            primary_email="admin@example.com",
            avatar_url=None,
            granted_scopes=["read:user"],
            access_token_encrypted="encrypted-token",
        )
        workspace = create_workspace(
            main.AUDIT_DB_PATH,
            billing_owner_user_id=admin_user.id,
            display_name="Activity Workspace",
            slug="activity-workspace",
        )
        session = create_user_session(
            main.AUDIT_DB_PATH,
            session_id="admin-logs-activity-session",
            user_id=admin_user.id,
            workspace_id=None,
            csrf_secret="csrf-logs-activity",
            expires_at=time.time() + 3600,
        )
        create_activity_event(
            activity_db_path,
            occurred_at=time.time(),
            source="control_plane",
            event_type="activity.only",
            workspace_id=workspace.id,
            actor_user_id=admin_user.id,
            actor_label="Admin User",
            repo_full=None,
            subject_type="workspace",
            subject_id=str(workspace.id),
            details={"display_name": workspace.display_name},
        )

        response = client.get("/admin?tab=logs", cookies={main.settings.session_cookie_name: session.session_id})

        assert response.status_code == 200
        assert "activity.only" in response.text
        assert "Activity Workspace" in response.text
        assert "Admin User" in response.text
    finally:
        main.settings.owner_github_login = original_login
        main.settings.owner_github_user_id = original_id
        main.settings.owner_email = original_email
        main.settings.activity_db_path = original_activity_db_path
        main.settings.activity_database_url = original_activity_database_url
        main.AUDIT_DB_PATH = original_db_path


def test_admin_logs_tab_preserves_primary_history_when_activity_db_is_enabled(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_activity_db_path = main.settings.activity_db_path
    original_activity_database_url = main.settings.activity_database_url
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    main.AUDIT_DB_PATH = str(tmp_path / "admin-logs-history-primary.db")
    activity_db_path = str(tmp_path / "admin-logs-history-activity.db")

    try:
        main.init_db(main.AUDIT_DB_PATH)
        main.settings.owner_github_login = "admin-user"
        main.settings.owner_github_user_id = ""
        main.settings.owner_email = ""

        from services.activity_schema_migrations import migrate_activity_database
        from services.control_plane_records import create_control_plane_audit_log, create_user_session, create_workspace, upsert_github_identity

        admin_user, _admin_identity = upsert_github_identity(
            main.AUDIT_DB_PATH,
            github_user_id="975",
            github_login="admin-user",
            display_name="Admin User",
            primary_email="admin@example.com",
            avatar_url=None,
            granted_scopes=["read:user"],
            access_token_encrypted="encrypted-token",
        )
        workspace = create_workspace(
            main.AUDIT_DB_PATH,
            billing_owner_user_id=admin_user.id,
            display_name="History Workspace",
            slug="history-workspace",
        )
        session = create_user_session(
            main.AUDIT_DB_PATH,
            session_id="admin-logs-history-session",
            user_id=admin_user.id,
            workspace_id=None,
            csrf_secret="csrf-logs-history",
            expires_at=time.time() + 3600,
        )

        main.settings.activity_database_url = ""
        main.settings.activity_db_path = ""
        create_control_plane_audit_log(
            main.AUDIT_DB_PATH,
            workspace_id=workspace.id,
            actor_user_id=admin_user.id,
            event_type="admin_history_only",
            subject_type="workspace",
            subject_id=str(workspace.id),
            payload={"display_name": workspace.display_name},
        )

        main.settings.activity_database_url = ""
        main.settings.activity_db_path = activity_db_path
        migrate_activity_database(activity_db_path)
        create_control_plane_audit_log(
            main.AUDIT_DB_PATH,
            workspace_id=workspace.id,
            actor_user_id=admin_user.id,
            event_type="admin_mirrored",
            subject_type="workspace",
            subject_id=str(workspace.id),
            payload={"display_name": workspace.display_name},
        )

        response = client.get("/admin?tab=logs", cookies={main.settings.session_cookie_name: session.session_id})

        assert response.status_code == 200
        assert "admin_history_only" in response.text
        assert "admin_mirrored" in response.text
        assert "2 matching log rows" in response.text
    finally:
        main.settings.owner_github_login = original_login
        main.settings.owner_github_user_id = original_id
        main.settings.owner_email = original_email
        main.settings.activity_db_path = original_activity_db_path
        main.settings.activity_database_url = original_activity_database_url
        main.AUDIT_DB_PATH = original_db_path


def test_admin_page_can_create_update_and_delete_users_workspaces_and_memberships(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    main.AUDIT_DB_PATH = str(tmp_path / "admin-crud.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""

    from services.control_plane_records import (
        create_user_session,
        get_user_by_id,
        get_workspace_by_id,
        get_workspace_entitlement,
        get_workspace_membership,
        get_workspace_subscription,
        list_recent_control_plane_audit_logs,
        list_admin_workspace_users,
        upsert_github_identity,
    )

    admin_user, _admin_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="980",
        github_login="admin-user",
        display_name="Admin User",
        primary_email="admin@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="admin-crud-session",
        user_id=admin_user.id,
        workspace_id=None,
        csrf_secret="csrf-admin",
        expires_at=time.time() + 3600,
    )
    cookie = {main.settings.session_cookie_name: session.session_id}

    create_user_response = client.post(
        "/admin/users/create",
        data={"display_name": "Managed User", "primary_email": "managed@example.com", "csrf_token": session.csrf_secret},
        cookies=cookie,
        follow_redirects=False,
    )
    assert create_user_response.status_code == 303

    managed_user_row = next(row for row in list_admin_workspace_users(main.AUDIT_DB_PATH) if row.primary_email == "managed@example.com")
    managed_user_id = managed_user_row.user_id

    create_workspace_response = client.post(
        "/admin/workspaces/create",
        data={
            "display_name": "Managed Workspace",
            "slug": "managed-workspace",
            "billing_owner_user_id": str(managed_user_id),
            "csrf_token": session.csrf_secret,
        },
        cookies=cookie,
        follow_redirects=False,
    )
    assert create_workspace_response.status_code == 303

    workspace_row = next(row for row in list_admin_workspace_users(main.AUDIT_DB_PATH) if row.workspace_slug == "managed-workspace")
    workspace_id = int(workspace_row.workspace_id or 0)
    assert workspace_id

    membership_response = client.post(
        "/admin/memberships/upsert",
        data={
            "workspace_id": str(workspace_id),
            "user_id": str(admin_user.id),
            "role": "admin",
            "csrf_token": session.csrf_secret,
        },
        cookies=cookie,
        follow_redirects=False,
    )
    assert membership_response.status_code == 303
    membership = get_workspace_membership(main.AUDIT_DB_PATH, workspace_id, admin_user.id)
    assert membership is not None
    assert membership.role == "admin"

    update_user_response = client.post(
        f"/admin/users/{managed_user_id}/update",
        data={
            "display_name": "Managed User Updated",
            "primary_email": "managed.updated@example.com",
            "csrf_token": session.csrf_secret,
        },
        cookies=cookie,
        follow_redirects=False,
    )
    assert update_user_response.status_code == 303
    updated_user = get_user_by_id(main.AUDIT_DB_PATH, managed_user_id)
    assert updated_user is not None
    assert updated_user.display_name == "Managed User Updated"
    assert updated_user.primary_email == "managed.updated@example.com"
    assert updated_user.active is False

    update_workspace_response = client.post(
        f"/admin/workspaces/{workspace_id}/update",
        data={
            "display_name": "Managed Workspace Updated",
            "slug": "managed-workspace-updated",
            "plan_code": "team",
            "csrf_token": session.csrf_secret,
        },
        cookies=cookie,
        follow_redirects=False,
    )
    assert update_workspace_response.status_code == 303
    updated_workspace = get_workspace_by_id(main.AUDIT_DB_PATH, workspace_id)
    updated_subscription = get_workspace_subscription(main.AUDIT_DB_PATH, workspace_id)
    updated_entitlement = get_workspace_entitlement(main.AUDIT_DB_PATH, workspace_id)
    assert updated_workspace is not None
    assert updated_workspace.display_name == "Managed Workspace Updated"
    assert updated_workspace.slug == "managed-workspace-updated"
    assert updated_subscription is not None
    assert updated_subscription.plan_code == "team"
    assert updated_entitlement is not None
    assert updated_entitlement.plan_code == "team"
    assert updated_entitlement.dashboard_enabled is True

    delete_membership_response = client.post(
        f"/admin/memberships/{workspace_id}/{admin_user.id}/delete",
        data={"csrf_token": session.csrf_secret},
        cookies=cookie,
        follow_redirects=False,
    )
    assert delete_membership_response.status_code == 303
    assert get_workspace_membership(main.AUDIT_DB_PATH, workspace_id, admin_user.id) is None

    delete_workspace_response = client.post(
        f"/admin/workspaces/{workspace_id}/delete",
        data={"csrf_token": session.csrf_secret},
        cookies=cookie,
        follow_redirects=False,
    )
    assert delete_workspace_response.status_code == 303
    assert get_workspace_by_id(main.AUDIT_DB_PATH, workspace_id) is None

    delete_user_response = client.post(
        f"/admin/users/{managed_user_id}/delete",
        data={"csrf_token": session.csrf_secret},
        cookies=cookie,
        follow_redirects=False,
    )
    assert delete_user_response.status_code == 303
    assert get_user_by_id(main.AUDIT_DB_PATH, managed_user_id) is None

    audit_events = [entry.event_type for entry in list_recent_control_plane_audit_logs(main.AUDIT_DB_PATH, limit=10)]
    assert "admin_user_created" in audit_events
    assert "admin_workspace_created" in audit_events
    assert "admin_membership_saved" in audit_events
    assert "admin_user_deleted" in audit_events

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_delete_forms_include_confirmation_prompts(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    main.AUDIT_DB_PATH = str(tmp_path / "admin-confirm.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity

    admin_user, _admin_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="990",
        github_login="admin-user",
        display_name="Admin User",
        primary_email="admin@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    managed_user, _managed_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="991",
        github_login="managed-user",
        display_name="Managed User",
        primary_email="managed@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    create_workspace(
        main.AUDIT_DB_PATH,
        slug="confirm-workspace",
        display_name="Confirm Workspace",
        billing_owner_user_id=managed_user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="admin-confirm-session",
        user_id=admin_user.id,
        workspace_id=None,
        csrf_secret="csrf-confirm",
        expires_at=time.time() + 3600,
    )

    response = client.get("/admin", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert "Delete this user and any linked workspace memberships?" in response.text
    assert "Delete this workspace and all linked records?" in response.text
    assert "Remove this user from the workspace?" in response.text

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.AUDIT_DB_PATH = original_db_path


def test_base44_handoff_creates_claim_url(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_secret = main.settings.billing_handoff_secret
    main.AUDIT_DB_PATH = str(tmp_path / "base44-handoff.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.billing_handoff_secret = "shared-secret"

    payload = {
        "provider": "base44",
        "external_purchase_id": "purchase-123",
        "plan_code": "starter",
        "billing_status": "active",
        "billing_email": "buyer@example.com",
        "source": "base44",
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = main.hmac.new(b"shared-secret", raw_body, main.hashlib.sha256).hexdigest()

    response = client.post(
        "/api/billing/handoff/base44",
        content=raw_body,
        headers={"X-DriftGuard-Signature": signature, "content-type": "application/json"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["claim_token"]
    assert "/claim/" in body["claim_url"]

    main.settings.billing_handoff_secret = original_secret
    main.AUDIT_DB_PATH = original_db_path


def test_base44_handoff_requires_billing_email(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_secret = main.settings.billing_handoff_secret
    main.AUDIT_DB_PATH = str(tmp_path / "base44-handoff-missing-email.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.billing_handoff_secret = "shared-secret"

    payload = {
        "provider": "base44",
        "external_purchase_id": "purchase-456",
        "plan_code": "starter",
        "billing_status": "active",
        "source": "base44",
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = main.hmac.new(b"shared-secret", raw_body, main.hashlib.sha256).hexdigest()

    response = client.post(
        "/api/billing/handoff/base44",
        content=raw_body,
        headers={"X-DriftGuard-Signature": signature, "content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Billing handoff payload must include a billing email."

    main.settings.billing_handoff_secret = original_secret
    main.AUDIT_DB_PATH = original_db_path


def test_billing_claim_rejects_workspace_user_with_mismatched_email(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "billing-claim-email-guard.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_billing_handoff_claim, create_user_session, create_workspace, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="955",
        github_login="wrong-buyer",
        display_name="Wrong Buyer",
        primary_email="wrong@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="claim-guard-workspace",
        display_name="Claim Guard Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="claim-guard-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    create_billing_handoff_claim(
        main.AUDIT_DB_PATH,
        claim_token="claim-email-guard-1",
        provider="base44",
        external_purchase_id="purchase-email-guard-1",
        plan_code="starter",
        billing_status="active",
        billing_email="buyer@example.com",
        source="base44",
        next_payment_at=time.time() + 604800,
        expires_at=time.time() + 604800,
    )

    response = client.get(
        "/billing/claim?claim=claim-email-guard-1",
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Billing handoff claim does not belong to this user."

    main.AUDIT_DB_PATH = original_db_path


def test_webhook_ignores_unallocated_repo(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "webhook-unallocated.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_workspace,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1201",
        github_login="webhook-owner",
        display_name="Webhook Owner",
        primary_email="webhook-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="webhook-workspace",
        display_name="Webhook Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:webhook:starter",
        stripe_price_id="local:starter",
        plan_code="starter",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "starter",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=1234,
        account_id="1234",
        account_login="example-org",
        account_type="Organization",
        target_type="Organization",
    )

    payload = {
        "action": "opened",
        "installation": {"id": 1234},
        "repository": {"full_name": "example/repo"},
        "pull_request": {
            "number": 7,
            "state": "open",
            "merged": False,
            "head": {"sha": "headsha"},
            "base": {"sha": "basesha"},
            "updated_at": "2025-01-01T00:00:00Z",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + main.hmac.new(main.GITHUB_WEBHOOK_SECRET.encode(), raw_body, main.hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook",
        content=raw_body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": signature, "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "ignored: repo not allocated"

    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_requires_session_when_control_plane_is_active(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = str(tmp_path / "gated.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.local_debug_disable_login = False

    from services.control_plane_records import create_workspace, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="700",
        github_login="owner",
        display_name="Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    create_workspace(
        main.AUDIT_DB_PATH,
        slug="gated-workspace",
        display_name="Gated Workspace",
        billing_owner_user_id=user.id,
    )

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_allows_local_operator_mode_when_control_plane_is_inactive(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_app_env = main.settings.app_env
    main.AUDIT_DB_PATH = str(tmp_path / "local-operator.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.app_env = "local"

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200

    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_api_requires_session_when_control_plane_is_inactive(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_app_env = main.settings.app_env
    main.AUDIT_DB_PATH = str(tmp_path / "inactive-dashboard-api.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.app_env = "local"

    overview_response = client.get("/api/dashboard/overview")
    repos_response = client.get("/api/repos")

    assert overview_response.status_code == 401
    assert overview_response.json()["detail"] == "Authentication required."
    assert repos_response.status_code == 401
    assert repos_response.json()["detail"] == "Authentication required."

    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_requires_session_in_production_even_without_workspaces(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_app_env = main.settings.app_env
    main.AUDIT_DB_PATH = str(tmp_path / "production-gated.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.app_env = "production"

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_requires_session_in_test_env_without_workspaces(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_app_env = main.settings.app_env
    main.AUDIT_DB_PATH = str(tmp_path / "test-gated.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.app_env = "test"

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_requires_session_when_local_env_uses_non_local_base_url(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_app_env = main.settings.app_env
    original_app_base_url = main.settings.app_base_url
    main.AUDIT_DB_PATH = str(tmp_path / "remote-local.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.app_env = "local"
    main.settings.app_base_url = "https://driftguard.example.com"

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    main.settings.app_base_url = original_app_base_url
    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_dashboard_local_debug_disable_login_unlocks_dashboard_reads(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_app_env = main.settings.app_env
    original_app_base_url = main.settings.app_base_url
    original_service_role = main.settings.service_role
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = str(tmp_path / "dashboard-local-debug.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.app_env = "local"
    main.settings.app_base_url = "http://127.0.0.1:8011"
    main.settings.service_role = "monolith"
    main.settings.local_debug_disable_login = True

    from services.control_plane_records import create_workspace, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="702",
        github_login="debug-owner",
        display_name="Debug Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    create_workspace(
        main.AUDIT_DB_PATH,
        slug="debug-workspace",
        display_name="Debug Workspace",
        billing_owner_user_id=user.id,
    )

    with TestClient(main.app) as local_client:
        dashboard_response = local_client.get("/dashboard", follow_redirects=False)
        overview_response = local_client.get("/api/dashboard/overview")

    assert dashboard_response.status_code == 200
    assert "Vipari" in dashboard_response.text
    assert overview_response.status_code == 200
    assert "attention_repos" in overview_response.json()

    main.settings.service_role = original_service_role
    main.settings.local_debug_disable_login = original_local_debug_disable_login
    main.settings.app_base_url = original_app_base_url
    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_local_debug_does_not_unlock_app_profile_without_session(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_app_env = main.settings.app_env
    original_app_base_url = main.settings.app_base_url
    original_service_role = main.settings.service_role
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = str(tmp_path / "profile-local-debug.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.app_env = "local"
    main.settings.app_base_url = "http://127.0.0.1:8011"
    main.settings.service_role = "monolith"
    main.settings.local_debug_disable_login = True

    from services.control_plane_records import create_workspace, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="703",
        github_login="profile-debug-owner",
        display_name="Profile Debug Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    create_workspace(
        main.AUDIT_DB_PATH,
        slug="profile-debug-workspace",
        display_name="Profile Debug Workspace",
        billing_owner_user_id=user.id,
    )

    with TestClient(main.app) as local_client:
        response = local_client.get("/profile")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."

    main.settings.local_debug_disable_login = original_local_debug_disable_login
    main.settings.service_role = original_service_role
    main.settings.app_base_url = original_app_base_url
    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_local_debug_does_not_activate_for_api_service_dashboard(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_app_env = main.settings.app_env
    original_app_base_url = main.settings.app_base_url
    original_service_role = main.settings.service_role
    original_local_debug_disable_login = main.settings.local_debug_disable_login
    main.AUDIT_DB_PATH = str(tmp_path / "dashboard-local-debug-api.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.app_env = "local"
    main.settings.app_base_url = "http://127.0.0.1:8011"
    main.settings.service_role = "api"
    main.settings.local_debug_disable_login = True

    from services.control_plane_records import create_workspace, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="704",
        github_login="api-debug-owner",
        display_name="API Debug Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    create_workspace(
        main.AUDIT_DB_PATH,
        slug="api-debug-workspace",
        display_name="API Debug Workspace",
        billing_owner_user_id=user.id,
    )

    with TestClient(main.app) as local_client:
        response = local_client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    main.settings.local_debug_disable_login = original_local_debug_disable_login
    main.settings.service_role = original_service_role
    main.settings.app_base_url = original_app_base_url
    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_persistence_api_requires_dashboard_access_when_control_plane_is_active(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "persistence-guard.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_workspace, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="701",
        github_login="persistence-owner",
        display_name="Persistence Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    create_workspace(
        main.AUDIT_DB_PATH,
        slug="persistence-workspace",
        display_name="Persistence Workspace",
        billing_owner_user_id=user.id,
    )

    response = client.get("/api/persistence")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."

    main.AUDIT_DB_PATH = original_db_path


def test_billing_install_allocation_flow_unlocks_dashboard(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "control-plane-flow.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        get_billing_customer_for_workspace,
        get_workspace_subscription,
        upsert_github_identity,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="800",
        github_login="workspace-owner",
        display_name="Workspace Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="promptdrift-team",
        display_name="PromptDrift Team",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="workspace-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    with patch.object(main.settings, "stripe_webhook_secret", "whsec_test"):
        billing_page_response = client.get(
            "/billing?plan=team&source=base44",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        assert billing_page_response.status_code == 200
        assert "Billing" in billing_page_response.text
        assert "Start with GitHub" in billing_page_response.text
        assert "Most popular" in billing_page_response.text
        assert "Talk to us" in billing_page_response.text
        assert 'aria-label="Repositories"' in billing_page_response.text
        assert 'aria-label="Agent Integrations"' in billing_page_response.text
        assert "Manage billing" in billing_page_response.text
        assert "Selected plan" in billing_page_response.text
        assert "Team" in billing_page_response.text
        assert "Ready to start" in billing_page_response.text
        assert "{{SELECTED_PLAN_LABEL}}" not in billing_page_response.text
        assert "{{CHECKOUT_STATE_LABEL}}" not in billing_page_response.text

        checkout_response = client.post(
            "/billing/checkout?plan=team",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={"csrf_token": session.csrf_secret},
            follow_redirects=False,
        )

        assert checkout_response.status_code == 303
        assert "/billing?checkout_session_id=" in checkout_response.headers["location"]

        access_after_checkout = client.get(
            "/api/auth/session",
            cookies={main.settings.session_cookie_name: session.session_id},
        ).json()
        assert access_after_checkout["access"]["state"] == "billing_pending_confirmation"

        customer = get_billing_customer_for_workspace(main.AUDIT_DB_PATH, workspace.id)
        subscription = get_workspace_subscription(main.AUDIT_DB_PATH, workspace.id)
        assert customer is not None
        assert subscription is not None

        stripe_event = {
            "id": "evt_subscription_active",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": subscription.stripe_subscription_id,
                    "customer": customer.stripe_customer_id,
                    "status": "active",
                    "cancel_at_period_end": False,
                    "current_period_start": int(time.time()),
                    "current_period_end": int(time.time()) + 86400,
                    "metadata": {
                        "workspace_id": str(workspace.id),
                        "plan_code": "team",
                        "price_id": "price_team",
                        "billing_email": "owner@example.com",
                    },
                }
            },
        }
        stripe_payload = json.dumps(stripe_event).encode("utf-8")
        stripe_signature = build_stripe_signature(stripe_payload, "whsec_test")
        with TestClient(main.app, raise_server_exceptions=False) as non_raising_client:
            webhook_response = non_raising_client.post(
                "/webhooks/stripe",
                content=stripe_payload,
                headers={"Stripe-Signature": stripe_signature, "Content-Type": "application/json"},
            )

    assert webhook_response.status_code == 200
    assert webhook_response.json()["status"] == "processed"

    access_after_billing = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session.session_id},
    ).json()
    assert access_after_billing["access"]["state"] == "awaiting_github_install"

    install_page_response = client.get(
        "/setup/install",
        cookies={main.settings.session_cookie_name: session.session_id},
    )
    assert install_page_response.status_code == 200
    assert "/setup/install/callback" in install_page_response.text

    install_response = client.post(
        "/setup/install/link",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={
            "csrf_token": session.csrf_secret,
            "installation_id": "12345",
            "account_login": "doria90",
            "account_type": "Organization",
            "repo_fulls": "doria90/dummyAI",
        },
        follow_redirects=False,
    )

    assert install_response.status_code == 303
    assert install_response.headers["location"] == "/repos"

    access_after_install = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session.session_id},
    ).json()
    assert access_after_install["access"]["state"] == "awaiting_repo_onboarding"

    with patch.object(main.settings, "github_app_id", "app-id"), patch.object(
        main.settings, "github_app_private_key", "dummy-private-key"
    ), patch("main.generate_jwt", return_value="jwt"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.onboard_repository", return_value=None):
        allocate_response = client.post(
            "/repos/allocate?repo_full=doria90/dummyAI",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={"csrf_token": session.csrf_secret},
            follow_redirects=False,
        )

    assert allocate_response.status_code == 303
    assert allocate_response.headers["location"] == "/workspace"

    access_after_allocation = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session.session_id},
    ).json()
    assert access_after_allocation["access"]["state"] == "active"

    dashboard_response = client.get(
        "/dashboard",
        cookies={main.settings.session_cookie_name: session.session_id},
    )
    assert dashboard_response.status_code == 200
    assert "Urgent changes to review" in dashboard_response.text
    assert "Posture map" in dashboard_response.text
    assert 'href="/repos"' in dashboard_response.text

    main.AUDIT_DB_PATH = original_db_path
    
def test_billing_page_renders_pricing_cards_and_plan_actions(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "billing-page-render.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="1800",
        github_login="billing-owner",
        display_name="Billing Owner",
        primary_email="billing-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="billing-render-workspace",
        display_name="Billing Render Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="billing-render-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf-billing-render",
        expires_at=time.time() + 3600,
    )

    response = client.get(
        "/billing?plan=starter&source=base44",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    assert response.status_code == 200
    assert response.text.count('<article class="billing-tier-card') == 4
    assert 'class="billing-tier-card billing-tier-card-featured billing-tier-card-current"' in response.text
    assert "Free forever" in response.text
    assert "$40" in response.text
    assert "$150" in response.text
    assert "Custom" in response.text
    assert "Limited automated PR comments on detected AI drift" in response.text
    assert "Unlimited PR drift comments" in response.text
    assert "Governance coverage views" in response.text
    assert "SSO &amp; enterprise features" in response.text
    assert response.text.count("Start with GitHub") == 3
    assert response.text.count("Talk to us") == 1
    assert response.text.count("billing-tier-button\" disabled") == 1
    assert response.text.count("billing-tier-button billing-tier-button-primary\" disabled") == 1
    assert 'action="/billing/checkout?plan=free&source=base44" class="billing-tier-form">\n                    <input type="hidden" name="csrf_token" value="csrf-billing-render" />\n                    <button type="submit" class="button billing-tier-button">Start with GitHub</button>' in response.text
    assert 'action="/billing/checkout?plan=free&source=base44"' in response.text
    assert 'action="/billing/checkout?plan=starter&source=base44"' in response.text
    assert 'action="/billing/checkout?plan=team&source=base44"' in response.text
    assert 'action="/billing/checkout?plan=enterprise&source=base44"' in response.text
    assert 'href="/settings" class="sidebar-nav-item sidebar-nav-item-active" aria-label="Settings"' in response.text
    assert response.text.index('href="/integrations/mcp" class="sidebar-nav-item" aria-label="Agent Integrations"') < response.text.index('href="/settings" class="sidebar-nav-item sidebar-nav-item-active" aria-label="Settings"')
    assert "Portal unavailable" in response.text

    main.AUDIT_DB_PATH = original_db_path


def test_stripe_webhook_rejects_workspace_metadata_mismatch(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "stripe-mismatch.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        get_billing_customer_for_workspace,
        get_workspace_subscription,
        upsert_github_identity,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="801",
        github_login="workspace-owner",
        display_name="Workspace Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="workspace-a",
        display_name="Workspace A",
        billing_owner_user_id=user.id,
    )
    other_workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="workspace-b",
        display_name="Workspace B",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="workspace-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    with patch.object(main.settings, "stripe_webhook_secret", "whsec_test"):
        checkout_response = client.post(
            "/billing/checkout?plan=team",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={"csrf_token": session.csrf_secret},
            follow_redirects=False,
        )

        assert checkout_response.status_code == 303

        customer = get_billing_customer_for_workspace(main.AUDIT_DB_PATH, workspace.id)
        subscription = get_workspace_subscription(main.AUDIT_DB_PATH, workspace.id)
        assert customer is not None
        assert subscription is not None

        stripe_event = {
            "id": "evt_workspace_mismatch",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": subscription.stripe_subscription_id,
                    "customer": customer.stripe_customer_id,
                    "status": "active",
                    "cancel_at_period_end": False,
                    "current_period_start": int(time.time()),
                    "current_period_end": int(time.time()) + 86400,
                    "metadata": {
                        "workspace_id": str(other_workspace.id),
                        "plan_code": "team",
                        "price_id": "price_team",
                        "billing_email": "owner@example.com",
                    },
                }
            },
        }
        stripe_payload = json.dumps(stripe_event).encode("utf-8")
        stripe_signature = build_stripe_signature(stripe_payload, "whsec_test")
        with TestClient(main.app, raise_server_exceptions=False) as non_raising_client:
            webhook_response = non_raising_client.post(
                "/webhooks/stripe",
                content=stripe_payload,
                headers={"Stripe-Signature": stripe_signature, "Content-Type": "application/json"},
            )

    assert webhook_response.status_code == 500
    assert get_workspace_subscription(main.AUDIT_DB_PATH, other_workspace.id) is None

    main.AUDIT_DB_PATH = original_db_path


def test_workspace_viewer_cannot_mutate_billing_or_repo_setup(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "viewer-role.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import _connect, create_user_session, create_workspace, upsert_github_identity

    owner, _owner_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="910",
        github_login="owner-user",
        display_name="Owner User",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    viewer, _viewer_identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="911",
        github_login="viewer-user",
        display_name="Viewer User",
        primary_email="viewer@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="viewer-locked-workspace",
        display_name="Viewer Locked Workspace",
        billing_owner_user_id=owner.id,
    )
    with _connect(main.AUDIT_DB_PATH) as conn:
        now = time.time()
        conn.execute(
            "INSERT INTO workspace_memberships (workspace_id, user_id, role, invitation_state, invited_by_user_id, joined_at, created_at, updated_at) VALUES (?, ?, 'viewer', 'accepted', ?, ?, ?, ?)",
            (workspace.id, viewer.id, owner.id, now, now, now),
        )
    viewer_session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="viewer-session",
        user_id=viewer.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    billing_response = client.post(
        "/billing/checkout?plan=team",
        cookies={main.settings.session_cookie_name: viewer_session.session_id},
        data={"csrf_token": viewer_session.csrf_secret},
    )
    install_response = client.post(
        "/setup/install/link",
        cookies={main.settings.session_cookie_name: viewer_session.session_id},
        data={
            "csrf_token": viewer_session.csrf_secret,
            "installation_id": "12345",
            "account_login": "doria90",
            "account_type": "Organization",
            "repo_fulls": "doria90/dummyAI",
        },
    )

    assert billing_response.status_code == 403
    assert install_response.status_code == 403

    main.AUDIT_DB_PATH = original_db_path


def test_install_callback_links_workspace_and_redirects_to_repo_setup(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "install-callback.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="820",
        github_login="install-owner",
        display_name="Install Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="install-callback-workspace",
        display_name="Install Callback Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="install-callback-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    install_state_cookie = main._encode_context_cookie(
        {"nonce": "install-state", "workspace_id": workspace.id},
        binding=main._context_cookie_binding_for_session_id(session.session_id),
    )

    with patch.object(main.settings, "github_app_id", "app-id"), patch.object(
        main.settings, "github_app_private_key", "dummy-private-key"
    ), patch(
        "main.sync_installation_repositories",
        return_value=(
            {"target_type": "Organization", "account": {"login": "doria90", "type": "Organization", "id": 77}},
            [{"repo_github_id": "1", "repo_full": "doria90/dummyAI", "default_branch": "main", "is_private": True, "status": "available"}],
        ),
    ):
        response = client.get(
            "/setup/install/callback?installation_id=12345&setup_action=install&state=install-state",
            cookies={
                main.settings.session_cookie_name: session.session_id,
                "promptdrift_install_state": install_state_cookie,
            },
            follow_redirects=False,
        )

        repo_setup_response = client.get(
            "/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/repos?installation_linked=1&setup_action=install"

    auth_payload = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session.session_id},
    ).json()
    assert auth_payload["access"]["state"] == "workspace_no_subscription"

    assert repo_setup_response.status_code == 200
    assert "doria90/dummyAI" in repo_setup_response.text
    assert 'repo-setup-page' in repo_setup_response.text
    assert "Repository inventory" in repo_setup_response.text
    assert "5 of 5 repository slots available on this plan." in repo_setup_response.text
    assert "Onboarded Repository Snapshot" in repo_setup_response.text
    assert 'class="repo-setup-inventory-list"' in repo_setup_response.text
    assert 'data-repo-summary-sort' in repo_setup_response.text
    assert 'href="/dashboard"' in repo_setup_response.text
    assert 'href="/repos"' in repo_setup_response.text
    assert "Allocate and onboard" in repo_setup_response.text

    main.AUDIT_DB_PATH = original_db_path


def test_repo_setup_treats_connected_history_repo_as_not_yet_onboarded(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-connected-history.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, replace_repo_connections, upsert_github_identity, upsert_github_installation
    from services.dashboard_views import RepoDashboardIndexEntry

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="840",
        github_login="repo-owner",
        display_name="Repo Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="repo-connected-history-workspace",
        display_name="Repo Connected History Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-connected-history-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repositories=[
            {"repo_github_id": "1", "repo_full": "doria90/dummyAI", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )

    with patch(
        "main.list_repo_dashboard_index",
        return_value=[
            RepoDashboardIndexEntry(
                "doria90/dummyAI",
                "main",
                "baseline_approved",
                5,
                time.time(),
                dashboard_scope="connected_history",
                allocation_status=None,
            )
        ],
    ):
        response = client.get(
            "/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    assert "Allocate and onboard" in response.text
    assert "Open audit" not in response.text
    assert "Onboarding active" not in response.text

    main.AUDIT_DB_PATH = original_db_path


def test_repo_setup_install_action_uses_install_start_route(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-install-route.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="841",
        github_login="install-route-owner",
        display_name="Install Route Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="repo-install-route-workspace",
        display_name="Repo Install Route Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-install-route-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    with patch("main._github_account_repo_inventory", return_value=[{"repo_full": "doria90/another-repo", "is_connected": False, "is_allocated": False, "is_onboarded": False}]):
        response = client.get(
            "/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    assert 'href="/setup/install/start"' in response.text
    assert "settings/installations" not in response.text

    main.AUDIT_DB_PATH = original_db_path


def test_repo_setup_disables_new_install_when_repo_limit_is_reached(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-install-limit.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="843",
        github_login="install-limit-owner",
        display_name="Install Limit Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="repo-install-limit-workspace",
        display_name="Repo Install Limit Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-install-limit-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:free:limit",
        stripe_price_id="local:free",
        plan_code="free",
        status="free_active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id="test-free-limit",
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "free",
            "subscription_status": "free_active",
            "dashboard_enabled": False,
            "pr_comments_enabled": True,
            "repo_limit": 1,
            "org_limit": 1,
            "seat_limit": 1,
            "retention_policy": "standard",
            "support_tier": "community",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repositories=[
            {"repo_github_id": "1", "repo_full": "doria90/current-repo", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )
    allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repo_github_id="1",
        repo_full="doria90/current-repo",
        baseline_mode="onboarding",
        activated_by_user_id=owner.id,
    )

    with patch(
        "main._github_account_repo_inventory",
        return_value=[
            {"repo_full": "doria90/current-repo", "is_connected": True, "is_allocated": True, "is_onboarded": False},
            {"repo_full": "doria90/another-repo", "is_connected": False, "is_allocated": False, "is_onboarded": False},
        ],
    ):
        response = client.get(
            "/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert "0 of 1 repository slots available on this plan." in response.text
    assert "Upgrade to add repo" in response.text
    assert 'href="/billing?plan=starter"' in response.text
    assert 'data-upgrade-required="repo-limit"' in response.text
    assert "Install app" not in response.text


def test_repo_disconnect_frees_slot_and_exposes_restore_action(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-disconnect.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        get_repo_allocation_for_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.dashboard_views import RepoDashboardIndexEntry

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="844",
        github_login="disconnect-owner",
        display_name="Disconnect Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="repo-disconnect-workspace",
        display_name="Repo Disconnect Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-disconnect-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:free:disconnect",
        stripe_price_id="local:free",
        plan_code="free",
        status="free_active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id="test-disconnect",
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "free",
            "subscription_status": "free_active",
            "dashboard_enabled": False,
            "pr_comments_enabled": True,
            "repo_limit": 1,
            "org_limit": 1,
            "seat_limit": 1,
            "retention_policy": "standard",
            "support_tier": "community",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repositories=[
            {"repo_github_id": "1", "repo_full": "doria90/current-repo", "default_branch": "main", "is_private": True, "status": "available"},
            {"repo_github_id": "2", "repo_full": "doria90/next-repo", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repo_github_id="1",
        repo_full="doria90/current-repo",
        baseline_mode="onboarding",
        activated_by_user_id=owner.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")
    summary_entry = RepoDashboardIndexEntry(
        "doria90/current-repo",
        "main",
        "baseline_approved",
        5,
        time.time(),
        dashboard_scope="allocated",
        allocation_status="onboarded",
    )

    with patch("main.list_repo_dashboard_index", side_effect=[[summary_entry], []]):
        disconnect_response = client.post(
            "/repos/disconnect?repo_full=doria90/current-repo",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={"csrf_token": session.csrf_secret},
            follow_redirects=False,
        )
        repo_setup_response = client.get(
            "/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    restored_allocation = get_repo_allocation_for_workspace(main.AUDIT_DB_PATH, workspace.id, "doria90/current-repo")
    with patch("main.generate_jwt", return_value="jwt"), patch("main.get_installation_token", return_value="installation-token"), patch(
        "main.onboard_repository", return_value=None
    ):
        next_repo_response = client.post(
            "/repos/allocate?repo_full=doria90/next-repo",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={"csrf_token": session.csrf_secret},
            follow_redirects=False,
        )

    main.AUDIT_DB_PATH = original_db_path

    assert disconnect_response.status_code == 303
    assert disconnect_response.headers["location"] == "/repos?repo_removed=1"
    assert restored_allocation is not None
    assert restored_allocation.allocation_status == "inactive"
    assert repo_setup_response.status_code == 200
    assert "1 of 1 repository slots available on this plan." in repo_setup_response.text
    assert "Restore repo" in repo_setup_response.text
    assert "doria90/current-repo" in repo_setup_response.text
    assert "No onboarded repositories yet" in repo_setup_response.text
    assert next_repo_response.status_code == 303
    assert next_repo_response.headers["location"] == "/workspace"


def test_repo_restore_reuses_inactive_allocation_row(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-restore.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        list_repo_allocations_for_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="845",
        github_login="restore-owner",
        display_name="Restore Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="repo-restore-workspace",
        display_name="Repo Restore Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-restore-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:team:restore",
        stripe_price_id="local:team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id="test-restore",
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repositories=[
            {"repo_github_id": "1", "repo_full": "doria90/current-repo", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repo_github_id="1",
        repo_full="doria90/current-repo",
        baseline_mode="onboarding",
        activated_by_user_id=owner.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "inactive")

    with patch("main.generate_jwt", return_value="jwt"), patch("main.get_installation_token", return_value="installation-token"), patch(
        "main.onboard_repository", return_value=None
    ):
        restore_response = client.post(
            "/repos/allocate?repo_full=doria90/current-repo",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={"csrf_token": session.csrf_secret},
            follow_redirects=False,
        )
    allocations = list_repo_allocations_for_workspace(main.AUDIT_DB_PATH, workspace.id)

    main.AUDIT_DB_PATH = original_db_path

    assert restore_response.status_code == 303
    assert restore_response.headers["location"] == "/workspace"
    assert len(allocations) == 1
    assert allocations[0].id == allocation.id
    assert allocations[0].allocation_status == "onboarded"


def test_repo_setup_page_hides_repo_removed_from_installation_scope(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-installation-removal-ui.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        apply_github_installation_repository_event,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="846",
        github_login="install-scope-owner",
        display_name="Install Scope Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="repo-installation-removal-workspace",
        display_name="Repo Installation Removal Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-installation-removal-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:team:installation-removal",
        stripe_price_id="local:team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id="test-installation-removal",
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    installation = upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repositories=[
            {"repo_github_id": "1", "repo_full": "doria90/removed-repo", "default_branch": "main", "is_private": True, "status": "available"},
            {"repo_github_id": "2", "repo_full": "doria90/kept-repo", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repo_github_id="1",
        repo_full="doria90/removed-repo",
        baseline_mode="onboarding",
        activated_by_user_id=owner.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")
    apply_github_installation_repository_event(
        main.AUDIT_DB_PATH,
        installation_id=installation.installation_id,
        repositories_removed=[{"id": 1, "full_name": "doria90/removed-repo", "default_branch": "main", "private": True}],
    )

    with patch("main.list_github_user_repositories", return_value=[]):
        response = client.get(
            "/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert "doria90/kept-repo" in response.text
    assert "doria90/removed-repo" not in response.text
    assert "No onboarded repositories yet" in response.text


def test_repo_dashboard_deep_link_returns_404_after_repo_removed_from_installation_scope(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-installation-removal-deep-link.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        apply_github_installation_repository_event,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="847",
        github_login="deep-link-owner",
        display_name="Deep Link Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="repo-installation-removal-deep-link-workspace",
        display_name="Repo Installation Removal Deep Link Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-installation-removal-deep-link-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:team:installation-removal-deep-link",
        stripe_price_id="local:team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=None,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id="test-installation-removal-deep-link",
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    installation = upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repositories=[
            {"repo_github_id": "1", "repo_full": "doria90/removed-repo", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repo_github_id="1",
        repo_full="doria90/removed-repo",
        baseline_mode="onboarding",
        activated_by_user_id=owner.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")
    apply_github_installation_repository_event(
        main.AUDIT_DB_PATH,
        installation_id=installation.installation_id,
        repositories_removed=[{"id": 1, "full_name": "doria90/removed-repo", "default_branch": "main", "private": True}],
    )

    with patch("main._control_plane_active", return_value=True):
        response = client.get(
            "/dashboard/doria90/removed-repo?artifact=prompts/system.txt&pr=42&head_sha=abc123",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 404
    assert 'data-dashboard-shell-state="repo_access_removed"' in response.text
    assert "Repository access removed" in response.text
    assert "doria90/removed-repo is no longer available in this workspace dashboard." in response.text
    assert 'href="/repos"' in response.text
    assert "Open Repository Setup" in response.text


def test_settings_page_hides_repo_removed_from_installation_scope(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "settings-installation-removal.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        apply_github_installation_repository_event,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="848",
        github_login="settings-removal-owner",
        display_name="Settings Removal Owner",
        primary_email="settings-removal@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="settings-removal-workspace",
        display_name="Settings Removal Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="settings-removal-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf-settings-removal",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="local:starter:settings-removal",
        stripe_price_id="local:starter",
        plan_code="starter",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "starter",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    installation = upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repositories=[
            {"repo_github_id": "1", "repo_full": "doria90/removed-repo", "default_branch": "main", "is_private": True, "status": "available"},
            {"repo_github_id": "2", "repo_full": "doria90/kept-repo", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )
    removed_allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repo_github_id="1",
        repo_full="doria90/removed-repo",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    kept_allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repo_github_id="2",
        repo_full="doria90/kept-repo",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, removed_allocation.id, "onboarded")
    update_repo_allocation_status(main.AUDIT_DB_PATH, kept_allocation.id, "onboarded")
    apply_github_installation_repository_event(
        main.AUDIT_DB_PATH,
        installation_id=installation.installation_id,
        repositories_removed=[{"id": 1, "full_name": "doria90/removed-repo", "default_branch": "main", "private": True}],
    )

    response = client.get(
        "/settings",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert "doria90/kept-repo" in response.text
    assert "doria90/removed-repo" not in response.text


def test_install_start_redirects_to_live_github_install_and_sets_state_cookie(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "install-start.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="842",
        github_login="install-start-owner",
        display_name="Install Start Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="install-start-workspace",
        display_name="Install Start Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="install-start-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    with patch.object(main.settings, "github_app_id", "app-id"), patch.object(
        main.settings, "github_app_private_key", "dummy-private-key"
    ), patch("main.get_live_github_install_url", return_value="https://github.com/apps/vipari/installations/new?state=test-state"):
        response = client.get(
            "/setup/install/start",
            cookies={main.settings.session_cookie_name: session.session_id},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "https://github.com/apps/vipari/installations/new?state=test-state"
    assert "promptdrift_install_state=" in response.headers.get("set-cookie", "")

    main.AUDIT_DB_PATH = original_db_path


def test_install_callback_rejects_workspace_link_without_valid_nonce(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "install-callback-invalid-state.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="821",
        github_login="install-owner-2",
        display_name="Install Owner Two",
        primary_email="owner2@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="install-callback-invalid-state",
        display_name="Install Callback Invalid State",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="install-callback-invalid-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    response = client.get(
        "/setup/install/callback?installation_id=12345&setup_action=install&state=forged-state",
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Install callback state validation failed."

    main.AUDIT_DB_PATH = original_db_path


def test_auth_session_identity_omits_encrypted_tokens(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "auth-session-sanitized.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, upsert_github_identity

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="880",
        github_login="session-owner",
        display_name="Session Owner",
        primary_email="session-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-access-token",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="session-sanitized-token",
        user_id=user.id,
        workspace_id=None,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    response = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    assert response.status_code == 200
    identity_payload = response.json()["identity"]
    assert identity_payload["github_login"] == "session-owner"
    assert "access_token_encrypted" not in identity_payload
    assert "refresh_token_encrypted" not in identity_payload

    main.AUDIT_DB_PATH = original_db_path


def test_export_download_serves_completed_artifact_without_rebuild(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "export-download-immutable.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.compliance_export_service import ComplianceExportResult

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="960",
        github_login="export-owner",
        display_name="Export Owner",
        primary_email="export-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="export-workspace",
        display_name="Export Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="export-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_export_owner",
        stripe_price_id="price_export_owner",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9600,
        account_id="9600",
        account_login="export-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9600,
        repositories=[
            {
                "repo_github_id": "export-org/repo-one",
                "repo_full": "export-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9600,
        repo_github_id="export-org/repo-one",
        repo_full="export-org/repo-one",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")

    created_result = ComplianceExportResult(
        zip_bytes=b"immutable-export-zip",
        manifest={"version": "1"},
        file_count=2,
        total_size_bytes=len(b"immutable-export-zip"),
    )
    with patch("main.build_compliance_export", return_value=created_result):
        create_response = client.post(
            "/api/repos/export-org/repo-one/export/compliance",
            cookies={main.settings.session_cookie_name: session.session_id},
            json={
                "from_ts": 1700000000,
                "to_ts": 1700086400,
                "export_mode": "compliance",
                "include_artifact_content": False,
            },
        )

    assert create_response.status_code == 200
    download_url = create_response.json()["download_url"]
    assert download_url

    with patch("main.build_compliance_export", side_effect=AssertionError("download should use stored artifact")):
        download_response = client.get(
            download_url,
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert download_response.status_code == 200
    assert download_response.content == b"immutable-export-zip"
    assert download_response.headers["content-type"] == "application/zip"

    main.AUDIT_DB_PATH = original_db_path


def test_api_compliance_export_sanitizes_failure_details(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "api-compliance-export-failure.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.export_jobs import list_export_jobs_for_requester

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="981",
        github_login="api-export-failure-user",
        display_name="API Export Failure User",
        primary_email="api-export-failure@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="api-export-failure-workspace",
        display_name="API Export Failure Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="api-export-failure-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_api_export_failure",
        stripe_price_id="price_api_export_failure",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9810,
        account_id="9810",
        account_login="export-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9810,
        repositories=[
            {
                "repo_github_id": "export-org/repo-one",
                "repo_full": "export-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9810,
        repo_github_id="export-org/repo-one",
        repo_full="export-org/repo-one",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")

    with patch("main.build_compliance_export", side_effect=RuntimeError("zip failed with stack details")):
        response = client.post(
            "/api/repos/export-org/repo-one/export/compliance",
            cookies={main.settings.session_cookie_name: session.session_id},
            json={
                "from_ts": 1700000000,
                "to_ts": 1700086400,
                "export_mode": "compliance",
                "include_artifact_content": False,
            },
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Export generation failed. Retry after checking onboarding and evidence coverage."

    jobs = list_export_jobs_for_requester(main.AUDIT_DB_PATH, "export-org/repo-one", workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
    assert jobs[0].last_error == "Export generation failed. Retry after checking onboarding and evidence coverage."

    main.AUDIT_DB_PATH = original_db_path


def test_export_download_rejects_cross_workspace_session_even_with_valid_token(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "export-download-cross-workspace.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.compliance_export_service import ComplianceExportResult

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="961",
        github_login="export-owner-a",
        display_name="Export Owner A",
        primary_email="export-owner-a@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token-a",
    )
    owner_workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="export-workspace-a",
        display_name="Export Workspace A",
        billing_owner_user_id=owner.id,
    )
    owner_session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="export-session-a",
        user_id=owner.id,
        workspace_id=owner_workspace.id,
        csrf_secret="csrf-a",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=owner_workspace.id,
        stripe_subscription_id="sub_export_owner_a",
        stripe_price_id="price_export_owner_a",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=owner_workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=owner_workspace.id,
        installation_id=9610,
        account_id="9610",
        account_login="export-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=owner_workspace.id,
        installation_id=9610,
        repositories=[
            {
                "repo_github_id": "export-org/repo-one",
                "repo_full": "export-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    owner_allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=owner_workspace.id,
        installation_id=9610,
        repo_github_id="export-org/repo-one",
        repo_full="export-org/repo-one",
        baseline_mode="onboarding",
        activated_by_user_id=owner.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, owner_allocation.id, "onboarded")

    created_result = ComplianceExportResult(
        zip_bytes=b"immutable-export-zip",
        manifest={"version": "1"},
        file_count=2,
        total_size_bytes=len(b"immutable-export-zip"),
    )
    with patch("main.build_compliance_export", return_value=created_result):
        create_response = client.post(
            "/api/repos/export-org/repo-one/export/compliance",
            cookies={main.settings.session_cookie_name: owner_session.session_id},
            json={
                "from_ts": 1700000000,
                "to_ts": 1700086400,
                "export_mode": "compliance",
                "include_artifact_content": False,
            },
        )

    assert create_response.status_code == 200
    download_url = create_response.json()["download_url"]
    assert download_url

    other_user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="962",
        github_login="export-owner-b",
        display_name="Export Owner B",
        primary_email="export-owner-b@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token-b",
    )
    other_workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="export-workspace-b",
        display_name="Export Workspace B",
        billing_owner_user_id=other_user.id,
    )
    other_session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="export-session-b",
        user_id=other_user.id,
        workspace_id=other_workspace.id,
        csrf_secret="csrf-b",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=other_workspace.id,
        stripe_subscription_id="sub_export_owner_b",
        stripe_price_id="price_export_owner_b",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=other_workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=other_workspace.id,
        installation_id=9620,
        account_id="9620",
        account_login="export-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=other_workspace.id,
        installation_id=9620,
        repositories=[
            {
                "repo_github_id": "export-org/repo-one-b",
                "repo_full": "export-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    other_allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=other_workspace.id,
        installation_id=9620,
        repo_github_id="export-org/repo-one-b",
        repo_full="export-org/repo-one",
        baseline_mode="onboarding",
        activated_by_user_id=other_user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, other_allocation.id, "onboarded")

    download_response = client.get(
        download_url,
        cookies={main.settings.session_cookie_name: other_session.session_id},
    )

    assert download_response.status_code == 404
    assert download_response.json()["detail"] == "Export job not found"

    main.AUDIT_DB_PATH = original_db_path


def test_compliance_page_lists_workspace_exports_and_repos(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-page.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_ai_system_classification,
        upsert_ai_system_for_repo,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.export_jobs import create_export_job, update_export_job_status
    from services.onboarding_records import DiscoveredArtifactInput, record_repository_onboarding

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="970",
        github_login="compliance-owner",
        display_name="Compliance Owner",
        primary_email="compliance-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="compliance-workspace",
        display_name="Compliance Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="compliance-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_compliance_owner",
        stripe_price_id="price_compliance_owner",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9700,
        account_id="9700",
        account_login="compliance-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9700,
        repositories=[
            {
                "repo_github_id": "1",
                "repo_full": "compliance-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
            {
                "repo_github_id": "2",
                "repo_full": "compliance-org/repo-two",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="compliance-org/repo-one",
        installation_id=9700,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                discovery_reason="Prompt file",
                confidence=0.9,
                baseline_content="You must follow the approved workflow.",
            ),
            DiscoveredArtifactInput(
                artifact_path="policies/governance.md",
                artifact_type="policy",
                discovery_reason="Governance policy",
                confidence=0.8,
                baseline_content="Human review is required for sensitive changes.",
            ),
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="compliance-org/repo-two",
        installation_id=9700,
        default_branch="main",
        status="pending_baseline_approval",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="tools/agent_tool.py",
                artifact_type="tool",
                discovery_reason="Tool implementation",
                confidence=0.8,
                baseline_content="def run_tool():\n    return 'ok'",
            ),
            DiscoveredArtifactInput(
                artifact_path="config/model.json",
                artifact_type="model_config",
                discovery_reason="Model configuration",
                confidence=0.8,
                baseline_content='{"model": "gpt-4o"}',
            ),
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    ready_ai_system = upsert_ai_system_for_repo(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        repo_full="compliance-org/repo-one",
        display_name="compliance-org/repo-one",
        latest_onboarding_status="baseline_approved",
        artifact_families=["prompt", "governance"],
        purpose_summary="Ready AI repo",
        created_by_user_id=user.id,
    )
    upsert_ai_system_for_repo(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        repo_full="compliance-org/repo-two",
        display_name="compliance-org/repo-two",
        latest_onboarding_status="pending_baseline_approval",
        artifact_families=["tool", "model"],
        purpose_summary="Pending AI repo",
        created_by_user_id=user.id,
    )
    update_ai_system_classification(
        main.AUDIT_DB_PATH,
        ai_system_id=ready_ai_system.id,
        risk_level="high-risk",
        eu_ai_act_domain="employment",
        purpose_summary="Ready AI repo",
        reviewed_by_user_id=user.id,
    )
    stale_timestamp = time.time() - (45 * 86400)
    with sqlite3.connect(main.AUDIT_DB_PATH) as conn:
        conn.execute(
            "UPDATE repository_onboardings SET updated_at = ? WHERE repo_full = ?",
            (stale_timestamp, "compliance-org/repo-two"),
        )
    job = create_export_job(
        db_path=main.AUDIT_DB_PATH,
        repo_full="compliance-org/repo-one",
        from_ts=1700000000,
        to_ts=1700086400,
        workspace_id=workspace.id,
        requested_by_user_id=user.id,
        requested_by_github_login="compliance-owner",
        export_mode="compliance",
        include_artifact_content=False,
    )
    update_export_job_status(
        main.AUDIT_DB_PATH,
        job.id,
        "completed",
        result_size_bytes=14,
        result_sha256="abc123",
        result_blob=b"zip-artifact",
    )

    response = client.get("/compliance", cookies={main.settings.session_cookie_name: session.session_id})
    frameworks_response = client.get("/compliance/frameworks", cookies={main.settings.session_cookie_name: session.session_id})
    exports_response = client.get("/compliance/exports", cookies={main.settings.session_cookie_name: session.session_id})
    evidence_response = client.get("/compliance/evidence", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert frameworks_response.status_code == 200
    assert exports_response.status_code == 200
    assert evidence_response.status_code == 200
    assert 'aria-label="Compliance"' in response.text
    assert "Workspace readiness" in response.text
    assert "Readiness verdict" in response.text
    assert "1 repo still relies on auto-prefilled registry context and needs reviewer confirmation." in response.text
    assert "Readiness by repository" in response.text
    assert "AI systems confirmed" in response.text
    assert "1/2" in response.text
    assert "Generate export" in response.text
    assert "Download latest export" in response.text
    assert "compliance-org/repo-one" in response.text
    assert "compliance-org/repo-two" in response.text
    assert "High Risk" in response.text or "High risk" in response.text
    assert "Classification pending" in response.text
    assert "Reviewer confirmed" in response.text
    assert "Auto-prefilled from repository evidence" in response.text
    assert "Last review: Not yet reviewed" in response.text
    assert 'aria-label="Repositories"' in response.text
    assert 'aria-label="Audit Logs"' in response.text
    assert "Framework mapping" in frameworks_response.text
    assert "EU AI Act" in frameworks_response.text
    assert "1 repo has reviewer-confirmed AI Act classifications." in frameworks_response.text
    assert "1 repo still relies on auto-prefilled registry context." in frameworks_response.text
    assert "SOC 2" in frameworks_response.text
    assert "ISO 27001" in frameworks_response.text
    assert "Run compliance exports" in exports_response.text
    assert "Export history" in exports_response.text
    assert "Review-ready preset" in exports_response.text
    assert "Needs readiness work" in exports_response.text
    assert "AI system: Reviewer confirmed" in exports_response.text
    assert "AI system: Auto-prefilled from repository evidence" in exports_response.text
    assert "AI system: Reviewer confirmed · Last review:" in exports_response.text
    assert "Download" in exports_response.text
    assert "Evidence posture" in evidence_response.text
    assert "Stale (45d)" in evidence_response.text
    assert "Baseline Review" in evidence_response.text
    assert "Missing Governance" in evidence_response.text
    assert "Classify AI systems" in response.text
    assert 'href="/policies"' in response.text
    assert '/compliance/evidence?gap=missing_governance' in response.text
    assert '/compliance/evidence?gap=baseline_review&amp;repo=compliance-org%2Frepo-two' in response.text

    filtered_evidence_response = client.get(
        "/compliance/evidence?gap=baseline_review&repo=compliance-org/repo-two",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    assert filtered_evidence_response.status_code == 200
    assert "Showing 1 of 2 repo for <strong>Baseline Review</strong> for <strong>compliance-org/repo-two</strong>." in filtered_evidence_response.text
    assert "compliance-org/repo-two" in filtered_evidence_response.text
    assert "compliance-org/repo-one" not in filtered_evidence_response.text

    main.AUDIT_DB_PATH = original_db_path


def test_compliance_page_can_create_exports_for_selected_repos(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-export-submit.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.compliance_export_service import ComplianceExportResult
    from services.export_jobs import list_export_jobs_for_workspace_requester

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="971",
        github_login="compliance-exporter",
        display_name="Compliance Exporter",
        primary_email="compliance-exporter@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="compliance-export-workspace",
        display_name="Compliance Export Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="compliance-export-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_compliance_exporter",
        stripe_price_id="price_compliance_exporter",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9710,
        account_id="9710",
        account_login="compliance-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9710,
        repositories=[
            {
                "repo_github_id": "1",
                "repo_full": "compliance-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
            {
                "repo_github_id": "2",
                "repo_full": "compliance-org/repo-two",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )

    created_result = ComplianceExportResult(
        zip_bytes=b"workspace-export-zip",
        manifest={"version": "1"},
        file_count=2,
        total_size_bytes=len(b"workspace-export-zip"),
    )
    with patch("main.build_compliance_export", return_value=created_result):
        response = client.post(
            "/compliance/export",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={
                "export_scope": "selected",
                "repo_fulls": ["compliance-org/repo-two"],
                "from_date": "2023-11-14",
                "to_date": "2023-11-15",
                "export_mode": "compliance",
                "csrf_token": session.csrf_secret,
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/compliance/exports?status=")

    jobs = list_export_jobs_for_workspace_requester(main.AUDIT_DB_PATH, workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].repo_full == "compliance-org/repo-two"
    assert jobs[0].status == "completed"
    assert jobs[0].ai_system_provenance_label == "No registry entry"
    assert jobs[0].ai_system_review_detail == "Last review: Not yet reviewed"

    main.AUDIT_DB_PATH = original_db_path


def test_compliance_exports_history_uses_snapshotted_ai_system_provenance(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-export-history-snapshot.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        replace_repo_connections,
        update_ai_system_classification,
        upsert_ai_system_for_repo,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.export_jobs import create_export_job, list_export_jobs_for_workspace_requester
    from services.onboarding_records import DiscoveredArtifactInput, record_repository_onboarding

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="971",
        github_login="snapshot-owner",
        display_name="Snapshot Owner",
        primary_email="snapshot-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="snapshot-workspace",
        display_name="Snapshot Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="snapshot-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_snapshot_owner",
        stripe_price_id="price_snapshot_owner",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9710,
        account_id="9710",
        account_login="snapshot-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9710,
        repositories=[
            {
                "repo_github_id": "1",
                "repo_full": "snapshot-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="snapshot-org/repo-one",
        installation_id=9710,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                discovery_reason="Prompt file",
                confidence=0.9,
                baseline_content="Follow the approved workflow.",
            ),
            DiscoveredArtifactInput(
                artifact_path="policies/governance.md",
                artifact_type="policy",
                discovery_reason="Governance policy",
                confidence=0.8,
                baseline_content="Human review is required for sensitive changes.",
            ),
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    ai_system = upsert_ai_system_for_repo(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        repo_full="snapshot-org/repo-one",
        display_name="snapshot-org/repo-one",
        latest_onboarding_status="baseline_approved",
        artifact_families=["prompt", "governance"],
        purpose_summary="Snapshot repo",
        created_by_user_id=user.id,
    )
    job = create_export_job(
        db_path=main.AUDIT_DB_PATH,
        repo_full="snapshot-org/repo-one",
        from_ts=1700000000,
        to_ts=1700086400,
        workspace_id=workspace.id,
        requested_by_user_id=user.id,
        requested_by_github_login="snapshot-owner",
        export_mode="compliance",
        include_artifact_content=False,
        ai_system_provenance_label="Auto-prefilled from repository evidence",
        ai_system_review_detail="Last review: Not yet reviewed",
    )
    update_ai_system_classification(
        main.AUDIT_DB_PATH,
        ai_system_id=ai_system.id,
        risk_level="high-risk",
        eu_ai_act_domain="employment",
        purpose_summary="Snapshot repo",
        reviewed_by_user_id=user.id,
    )

    exports_response = client.get("/compliance/exports", cookies={main.settings.session_cookie_name: session.session_id})

    assert exports_response.status_code == 200
    assert "AI system: Reviewer confirmed" in exports_response.text
    assert "AI system: Auto-prefilled from repository evidence · Last review: Not yet reviewed" in exports_response.text

    jobs = list_export_jobs_for_workspace_requester(main.AUDIT_DB_PATH, workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].id == job.id
    assert jobs[0].ai_system_provenance_label == "Auto-prefilled from repository evidence"
    assert jobs[0].ai_system_review_detail == "Last review: Not yet reviewed"

    main.AUDIT_DB_PATH = original_db_path
def test_compliance_page_marks_failed_exports_and_reports_retryable_status(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-export-failure.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.export_jobs import list_export_jobs_for_workspace_requester

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="973",
        github_login="failing-exporter",
        display_name="Failing Exporter",
        primary_email="failing-exporter@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="compliance-export-failure-workspace",
        display_name="Compliance Export Failure Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="compliance-export-failure-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_compliance_failure",
        stripe_price_id="price_compliance_failure",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9730,
        account_id="9730",
        account_login="compliance-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9730,
        repositories=[
            {
                "repo_github_id": "1",
                "repo_full": "compliance-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )

    with patch("main.build_compliance_export", side_effect=RuntimeError("zip failed")):
        response = client.post(
            "/compliance/export",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={
                "export_scope": "selected",
                "repo_fulls": ["compliance-org/repo-one"],
                "from_date": "2023-11-14",
                "to_date": "2023-11-15",
                "export_mode": "compliance",
                "csrf_token": session.csrf_secret,
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "Completed%20exports%20for%200%20repo%28s%29.%201%20repo%28s%29%20failed%20and%20can%20be%20retried." in response.headers["location"]
    assert "First%20failure%3A%20compliance-org/repo-one%3A%20Export%20generation%20failed.%20Retry%20after%20checking%20onboarding%20and%20evidence%20coverage." in response.headers["location"]

    jobs = list_export_jobs_for_workspace_requester(main.AUDIT_DB_PATH, workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].repo_full == "compliance-org/repo-one"
    assert jobs[0].status == "failed"
    assert jobs[0].last_error == "Export generation failed. Retry after checking onboarding and evidence coverage."

    exports_response = client.get(
        response.headers["location"],
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    assert exports_response.status_code == 200
    assert "First failure: compliance-org/repo-one: Export generation failed. Retry after checking onboarding and evidence coverage." in exports_response.text
    assert "Failure: Export generation failed. Retry after checking onboarding and evidence coverage." in exports_response.text

    main.AUDIT_DB_PATH = original_db_path


def test_compliance_page_selected_exports_can_include_visible_repo_without_onboarding(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-export-selected-visible.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.export_jobs import list_export_jobs_for_workspace_requester

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="974",
        github_login="selected-visible-exporter",
        display_name="Selected Visible Exporter",
        primary_email="selected-visible-exporter@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="compliance-export-selected-visible-workspace",
        display_name="Compliance Export Selected Visible Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="compliance-export-selected-visible-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_compliance_selected_visible",
        stripe_price_id="price_compliance_selected_visible",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9740,
        account_id="9740",
        account_login="compliance-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9740,
        repositories=[
            {
                "repo_github_id": "1",
                "repo_full": "compliance-org/repo-connected-only",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            }
        ],
    )

    response = client.post(
        "/compliance/export",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={
            "export_scope": "selected",
            "repo_fulls": ["compliance-org/repo-connected-only"],
            "from_date": "2023-11-14",
            "to_date": "2023-11-15",
            "export_mode": "compliance",
            "csrf_token": session.csrf_secret,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Completed%20exports%20for%200%20repo%28s%29.%201%20repo%28s%29%20failed%20and%20can%20be%20retried." in response.headers["location"]
    assert "First%20failure%3A%20compliance-org/repo-connected-only%3A%20No%20onboarding%20evidence%20is%20available%20for%20this%20repository%20yet." in response.headers["location"]

    jobs = list_export_jobs_for_workspace_requester(main.AUDIT_DB_PATH, workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].repo_full == "compliance-org/repo-connected-only"
    assert jobs[0].status == "failed"
    assert jobs[0].last_error == "No onboarding evidence is available for this repository yet."

    main.AUDIT_DB_PATH = original_db_path


def test_compliance_page_can_create_exports_for_review_ready_preset(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-export-preset.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.compliance_export_service import ComplianceExportResult
    from services.export_jobs import list_export_jobs_for_workspace_requester
    from services.onboarding_records import DiscoveredArtifactInput, record_repository_onboarding

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="972",
        github_login="preset-exporter",
        display_name="Preset Exporter",
        primary_email="preset-exporter@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="compliance-export-preset-workspace",
        display_name="Compliance Export Preset Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="compliance-export-preset-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_compliance_preset",
        stripe_price_id="price_compliance_preset",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9720,
        account_id="9720",
        account_login="compliance-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=9720,
        repositories=[
            {
                "repo_github_id": "1",
                "repo_full": "compliance-org/repo-one",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
            {
                "repo_github_id": "2",
                "repo_full": "compliance-org/repo-two",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="compliance-org/repo-one",
        installation_id=9720,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                discovery_reason="Prompt file",
                confidence=0.9,
                baseline_content="You must follow the approved workflow.",
            ),
            DiscoveredArtifactInput(
                artifact_path="policies/policy.md",
                artifact_type="policy",
                discovery_reason="Governance policy",
                confidence=0.8,
                baseline_content="Human review is required for sensitive changes.",
            ),
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="compliance-org/repo-two",
        installation_id=9720,
        default_branch="main",
        status="pending_baseline_approval",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="tools/agent_tool.py",
                artifact_type="tool",
                discovery_reason="Tool implementation",
                confidence=0.8,
                baseline_content="def run_tool():\n    return 'ok'",
            ),
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )

    created_result = ComplianceExportResult(
        zip_bytes=b"workspace-export-zip",
        manifest={"version": "1"},
        file_count=2,
        total_size_bytes=len(b"workspace-export-zip"),
    )
    with patch("main.build_compliance_export", return_value=created_result):
        response = client.post(
            "/compliance/export",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={
                "export_scope": "all",
                "export_preset": "review_ready",
                "from_date": "2023-11-14",
                "to_date": "2023-11-15",
                "export_mode": "compliance",
                "csrf_token": session.csrf_secret,
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/compliance/exports?status=")

    jobs = list_export_jobs_for_workspace_requester(main.AUDIT_DB_PATH, workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].repo_full == "compliance-org/repo-one"
    assert jobs[0].status == "completed"

    main.AUDIT_DB_PATH = original_db_path


def test_compliance_page_all_visible_exports_only_include_compliance_view_repos(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-export-all-visible.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        replace_repo_connections,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )
    from services.compliance_export_service import ComplianceExportResult
    from services.export_jobs import list_export_jobs_for_workspace_requester
    from services.onboarding_records import DiscoveredArtifactInput, record_repository_onboarding

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="9711",
        github_login="all-visible-exporter",
        display_name="All Visible Exporter",
        primary_email="all-visible-exporter@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="compliance-export-all-visible-workspace",
        display_name="Compliance Export All Visible Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="compliance-export-all-visible-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_compliance_all_visible",
        stripe_price_id="price_compliance_all_visible",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=None,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=97110,
        account_id="97110",
        account_login="compliance-org",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=97110,
        repositories=[
            {
                "repo_github_id": "1",
                "repo_full": "compliance-org/repo-ready",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
            {
                "repo_github_id": "2",
                "repo_full": "compliance-org/repo-connected-only",
                "default_branch": "main",
                "is_private": True,
                "status": "available",
            },
        ],
    )
    record_repository_onboarding(
        main.AUDIT_DB_PATH,
        repo_full="compliance-org/repo-ready",
        installation_id=97110,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                discovery_reason="Prompt file",
                confidence=0.9,
                baseline_content="Use the approved workflow.",
            ),
            DiscoveredArtifactInput(
                artifact_path="policies/governance.md",
                artifact_type="policy",
                discovery_reason="Governance policy",
                confidence=0.8,
                baseline_content="Human review is required.",
            ),
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )

    created_result = ComplianceExportResult(
        zip_bytes=b"workspace-export-zip",
        manifest={"version": "1"},
        file_count=2,
        total_size_bytes=len(b"workspace-export-zip"),
    )
    with patch("main.build_compliance_export", return_value=created_result) as build_export_mock:
        response = client.post(
            "/compliance/export",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={
                "export_scope": "all_visible",
                "export_preset": "none",
                "from_date": "2023-11-14",
                "to_date": "2023-11-15",
                "export_mode": "compliance",
                "csrf_token": session.csrf_secret,
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "Completed%20exports%20for%201%20repo%28s%29." in response.headers["location"]
    assert "failed" not in response.headers["location"]
    assert build_export_mock.call_count == 1
    assert build_export_mock.call_args.args[1].repo_full == "compliance-org/repo-ready"

    jobs = list_export_jobs_for_workspace_requester(main.AUDIT_DB_PATH, workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].repo_full == "compliance-org/repo-ready"
    assert jobs[0].status == "completed"

    main.AUDIT_DB_PATH = original_db_path


def test_versioned_dashboard_assets_are_cacheable():
    css_response = client.get("/static/dashboard.css?v=123")
    js_response = client.get("/static/dashboard-index.js?v=123")
    plain_response = client.get("/static/dashboard.css")

    assert css_response.status_code == 200
    assert js_response.status_code == 200
    assert css_response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert js_response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert plain_response.headers["cache-control"] == "public, max-age=300"


def test_dashboard_index_asset_includes_scoped_review_cues():
    js_response = client.get("/static/dashboard-index.js?v=123")
    css_response = client.get("/static/dashboard.css?v=123")

    assert js_response.status_code == 200
    assert css_response.status_code == 200
    assert "function reviewScopeLabel(repoLike)" in js_response.text
    assert 'class="repo-atlas-context"' in js_response.text
    assert 'class="escalation-meta-context"' in js_response.text
    assert ".repo-atlas-context" in css_response.text
    assert ".escalation-meta-context" in css_response.text


def test_dashboard_pages_emit_server_timing_headers():
    with patch("main._dashboard_redirect_for_request", return_value=(None, None)):
        dashboard_response = client.get("/dashboard")
        repo_response = client.get("/dashboard/doria90/dummyAI")

    assert dashboard_response.status_code == 200
    assert repo_response.status_code == 200
    assert "access;dur=" in dashboard_response.headers["server-timing"]
    assert "render;dur=" in dashboard_response.headers["server-timing"]
    assert "total;dur=" in dashboard_response.headers["server-timing"]
    assert "access;dur=" in repo_response.headers["server-timing"]
    assert "render;dur=" in repo_response.headers["server-timing"]
    assert "total;dur=" in repo_response.headers["server-timing"]


def test_dashboard_api_endpoints_emit_server_timing_headers():
    from services.audit_records import RepoStaticDriftSummary
    from services.dashboard_views import DashboardOverviewRiskState, DashboardOverviewView, RepoDashboardBackfillSummary, RepoDashboardView

    overview_view = DashboardOverviewView(
        risk_state=DashboardOverviewRiskState(
            status="baseline",
            headline="Stable",
            summary="steady",
            review_now_repo_count=0,
            watch_repo_count=0,
            baseline_review_repo_count=0,
            highest_risk_repo_full=None,
            highest_risk_artifact_path=None,
            highest_risk_title=None,
            highest_drift_magnitude=0.0,
        ),
        metrics=[],
        regression_patterns=[],
        highest_risk_items=[],
        control_surface_risk=[],
        attention_repos=[],
        control_surface_coverage=[],
        repos=[],
    )
    repo_view = RepoDashboardView(
        repo_full="doria90/dummyAI",
        onboarding=None,
        baseline_review=None,
        backfill=RepoDashboardBackfillSummary(
            job_count=0,
            planned_job_count=0,
            processing_job_count=0,
            completed_job_count=0,
            failed_job_count=0,
            total_historical_versions=0,
            total_historical_profiles=0,
        ),
        pull_request_audit_count=0,
        baseline_version_count=0,
        drift_summary=RepoStaticDriftSummary(
            repo_full="doria90/dummyAI",
            artifact_count=0,
            profile_count=0,
            baseline_linked_profile_count=0,
            avg_semantic_distance=0.0,
            avg_guardrail_shift=0.0,
            avg_capability_shift=0.0,
            avg_autonomy_shift=0.0,
            highest_capability_artifact_path=None,
            highest_capability_delta=0.0,
        ),
        top_drifting_artifacts=[],
        insights=[],
        lower_confidence_insights=[],
        control_surface_groups=[],
        history_timelines=[],
        featured_storyline=None,
        history_cues=[],
        design_profiles=[],
        artifacts=[],
        journey_snapshots=[],
        journey_comparison=None,
    )

    with patch("main._require_dashboard_access", return_value={"workspace": object()}), patch(
        "main._dashboard_repo_visibility",
        return_value={
            "allowed_repo_fulls": {"doria90/dummyAI"},
            "repo_scope_by_full": {"doria90/dummyAI": "allocated"},
            "allocation_status_by_full": {"doria90/dummyAI": "onboarded"},
        },
    ), patch("main.list_repo_dashboard_index", return_value=[]), patch(
        "main.build_dashboard_overview_view", return_value=overview_view
    ), patch("main._require_repo_dashboard_read_access", return_value={"workspace": object()}), patch(
        "main.build_repo_dashboard_view", return_value=repo_view
    ):
        repos_response = client.get("/api/repos")
        overview_response = client.get("/api/dashboard/overview")
        repo_response = client.get("/api/repos/doria90/dummyAI/dashboard")

    assert repos_response.status_code == 200
    assert overview_response.status_code == 200
    assert repo_response.status_code == 200
    assert "access;dur=" in repos_response.headers["server-timing"]
    assert "visibility;dur=" in repos_response.headers["server-timing"]
    assert "list;dur=" in repos_response.headers["server-timing"]
    assert "total;dur=" in repos_response.headers["server-timing"]
    assert "access;dur=" in overview_response.headers["server-timing"]
    assert "visibility;dur=" in overview_response.headers["server-timing"]
    assert "build;dur=" in overview_response.headers["server-timing"]
    assert "json;dur=" in overview_response.headers["server-timing"]
    assert "total;dur=" in overview_response.headers["server-timing"]
    assert "access;dur=" in repo_response.headers["server-timing"]
    assert "build;dur=" in repo_response.headers["server-timing"]
    assert "json;dur=" in repo_response.headers["server-timing"]
    assert "total;dur=" in repo_response.headers["server-timing"]


def test_public_install_callback_persists_unclaimed_installation(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "public-install-callback.db")
    main.init_db(main.AUDIT_DB_PATH)

    with patch.object(main.settings, "github_app_id", "app-id"), patch.object(
        main.settings, "github_app_private_key", "dummy-private-key"
    ), patch(
        "main.sync_installation_repositories",
        return_value=(
            {"target_type": "Organization", "account": {"login": "marketplace-org", "type": "Organization", "id": 88}},
            [{"repo_github_id": "1", "repo_full": "marketplace-org/repo-one", "default_branch": "main", "is_private": True, "status": "available"}],
        ),
    ):
        response = client.get(
            "/setup/install/callback?installation_id=12345&setup_action=install",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/github/start")

    from services.control_plane_records import _connect

    with _connect(main.AUDIT_DB_PATH) as conn:
        installation = conn.execute("SELECT * FROM github_installations WHERE installation_id = 12345").fetchone()
        repo_connection = conn.execute("SELECT * FROM repo_connections WHERE installation_id = 12345").fetchone()

    assert installation is not None
    assert installation["workspace_id"] is None
    assert installation["account_login"] == "marketplace-org"
    assert repo_connection is not None
    assert repo_connection["workspace_id"] is None
    assert repo_connection["repo_full"] == "marketplace-org/repo-one"

    main.AUDIT_DB_PATH = original_db_path


def test_billing_checkout_rejects_unknown_plan(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "invalid-plan.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="830",
        github_login="plan-owner",
        display_name="Plan Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="invalid-plan-workspace",
        display_name="Invalid Plan Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="invalid-plan-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    response = client.post(
        "/billing/checkout?plan=unknown",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={"csrf_token": session.csrf_secret},
    )

    assert response.status_code == 400
    main.AUDIT_DB_PATH = original_db_path


def test_repo_setup_slot_summary_counts_onboarded_repos_shown_on_page(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-slot-summary.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, replace_repo_connections, upsert_entitlement, upsert_github_identity, upsert_github_installation
    from services.dashboard_views import RepoDashboardIndexEntry

    owner, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="821",
        github_login="team-owner",
        display_name="Team Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="team-repo-slot-workspace",
        display_name="Team Repo Slot Workspace",
        billing_owner_user_id=owner.id,
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="team-repo-slot-session",
        user_id=owner.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 20,
            "org_limit": 1,
            "seat_limit": 20,
            "retention_policy": "standard",
            "support_tier": "email",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    replace_repo_connections(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        repositories=[
            {"repo_github_id": "1", "repo_full": "doria90/repo-one", "default_branch": "main", "is_private": True, "status": "available"},
            {"repo_github_id": "2", "repo_full": "doria90/repo-two", "default_branch": "main", "is_private": True, "status": "available"},
            {"repo_github_id": "3", "repo_full": "doria90/repo-three", "default_branch": "main", "is_private": True, "status": "available"},
        ],
    )

    with patch(
        "main.list_repo_dashboard_index",
        return_value=[
            RepoDashboardIndexEntry("doria90/repo-one", "main", "baseline_approved", 5, time.time()),
            RepoDashboardIndexEntry("doria90/repo-two", "main", "baseline_approved", 4, time.time()),
            RepoDashboardIndexEntry("doria90/repo-three", "main", "baseline_approved", 3, time.time()),
        ],
    ):
        response = client.get(
            "/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    assert "17 of 20 repository slots available on this plan." in response.text

    main.AUDIT_DB_PATH = original_db_path


def test_repo_setup_page_ignores_invalid_github_app_private_key(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "repo-setup-invalid-key.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="961",
        github_login="repo-owner",
        display_name="Repo Owner",
        primary_email="repo-owner@example.com",
        avatar_url=None,
        granted_scopes=["repo"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        billing_owner_user_id=user.id,
        display_name="Repo Setup Workspace",
        slug="repo-setup-workspace",
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_repo_setup",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=time.time() + 86400,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 20,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "standard",
            "feature_flags_json": "{}",
        },
    )
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=12345,
        account_id="77",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-setup-invalid-key-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    with patch.object(main.settings, "github_app_id", "app-id"), patch.object(
        main.settings, "github_app_private_key", "not-a-pem"
    ), patch("main.sync_installation_repositories", side_effect=InvalidKeyError("bad key")):
        response = client.get(
            "/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    assert "Available Repositories" in response.text
    assert "20 of 20 repository slots available on this plan." in response.text

    main.AUDIT_DB_PATH = original_db_path


def test_repo_setup_page_falls_back_to_github_oauth_repo_inventory(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_encryption_key = main.settings.app_encryption_key
    main.AUDIT_DB_PATH = str(tmp_path / "repo-setup-oauth-inventory.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        upsert_entitlement,
        upsert_github_identity,
        upsert_subscription,
    )
    from services.auth_service import GithubUserRepository

    main.settings.app_encryption_key = "very-secret"
    encrypted_token = encrypt_text("oauth-token", main.settings.app_encryption_key)

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="962",
        github_login="repo-viewer",
        display_name="Repo Viewer",
        primary_email="repo-viewer@example.com",
        avatar_url=None,
        granted_scopes=["repo"],
        access_token_encrypted=encrypted_token,
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        billing_owner_user_id=user.id,
        display_name="OAuth Repo Workspace",
        slug="oauth-repo-workspace",
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_repo_oauth",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=time.time() + 86400,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 20,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "standard",
            "feature_flags_json": "{}",
        },
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="repo-setup-oauth-inventory-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=time.time() + 3600,
    )

    with patch("main.list_github_user_repositories") as list_repositories:
        list_repositories.return_value = [
            GithubUserRepository(
                github_repo_id="1",
                full_name="doria90/dummyAI",
                default_branch="main",
                is_private=True,
                html_url="https://github.com/doria90/dummyAI",
            ),
            GithubUserRepository(
                github_repo_id="2",
                full_name="doria90/PromptDrift",
                default_branch="main",
                is_private=True,
                html_url="https://github.com/doria90/PromptDrift",
            ),
        ]
        response = client.get(
            "/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    assert "Available Repositories" in response.text
    assert "doria90/dummyAI" in response.text
    assert "doria90/PromptDrift" in response.text

    main.settings.app_encryption_key = original_encryption_key
    main.AUDIT_DB_PATH = original_db_path


# ===========================================================================
# Customer API-key management UI tests
# ===========================================================================


def _setup_api_keys_db(tmp_path, db_suffix: str, user_suffix: str, role: str = "owner"):
    """Seed a DB with one user/workspace/subscription/entitlement + session and return the session."""
    from services.control_plane_records import (
        create_user_session,
        create_workspace,
        upsert_entitlement,
        upsert_github_identity,
        upsert_subscription,
        upsert_workspace_membership,
    )

    db_path = str(tmp_path / f"api-keys-{db_suffix}.db")
    main.AUDIT_DB_PATH = db_path
    main.init_db(main.AUDIT_DB_PATH)

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id=f"ak-{user_suffix}",
        github_login=f"ak-user-{user_suffix}",
        display_name=f"AK User {user_suffix}",
        primary_email=f"ak-{user_suffix}@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        billing_owner_user_id=user.id,
        display_name=f"AK Workspace {user_suffix}",
        slug=f"ak-workspace-{user_suffix}",
    )
    upsert_workspace_membership(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        user_id=user.id,
        role=role,
        invitation_state="accepted",
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id=f"sub_ak_{user_suffix}",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=time.time() + 86400,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "standard",
            "feature_flags_json": "{}",
        },
    )
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id=f"ak-session-{db_suffix}",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret=f"csrf-{db_suffix}",
        expires_at=time.time() + 3600,
    )
    return user, workspace, session


def test_api_keys_page_loads_for_owner(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "owner-load", "1001")

    response = client.get(
        "/settings/api-keys",
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    page = client.get(
        response.headers["location"],
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 303
    assert response.headers["location"] == "/integrations/mcp?tab=api-keys"
    assert page.status_code == 200
    assert "Machine principal credentials" in page.text


def test_api_keys_page_denied_for_viewer(tmp_path):
    original_db_path = main.AUDIT_DB_PATH

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "viewer-deny", "1002", role="viewer")

    response = client.get(
        "/settings/api-keys",
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    page = client.get(
        response.headers["location"],
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 303
    assert response.headers["location"] == "/integrations/mcp?tab=api-keys"
    assert page.status_code == 200
    assert "Machine principal credentials" not in page.text
    assert "Only workspace owners and admins" not in page.text
    assert "Download connector" in page.text
    assert "Request API-key access" in page.text
    assert 'aria-current="page">Overview<' in page.text
def test_api_keys_create_delivers_secret_in_flash_once(tmp_path):
    """POST create → 303, GET → secret shown; second GET → secret absent."""
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "flash-secret", "1003")

    # Create key → should redirect back to the page
    create_resp = client.post(
        "/settings/api-keys",
        data={
            "display_name": "flash-bot",
            "csrf_token": f"csrf-flash-secret",
            "scope_drift_read": "on",
        },
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )
    assert create_resp.status_code == 303
    assert create_resp.headers["location"] == "/integrations/mcp?tab=api-keys"

    # First GET — secret must appear
    get1 = client.get(
        "/integrations/mcp?tab=api-keys",
        cookies={main.settings.session_cookie_name: session.session_id},
    )
    assert get1.status_code == 200
    assert "API key created" in get1.text
    assert "This secret will not be shown again" in get1.text

    # Second GET — secret must be gone (consumed)
    get2 = client.get(
        "/integrations/mcp?tab=api-keys",
        cookies={main.settings.session_cookie_name: session.session_id},
    )
    assert get2.status_code == 200
    # The one-time secret section should no longer be rendered
    assert "This secret will not be shown again" not in get2.text

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path


def test_api_keys_create_respects_staging_cp_api_entitlement_flag(tmp_path):
    from services.control_plane_records import upsert_entitlement

    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    original_app_env = main.settings.app_env
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"
    main.settings.app_env = "staging"

    _user, workspace, session = _setup_api_keys_db(tmp_path, "staging-gate", "1004")
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "standard",
            "feature_flags_json": '{"cp_api_enabled": false}',
        },
    )

    response = client.post(
        "/settings/api-keys",
        data={
            "display_name": "staging-gated-bot",
            "csrf_token": "csrf-staging-gate",
            "scope_drift_read": "on",
        },
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Control plane API is not enabled for this workspace."

    main.settings.app_encryption_key = original_enc
    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_api_keys_revoke_works(tmp_path):
    """POST revoke → 303; principal status becomes 'revoked'."""
    from services.control_plane_records import (
        create_machine_principal,
        get_machine_principal_by_client_id,
    )
    from services.secure_store import encrypt_text as _enc

    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, workspace, session = _setup_api_keys_db(tmp_path, "revoke-ok", "1004")

    # Seed a principal directly in the DB
    enc_secret = _enc("raw-secret-here", main.settings.app_encryption_key)
    principal = create_machine_principal(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        display_name="revoke-target",
        principal_kind="service_account",
        client_id="revoke-client-id-ui",
        client_secret_encrypted=enc_secret,
        scopes=["drift.read"],
    )

    revoke_resp = client.post(
        f"/settings/api-keys/{principal.client_id}/revoke",
        data={"csrf_token": f"csrf-revoke-ok"},
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )
    assert revoke_resp.status_code == 303
    assert revoke_resp.headers["location"] == "/integrations/mcp?tab=api-keys"

    updated = get_machine_principal_by_client_id(main.AUDIT_DB_PATH, principal.client_id)
    assert updated is not None
    assert updated.status == "revoked"

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path


def test_api_keys_revoke_idor_rejected(tmp_path):
    """A user in workspace1 must not be able to revoke a principal in workspace2."""
    from services.control_plane_records import (
        create_machine_principal,
        create_user_session,
        create_workspace,
        upsert_entitlement,
        upsert_github_identity,
        upsert_subscription,
        upsert_workspace_membership,
    )
    from services.secure_store import encrypt_text as _enc

    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    # Setup workspace1 (attacker)
    _user1, workspace1, session1 = _setup_api_keys_db(tmp_path, "idor-ws1", "2001")

    # Setup workspace2 (victim) in the same DB
    user2, _id2 = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="ak-2002",
        github_login="ak-user-2002",
        display_name="AK User 2002",
        primary_email="ak-2002@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted",
    )
    workspace2 = create_workspace(
        main.AUDIT_DB_PATH,
        billing_owner_user_id=user2.id,
        display_name="AK Workspace 2002",
        slug="ak-workspace-2002",
    )
    upsert_workspace_membership(
        main.AUDIT_DB_PATH,
        workspace_id=workspace2.id,
        user_id=user2.id,
        role="owner",
        invitation_state="accepted",
    )
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace2.id,
        stripe_subscription_id="sub_ak_2002",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=time.time(),
        current_period_end_at=time.time() + 86400,
        next_payment_at=time.time() + 86400,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        main.AUDIT_DB_PATH,
        workspace_id=workspace2.id,
        payload={
            "plan_code": "team",
            "subscription_status": "active",
            "dashboard_enabled": True,
            "pr_comments_enabled": True,
            "repo_limit": 5,
            "org_limit": 1,
            "seat_limit": 5,
            "retention_policy": "standard",
            "support_tier": "standard",
            "feature_flags_json": "{}",
        },
    )

    # Seed a principal in workspace2
    enc_secret = _enc("raw-secret-idor", main.settings.app_encryption_key)
    victim_principal = create_machine_principal(
        main.AUDIT_DB_PATH,
        workspace_id=workspace2.id,
        display_name="victim-principal",
        principal_kind="service_account",
        client_id="victim-client-id-idor",
        client_secret_encrypted=enc_secret,
        scopes=["drift.read"],
    )

    # Attacker (session1 → workspace1) tries to revoke workspace2's principal
    resp = client.post(
        f"/settings/api-keys/{victim_principal.client_id}/revoke",
        data={"csrf_token": "csrf-idor-ws1"},
        cookies={main.settings.session_cookie_name: session1.session_id},
        follow_redirects=False,
    )
    assert resp.status_code == 403

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path


def test_api_keys_create_rejects_empty_scopes(tmp_path):
    """POST with no scope checkboxes → 400."""
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "empty-scopes", "1005")

    response = client.post(
        "/settings/api-keys",
        data={
            "display_name": "no-scope-bot",
            "csrf_token": "csrf-empty-scopes",
            # Deliberately omit all scope_* fields
        },
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 400


def test_api_keys_page_shows_scope_checkboxes(tmp_path):
    """GET MCP API-keys tab must show all three customer scope labels."""
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "scope-ui", "1006")

    response = client.get(
        "/integrations/mcp?tab=api-keys",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert 'type="checkbox" name="scope_drift_read"' in response.text
    assert 'type="checkbox" name="scope_drift_write_low"' in response.text
    assert 'type="checkbox" name="scope_drift_write_high"' in response.text
    assert "drift.read" in response.text
    assert "drift.write.low" in response.text
    assert "drift.write.high" in response.text


def test_mcp_integrations_page_loads_for_owner(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "mcp-page", "1101")

    response = client.get(
        "/integrations/mcp",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert "Agent Integrations" in response.text
    assert 'href="/integrations/mcp" class="sidebar-nav-item sidebar-nav-item-active" aria-label="Agent Integrations"' in response.text
    assert response.text.index('href="/integrations/mcp" class="sidebar-nav-item sidebar-nav-item-active" aria-label="Agent Integrations"') < response.text.index('href="/settings" class="sidebar-nav-item" aria-label="Settings"')
    assert "Customer MCP connector package" in response.text
    assert "hosted Vipari broker" in response.text
    assert "internal Vipari bearer tokens" in response.text
    assert "Trust boundary" in response.text
    assert "/integrations/mcp/download" in response.text
    assert "API keys" in response.text
    assert "Activity" in response.text
    assert "Download connector" in response.text
    assert "Host configuration" in response.text
    assert "One connector session maps to one workspace." in response.text


def test_mcp_integrations_page_loads_for_viewer(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "mcp-viewer", "1104", role="viewer")

    response = client.get(
        "/integrations/mcp",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert "Agent Integrations" in response.text
    assert "Download connector" in response.text
    assert "API keys" not in response.text
    assert "Activity" not in response.text
    assert "Request API-key access" in response.text
    assert "Coordinate rollout" in response.text
    assert 'aria-current="page">Overview<' in response.text
    assert "Client ID" not in response.text


def test_mcp_integrations_sensitive_tabs_fall_back_to_overview_for_viewer(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "mcp-viewer-tabs", "1106", role="viewer")

    api_keys_response = client.get(
        "/integrations/mcp?tab=api-keys",
        cookies={main.settings.session_cookie_name: session.session_id},
    )
    activity_response = client.get(
        "/integrations/mcp?tab=activity",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path

    assert api_keys_response.status_code == 200
    assert activity_response.status_code == 200
    assert "Download connector" in api_keys_response.text
    assert "Download connector" in activity_response.text
    assert "Machine principal credentials" not in api_keys_response.text
    assert "Recent integration and API-key events" not in activity_response.text
    assert 'aria-current="page">Overview<' in api_keys_response.text
    assert 'aria-current="page">Overview<' in activity_response.text


def test_settings_page_hides_mcp_integrations_block(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "mcp-settings-link", "1103")

    response = client.get(
        "/settings",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert "Vipari MCP connector" not in response.text
    assert "Open Agent Integrations" not in response.text


def test_mcp_integrations_download_returns_customer_bundle(tmp_path):
    import io as _io
    import zipfile as _zipfile

    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    _user, _workspace, session = _setup_api_keys_db(tmp_path, "mcp-download", "1102")

    response = client.get(
        "/integrations/mcp/download",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "vipari-mcp-connector.zip" in response.headers["content-disposition"]

    archive = _zipfile.ZipFile(_io.BytesIO(response.content))
    names = set(archive.namelist())
    assert "vipari_mcp_server.py" in names
    assert "requirements.txt" in names
    assert "vipari.env.example" in names
    assert "claude-desktop-config.json.example" in names
    assert "tool-manifest.json" in names
    env_example = archive.read("vipari.env.example").decode("utf-8")
    expected_broker_url = f"VIPARI_MCP_BROKER_URL={main.settings.app_base_url}/api/agent-integrations/mcp"
    assert expected_broker_url in env_example
    claude_example = archive.read("claude-desktop-config.json.example").decode("utf-8")
    assert f'"VIPARI_MCP_BROKER_URL": "{main.settings.app_base_url}/api/agent-integrations/mcp"' in claude_example
    readme = archive.read("README.md").decode("utf-8")
    assert "Recommended rollout order" in readme
    assert "Vipari shows the secret once at creation time." in readme
    assert "No tools appear in the MCP host" in readme


def test_mcp_activity_tab_shows_workspace_audit_events(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_enc = main.settings.app_encryption_key
    main.settings.app_encryption_key = "very-secret-key-exactly-32chars!"

    user, workspace, session = _setup_api_keys_db(tmp_path, "mcp-activity", "1105")
    main.create_control_plane_audit_log(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        event_type="principal.created",
        subject_type="machine_principal",
        subject_id="activity-client-id",
        payload={"source": "self_service", "scopes": ["drift.read"]},
    )

    response = client.get(
        "/integrations/mcp?tab=activity",
        cookies={main.settings.session_cookie_name: session.session_id},
    )

    main.settings.app_encryption_key = original_enc
    main.AUDIT_DB_PATH = original_db_path

    assert response.status_code == 200
    assert "Recent integration and API-key events" in response.text
    assert "principal.created" in response.text
    assert "source=self_service" in response.text
