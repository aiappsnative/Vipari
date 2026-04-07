import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.access_state import WorkspaceAccessSnapshot, resolve_workspace_access_state


def test_resolve_workspace_access_state_unauthenticated():
    resolution = resolve_workspace_access_state(WorkspaceAccessSnapshot(is_authenticated=False))

    assert resolution.state == "unauthenticated"
    assert resolution.can_access_dashboard is False
    assert resolution.primary_cta == "Continue with GitHub"


def test_resolve_workspace_access_state_requires_plan_before_install():
    resolution = resolve_workspace_access_state(
        WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            has_subscription_record=False,
        )
    )

    assert resolution.state == "workspace_no_subscription"
    assert resolution.required_next_action == "Choose plan"


def test_resolve_workspace_access_state_awaits_install_after_billing():
    resolution = resolve_workspace_access_state(
        WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            has_subscription_record=True,
            dashboard_enabled=True,
            has_linked_installation=False,
        )
    )

    assert resolution.state == "awaiting_github_install"
    assert resolution.secondary_cta == "Copy install link"


def test_resolve_workspace_access_state_active():
    resolution = resolve_workspace_access_state(
        WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            has_subscription_record=True,
            dashboard_enabled=True,
            has_linked_installation=True,
            allocated_repo_count=2,
            onboarded_repo_count=1,
        )
    )

    assert resolution.state == "active"
    assert resolution.can_access_dashboard is True
    assert resolution.is_read_only is False