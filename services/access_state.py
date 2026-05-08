from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AccessState = Literal[
    "unauthenticated",
    "authenticated_no_workspace",
    "invited_pending_acceptance",
    "workspace_no_subscription",
    "billing_pending_confirmation",
    "payment_failed",
    "awaiting_github_install",
    "awaiting_repo_onboarding",
    "active_comments_only",
    "active",
    "canceled_active_until_period_end",
    "expired_read_only",
    "forbidden",
]

ChecklistStatus = Literal["complete", "current", "blocked", "pending"]


@dataclass(frozen=True)
class AccessChecklistItem:
    key: str
    label: str
    status: ChecklistStatus
    detail: str
    cta: str | None = None


@dataclass(frozen=True)
class WorkspaceAccessSnapshot:
    is_authenticated: bool
    has_workspace: bool = False
    invitation_pending: bool = False
    has_membership: bool = True
    role: str | None = None
    has_subscription_record: bool = False
    billing_pending_confirmation: bool = False
    payment_failed: bool = False
    dashboard_enabled: bool = False
    pr_comments_enabled: bool = False
    has_linked_installation: bool = False
    allocated_repo_count: int = 0
    onboarded_repo_count: int = 0
    cancel_at_period_end: bool = False
    subscription_expired: bool = False


@dataclass(frozen=True)
class WorkspaceAccessResolution:
    state: AccessState
    can_access_dashboard: bool
    is_read_only: bool
    required_next_action: str | None
    ui_title: str
    ui_body: str
    primary_cta: str | None
    secondary_cta: str | None
    checklist: list[AccessChecklistItem]


def _build_checklist(snapshot: WorkspaceAccessSnapshot) -> list[AccessChecklistItem]:
    billing_complete = (
        snapshot.has_subscription_record
        and not snapshot.billing_pending_confirmation
        and not snapshot.payment_failed
        and not snapshot.subscription_expired
    )
    plan_active = billing_complete or (snapshot.has_subscription_record and snapshot.pr_comments_enabled)
    install_complete = plan_active and snapshot.has_linked_installation
    repo_allocated = install_complete and snapshot.allocated_repo_count > 0
    onboarding_complete = repo_allocated and snapshot.onboarded_repo_count > 0

    return [
        AccessChecklistItem(
            key="billing",
            label="Plan active",
            status="complete" if billing_complete else "current" if snapshot.has_workspace else "pending",
            detail="Workspace entitlements come from the selected Vipari plan and billing provider.",
            cta=None if billing_complete else "Choose plan",
        ),
        AccessChecklistItem(
            key="workspace",
            label="Workspace linked",
            status="complete" if snapshot.has_workspace else "current" if snapshot.is_authenticated else "pending",
            detail="Users act inside a specific Vipari workspace with plan-scoped access.",
            cta=None if snapshot.has_workspace else "Create workspace",
        ),
        AccessChecklistItem(
            key="github_login",
            label="GitHub connected",
            status="complete" if snapshot.is_authenticated else "current",
            detail="GitHub login establishes the user identity layer.",
            cta=None if snapshot.is_authenticated else "Continue with GitHub",
        ),
        AccessChecklistItem(
            key="installation",
            label="GitHub App installed",
            status="complete" if install_complete else "current" if plan_active else "blocked",
            detail="Vipari needs GitHub App installation authority before it can enumerate repos.",
            cta=None if install_complete else "Install Vipari",
        ),
        AccessChecklistItem(
            key="repo_allocation",
            label="Repository allocated",
            status="complete" if repo_allocated else "current" if install_complete else "blocked",
            detail="Allocated repositories consume plan entitlement capacity.",
            cta=None if repo_allocated else "Select repositories",
        ),
        AccessChecklistItem(
            key="first_scan",
            label="First scan completed",
            status="complete" if onboarding_complete else "current" if repo_allocated else "blocked",
            detail="At least one allocated repository needs a completed onboarding pass.",
            cta=None if onboarding_complete else "Run onboarding",
        ),
    ]


