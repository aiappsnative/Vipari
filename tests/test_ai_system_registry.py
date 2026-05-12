import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile
from services.ai_system_registry import sync_ai_system_for_repo
from services.audit_jobs import init_db
from services.control_plane_records import create_workspace, update_ai_system_classification, upsert_ai_system_for_repo, upsert_github_identity
from services.onboarding_records import DiscoveredArtifactInput, record_repository_onboarding


def test_sync_ai_system_for_repo_prefills_domain_and_purpose_from_onboarding_evidence(tmp_path):
    db_path = str(tmp_path / "ai-system-prefill.db")
    init_db(db_path)

    user, _identity = upsert_github_identity(
        db_path,
        github_user_id="801",
        github_login="registry-owner",
        display_name="Registry Owner",
        primary_email="owner@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        db_path,
        slug="registry-prefill-workspace",
        display_name="Registry Prefill Workspace",
        billing_owner_user_id=user.id,
    )

    record_repository_onboarding(
        db_path,
        repo_full="acme/hiring-assistant",
        installation_id=8010,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/hiring_system.txt",
                artifact_type="prompt",
                discovery_reason="Hiring prompt",
                confidence=0.9,
                baseline_content="Screen candidates and summarize interview notes for recruiters.",
            )
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )

    ai_system = sync_ai_system_for_repo(
        db_path,
        workspace_id=workspace.id,
        repo_full="acme/hiring-assistant",
        created_by_user_id=user.id,
    )

    assert ai_system.eu_ai_act_domain == "employment"
    assert "employment and worker-management workflows" in (ai_system.purpose_summary or "")
    assert "prompt" in ai_system.artifact_families_json


def test_sync_ai_system_for_repo_preserves_manual_domain_and_purpose(tmp_path):
    db_path = str(tmp_path / "ai-system-manual.db")
    init_db(db_path)

    user, _identity = upsert_github_identity(
        db_path,
        github_user_id="802",
        github_login="registry-editor",
        display_name="Registry Editor",
        primary_email="editor@example.com",
        avatar_url=None,
        granted_scopes=["read:user"],
        access_token_encrypted="encrypted-token",
    )
    workspace = create_workspace(
        db_path,
        slug="registry-manual-workspace",
        display_name="Registry Manual Workspace",
        billing_owner_user_id=user.id,
    )

    manual_system = upsert_ai_system_for_repo(
        db_path,
        workspace_id=workspace.id,
        repo_full="acme/internal-copilot",
        display_name="acme/internal-copilot",
        latest_onboarding_status="baseline_approved",
        artifact_families=["prompt"],
        eu_ai_act_domain="internal_productivity",
        purpose_summary="Manual summary",
        created_by_user_id=user.id,
    )
    update_ai_system_classification(
        db_path,
        ai_system_id=manual_system.id,
        risk_level="limited-risk",
        eu_ai_act_domain="internal_productivity",
        purpose_summary="Manual summary",
        reviewed_by_user_id=user.id,
    )

    record_repository_onboarding(
        db_path,
        repo_full="acme/internal-copilot",
        installation_id=8020,
        default_branch="main",
        status="baseline_approved",
        discovered_artifacts=[
            DiscoveredArtifactInput(
                artifact_path="prompts/hiring_system.txt",
                artifact_type="prompt",
                discovery_reason="Hiring prompt",
                confidence=0.9,
                baseline_content="Screen candidates and summarize interview notes for recruiters.",
            )
        ],
        extract_signal_terms_fn=extract_signal_terms_from_text,
        build_profile_fn=build_attribute_profile,
    )

    ai_system = sync_ai_system_for_repo(
        db_path,
        workspace_id=workspace.id,
        repo_full="acme/internal-copilot",
        created_by_user_id=user.id,
    )

    assert ai_system.eu_ai_act_domain == "internal_productivity"
    assert ai_system.purpose_summary == "Manual summary"