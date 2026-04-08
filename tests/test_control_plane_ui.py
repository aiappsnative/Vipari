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


def test_app_page_renders_preview_state():
    response = client.get("/app?state=payment_failed")

    assert response.status_code == 200
    assert "Billing needs attention" in response.text
    assert "Fix billing" in response.text


def test_app_page_redirects_to_login_when_unauthenticated():
    response = client.get("/app", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_github_auth_start_redirects_to_provider_when_configured():
    with patch.object(main.settings, "github_oauth_client_id", "client-id"), patch.object(
        main.settings, "github_oauth_client_secret", "client-secret"
    ), patch.object(main.settings, "github_oauth_callback_url", "http://testserver/auth/github/callback"):
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


def test_billing_install_allocation_flow_unlocks_dashboard(tmp_path):
    original_db_path = main.AUDIT_DB_PATH
    main.AUDIT_DB_PATH = str(tmp_path / "control-plane-flow.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import create_user_session, create_workspace, upsert_github_identity

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
            follow_redirects=False,
        )

        assert checkout_response.status_code == 303
        assert "/app/billing?checkout_session_id=" in checkout_response.headers["location"]

        access_after_checkout = client.get(
            "/api/auth/session",
            cookies={main.settings.session_cookie_name: session.session_id},
        ).json()
        assert access_after_checkout["access"]["state"] == "billing_pending_confirmation"

        stripe_event = {
            "id": "evt_subscription_active",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_team_active",
                    "customer": "cus_team_active",
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
        webhook_response = client.post(
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
    )
    install_response = client.post(
        "/app/setup/install/link",
        cookies={main.settings.session_cookie_name: viewer_session.session_id},
        data={
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
    )

    assert response.status_code == 400
    main.AUDIT_DB_PATH = original_db_path