def resolve_workspace_access_state(snapshot: WorkspaceAccessSnapshot) -> WorkspaceAccessResolution:
    checklist = _build_checklist(snapshot)

    if not snapshot.is_authenticated:
        return WorkspaceAccessResolution(
            state="unauthenticated",
            can_access_dashboard=False,
            is_read_only=False,
            required_next_action="Continue with GitHub",
            ui_title="Sign in to Vipari",
            ui_body="Vipari uses GitHub as the primary identity layer so workspace membership and repository access stay aligned.",
            primary_cta="Continue with GitHub",
            secondary_cta=None,
            checklist=checklist,
        )

    if not snapshot.has_workspace:
        return WorkspaceAccessResolution(
            state="authenticated_no_workspace",
            can_access_dashboard=False,
            is_read_only=False,
            required_next_action="Create workspace",
            ui_title="Create or join a workspace",
            ui_body="Your GitHub identity is connected, but Vipari still needs a workspace context before billing or onboarding can begin.",
            primary_cta="Create workspace",
            secondary_cta="Join with invite",
            checklist=checklist,
        )

    if snapshot.invitation_pending:
        return WorkspaceAccessResolution(
            state="invited_pending_acceptance",
            can_access_dashboard=False,
            is_read_only=False,
            required_next_action="Accept invitation",
            ui_title="Accept your workspace invitation",
            ui_body="This GitHub identity matches a pending Vipari invitation. Accept it to continue into the workspace.",
            primary_cta="Accept invitation",
            secondary_cta="Switch workspace",
            checklist=checklist,
        )

    if not snapshot.has_membership:
        return WorkspaceAccessResolution(
            state="forbidden",
            can_access_dashboard=False,
            is_read_only=False,
            required_next_action="Switch workspace",
            ui_title="You do not have access to this workspace",
            ui_body="Your session is valid, but this workspace does not grant you the requested role or membership.",
            primary_cta="Switch workspace",
            secondary_cta=None,
            checklist=checklist,
        )

    if not snapshot.has_subscription_record:
        return WorkspaceAccessResolution(
            state="workspace_no_subscription",
            can_access_dashboard=False,
            is_read_only=False,
            required_next_action="Choose plan",
            ui_title="Choose a plan to unlock Vipari",
            ui_body="This workspace exists, but no active subscription or pending paid plan is linked to it yet.",
            primary_cta="Choose plan",
            secondary_cta=None,
            checklist=checklist,
        )

    if snapshot.billing_pending_confirmation:
        return WorkspaceAccessResolution(
            state="billing_pending_confirmation",
            can_access_dashboard=False,
            is_read_only=False,
            required_next_action="Refresh status",
            ui_title="Activating your workspace",
            ui_body="Payment has been initiated, but Vipari is still waiting for webhook-confirmed billing activation before access is granted.",
            primary_cta="Refresh status",
            secondary_cta="Open billing",
            checklist=checklist,
        )

    if snapshot.payment_failed:
        return WorkspaceAccessResolution(
            state="payment_failed",
            can_access_dashboard=False,
            is_read_only=False,
            required_next_action="Fix billing",
            ui_title="Billing needs attention",
            ui_body="Vipari cannot grant workspace access until the subscription payment issue is resolved.",
            primary_cta="Fix billing",
            secondary_cta="Open customer portal",
            checklist=checklist,
        )

    if not snapshot.has_linked_installation:
        return WorkspaceAccessResolution(
            state="awaiting_github_install",
            can_access_dashboard=False,
            is_read_only=False,
            required_next_action="Install Vipari",
            ui_title="Install Vipari on GitHub",
            ui_body="Billing is active, but Vipari still needs GitHub App installation authority before repositories can be allocated.",
            primary_cta="Install Vipari",
            secondary_cta="Copy install link",
            checklist=checklist,
        )

    if snapshot.allocated_repo_count <= 0 or snapshot.onboarded_repo_count <= 0:
        return WorkspaceAccessResolution(
            state="awaiting_repo_onboarding",
            can_access_dashboard=False,
            is_read_only=False,
            required_next_action="Select repositories",
            ui_title="Allocate repositories and finish onboarding",
            ui_body="Your workspace is connected to GitHub, but no licensed repositories are fully onboarded yet.",
            primary_cta="Select repositories",
            secondary_cta=None,
            checklist=checklist,
        )

    if snapshot.pr_comments_enabled and not snapshot.dashboard_enabled:
        return WorkspaceAccessResolution(
            state="active_comments_only",
            can_access_dashboard=True,
            is_read_only=True,
            required_next_action="Upgrade to Starter",
            ui_title="Free tier active",
            ui_body="GitHub install, repository allocation, and onboarding are complete. This workspace can receive PR comments for one repository and view dashboards in read-only mode. Paid plans unlock full editing and unlimited repositories.",
            primary_cta="Upgrade to Starter",
            secondary_cta="Manage repository setup",
            checklist=checklist,
        )

    if snapshot.subscription_expired or not snapshot.dashboard_enabled:
        return WorkspaceAccessResolution(
            state="expired_read_only",
            can_access_dashboard=True,
            is_read_only=True,
            required_next_action="Reactivate",
            ui_title="Workspace access is read-only",
            ui_body="Vipari preserved limited visibility, but paid access needs to be reactivated before the workspace can change onboarding or billing state.",
            primary_cta="Reactivate",
            secondary_cta="Open billing",
            checklist=checklist,
        )

    if snapshot.cancel_at_period_end:
        return WorkspaceAccessResolution(
            state="canceled_active_until_period_end",
            can_access_dashboard=True,
            is_read_only=False,
            required_next_action="Resume subscription",
            ui_title="Subscription ends at period close",
            ui_body="The workspace is still active, but renewal is disabled and access will expire unless the subscription is resumed.",
            primary_cta="Resume subscription",
            secondary_cta="Open billing",
            checklist=checklist,
        )

    return WorkspaceAccessResolution(
        state="active",
        can_access_dashboard=True,
        is_read_only=False,
        required_next_action=None,
        ui_title="Workspace active",
        ui_body="Billing, installation, repository allocation, and onboarding are all in place for this workspace.",
        primary_cta=None,
        secondary_cta=None,
        checklist=checklist,
    )