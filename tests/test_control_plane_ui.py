import json
import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

import main
from services.billing_service import build_stripe_signature
from services.auth_service import GithubOAuthToken, GithubUserProfile
from services.secure_store import decrypt_text


client = TestClient(main.app)


@pytest.fixture(autouse=True)
def reset_test_client_cookies():
    client.cookies.clear()
    yield
    client.cookies.clear()


def test_marketing_page_renders():
    response = client.get("/")

    assert response.status_code == 200
    assert "DriftGuard Control Plane" in response.text
    assert "GitHub-native AI governance" in response.text


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
        granted_scopes=["read:user"],
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


def test_github_auth_start_requires_encryption_key_when_oauth_is_enabled():
    with patch.object(main.settings, "github_oauth_client_id", "client-id"), patch.object(
        main.settings, "github_oauth_client_secret", "client-secret"
    ), patch.object(main.settings, "app_encryption_key", ""):
        response = client.get("/auth/github/start", follow_redirects=False)

    assert response.status_code == 503
    assert response.json()["detail"] == "APP_ENCRYPTION_KEY must be configured before GitHub OAuth can store user tokens."


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
    assert "Setup checklist" in get_response.text
    assert "Plan active" in get_response.text
    assert 'href="/dashboard"' in get_response.text
    assert "sidebar" in get_response.text
    assert 'data-theme="dark"' in get_response.text
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
    assert "Urgent Items for Review" in dashboard_html
    assert "Version Journey" in dashboard_html
    assert "Repo Posture Radar" in dashboard_html
    assert "Coverage" in dashboard_html
    assert 'href="/app/setup/repos"' in dashboard_html
    assert 'id="audit-logs-link"' in dashboard_html
    assert 'class="sidebar-profile-link"' in dashboard_html
    assert 'id="journey-repo-name"' in dashboard_html

    repo_dashboard_html = render_repo_dashboard_page("doria90/hermes-agent", get_user_by_id(main.AUDIT_DB_PATH, user.id).theme_preference)
    assert 'class="repo-audit-page"' in repo_dashboard_html
    assert "Audit Page" in repo_dashboard_html
    assert "Audit Queue" in repo_dashboard_html
    assert 'href="/dashboard/doria90/hermes-agent"' in repo_dashboard_html
    assert 'href="/app/setup/repos"' in repo_dashboard_html

    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_requires_explicit_allowlist(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_logins = main.settings.admin_github_logins
    original_ids = main.settings.admin_github_user_ids
    original_emails = main.settings.admin_emails
    main.AUDIT_DB_PATH = str(tmp_path / "admin-guard.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.admin_github_logins = ""
    main.settings.admin_github_user_ids = ""
    main.settings.admin_emails = ""

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
    assert response.json()["detail"] == "Admin access is not enabled for this GitHub identity."

    main.settings.admin_github_logins = original_logins
    main.settings.admin_github_user_ids = original_ids
    main.settings.admin_emails = original_emails
    main.AUDIT_DB_PATH = original_db_path


def test_admin_page_renders_registered_and_unclaimed_install_data(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    original_logins = main.settings.admin_github_logins
    main.AUDIT_DB_PATH = str(tmp_path / "admin-data.db")
    main.init_db(main.AUDIT_DB_PATH)
    main.settings.admin_github_logins = "admin-user"

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
    assert "Free Installed User" in response.text
    assert response.text.count("Free Installed Workspace") == 1
    assert "free-install-org (2 installs)" in response.text
    assert ">2<" in response.text
    assert ">0<" in response.text
    assert "marketplace-org" in response.text
    assert "purchase-admin-1" in response.text

    main.settings.admin_github_logins = original_logins
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
    assert install_response.headers["location"] == "/app/setup/repos"

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
            "/app/setup/repos/allocate?repo_full=doria90/dummyAI",
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
    assert "Urgent Items for Review" in dashboard_response.text
    assert "Repo Posture Radar" in dashboard_response.text
    assert 'href="/app/setup/repos"' in dashboard_response.text

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

    assert response.status_code == 303
    assert response.headers["location"] == "/app/setup/repos?installation_linked=1&setup_action=install"

    auth_payload = client.get(
        "/api/auth/session",
        cookies={main.settings.session_cookie_name: session.session_id},
    ).json()
    assert auth_payload["access"]["state"] == "workspace_no_subscription"

    repo_setup_response = client.get(
        "/app/setup/repos",
        cookies={main.settings.session_cookie_name: session.session_id},
    )
    assert repo_setup_response.status_code == 200
    assert "doria90/dummyAI" in repo_setup_response.text
    assert 'class="repo-setup-page"' in repo_setup_response.text
    assert "Available Repositories" in repo_setup_response.text
    assert 'href="/dashboard"' in repo_setup_response.text
    assert 'href="/app/setup/repos"' in repo_setup_response.text
    assert 'href="/dashboard/doria90%2FdummyAI"' in repo_setup_response.text
    assert "Open audit page" in repo_setup_response.text

    main.AUDIT_DB_PATH = original_db_path


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