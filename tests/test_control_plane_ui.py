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


client = TestClient(main.app)


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


def test_dashboard_actor_login_uses_authenticated_identity_context():
    request = SimpleNamespace()
    identity = SimpleNamespace(github_login="doria90")

    with patch("main._control_plane_active", return_value=True), patch(
        "main._current_authenticated_identity_context",
        return_value={"identity": identity},
    ):
        actor_login = main._dashboard_actor_login(request)

    assert actor_login == "doria90"


def test_marketing_page_renders():
    response = client.get("/")

    assert response.status_code == 200
    assert "DriftGuard Control Plane" in response.text
    assert "GitHub-native AI governance" in response.text
    assert "AI-assisted review output labels" in response.text


def test_pricing_page_renders_plan_cards():
    response = client.get("/pricing")

    assert response.status_code == 200
    assert "Starter" in response.text
    assert "Team" in response.text
    assert "Enterprise" in response.text
    assert "Business" in response.text


def test_login_page_renders_github_entry():
    response = client.get("/login")

    assert response.status_code == 200
    assert "Sign in with GitHub" in response.text


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
    response = client.get("/app", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_app_page_ignores_preview_state_and_redirects_to_login_when_unauthenticated():
    response = client.get("/app?state=payment_failed", follow_redirects=False)

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
    assert response.headers["location"] == "/app/workspaces/new?source=base44&plan=team"
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
    assert response.headers["location"] == "/app/workspaces/new?source=base44&plan=team"
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
        "/app/workspaces/bootstrap?name=PromptDrift%20Team",
        data={"csrf_token": session.csrf_secret},
        cookies={
            main.settings.session_cookie_name: session.session_id,
            "promptdrift_oauth_context": main._encode_context_cookie({"source": "base44", "plan": "team"}),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/app/billing?source=base44&plan=team"

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
        "/app/billing/checkout",
        data={"plan": "free", "csrf_token": session.csrf_secret},
        cookies={main.settings.session_cookie_name: session.session_id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/app/setup/install?free_activated=1"

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

    assert response.status_code == 403
    assert response.json()["detail"] == "Dashboard access is not available for this workspace."

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

    response = client.get("/app/profile", cookies={main.settings.session_cookie_name: session.session_id})

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

    get_response = client.get("/app/profile", cookies={main.settings.session_cookie_name: session.session_id})
    assert get_response.status_code == 200
    assert "Starter User" in get_response.text
    assert "starter-user" in get_response.text
    assert "Next payment date" in get_response.text
    assert "Setup checklist" not in get_response.text
    assert "<span class=\"control-page-stat-label\">Plan</span>" in get_response.text
    assert "Permission level" in get_response.text
    assert 'href="/dashboard"' in get_response.text
    assert 'href="/app/admin"' not in get_response.text
    assert "sidebar" in get_response.text
    assert 'data-theme="dark"' in get_response.text
    assert 'data-theme-toggle' in get_response.text
    assert 'value="dark" checked' in get_response.text

    workspace_response = client.get("/app", cookies={main.settings.session_cookie_name: session.session_id}, follow_redirects=False)
    assert workspace_response.status_code == 303
    assert workspace_response.headers["location"] == "/app/profile"

    post_response = client.post(
        "/app/profile",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={"display_name": "Updated Starter User", "theme_preference": "light", "csrf_token": session.csrf_secret},
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/app/profile?updated=1"
    assert get_user_by_id(main.AUDIT_DB_PATH, user.id).display_name == "Updated Starter User"
    assert get_user_by_id(main.AUDIT_DB_PATH, user.id).theme_preference == "light"

    updated_get_response = client.get("/app/profile", cookies={main.settings.session_cookie_name: session.session_id})
    assert updated_get_response.status_code == 200
    assert 'data-theme="light"' in updated_get_response.text
    assert 'value="light" checked' in updated_get_response.text

    from services.dashboard_frontend import render_dashboard_index_page, render_repo_dashboard_page

    dashboard_html = render_dashboard_index_page(get_user_by_id(main.AUDIT_DB_PATH, user.id).theme_preference)
    assert 'data-theme="light"' in dashboard_html
    assert 'class="dashboard-index-page"' in dashboard_html
    assert "Needs attention now" in dashboard_html
    assert "History and drift timeline" in dashboard_html
    assert "Posture map" in dashboard_html
    assert "Coverage" in dashboard_html
    assert 'href="/app/repos"' in dashboard_html
    assert 'href="/app/compliance"' in dashboard_html
    assert 'id="audit-logs-toggle"' in dashboard_html
    assert 'class="sidebar-profile-link"' in dashboard_html
    assert 'id="journey-repo-name"' in dashboard_html
    assert 'class="journey-stage loading-shell"' in dashboard_html
    assert dashboard_html.index("Repository map") < dashboard_html.index("Needs attention now")

    repo_dashboard_html = render_repo_dashboard_page("doria90/hermes-agent", get_user_by_id(main.AUDIT_DB_PATH, user.id).theme_preference)
    assert 'class="repo-audit-page"' in repo_dashboard_html
    assert 'data-theme="light"' in repo_dashboard_html
    assert "Audit Page" in repo_dashboard_html
    assert "Audit Queue" in repo_dashboard_html
    assert "EU AI Act relevance" in repo_dashboard_html
    assert "Governance attention" in repo_dashboard_html
    assert "Loading EU AI Act, SOC 2, and ISO 27001 governance guidance..." in repo_dashboard_html
    assert "Static posture" not in repo_dashboard_html
    assert 'id="audit-logs-toggle"' in repo_dashboard_html
    assert 'href="/app/repos"' in repo_dashboard_html
    assert 'href="/app/compliance"' in repo_dashboard_html
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
        create_user_session,
        create_workspace,
        get_workspace_by_id,
        upsert_entitlement,
        upsert_github_identity,
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

    get_response = client.get("/app/settings", cookies={main.settings.session_cookie_name: session.session_id})
    assert get_response.status_code == 200
    assert "Workspace settings" in get_response.text
    assert 'value="Settings Workspace"' in get_response.text
    assert "PR comments" in get_response.text
    assert "Effective status" in get_response.text
    assert "Allowed users and permissions" in get_response.text
    assert 'aria-label="Add user"' in get_response.text
    assert "Onboarded and allocated repositories" in get_response.text
    assert 'data-theme-toggle' in get_response.text
    assert 'value="on" checked' in get_response.text
    assert "{{WORKSPACE_NAME_INPUT}}" not in get_response.text
    assert "{{WORKSPACE_MEMBER_ACTIONS}}" not in get_response.text

    post_response = client.post(
        "/app/settings",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={
            "workspace_name": "Renamed Settings Workspace",
            "pr_comments_setting": "off",
            "csrf_token": session.csrf_secret,
        },
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/app/settings?updated=1"
    updated_workspace = get_workspace_by_id(main.AUDIT_DB_PATH, workspace.id)
    assert updated_workspace.pr_comments_setting_enabled is False
    assert updated_workspace.display_name == "Renamed Settings Workspace"

    updated_get_response = client.get("/app/settings", cookies={main.settings.session_cookie_name: session.session_id})
    assert updated_get_response.status_code == 200
    assert 'value="off" checked' in updated_get_response.text
    assert 'value="Renamed Settings Workspace"' in updated_get_response.text
    assert "Paused" in updated_get_response.text

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
        "/app/settings/invite",
        cookies={main.settings.session_cookie_name: session.session_id},
        data={"github_login": "@new-teammate", "role": "admin", "csrf_token": session.csrf_secret},
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/app/settings?invite_added=1"

    invites = list_workspace_invites_for_workspace(main.AUDIT_DB_PATH, workspace.id)
    assert len(invites) == 1
    assert invites[0].invited_github_login == "new-teammate"
    assert invites[0].role == "admin"
    assert invites[0].invitation_state == "pending"

    get_response = client.get("/app/settings?invite_added=1", cookies={main.settings.session_cookie_name: session.session_id})
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


def test_help_and_policies_pages_render_tbd_placeholders(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "placeholder-pages.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_entitlement, upsert_github_identity, upsert_subscription

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

    help_response = client.get("/app/help", cookies={main.settings.session_cookie_name: session.session_id})
    policies_response = client.get("/app/policies", cookies={main.settings.session_cookie_name: session.session_id})

    assert help_response.status_code == 200
    assert policies_response.status_code == 200
    assert "We are working on this" in help_response.text
    assert "We are working on this" in policies_response.text
    assert 'href="/app/compliance"' in help_response.text
    assert 'href="/app/compliance"' in policies_response.text
    assert 'data-theme-toggle' in help_response.text
    assert 'data-theme-toggle' in policies_response.text

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

    response = client.get("/app/admin", cookies={main.settings.session_cookie_name: session.session_id})

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

    response = client.get("/app/profile", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert 'href="/app/admin"' in response.text
    assert "Open system admin" in response.text

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.settings.app_env = original_app_env
    main.AUDIT_DB_PATH = original_db_path


def test_settings_help_and_policies_show_admin_link_for_local_billing_owner_without_owner_config(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    original_id = main.settings.owner_github_user_id
    original_email = main.settings.owner_email
    original_app_env = main.settings.app_env
    main.AUDIT_DB_PATH = str(tmp_path / "local-owner-nav.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = ""
    main.settings.owner_github_user_id = ""
    main.settings.owner_email = ""
    main.settings.app_env = "local"

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
    settings_response = client.get("/app/settings", cookies=cookies)
    help_response = client.get("/app/help", cookies=cookies)
    policies_response = client.get("/app/policies", cookies=cookies)

    assert settings_response.status_code == 200
    assert help_response.status_code == 200
    assert policies_response.status_code == 200
    assert 'href="/app/admin"' in settings_response.text
    assert 'href="/app/admin"' in help_response.text
    assert 'href="/app/admin"' in policies_response.text

    main.settings.owner_github_login = original_login
    main.settings.owner_github_user_id = original_id
    main.settings.owner_email = original_email
    main.settings.app_env = original_app_env
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
    main.AUDIT_DB_PATH = str(tmp_path / "admin-data.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"

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

    response = client.get("/app/admin", cookies={main.settings.session_cookie_name: admin_session.session_id})

    assert response.status_code == 200
    assert "Control-plane oversight" in response.text
    assert "Aggregated workspace accounts" in response.text
    assert "Add user" in response.text
    assert "Free Installed User" in response.text
    assert "Free Installed Workspace" in response.text
    assert "Installs 1" in response.text
    assert "Connected 2" in response.text
    assert "Onboarded 0" in response.text
    assert "marketplace-org" in response.text
    assert "purchase-admin-1" in response.text

    main.settings.owner_github_login = original_login
    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_renders_github_profile_details(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    main.AUDIT_DB_PATH = str(tmp_path / "admin-profile-data.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"

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

    response = client.get("/app/admin", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert "profile@example.com" in response.text
    assert "PromptDrift" in response.text
    assert "Berlin" in response.text
    assert "Builds review pipelines." in response.text
    assert "profile_user" in response.text
    assert "https://github.com/profile-user" in response.text
    assert "Recent admin activity" in response.text

    main.settings.owner_github_login = original_login
    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_can_create_update_and_delete_users_workspaces_and_memberships(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    main.AUDIT_DB_PATH = str(tmp_path / "admin-crud.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"

    from services.control_plane_records import (
        create_user_session,
        get_user_by_id,
        get_workspace_by_id,
        get_workspace_membership,
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
        "/app/admin/users/create",
        data={"display_name": "Managed User", "primary_email": "managed@example.com", "csrf_token": session.csrf_secret},
        cookies=cookie,
        follow_redirects=False,
    )
    assert create_user_response.status_code == 303

    managed_user_row = next(row for row in list_admin_workspace_users(main.AUDIT_DB_PATH) if row.primary_email == "managed@example.com")
    managed_user_id = managed_user_row.user_id

    create_workspace_response = client.post(
        "/app/admin/workspaces/create",
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
        "/app/admin/memberships/upsert",
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
        f"/app/admin/users/{managed_user_id}/update",
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
        f"/app/admin/workspaces/{workspace_id}/update",
        data={
            "display_name": "Managed Workspace Updated",
            "slug": "managed-workspace-updated",
            "csrf_token": session.csrf_secret,
        },
        cookies=cookie,
        follow_redirects=False,
    )
    assert update_workspace_response.status_code == 303
    updated_workspace = get_workspace_by_id(main.AUDIT_DB_PATH, workspace_id)
    assert updated_workspace is not None
    assert updated_workspace.display_name == "Managed Workspace Updated"
    assert updated_workspace.slug == "managed-workspace-updated"

    delete_membership_response = client.post(
        f"/app/admin/memberships/{workspace_id}/{admin_user.id}/delete",
        data={"csrf_token": session.csrf_secret},
        cookies=cookie,
        follow_redirects=False,
    )
    assert delete_membership_response.status_code == 303
    assert get_workspace_membership(main.AUDIT_DB_PATH, workspace_id, admin_user.id) is None

    delete_workspace_response = client.post(
        f"/app/admin/workspaces/{workspace_id}/delete",
        data={"csrf_token": session.csrf_secret},
        cookies=cookie,
        follow_redirects=False,
    )
    assert delete_workspace_response.status_code == 303
    assert get_workspace_by_id(main.AUDIT_DB_PATH, workspace_id) is None

    delete_user_response = client.post(
        f"/app/admin/users/{managed_user_id}/delete",
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
    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_delete_forms_include_confirmation_prompts(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_login = main.settings.owner_github_login
    main.AUDIT_DB_PATH = str(tmp_path / "admin-confirm.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.owner_github_login = "admin-user"

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

    response = client.get("/app/admin", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert "Delete this user and any linked workspace memberships?" in response.text
    assert "Delete this workspace and all linked records?" in response.text
    assert "Remove this user from the workspace?" in response.text

    main.settings.owner_github_login = original_login
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
        "/app/billing/claim?claim=claim-email-guard-1",
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
    main.AUDIT_DB_PATH = str(tmp_path / "gated.db")
    main.init_db(main.AUDIT_DB_PATH)

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
            "/app/billing?plan=team&source=base44",
            cookies={main.settings.session_cookie_name: session.session_id},
        )
        assert billing_page_response.status_code == 200
        assert "Continue with this plan" in billing_page_response.text

        checkout_response = client.post(
            "/app/billing/checkout?plan=team",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={"csrf_token": session.csrf_secret},
            follow_redirects=False,
        )

        assert checkout_response.status_code == 303
        assert "/app/billing?checkout_session_id=" in checkout_response.headers["location"]

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
        "/app/setup/install",
        cookies={main.settings.session_cookie_name: session.session_id},
    )
    assert install_page_response.status_code == 200
    assert "/app/setup/install/callback" in install_page_response.text

    install_response = client.post(
        "/app/setup/install/link",
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
    assert install_response.headers["location"] == "/app/repos"

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
            "/app/repos/allocate?repo_full=doria90/dummyAI",
            cookies={main.settings.session_cookie_name: session.session_id},
            data={"csrf_token": session.csrf_secret},
            follow_redirects=False,
        )

    assert allocate_response.status_code == 303
    assert allocate_response.headers["location"] == "/app"

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
    assert "Needs attention now" in dashboard_response.text
    assert "Posture map" in dashboard_response.text
    assert 'href="/app/repos"' in dashboard_response.text

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
            "/app/billing/checkout?plan=team",
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
        "/app/billing/checkout?plan=team",
        cookies={main.settings.session_cookie_name: viewer_session.session_id},
        data={"csrf_token": viewer_session.csrf_secret},
    )
    install_response = client.post(
        "/app/setup/install/link",
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
            f"/app/setup/install/callback?installation_id=12345&setup_action=install&state={workspace.id}",
            cookies={main.settings.session_cookie_name: session.session_id},
            follow_redirects=False,
        )

        repo_setup_response = client.get(
            "/app/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/app/repos?installation_linked=1&setup_action=install"

    auth_payload = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session.session_id},
    ).json()
    assert auth_payload["access"]["state"] == "workspace_no_subscription"

    assert repo_setup_response.status_code == 200
    assert "doria90/dummyAI" in repo_setup_response.text
    assert 'class="repo-setup-page"' in repo_setup_response.text
    assert "Repository Inventory" in repo_setup_response.text
    assert "5 of 5 repository slots available on this plan." in repo_setup_response.text
    assert "Onboarded Repository Snapshot" in repo_setup_response.text
    assert 'class="repo-setup-inventory-list"' in repo_setup_response.text
    assert 'data-repo-summary-sort' in repo_setup_response.text
    assert 'href="/dashboard"' in repo_setup_response.text
    assert 'href="/app/repos"' in repo_setup_response.text
    assert "Already there" in repo_setup_response.text

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


def test_compliance_page_lists_workspace_exports_and_repos(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "compliance-page.db")
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

    response = client.get("/app/compliance", cookies={main.settings.session_cookie_name: session.session_id})

    assert response.status_code == 200
    assert "Compliance workspace" in response.text
    assert "compliance-org/repo-one" in response.text
    assert "compliance-org/repo-two" in response.text
    assert "Run compliance exports" in response.text
    assert "Download" in response.text
    assert 'aria-label="Audit Logs"' in response.text
    assert "EU AI Act relevance assessment" in response.text
    assert "Next actions for stronger review packs" in response.text
    assert "How current the stored review evidence is" in response.text
    assert "AI control surface" in response.text
    assert "Governance surface" in response.text
    assert "AI-assisted tool surface" in response.text
    assert "Model/config surface" in response.text
    assert "Human-reviewed baseline" in response.text
    assert "No governance or policy artifact detected" in response.text
    assert "Approve or reject the pending baseline" in response.text
    assert "Stale evidence (45d)" in response.text
    assert "Review-ready preset" in response.text
    assert "Not review-ready yet" in response.text
    assert "Pending baseline approval keeps this repo out of review-ready presets" in response.text

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
            "/app/compliance/export",
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
    assert response.headers["location"].startswith("/app/compliance?status=")

    jobs = list_export_jobs_for_workspace_requester(main.AUDIT_DB_PATH, workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].repo_full == "compliance-org/repo-two"
    assert jobs[0].status == "completed"

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
            "/app/compliance/export",
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

    jobs = list_export_jobs_for_workspace_requester(main.AUDIT_DB_PATH, workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].repo_full == "compliance-org/repo-one"
    assert jobs[0].status == "failed"
    assert jobs[0].last_error == "zip failed"

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
            "/app/compliance/export",
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
    assert response.headers["location"].startswith("/app/compliance?status=")

    jobs = list_export_jobs_for_workspace_requester(main.AUDIT_DB_PATH, workspace.id, user.id)
    assert len(jobs) == 1
    assert jobs[0].repo_full == "compliance-org/repo-one"
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
            "/app/setup/install/callback?installation_id=12345&setup_action=install",
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
        "/app/billing/checkout?plan=unknown",
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
            "/app/repos",
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
            "/app/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    assert "Repository Inventory" in response.text
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
            "/app/repos",
            cookies={main.settings.session_cookie_name: session.session_id},
        )

    assert response.status_code == 200
    assert "Repository Inventory" in response.text
    assert "doria90/dummyAI" in response.text
    assert "doria90/PromptDrift" in response.text

    main.settings.app_encryption_key = original_encryption_key
    main.AUDIT_DB_PATH = original_db_path