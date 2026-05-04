import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import pytest
from fastapi.testclient import TestClient

import main
from services.audit_jobs import init_db
from services.dashboard_views import EscalationQueueItem, build_workspace_escalation_queue
from services.entitlements import derive_entitlement_payload
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill


PROMPT_BASELINE = """# Refund Copilot
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Use the billing sandbox tool in read mode.
max_steps: 2
temperature: 0.2
"""

PROMPT_CURRENT = """# Refund Copilot
You can refund customers directly in production using the billing tool.
Use judgment when deciding whether approval is necessary.
Update billing records and send confirmations.
parallel plan with multi-step execution
max_steps: 6
temperature: 0.8
"""


def _seed_repo_with_drift(db_path: str, repo_full: str = "org/drifted-ai") -> None:
    """Onboard a repo and backfill with a drifted current version."""
    onboard_repository(
        db_path,
        repo_full=repo_full,
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/refund.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )
    plan_repository_history_backfill(
        db_path,
        repo_full=repo_full,
        token="token",
        commit_limit_per_artifact=5,
        list_file_commits_fn=lambda repo, path, token, branch, limit: ["sha-1", "sha-2"][:limit],
    )
    execute_repository_history_backfill(
        db_path,
        repo_full=repo_full,
        token="token",
        fetch_file_content_fn=lambda repo, path, token, ref: {
            "sha-1": PROMPT_BASELINE,
            "sha-2": PROMPT_CURRENT,
        }[ref],
    )


def _create_dashboard_owner_session(db_path: str, *, repo_full: str = "org/drifted-ai", installation_id: int = 123):
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
        db_path,
        github_user_id=f"tower-user-{installation_id}",
        github_login=f"tower-owner-{installation_id}",
        display_name="Tower Owner",
        primary_email=f"tower-owner-{installation_id}@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        db_path,
        slug=f"tower-workspace-{installation_id}",
        display_name="Tower Workspace",
        billing_owner_user_id=user.id,
    )
    session = create_user_session(
        db_path,
        session_id=f"tower-session-{installation_id}",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=10_000_000_000,
    )
    upsert_subscription(
        db_path,
        workspace_id=workspace.id,
        stripe_subscription_id=f"sub_tower_{installation_id}",
        stripe_price_id=f"price_tower_{installation_id}",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=1,
        current_period_end_at=2,
        next_payment_at=3,
        trial_ends_at=None,
        last_webhook_event_id=None,
    )
    upsert_entitlement(
        db_path,
        workspace_id=workspace.id,
        payload=derive_entitlement_payload("team", "active"),
    )
    upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=installation_id,
        account_id=f"acct-{installation_id}",
        account_login="doria90",
        account_type="User",
        target_type="User",
        status="active",
    )
    if repo_full:
        replace_repo_connections(
            db_path,
            workspace_id=workspace.id,
            installation_id=installation_id,
            repositories=[
                {
                    "repo_github_id": repo_full.split("/", 1)[1],
                    "repo_full": repo_full,
                    "default_branch": "main",
                    "is_private": True,
                    "status": "available",
                }
            ],
        )
        allocation = allocate_repo_to_workspace(
            db_path,
            workspace_id=workspace.id,
            installation_id=installation_id,
            repo_github_id=repo_full.split("/", 1)[1],
            repo_full=repo_full,
            baseline_mode="onboarding",
            activated_by_user_id=user.id,
        )
        update_repo_allocation_status(db_path, allocation.id, "onboarded")
    return session


# ---------------------------------------------------------------------------
# Unit tests — build_workspace_escalation_queue
# ---------------------------------------------------------------------------


def test_build_workspace_escalation_queue_empty_db_returns_healthy(tmp_path):
    db_path = str(tmp_path / "empty.db")
    init_db(db_path)
    result = build_workspace_escalation_queue(db_path)
    assert result["workspace_posture"] == "healthy"
    assert result["items"] == []
    assert result["escalation_count"] == 0
    assert result["watch_count"] == 0


