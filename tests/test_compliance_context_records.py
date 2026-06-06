import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.audit_jobs import init_db
from services.compliance_context_records import (
    default_workspace_compliance_context,
    resolve_effective_compliance_context,
    upsert_repo_compliance_context_override,
    upsert_workspace_compliance_context,
)
from services.control_plane_records import (
    allocate_repo_to_workspace,
    create_user,
    create_workspace,
    upsert_github_installation,
)


def _seed_workspace_and_allocation(db_path: str) -> tuple[int, int]:
    init_db(db_path)
    owner = create_user(db_path, display_name="Owner", primary_email="owner@example.com")
    workspace = create_workspace(
        db_path,
        slug="compliance-context-ws",
        display_name="Compliance Context WS",
        billing_owner_user_id=owner.id,
    )
    installation = upsert_github_installation(
        db_path,
        workspace_id=workspace.id,
        installation_id=4567,
        account_id="acct-4567",
        account_login="org",
        account_type="Organization",
        target_type="Organization",
    )
    allocation = allocate_repo_to_workspace(
        db_path,
        workspace_id=workspace.id,
        installation_id=installation.installation_id,
        repo_github_id="repo-1",
        repo_full="org/repo-one",
        baseline_mode="onboarding",
        activated_by_user_id=owner.id,
    )
    return workspace.id, allocation.id


def test_resolve_effective_compliance_context_persists_default_workspace_context(tmp_path):
    db_path = str(tmp_path / "compliance-context-default.db")
    workspace_id, allocation_id = _seed_workspace_and_allocation(db_path)

    resolved = resolve_effective_compliance_context(
        db_path,
        workspace_id=workspace_id,
        repo_allocation_id=allocation_id,
    )

    assert resolved.source == "workspace_default"
    assert resolved.context == default_workspace_compliance_context()


def test_resolve_effective_compliance_context_applies_repo_override_over_workspace_defaults(tmp_path):
    db_path = str(tmp_path / "compliance-context-override.db")
    workspace_id, allocation_id = _seed_workspace_and_allocation(db_path)

    upsert_workspace_compliance_context(
        db_path,
        workspace_id=workspace_id,
        context={
            "risk_tier": "limited",
            "customer_impact": "customer_facing",
            "human_oversight": "required",
            "handles_personal_data": False,
            "handles_biometric_data": False,
            "deployment_regions": ["eu-west-1"],
            "notes": "workspace default",
        },
    )
    upsert_repo_compliance_context_override(
        db_path,
        workspace_id=workspace_id,
        repo_allocation_id=allocation_id,
        context_override={
            "risk_tier": "high",
            "handles_personal_data": True,
            "deployment_regions": ["us-east-1", "eu-west-1"],
        },
    )

    resolved = resolve_effective_compliance_context(
        db_path,
        workspace_id=workspace_id,
        repo_allocation_id=allocation_id,
    )

    assert resolved.source == "repo_override"
    assert resolved.context["risk_tier"] == "high"
    assert resolved.context["customer_impact"] == "customer_facing"
    assert resolved.context["handles_personal_data"] is True
    assert resolved.context["deployment_regions"] == ["eu-west-1", "us-east-1"]
