from __future__ import annotations

import json

from services.audit_jobs import init_db
from services.control_plane_records import allocate_repo_to_workspace, create_user, create_workspace, upsert_github_installation
from services.operational_policy import default_policy_for_preset, normalize_repo_policy_override
from services.operational_policy_records import (
    get_repo_policy_override,
    get_workspace_policy,
    list_policy_versions_for_scope,
    resolve_effective_policy_record,
    upsert_repo_policy_override,
    upsert_workspace_policy,
)


def test_upsert_workspace_policy_creates_append_only_version_history(tmp_path):
    db_path = str(tmp_path / "workspace-policy.db")
    init_db(db_path)

    user = create_user(db_path, display_name="Owner", primary_email="owner@example.com")
    workspace = create_workspace(db_path, slug="ops-team", display_name="Ops Team", billing_owner_user_id=user.id)

    first_policy = default_policy_for_preset("balanced")
    second_policy = default_policy_for_preset("conservative")

    created = upsert_workspace_policy(
        db_path,
        workspace_id=workspace.id,
        policy=first_policy,
        created_by_user_id=user.id,
    )
    unchanged = upsert_workspace_policy(
        db_path,
        workspace_id=workspace.id,
        policy=first_policy,
        created_by_user_id=user.id,
    )
    updated = upsert_workspace_policy(
        db_path,
        workspace_id=workspace.id,
        policy=second_policy,
        created_by_user_id=user.id,
    )

    assert created.active_version_id is not None
    assert unchanged.id == created.id
    assert unchanged.active_version_id == created.active_version_id
    assert updated.active_version_id is not None
    assert updated.policy_hash != created.policy_hash

    stored = get_workspace_policy(db_path, workspace.id)
    assert stored is not None
    assert stored.active_version_id == updated.active_version_id

    versions = list_policy_versions_for_scope(db_path, scope_type="workspace", scope_ref_id=stored.id)
    assert [version.version_number for version in versions] == [1, 2]
    assert json.loads(versions[0].diff_summary_json)["changed_sections"] == ["initial_create"]
    assert json.loads(versions[1].diff_summary_json)["changed_sections"] == ["preset_key", "categories", "llm_strategy"]


def test_upsert_repo_policy_override_creates_override_row_and_version(tmp_path):
    db_path = str(tmp_path / "repo-policy-override.db")
    init_db(db_path)

    user = create_user(db_path, display_name="Owner", primary_email="owner@example.com")
    workspace = create_workspace(db_path, slug="ops-team", display_name="Ops Team", billing_owner_user_id=user.id)
    upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=99,
        account_id="99",
        account_login="example-org",
        account_type="Organization",
        target_type="Organization",
        status="active",
    )
    allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=99,
        repo_github_id="repo-99",
        repo_full="example/repo-99",
        baseline_mode="default_branch",
        activated_by_user_id=user.id,
    )

    override = normalize_repo_policy_override(
        {
            "categories": {
                "tool_authority": {
                    "default_severity": "high",
                    "min_final_risk_level": "high",
                    "merge_action": "block",
                }
            },
            "gating": {"medium_risk_action": "warn"},
        }
    )

    updated = upsert_repo_policy_override(
        db_path,
        workspace_id=workspace.id,
        repo_allocation_id=allocation.id,
        policy_override=override,
        created_by_user_id=user.id,
    )

    stored = get_repo_policy_override(db_path, allocation.id)
    assert stored is not None
    assert stored.id == updated.id
    assert stored.active_version_id == updated.active_version_id

    versions = list_policy_versions_for_scope(db_path, scope_type="repo", scope_ref_id=stored.id)
    assert [version.version_number for version in versions] == [1]
    assert json.loads(versions[0].diff_summary_json)["changed_sections"] == ["initial_create"]
    assert json.loads(versions[0].policy_json)["categories"]["tool_authority"]["merge_action"] == "block"


def test_resolve_effective_policy_record_uses_repo_override_and_creates_default_workspace_policy(tmp_path):
    db_path = str(tmp_path / "effective-policy.db")
    init_db(db_path)

    user = create_user(db_path, display_name="Owner", primary_email="owner@example.com")
    workspace = create_workspace(db_path, slug="ops-team", display_name="Ops Team", billing_owner_user_id=user.id)
    upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=77,
        account_id="77",
        account_login="example-org",
        account_type="Organization",
        target_type="Organization",
        status="active",
    )
    allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=77,
        repo_github_id="repo-77",
        repo_full="example-org/policy-target",
        baseline_mode="default_branch",
        activated_by_user_id=user.id,
    )

    assert get_workspace_policy(db_path, workspace.id) is None

    override = upsert_repo_policy_override(
        db_path,
        workspace_id=workspace.id,
        repo_allocation_id=allocation.id,
        policy_override=normalize_repo_policy_override(
            {
                "llm_strategy": {"when_to_run_verifier": "on_medium_plus"},
                "gating": {"medium_risk_action": "require_escalation"},
            }
        ),
        created_by_user_id=user.id,
    )

    resolved = resolve_effective_policy_record(
        db_path,
        workspace_id=workspace.id,
        repo_allocation_id=allocation.id,
    )

    workspace_policy = get_workspace_policy(db_path, workspace.id)
    assert workspace_policy is not None
    assert workspace_policy.active_version_id == resolved.workspace_policy_version_id
    assert override.active_version_id == resolved.repo_policy_version_id
    assert resolved.effective_policy_source == "repo"
    assert resolved.effective_policy_hash

    policy_decision = json.loads(resolved.policy_decision_json)
    assert policy_decision["effective_policy_source"] == "repo"
    assert policy_decision["workspace_policy_version_id"] == resolved.workspace_policy_version_id
    assert policy_decision["repo_policy_version_id"] == resolved.repo_policy_version_id
    assert policy_decision["effective_policy"]["llm_strategy"]["when_to_run_verifier"] == "on_medium_plus"
    assert policy_decision["effective_policy"]["gating"]["medium_risk_action"] == "require_escalation"