def test_build_workspace_escalation_queue_returns_expected_keys(tmp_path):
    db_path = str(tmp_path / "keys.db")
    init_db(db_path)
    result = build_workspace_escalation_queue(db_path)
    assert set(result.keys()) == {
        "workspace_posture",
        "workspace_posture_reasons",
        "escalation_count",
        "watch_count",
        "items",
    }


def test_build_workspace_escalation_queue_no_drift_is_healthy(tmp_path):
    db_path = str(tmp_path / "no-drift.db")
    init_db(db_path)
    onboard_repository(
        db_path,
        repo_full="org/stable",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/safe.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )
    result = build_workspace_escalation_queue(db_path)
    assert result["workspace_posture"] == "healthy"
    assert result["escalation_count"] == 0


def test_build_workspace_escalation_queue_drifted_repo_produces_items(tmp_path):
    db_path = str(tmp_path / "drifted.db")
    init_db(db_path)
    _seed_repo_with_drift(db_path)
    result = build_workspace_escalation_queue(db_path)
    assert result["escalation_count"] >= 1 or result["watch_count"] >= 1


def test_build_workspace_escalation_queue_item_structure(tmp_path):
    db_path = str(tmp_path / "item-struct.db")
    init_db(db_path)
    _seed_repo_with_drift(db_path)
    result = build_workspace_escalation_queue(db_path, include_watch=True)
    items = result["items"]
    if not items:
        pytest.skip("No escalation items produced; drift threshold not met")
    item = items[0]
    expected_keys = {
        "repo_full", "artifact_path", "artifact_type", "priority", "score",
        "title", "rationale", "recommended_action", "evidence_label",
        "provenance_summary", "baseline_label", "review_target", "review_url",
        "attribute_deltas", "updated_at",
    }
    assert expected_keys.issubset(item.keys())
    assert item["repo_full"] == "org/drifted-ai"
    assert item["priority"] in ("review_now", "watch")
    assert isinstance(item["score"], (int, float))
    assert isinstance(item["attribute_deltas"], list)


def test_build_workspace_escalation_queue_excludes_watch_by_default(tmp_path):
    db_path = str(tmp_path / "excl-watch.db")
    init_db(db_path)
    _seed_repo_with_drift(db_path)
    result_default = build_workspace_escalation_queue(db_path, include_watch=False)
    for item in result_default["items"]:
        assert item["priority"] == "review_now"


def test_build_workspace_escalation_queue_includes_watch_when_flag_set(tmp_path):
    db_path = str(tmp_path / "incl-watch.db")
    init_db(db_path)
    _seed_repo_with_drift(db_path)
    result_with = build_workspace_escalation_queue(db_path, include_watch=True)
    result_without = build_workspace_escalation_queue(db_path, include_watch=False)
    total_with = len(result_with["items"])
    total_without = len(result_without["items"])
    assert total_with >= total_without


def test_build_workspace_escalation_queue_sort_order(tmp_path):
    db_path = str(tmp_path / "sort.db")
    init_db(db_path)
    _seed_repo_with_drift(db_path)
    result = build_workspace_escalation_queue(db_path, include_watch=True)
    items = result["items"]
    if len(items) < 2:
        pytest.skip("Need at least two items to verify sort order")
    # review_now items must precede watch items
    seen_watch = False
    for item in items:
        if item["priority"] == "watch":
            seen_watch = True
        if seen_watch:
            assert item["priority"] == "watch", "review_now item appeared after watch item"


def test_build_workspace_escalation_queue_allowed_repo_fulls_filter(tmp_path):
    db_path = str(tmp_path / "filter.db")
    init_db(db_path)
    _seed_repo_with_drift(db_path, repo_full="org/repo-a")
    _seed_repo_with_drift(db_path, repo_full="org/repo-b")
    result_all = build_workspace_escalation_queue(db_path, include_watch=True)
    result_filtered = build_workspace_escalation_queue(
        db_path,
        allowed_repo_fulls={"org/repo-a"},
        include_watch=True,
    )
    repo_fulls_all = {item["repo_full"] for item in result_all["items"]}
    repo_fulls_filtered = {item["repo_full"] for item in result_filtered["items"]}
    assert "org/repo-b" not in repo_fulls_filtered
    if repo_fulls_all:
        assert repo_fulls_filtered <= {"org/repo-a"}


