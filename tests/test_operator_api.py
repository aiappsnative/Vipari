import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

import main
from services.audit_records import RepoStaticDriftSummary
from services.dashboard_views import (
    DashboardProfileVector,
    RepoDashboardArtifactEntry,
    RepoArtifactHistoryTimeline,
    RepoArtifactDesignProfile,
    RepoArtifactProvenance,
    RepoArtifactTimelinePoint,
    RepoDashboardBackfillSummary,
    RepoDashboardControlSurfaceGroup,
    RepoDashboardInsightEntry,
    RepoDashboardView,
)
from services.onboarding import HistoricalBackfillExecutionResult, RepositoryOnboardingResult
from services.onboarding_records import (
    HistoricalBackfillJobRecord,
    OnboardedArtifactRecord,
    OnboardingBaselineVersionRecord,
    RepositoryOnboardingRecord,
)
from engine.drift_profile import AgentAttributeProfile, StaticSignals


def _profile() -> AgentAttributeProfile:
    return AgentAttributeProfile(
        guardrail_robustness=0.7,
        capability_risk=0.2,
        autonomy_level=0.3,
        stability_vs_creativity=0.8,
        governance_strength=0.6,
        change_frequency=0.1,
        semantic_density=0.4,
        signals=StaticSignals(
            token_count=10,
            char_count=40,
            section_count=1,
            example_count=0,
            instruction_density=0.2,
            constraint_count=2,
            explicit_limit_count=1,
            ambiguity_count=0,
        ),
    )


