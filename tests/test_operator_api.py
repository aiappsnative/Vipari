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


def test_onboard_api_runs_workflow_and_returns_dashboard_payload(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator.db")

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


def test_persistence_api_returns_backend_metadata(tmp_path):
    main.AUDIT_WORKER_ENABLED = False
    main.AUDIT_DB_PATH = str(tmp_path / "operator.db")

    with TestClient(main.app) as client:
        response = client.get("/api/persistence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"] == "sqlite"
    assert payload["production_target"] == "postgresql"
    assert "audit_jobs" in payload["operational_tables"]
    assert "database_path" not in payload


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
    assert "DriftGuard Dashboard" in index_response.text
    assert "/static/dashboard-index.js" in index_response.text
    index_text = index_response.text.lower()
    assert "urgent items for review" in index_text
    assert "repository atlas" in index_text
    assert "repo posture radar" in index_text
    assert "version journey" in index_text
    assert "coverage" in index_text
    assert "overview-rebaseline-modal" in index_response.text

    assert repo_response.status_code == 200
    repo_text = repo_response.text.lower()
    assert "audit page" in repo_text
    assert "available repositories" in repo_text
    assert "governance attention" in repo_text
    assert "loading eu ai act, soc 2, and iso 27001 governance guidance" in repo_text
    assert "attribute drift" in repo_text
    assert "control surface coverage" in repo_text
    assert "drift storyline" in repo_text
    assert "baseline-review-panel" in repo_response.text
    assert "Baseline Review" in repo_response.text
    assert "driftguard-repo-full" in repo_response.text
    assert "/static/dashboard-repo.js" in repo_response.text

    assert css_response.status_code == 200
    assert ".app-shell" in css_response.text
    assert ".posture-strip" in css_response.text
    assert ".detail-panel" in css_response.text
    assert "--color-border" in css_response.text
    assert index_js_response.status_code == 200
    assert "renderUrgentRow" in index_js_response.text
    assert "renderRepoAtlasCard" in index_js_response.text
    assert "submitOverviewRebaseline" in index_js_response.text
    assert "loadOverview" in index_js_response.text
    assert "drawRadar" in index_js_response.text
    assert "Unable to load dashboard overview" in index_js_response.text
    assert "loadOverview" in index_js_response.text
    assert repo_js_response.status_code == 200
    assert "renderRepoTriageRow" in repo_js_response.text
    assert "renderAttributeBars" in repo_js_response.text
    assert "Unable to load repository dashboard" in repo_js_response.text
    assert "loadDashboard" in repo_js_response.text