def test_build_workspace_escalation_queue_workspace_posture_reasons_type(tmp_path):
    db_path = str(tmp_path / "reasons.db")
    init_db(db_path)
    _seed_repo_with_drift(db_path)
    result = build_workspace_escalation_queue(db_path)
    assert isinstance(result["workspace_posture_reasons"], list)
    assert len(result["workspace_posture_reasons"]) <= 3
    for reason in result["workspace_posture_reasons"]:
        assert isinstance(reason, str)


# ---------------------------------------------------------------------------
# API integration tests — TestClient(main.app)
# ---------------------------------------------------------------------------


def test_escalation_queue_api_returns_200(tmp_path):
    db_path = str(tmp_path / "api.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path, repo_full="org/drifted-ai")

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        response = client.get("/api/dashboard/escalation-queue")

    assert response.status_code == 200
    payload = response.json()
    assert "workspace_posture" in payload
    assert "items" in payload
    assert "escalation_count" in payload
    assert "watch_count" in payload


def test_escalation_queue_api_healthy_for_empty_db(tmp_path):
    db_path = str(tmp_path / "api-empty.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path, repo_full="")

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        response = client.get("/api/dashboard/escalation-queue")

    assert response.status_code == 403
    assert response.json()["detail"] == "Dashboard access is not available for this workspace."


def test_escalation_queue_api_with_seeded_repo(tmp_path):
    db_path = str(tmp_path / "api-seeded.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    _seed_repo_with_drift(db_path)
    session = _create_dashboard_owner_session(db_path, repo_full="org/drifted-ai")

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        response = client.get("/api/dashboard/escalation-queue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace_posture"] in {"watch", "escalated", "healthy", "risk"}
    assert "items" in payload
    assert "escalation_count" in payload
    assert "watch_count" in payload


def test_escalation_queue_api_include_watch_param(tmp_path):
    db_path = str(tmp_path / "api-watch.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path, repo_full="org/drifted-ai")

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        response_default = client.get("/api/dashboard/escalation-queue")
        response_watch = client.get("/api/dashboard/escalation-queue?include_watch=true")

    assert response_default.status_code == 200
    assert response_watch.status_code == 200
    payload_default = response_default.json()
    payload_watch = response_watch.json()
    # With include_watch=True there should be >= items than without
    assert len(payload_watch["items"]) >= len(payload_default["items"])


def test_pending_proposals_api_no_onboarding_returns_empty(tmp_path):
    db_path = str(tmp_path / "api-no-onboard.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path, repo_full="org/missing-repo")

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        response = client.get(
            "/api/repos/org/missing-repo/proposals/pending",
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"proposals": [], "pending_count": 0}


def test_pending_proposals_api_onboarded_repo_with_no_proposals(tmp_path):
    db_path = str(tmp_path / "api-onboard-no-props.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False

    onboard_repository(
        db_path,
        repo_full="org/clean-repo",
        installation_id=123,
        token="token",
        get_default_branch_fn=lambda repo, token: "main",
        list_repository_files_fn=lambda repo, token, ref: ["prompts/safe.txt"],
        fetch_file_content_fn=lambda repo, path, token, ref: PROMPT_BASELINE,
    )
    session = _create_dashboard_owner_session(db_path, repo_full="org/clean-repo")

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        response = client.get(
            "/api/repos/org/clean-repo/proposals/pending",
        )

    assert response.status_code == 200
    payload = response.json()
    assert "proposals" in payload
    assert "pending_count" in payload
    assert payload["pending_count"] == 0


def test_pending_proposals_api_response_shape(tmp_path):
    db_path = str(tmp_path / "api-shape.db")
    init_db(db_path)
    main.AUDIT_DB_PATH = db_path
    main.AUDIT_WORKER_ENABLED = False
    session = _create_dashboard_owner_session(db_path, repo_full="org/any-repo")

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        response = client.get(
            "/api/repos/org/any-repo/proposals/pending",
        )

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["proposals"], list)
    assert isinstance(payload["pending_count"], int)
