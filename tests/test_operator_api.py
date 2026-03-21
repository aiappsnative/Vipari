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
            created_at=1.0,
            updated_at=1.0,
        ),
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
                        source="pull_request",
                        label="PR #42",
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
                provenance=RepoArtifactProvenance(source_type="pull_request", label="PR #42 · sha-cur", created_at=2.0),
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
    assert "PromptDrift Dashboard" in index_response.text
    assert "/static/dashboard-index.js" in index_response.text
    assert "portfolio-risk-state" in index_response.text
    assert "Regression patterns" in index_response.text
    assert "No production traffic or user data is analyzed" in index_response.text
    assert "Highest-risk drift" in index_response.text
    assert "Risk by control surface" in index_response.text
    assert "Review queue" in index_response.text
    assert "Control surface coverage" in index_response.text

    assert repo_response.status_code == 200
    assert "Unified view of onboarding, backfill lineage, and pull-request drift history." in repo_response.text
    assert "Static-only analysis" in repo_response.text
    assert "Baseline vs current design posture" in repo_response.text
    assert "Needs attention now" in repo_response.text
    assert "Control surface map" in repo_response.text
    assert "History and drift timeline" in repo_response.text
    assert "promptdrift-repo-full" in repo_response.text
    assert "/static/dashboard-repo.js" in repo_response.text

    assert css_response.status_code == 200
    assert ".hero-panel" in css_response.text
    assert ".risk-surface-card" in css_response.text
    assert "--panel-border" in css_response.text
    assert index_js_response.status_code == 200
    assert "renderRiskState" in index_js_response.text
    assert "renderHighestRiskItems" in index_js_response.text
    assert "renderControlSurfaceRisk" in index_js_response.text
    assert "loadOverview" in index_js_response.text
    assert repo_js_response.status_code == 200
    assert "renderDesignProfiles" in repo_js_response.text
    assert "renderHistoryTimelines" in repo_js_response.text
    assert "loadDashboard" in repo_js_response.text