def _dashboard(repo_full: str) -> RepoDashboardView:
    return RepoDashboardView(
        repo_full=repo_full,
        onboarding=RepositoryOnboardingRecord(
            id=1,
            repo_full=repo_full,
            installation_id=123,
            default_branch="main",
            status="completed",
            discovered_artifact_count=1,
            approved_by=None,
            approved_at=None,
            created_at=1.0,
            updated_at=1.0,
        ),
        baseline_review=None,
        backfill=RepoDashboardBackfillSummary(
            job_count=1,
            planned_job_count=0,
            processing_job_count=0,
            completed_job_count=1,
            failed_job_count=0,
            total_historical_versions=2,
            total_historical_profiles=2,
        ),
        pull_request_audit_count=0,
        baseline_version_count=1,
        drift_summary=RepoStaticDriftSummary(
            repo_full=repo_full,
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
        insights=[
            RepoDashboardInsightEntry(
                title="High-value control surface to baseline",
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                priority="baseline_review",
                score=0.8,
                rationale="This artifact looks like a real AI control surface but does not yet have meaningful drift context.",
                recommended_action="Confirm whether this is a true AI control surface and keep it in the monitored baseline set.",
            )
        ],
        control_surface_groups=[
            RepoDashboardControlSurfaceGroup(
                group_key="prompts",
                label="Prompts and instructions",
                artifact_count=1,
                high_confidence_count=1,
                top_artifact_paths=["prompts/system.txt"],
            )
        ],
        history_timelines=[
            RepoArtifactHistoryTimeline(
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                point_count=2,
                max_drift_magnitude=0.7,
                points=[
                    RepoArtifactTimelinePoint(
                        source="historical",
                        label="commit sha-1",
                        created_at=1.0,
                        semantic_distance=0.2,
                        capability_shift=0.1,
                        guardrail_shift=0.0,
                        autonomy_shift=0.1,
                        drift_magnitude=0.4,
                    ),
                    RepoArtifactTimelinePoint(
                        source="historical",
                        label="commit sha-2",
                        created_at=2.0,
                        semantic_distance=0.3,
                        capability_shift=0.2,
                        guardrail_shift=-0.1,
                        autonomy_shift=0.1,
                        drift_magnitude=0.7,
                    ),
                ],
            )
        ],
        design_profiles=[
            RepoArtifactDesignProfile(
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                drift_from_baseline=0.7,
                baseline_profile=DashboardProfileVector(
                    guardrail_robustness=0.7,
                    capability_risk=0.2,
                    autonomy_level=0.3,
                    stability_vs_creativity=0.8,
                    governance_strength=0.6,
                ),
                current_profile=DashboardProfileVector(
                    guardrail_robustness=0.5,
                    capability_risk=0.5,
                    autonomy_level=0.6,
                    stability_vs_creativity=0.4,
                    governance_strength=0.6,
                ),
                risk_tags=["capability expanded", "autonomy increased"],
                narrative=["Capability risk increased due to broader or more sensitive actions."],
                provenance=RepoArtifactProvenance(source_type="historical", label="commit sha-2", created_at=2.0),
            )
        ],
        artifacts=[
            RepoDashboardArtifactEntry(
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                discovery_reason="Path indicates a prompt artifact.",
                discovery_confidence=0.9,
                baseline_line_count=4,
                historical_version_count=2,
                historical_profile_count=2,
                latest_historical_semantic_distance=0.3,
                latest_historical_drift_magnitude=0.7,
                latest_historical_capability_shift=0.2,
                latest_historical_guardrail_shift=-0.1,
                latest_historical_governance_shift=0.0,
                latest_historical_autonomy_shift=0.1,
                pr_profile_count=0,
                latest_pr_semantic_distance=0.0,
                latest_pr_capability_shift=0.0,
                latest_pr_guardrail_shift=0.0,
                latest_pr_autonomy_shift=0.0,
                leaderboard_drift_magnitude=0.0,
            )
        ],
    )


def _seed_repo_dashboard_access(tmp_path, *, repo_full: str = "doria90/dummyAI", installation_id: int = 123, session_id: str = "operator-session"):
    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
        upsert_workspace_membership,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id=f"user-{session_id}",
        github_login=f"owner-{session_id}",
        display_name="Operator Owner",
        primary_email=f"{session_id}@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug=f"workspace-{session_id}",
        display_name="Operator Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_workspace_membership(main.AUDIT_DB_PATH, workspace_id=workspace.id, user_id=user.id, role="owner", invitation_state="accepted")
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id=f"sub-{session_id}",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=1.0,
        current_period_end_at=2.0,
        next_payment_at=2.0,
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
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation_id,
        account_id=str(installation_id),
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=installation_id,
        repo_github_id="dummyAI",
        repo_full=repo_full,
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")
    return create_user_session(
        main.AUDIT_DB_PATH,
        session_id=session_id,
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=9999999999.0,
    )


def test_onboard_api_runs_workflow_and_returns_dashboard_payload(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
        upsert_workspace_membership,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="4000",
        github_login="operator-owner",
        display_name="Operator Owner",
        primary_email="operator-owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="operator-workspace",
        display_name="Operator Workspace",
        billing_owner_user_id=user.id,
    )
    upsert_workspace_membership(main.AUDIT_DB_PATH, workspace_id=workspace.id, user_id=user.id, role="owner", invitation_state="accepted")
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_operator",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=1.0,
        current_period_end_at=2.0,
        next_payment_at=2.0,
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
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=123,
        account_id="123",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=123,
        repo_github_id="dummyAI",
        repo_full="doria90/dummyAI",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="operator-session",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=9999999999.0,
    )

    onboarding_record = RepositoryOnboardingRecord(
        id=1,
        repo_full="doria90/dummyAI",
        installation_id=123,
        default_branch="main",
        status="completed",
        discovered_artifact_count=1,
        approved_by=None,
        approved_at=None,
        created_at=1.0,
        updated_at=1.0,
    )
    onboarding_result = RepositoryOnboardingResult(
        onboarding=onboarding_record,
        artifacts=[
            OnboardedArtifactRecord(
                id=1,
                onboarding_id=1,
                repo_full="doria90/dummyAI",
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                discovery_reason="Path indicates a prompt artifact.",
                confidence=0.9,
                created_at=1.0,
            )
        ],
        baseline_versions=[
            OnboardingBaselineVersionRecord(
                id=1,
                onboarding_id=1,
                onboarded_artifact_id=1,
                normalized_artifact_id="doria90/dummyai::prompts/system.txt",
                artifact_path="prompts/system.txt",
                artifact_type="prompt",
                version_hash="hash",
                signal_terms=["safe"],
                line_count=4,
                profile=_profile(),
                approval_status="pending",
                approved_by=None,
                approved_at=None,
                approval_note=None,
                created_at=1.0,
            )
        ],
    )
    backfill_job = HistoricalBackfillJobRecord(
        id=1,
        onboarding_id=1,
        onboarded_artifact_id=1,
        repo_full="doria90/dummyAI",
        artifact_path="prompts/system.txt",
        artifact_type="prompt",
        job_kind="historical_backfill",
        status="completed",
        commit_count=2,
        completed_commit_count=2,
        commit_shas=["sha-2", "sha-1"],
        last_error=None,
        created_at=1.0,
        updated_at=1.0,
    )

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.onboard_repository", return_value=onboarding_result), patch(
        "main.plan_repository_history_backfill", return_value=[backfill_job]
    ), patch(
        "main.execute_repository_history_backfill",
        return_value=[HistoricalBackfillExecutionResult(job=backfill_job, versions=[], profiles=[])],
    ), patch("main.build_repo_dashboard_view", return_value=_dashboard("doria90/dummyAI")):
        with TestClient(main.app) as client:
            client.cookies.set(main.settings.session_cookie_name, session.session_id)
            response = client.post(
                "/api/repos/doria90/dummyAI/onboard",
                json={
                    "installation_id": 123,
                    "commit_limit_per_artifact": 5,
                    "plan_backfill": True,
                    "execute_backfill": True,
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_full"] == "doria90/dummyAI"
    assert payload["discovered_artifact_count"] == 1
    assert payload["planned_backfill_job_count"] == 1
    assert payload["executed_backfill_job_count"] == 1
    assert payload["dashboard"]["insights"][0]["artifact_path"] == "prompts/system.txt"
    assert payload["dashboard"]["artifacts"][0]["artifact_path"] == "prompts/system.txt"


def test_onboard_api_rejects_installation_mismatch(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator.db")
    main.init_db(main.AUDIT_DB_PATH)

    from services.control_plane_records import (
        allocate_repo_to_workspace,
        create_user_session,
        create_workspace,
        update_repo_allocation_status,
        upsert_entitlement,
        upsert_github_identity,
        upsert_github_installation,
        upsert_subscription,
        upsert_workspace_membership,
    )

    user, _identity = upsert_github_identity(
        main.AUDIT_DB_PATH,
        github_user_id="4001",
        github_login="operator-owner-2",
        display_name="Operator Owner Two",
        primary_email="operator-owner-2@example.com",
        avatar_url=None,
        granted_scopes=["read:user", "repo", "read:org"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        main.AUDIT_DB_PATH,
        slug="operator-workspace-2",
        display_name="Operator Workspace Two",
        billing_owner_user_id=user.id,
    )
    upsert_workspace_membership(main.AUDIT_DB_PATH, workspace_id=workspace.id, user_id=user.id, role="owner", invitation_state="accepted")
    upsert_subscription(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        stripe_subscription_id="sub_operator_2",
        stripe_price_id="price_team",
        plan_code="team",
        status="active",
        cancel_at_period_end=False,
        current_period_start_at=1.0,
        current_period_end_at=2.0,
        next_payment_at=2.0,
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
    upsert_github_installation(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=123,
        account_id="123",
        account_login="doria90",
        account_type="Organization",
        target_type="Organization",
    )
    allocation = allocate_repo_to_workspace(
        main.AUDIT_DB_PATH,
        workspace_id=workspace.id,
        installation_id=123,
        repo_github_id="dummyAI",
        repo_full="doria90/dummyAI",
        baseline_mode="onboarding",
        activated_by_user_id=user.id,
    )
    update_repo_allocation_status(main.AUDIT_DB_PATH, allocation.id, "onboarded")
    session = create_user_session(
        main.AUDIT_DB_PATH,
        session_id="operator-session-2",
        user_id=user.id,
        workspace_id=workspace.id,
        csrf_secret="csrf",
        expires_at=9999999999.0,
    )

    with TestClient(main.app) as client:
        client.cookies.set(main.settings.session_cookie_name, session.session_id)
        response = client.post(
            "/api/repos/doria90/dummyAI/onboard",
            json={
                "installation_id": 999,
                "commit_limit_per_artifact": 5,
                "plan_backfill": False,
                "execute_backfill": False,
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Installation mismatch for workspace access."


def test_repo_artifact_options_api_lists_untracked_files(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator-artifact-options.db")
    main.init_db(main.AUDIT_DB_PATH)

    session = _seed_repo_dashboard_access(tmp_path, session_id="artifact-options-session")

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch(
        "main.list_repository_files", return_value=["prompts/system.txt", "policies/usage.md", "src/app.py"]
    ), patch(
        "main.build_repo_dashboard_view", return_value=_dashboard("doria90/dummyAI")
    ):
        with TestClient(main.app) as client:
            client.cookies.set(main.settings.session_cookie_name, session.session_id)
            response = client.get("/api/repos/doria90%2FdummyAI/artifacts/options")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tracked_paths"] == ["prompts/system.txt"]
    file_paths = [item["path"] for item in payload["files"]]
    inferred_types = {item["path"]: item["inferred_artifact_type"] for item in payload["files"]}
    assert "prompts/system.txt" not in file_paths
    assert "policies/usage.md" in file_paths
    assert inferred_types["policies/usage.md"] == "policy"
    assert "prompt" in payload["artifact_type_options"]


def test_repo_artifact_mutation_apis_return_refreshed_dashboard(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator-artifact-mutations.db")
    main.init_db(main.AUDIT_DB_PATH)

    session = _seed_repo_dashboard_access(tmp_path, session_id="artifact-mutations-session")

    added_artifact = OnboardedArtifactRecord(
        id=2,
        onboarding_id=1,
        repo_full="doria90/dummyAI",
        artifact_path="policies/usage.md",
        artifact_type="policy",
        discovery_reason="Manually added from repository audit page.",
        confidence=1.0,
        created_at=2.0,
    )
    added_baseline = OnboardingBaselineVersionRecord(
        id=2,
        onboarding_id=1,
        onboarded_artifact_id=2,
        normalized_artifact_id="doria90/dummyai::policies/usage.md",
        artifact_path="policies/usage.md",
        artifact_type="policy",
        version_hash="hash-2",
        signal_terms=["review"],
        line_count=2,
        profile=_profile(),
        approval_status="approved",
        approved_by=None,
        approved_at=2.0,
        approval_note=None,
        created_at=2.0,
    )
    updated_artifact = OnboardedArtifactRecord(
        id=2,
        onboarding_id=1,
        repo_full="doria90/dummyAI",
        artifact_path="policies/usage.md",
        artifact_type="guardrail",
        discovery_reason="Manually added from repository audit page.",
        confidence=1.0,
        created_at=2.0,
    )

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch(
        "main.add_repo_artifact_to_onboarding", return_value=(added_artifact, added_baseline)
    ), patch(
        "main.update_repo_artifact_type", return_value=updated_artifact
    ), patch(
        "main.remove_repo_artifact_from_onboarding", return_value=None
    ), patch(
        "main.build_repo_dashboard_view", return_value=_dashboard("doria90/dummyAI")
    ):
        with TestClient(main.app) as client:
            client.cookies.set(main.settings.session_cookie_name, session.session_id)

            add_response = client.post(
                "/api/repos/doria90%2FdummyAI/artifacts",
                json={"artifact_path": "policies/usage.md"},
            )
            patch_response = client.patch(
                "/api/repos/doria90%2FdummyAI/artifacts/policies%2Fusage.md",
                json={"artifact_type": "guardrail"},
            )
            delete_response = client.delete("/api/repos/doria90%2FdummyAI/artifacts/policies%2Fusage.md")

    assert add_response.status_code == 200
    assert add_response.json()["artifact"]["artifact_path"] == "policies/usage.md"
    assert add_response.json()["baseline"]["artifact_type"] == "policy"
    assert add_response.json()["dashboard"]["artifacts"][0]["artifact_path"] == "prompts/system.txt"

    assert patch_response.status_code == 200
    assert patch_response.json()["artifact"]["artifact_type"] == "guardrail"
    assert patch_response.json()["dashboard"]["artifacts"][0]["artifact_path"] == "prompts/system.txt"

    assert delete_response.status_code == 200
    assert delete_response.json()["artifact_path"] == "policies/usage.md"
    assert delete_response.json()["dashboard"]["artifacts"][0]["artifact_path"] == "prompts/system.txt"


def test_persistence_api_requires_authentication(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator.db")
    main.init_db(main.AUDIT_DB_PATH)

    with TestClient(main.app) as client:
        response = client.get("/api/persistence")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


def test_dashboard_html_pages_render(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator.db")

    with TestClient(main.app) as client:
        index_response = client.get("/dashboard")
        repo_response = client.get("/dashboard/doria90/dummyAI")
        css_response = client.get("/static/dashboard.css")
        index_js_response = client.get("/static/dashboard-index.js")
        repo_js_response = client.get("/static/dashboard-repo.js")

    assert index_response.status_code == 200
    assert "Vipari Dashboard" in index_response.text
    assert "/static/dashboard-index.js" in index_response.text
    index_text = index_response.text.lower()
    assert "ai change overview" in index_text
    assert "urgent changes to review" in index_text
    assert "recent changes this week" in index_text
    assert "posture map" in index_text
    assert "change timeline" in index_text
    assert "coverage" in index_text
    assert "overview-rebaseline-modal" in index_response.text

    assert repo_response.status_code == 200
    repo_text = repo_response.text.lower()
    assert "audit page" in repo_text
    assert "audit brief" in repo_text
    assert "governance attention" in repo_text
    assert "loading eu ai act, soc 2, and iso 27001 governance guidance" in repo_text
    assert "attribute profile" in repo_text
    assert "control surface coverage" in repo_text
    assert "supporting history" in repo_text
    assert "baseline-review-panel" in repo_response.text
    assert "Baseline Review" in repo_response.text
    assert "driftguard-repo-full" in repo_response.text
    assert "/static/dashboard-repo.js" in repo_response.text
    assert 'data-repo-tab-link="audit"' in repo_response.text
    assert 'href="/dashboard/doria90%2FdummyAI/audit"' in repo_response.text
    assert 'id="artifact-add-controls"' in repo_response.text
    assert 'id="artifact-action-status"' in repo_response.text
    assert "available repositories" not in repo_text
    assert "/api/dashboard/overview" not in repo_response.text

    assert css_response.status_code == 200
    assert ".app-shell" in css_response.text
    assert ".posture-strip" in css_response.text
    assert ".detail-panel" in css_response.text
    assert "--color-border" in css_response.text
    assert ".artifact-add-controls" in css_response.text
    assert ".artifact-action-group" in css_response.text
    assert index_js_response.status_code == 200
    assert "renderUrgentRow" in index_js_response.text
    assert "renderRepoAtlasCard" in index_js_response.text
    assert repo_js_response.status_code == 200
    assert "function repoTabUrl" in repo_js_response.text
    assert 'repoTabUrl("audit", { artifactPath: topInsight?.artifact_path || "", hash: "repo-audit-brief-section" })' in repo_js_response.text
    assert 'repoTabUrl("baseline", { hash: "baseline-review-panel" })' in repo_js_response.text
    assert 'repoTabUrl("reports", { hash: "repo-export-section" })' in repo_js_response.text
    assert "Open baseline review" in repo_js_response.text
    assert "/artifacts/options" in repo_js_response.text
    assert "data-artifact-edit-path" in repo_js_response.text
    assert "data-artifact-remove-path" in repo_js_response.text
    assert "audit-workflow-step-head" in repo_js_response.text
    assert "Review the flagged change" in repo_js_response.text
    assert "Compare repository context" in repo_js_response.text
    assert "Prepare the handoff" in repo_js_response.text
    assert "Review queue is clear" in repo_js_response.text
    assert "No baseline or disposition proposals are waiting on this repository right now." in repo_js_response.text


def test_dashboard_repo_audit_route_renders_active_tab(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator-audit.db")

    with TestClient(main.app) as client:
        response = client.get("/dashboard/doria90/dummyAI/audit")

    assert response.status_code == 200
    assert 'data-active-repo-tab="audit"' in response.text
    assert 'data-repo-tab-link="audit"' in response.text
    assert 'href="/dashboard/doria90%2FdummyAI/audit"' in response.text


def test_dashboard_repo_tab_query_param_renders_active_tab(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator.db")

    with TestClient(main.app) as client:
        response = client.get("/dashboard/doria90/dummyAI?tab=reports")

    assert response.status_code == 200
    assert 'data-active-repo-tab="reports"' in response.text
    assert 'data-repo-tab-link="reports"' in response.text
    assert 'data-repo-tab-link="version-control"' in response.text
    assert '?tab=version-control' in response.text
    assert '?tab=baseline' in response.text
    assert '?tab=compliance' in response.text
    assert 'secondary-details-summary-static' in response.text
    assert '<div class="secondary-panel secondary-panel-disclosure" id="repo-journey-section">' in response.text
    assert '<details class="secondary-details"><summary class="secondary-details-summary">Version journey and baseline comparison</summary>' not in response.text


def test_dashboard_index_query_params_render_active_controls(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator.db")

    with TestClient(main.app) as client:
        response = client.get("/dashboard?range=30d&filter=critical")

    assert response.status_code == 200
    assert 'data-active-overview-range="30d"' in response.text
    assert 'data-active-overview-filter="critical"' in response.text
    assert 'data-overview-range="30d"' in response.text
    assert 'data-overview-filter="critical"' in response.text
    assert "Review urgent changes" in response.text
