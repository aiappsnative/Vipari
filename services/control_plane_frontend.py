from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from urllib.parse import quote

from .access_state import WorkspaceAccessResolution, WorkspaceAccessSnapshot, resolve_workspace_access_state
from .compliance_readiness import ComplianceExportSummary, ComplianceFrameworkCard, ComplianceGapItem, ComplianceRepoReadinessRow, ComplianceWorkspaceView, filter_compliance_evidence_view, normalize_compliance_gap_filter, normalize_compliance_repo_filter
from .entitlements import PLAN_DEFINITIONS
from .export_jobs import ExportJob
from .mcp_broker import MCP_BROKER_TOOLS


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"


def _asset_url(path: str) -> str:
    asset_path = BASE_DIR / path.lstrip("/")
    try:
        version = asset_path.stat().st_mtime_ns
    except OSError:
        version = 0
    return f"{path}?v={version}"


def _load_template(name: str) -> str:
    template = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    return (
        template.replace("/static/control-plane.css", _asset_url("/static/control-plane.css"))
        .replace("/static/dashboard.css", _asset_url("/static/dashboard.css"))
        .replace("/static/theme-toggle.js", _asset_url("/static/theme-toggle.js"))
    )


def _resolution_for_preview_state(state: str | None) -> WorkspaceAccessResolution:
    normalized = (state or "awaiting_github_install").strip().lower()
    snapshots = {
        "unauthenticated": WorkspaceAccessSnapshot(is_authenticated=False),
        "authenticated_no_workspace": WorkspaceAccessSnapshot(is_authenticated=True, has_workspace=False),
        "invited_pending_acceptance": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            invitation_pending=True,
        ),
        "workspace_no_subscription": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            has_subscription_record=False,
        ),
        "billing_pending_confirmation": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            has_subscription_record=True,
            billing_pending_confirmation=True,
        ),
        "payment_failed": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            has_subscription_record=True,
            payment_failed=True,
        ),
        "awaiting_github_install": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            has_subscription_record=True,
            dashboard_enabled=True,
            has_linked_installation=False,
        ),
        "awaiting_repo_onboarding": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            has_subscription_record=True,
            dashboard_enabled=True,
            has_linked_installation=True,
            allocated_repo_count=2,
            onboarded_repo_count=0,
        ),
        "active_comments_only": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            role="owner",
            has_subscription_record=True,
            pr_comments_enabled=True,
            has_linked_installation=True,
            allocated_repo_count=1,
            onboarded_repo_count=1,
        ),
        "active": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            role="owner",
            has_subscription_record=True,
            dashboard_enabled=True,
            has_linked_installation=True,
            allocated_repo_count=3,
            onboarded_repo_count=3,
        ),
        "canceled_active_until_period_end": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            role="owner",
            has_subscription_record=True,
            dashboard_enabled=True,
            has_linked_installation=True,
            allocated_repo_count=3,
            onboarded_repo_count=3,
            cancel_at_period_end=True,
        ),
        "expired_read_only": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=True,
            role="viewer",
            has_subscription_record=True,
            dashboard_enabled=False,
            has_linked_installation=True,
            allocated_repo_count=3,
            onboarded_repo_count=3,
            subscription_expired=True,
        ),
        "forbidden": WorkspaceAccessSnapshot(
            is_authenticated=True,
            has_workspace=True,
            has_membership=False,
        ),
    }
    return resolve_workspace_access_state(snapshots.get(normalized, snapshots["awaiting_github_install"]))


def _render_checklist(resolution: WorkspaceAccessResolution) -> str:
    cta_links = _checklist_cta_links(resolution)
    return "".join(
        f"""
        <li class=\"checklist-item checklist-item-{html_escape(item.status)}\">
            <div>
                <span class=\"checklist-label\">{html_escape(item.label)}</span>
                <span class=\"checklist-detail\">{html_escape(item.detail)}</span>
            </div>
            <div class=\"checklist-meta\">
                <span>{html_escape(item.status.replace('_', ' '))}</span>
                {f'<a class="subtle-link" href="{html_escape(cta_links.get(item.key) or "#")}">{html_escape(item.cta)}</a>' if item.cta and cta_links.get(item.key) else (f'<span>{html_escape(item.cta)}</span>' if item.cta else '')}
            </div>
        </li>
        """
        for item in resolution.checklist
    )


def _state_primary_action_url(resolution: WorkspaceAccessResolution) -> str | None:
    mapping = {
        "unauthenticated": "/login",
        "authenticated_no_workspace": "/app/workspaces/new",
        "invited_pending_acceptance": "/app",
        "forbidden": "/app",
        "workspace_no_subscription": "/app/billing",
        "billing_pending_confirmation": "/app/billing",
        "payment_failed": "/app/billing",
        "awaiting_github_install": "/app/setup/install",
        "awaiting_repo_onboarding": "/app/repos",
        "active_comments_only": "/app/billing?plan=starter",
        "active": "/dashboard",
        "canceled_active_until_period_end": "/app/billing",
        "expired_read_only": "/app/billing",
    }
    return mapping.get(resolution.state)


def _state_secondary_action_url(resolution: WorkspaceAccessResolution) -> str | None:
    mapping = {
        "billing_pending_confirmation": "/app/billing",
        "payment_failed": "/app/billing/portal",
        "awaiting_github_install": "/app/setup/install",
        "active_comments_only": "/app/repos",
        "canceled_active_until_period_end": "/app/billing",
        "expired_read_only": "/app/billing",
    }
    return mapping.get(resolution.state)


def _state_next_action_url(resolution: WorkspaceAccessResolution) -> str | None:
    mapping = {
        "unauthenticated": "/login",
        "authenticated_no_workspace": "/app/workspaces/new",
        "workspace_no_subscription": "/app/billing",
        "billing_pending_confirmation": "/app/billing",
        "payment_failed": "/app/billing/portal",
        "awaiting_github_install": "/app/setup/install",
        "awaiting_repo_onboarding": "/app/repos",
        "active_comments_only": "/app/repos",
        "active": "/dashboard",
        "canceled_active_until_period_end": "/dashboard",
        "expired_read_only": "/app/billing",
    }
    return mapping.get(resolution.state)


def _checklist_cta_links(resolution: WorkspaceAccessResolution) -> dict[str, str]:
    links = {
        "billing": "/app/billing",
        "workspace": "/app/workspaces/new",
        "github_login": "/login",
        "installation": "/app/setup/install",
        "repo_allocation": "/app/repos",
        "first_scan": "/app/repos",
    }
    if resolution.state == "active":
        links["repo_allocation"] = "/dashboard"
        links["first_scan"] = "/dashboard"
    if resolution.state == "active_comments_only":
        links["repo_allocation"] = "/app/repos"
        links["first_scan"] = "/app/repos"
    return links


def _render_action_chip(label: str | None, href: str | None, *, fallback: str) -> str:
    text = label or fallback
    if href:
        return f'<a class="button" href="{html_escape(href)}">{html_escape(text)}</a>'
    return f'<span>{html_escape(text)}</span>'


def _csrf_input(csrf_token: str) -> str:
    return f'<input type="hidden" name="csrf_token" value="{html_escape(csrf_token)}" />'


def _render_quick_links(*, profile_url: str | None = None, admin_url: str | None = None) -> str:
    links = ['<a class="subtle-link" href="/app">Workspace</a>']
    if profile_url:
        links.append(f'<a class="subtle-link" href="{html_escape(profile_url)}">Profile</a>')
    if admin_url:
        links.append(f'<a class="subtle-link" href="{html_escape(admin_url)}">Admin</a>')
    return "".join(links)


def _format_timestamp(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _slugify_value(value: str) -> str:
    normalized = "-".join(part for part in "".join(char.lower() if char.isalnum() else "-" for char in value).split("-") if part)
    return normalized or "workspace"


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '<div class="empty-state">No records yet.</div>'
    head_html = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
    row_html = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f'<div class="table-shell"><table class="data-table"><thead><tr>{head_html}</tr></thead><tbody>{row_html}</tbody></table></div>'


def _permission_label(role: str | None) -> str:
    normalized = (role or "").strip().lower()
    return {
        "owner": "Owner",
        "admin": "Edit",
        "viewer": "Read",
    }.get(normalized, normalized.replace("_", " ").title() or "Unknown")


def _setup_state_label(setup_state: str | None) -> str:
    normalized = (setup_state or "").strip().lower()
    labels = {
        "workspace_no_subscription": "Billing setup needed",
        "billing_pending_confirmation": "Billing pending",
        "payment_failed": "Billing issue",
        "awaiting_github_install": "GitHub install needed",
        "awaiting_repo_onboarding": "Repo onboarding needed",
        "active_comments_only": "Comments only",
        "active": "Active",
        "canceled_active_until_period_end": "Active until period end",
        "expired_read_only": "Read only",
    }
    return labels.get(normalized, normalized.replace("_", " ").title() or "Unknown")


def _member_state_badge_label(state: str | None) -> str:
    normalized = (state or "accepted").strip().lower()
    return {
        "accepted": "Active",
        "pending": "Pending",
    }.get(normalized, normalized.replace("_", " ").title() or "Unknown")


def _member_state_badge_class(state: str | None) -> str:
    normalized = (state or "accepted").strip().lower()
    return "member-badge-pending" if normalized == "pending" else "member-badge-active"


def _render_workspace_members_list(workspace_members: list[dict[str, object]]) -> str:
    if not workspace_members:
        return '<div class="control-page-empty">No workspace members yet.</div>'
    items = []
    for member in workspace_members:
        display_name = html_escape(str(member.get("display_name") or "Unknown"))
        github_login = html_escape(str(member.get("github_login") or "Unavailable"))
        permission = html_escape(_permission_label(str(member.get("role") or "")))
        state = str(member.get("state") or "Accepted")
        state_label = html_escape(_member_state_badge_label(state))
        items.append(
            f'''
            <div class="member-row member-row-{html_escape(state.strip().lower() or "accepted")}">
                <div class="member-row-identity">
                    <div class="member-row-avatar" aria-hidden="true">{display_name[:1] or "?"}</div>
                    <div class="member-row-copy">
                        <strong>{display_name}</strong>
                        <span>@{github_login}</span>
                    </div>
                </div>
                <div class="member-row-meta">
                    <span class="member-badge member-badge-permission">{permission}</span>
                    <span class="member-badge {_member_state_badge_class(state)}">{state_label}</span>
                </div>
            </div>
            '''
        )
    return '<div class="member-list">' + "".join(items) + "</div>"


def _render_workspace_invite_role_options() -> str:
    return (
        '<option value="admin">Edit</option>'
        '<option value="viewer">Read</option>'
    )


def _render_workspace_member_invite_form(*, csrf_token: str, invite_enabled: bool) -> str:
    note = (
        '<span class="member-toolbar-note">Invite by GitHub login. They join automatically after their next GitHub sign-in.</span>'
        if invite_enabled
        else '<span class="member-toolbar-note">Only workspace owners and admins can add users.</span>'
    )
    disabled = "disabled" if not invite_enabled else ""
    return f'''
        <div class="member-toolbar">
            <form method="post" action="/app/settings/invite" class="member-toolbar-form">
                {_csrf_input(csrf_token)}
                <input class="control-page-input member-toolbar-input" name="github_login" placeholder="GitHub login" maxlength="39" {disabled} />
                <select class="control-page-select member-toolbar-select" name="role" {disabled}>
                    {_render_workspace_invite_role_options()}
                </select>
                <button type="submit" class="control-page-icon-button member-toolbar-button" aria-label="Add user" {disabled}>+</button>
            </form>
            {note}
        </div>
    '''


def _render_workspace_repos_table(repo_rows: list[dict[str, object]]) -> str:
    rows = [
        [
            f'<a class="link" href="{html_escape(str(repo.get("href") or "#"))}">{html_escape(str(repo.get("repo_full") or "Unknown"))}</a>',
            html_escape(str(repo.get("status") or "Unknown")),
            html_escape(str(repo.get("branch") or "unknown")),
            html_escape(str(repo.get("visibility") or "Private")),
        ]
        for repo in repo_rows
    ]
    return _render_table(["Repository", "Status", "Default branch", "Visibility"], rows)


def _admin_sidebar_item(admin_url: str | None) -> str:
    if not admin_url:
        return ""
    return f'''<a href="{html_escape(admin_url)}" class="sidebar-nav-item" aria-label="Admin">
                    <svg class="sidebar-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l7 4v5c0 4.5-2.7 7.8-7 9-4.3-1.2-7-4.5-7-9V7l7-4z"/><path d="M9 12h6"/><path d="M12 9v6"/></svg>
                    <span>Admin</span>
                </a>'''


def _render_state_links(active_state: str) -> str:
    states = [
        "unauthenticated",
        "authenticated_no_workspace",
        "workspace_no_subscription",
        "billing_pending_confirmation",
        "payment_failed",
        "awaiting_github_install",
        "awaiting_repo_onboarding",
        "active_comments_only",
        "active",
        "canceled_active_until_period_end",
        "expired_read_only",
    ]
    return "".join(
        f'<a class="state-pill {"state-pill-active" if state == active_state else ""}" href="/app?state={html_escape(state)}">{html_escape(state.replace("_", " "))}</a>'
        for state in states
    )


def render_control_plane_marketing_page() -> str:
    return _load_template("control_plane_marketing.html")


def render_control_plane_login_page(*, auth_start_url: str, context_note: str | None = None, auth_available: bool = True) -> str:
    template = _load_template("control_plane_login.html")
    action_markup = (
        f'<a class="button" href="{html_escape(auth_start_url)}">Sign in with GitHub</a>'
        if auth_available
        else '<span class="button button-disabled" aria-disabled="true">Sign in with GitHub</span>'
    )
    return (
        template.replace("{{AUTH_ACTION}}", action_markup)
        .replace("{{AUTH_START_URL}}", html_escape(auth_start_url))
        .replace("{{CONTEXT_NOTE}}", html_escape(context_note or "GitHub identity anchors workspace membership, install authority, and repository allocation."))
    )


def render_control_plane_pricing_page() -> str:
    return _load_template("control_plane_pricing.html")


def render_control_plane_workspace_new_page(*, selected_plan_label: str | None = None, source_label: str | None = None, csrf_token: str) -> str:
    template = _load_template("control_plane_workspace_new.html")
    context_lines: list[str] = []
    if selected_plan_label:
        context_lines.append(f"Selected plan: {selected_plan_label}.")
    if source_label:
        context_lines.append(f"Entry source: {source_label}.")
    context_message = " ".join(context_lines) if context_lines else "Create the first DriftGuard workspace before billing and GitHub installation continue."
    return template.replace("{{WORKSPACE_CONTEXT}}", html_escape(context_message)).replace("{{CSRF_INPUT}}", _csrf_input(csrf_token))


def render_control_plane_billing_page(
    *,
    workspace_name: str,
    current_plan_label: str,
    subscription_status: str,
    selected_plan_code: str,
    checkout_status_note: str | None,
    flow_context: dict[str, str],
    portal_url: str | None,
    csrf_token: str,
) -> str:
    template = _load_template("control_plane_billing.html")
    portal_block = (
        f'<a class="subtle-link" href="{html_escape(portal_url)}">Open billing portal</a>' if portal_url else '<span class="subtle-link">Portal unavailable</span>'
    )
    flow_query = ""
    if flow_context:
        flow_query = "?" + "&".join(f"{html_escape(key)}={html_escape(value)}" for key, value in flow_context.items())
    plan_cards = []
    for code, plan in PLAN_DEFINITIONS.items():
        recommendation = "Recommended from Base44." if code == selected_plan_code else ""
        button_label = "Continue with this plan" if code == selected_plan_code else f"Choose {plan.label}"
        plan_cards.append(
            f'''
            <article class="action-card{' action-card-strong' if code == selected_plan_code else ''}">
                <div class="eyebrow">{html_escape(plan.label)}</div>
                <h2>{html_escape(plan.label)}</h2>
                <p>Repo limit: {plan.repo_limit}. Seats: {plan.seat_limit}. {html_escape(recommendation)}</p>
                <form method="post" action="/app/billing/checkout?plan={html_escape(code)}{flow_query}">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="button">{html_escape(button_label)}</button>
                </form>
            </article>
            '''
        )
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{CURRENT_PLAN_LABEL}}", html_escape(current_plan_label))
        .replace("{{SUBSCRIPTION_STATUS}}", html_escape(subscription_status))
        .replace("{{CHECKOUT_STATUS_NOTE}}", html_escape(checkout_status_note or "Choose a plan to create or resume Stripe checkout."))
        .replace("{{PLAN_CARDS}}", "".join(plan_cards))
        .replace("{{PORTAL_ACTION}}", portal_block)
    )


def render_control_plane_install_page(
    *,
    workspace_name: str,
    install_hint: str,
    installation_summary: str,
    install_url: str | None,
    install_callback_url: str,
    csrf_token: str,
) -> str:
    template = _load_template("control_plane_install.html")
    install_action = (
        f'<a class="button" href="{html_escape(install_url)}">Start GitHub App install</a>' if install_url else '<span class="subtle-link">GitHub App install URL unavailable</span>'
    )
    manual_link_form = f'''
        <form method="post" action="/app/setup/install/link" class="action-form">
            {_csrf_input(csrf_token)}
            <label class="field-label" for="installation-id">Installation id</label>
            <input class="field-input" id="installation-id" name="installation_id" placeholder="12345678" />
            <label class="field-label" for="account-login">Account login</label>
            <input class="field-input" id="account-login" name="account_login" placeholder="your-org" />
            <label class="field-label" for="repo-fulls">Fallback repo list</label>
            <textarea class="field-input field-input-area" id="repo-fulls" name="repo_fulls" placeholder="owner/repo-one&#10;owner/repo-two"></textarea>
            <button type="submit" class="button">Link installation manually</button>
        </form>
    '''
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{INSTALL_HINT}}", html_escape(install_hint))
        .replace("{{INSTALLATION_SUMMARY}}", html_escape(installation_summary))
        .replace("{{INSTALL_ACTION}}", install_action)
        .replace("{{INSTALL_CALLBACK_URL}}", html_escape(install_callback_url))
        .replace("{{MANUAL_LINK_FORM}}", manual_link_form)
    )


def _repo_dashboard_href(repo_full: str) -> str:
    return f'/dashboard/{quote(repo_full, safe="")}'


def _repo_github_installation_href(repo_full: str) -> str:
    return f'https://github.com/{quote(repo_full, safe="/")}/settings/installations'


def render_control_plane_repo_setup_page(*, workspace_name: str, inventory_summary: str, inventory_cards: str, onboarding_metrics: str, onboarding_summary_cards: str, audit_href: str, theme_preference: str = "dark") -> str:
    template = _load_template("control_plane_repo_setup.html")
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{INVENTORY_SUMMARY}}", html_escape(inventory_summary))
        .replace("{{INVENTORY_CARDS}}", inventory_cards)
        .replace("{{ONBOARDING_METRICS}}", onboarding_metrics)
        .replace("{{ONBOARDING_SUMMARY_CARDS}}", onboarding_summary_cards)
        .replace("{{AUDIT_HREF}}", html_escape(audit_href))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
    )


def _repo_setup_state_label(connection: dict[str, object] | None, allocation: dict[str, object] | None, summary: dict[str, object] | None) -> tuple[str, str]:
    if summary is not None:
        onboarding_status = str(summary.get("onboarding_status") or "").lower()
        if onboarding_status == "baseline_approved":
            return "Onboarded", "repo-setup-chip-strong"
        if onboarding_status == "pending_baseline_approval":
            return "Baseline pending", "repo-setup-chip-warm"
        return "Onboarding active", "repo-setup-chip-cool"
    if allocation is not None:
        return "Allocated", "repo-setup-chip-cool"
    if connection is not None:
        return "Available", "repo-setup-chip"
    return "Unknown", "repo-setup-chip"


def _repo_setup_inventory_copy(connection: dict[str, object] | None, allocation: dict[str, object] | None, summary: dict[str, object] | None) -> str:
    if summary is not None:
        onboarding_status = str(summary.get("onboarding_status") or "").lower()
        if onboarding_status == "baseline_approved":
            return "Baseline and onboarding are locked in, so this repository is already feeding posture, coverage, and version-history views."
        if onboarding_status == "pending_baseline_approval":
            return "The repository is onboarded, but its latest baseline still needs approval before DriftGuard treats it as the authoritative posture checkpoint."
        return "The repository has been allocated and partially onboarded, so DriftGuard is collecting artifacts and building its first stable baseline."
    if allocation is not None:
        return "This repository is already attached to the workspace and ready for its next onboarding or baseline pass."
    return "Allocate this repository to start onboarding, baseline capture, and repo-level journey tracking."


def _repo_setup_summary_copy(summary: dict[str, object]) -> str:
    onboarding_status = str(summary.get("onboarding_status") or "").lower()
    if onboarding_status == "baseline_approved":
        return "Stable baseline coverage is in place and the repo is contributing full posture tracking."
    if onboarding_status == "pending_baseline_approval":
        return "Artifact discovery is complete, but the baseline still needs approval before it becomes the reference posture."
    return "DriftGuard has started collecting artifacts and history for this repo, but onboarding is still maturing."


def _repo_setup_state_key(connection: dict[str, object] | None, allocation: dict[str, object] | None, summary: dict[str, object] | None) -> str:
    if summary is not None:
        onboarding_status = str(summary.get("onboarding_status") or "").lower()
        if onboarding_status == "baseline_approved":
            return "onboarded"
        if onboarding_status == "pending_baseline_approval":
            return "baseline_pending"
        return "onboarding"
    if allocation is not None:
        return "allocated"
    if connection is not None:
        return "available"
    return "unknown"


def render_repo_inventory_cards(repositories: list[dict[str, object]]) -> str:
    if not repositories:
        return '<article class="repo-setup-card repo-setup-card-empty"><div class="repo-setup-card-label">Repository inventory</div><h3>No repositories available yet</h3><p>Reconnect GitHub if repository enumeration has not been granted for this workspace identity.</p></article>'

    rendered: list[str] = []
    for repository in sorted(repositories, key=lambda item: str(item.get("repo_full") or "").lower()):
        repo_full = str(repository.get("repo_full") or "")
        if not repo_full:
            continue
        is_onboarded = bool(repository.get("is_onboarded"))
        action = (
            '<span class="repo-setup-chip repo-setup-chip-strong">Already there</span>'
            if is_onboarded
            else f'<a class="repo-setup-button repo-setup-button-link" href="{html_escape(str(repository.get("install_href") or _repo_github_installation_href(repo_full)))}" target="_blank" rel="noreferrer">Onboard</a>'
        )
        rendered.append(
            f'''
            <article class="repo-setup-inventory-row" data-repo-inventory-card="true" data-status="{'onboarded' if is_onboarded else 'available'}" data-repo-full="{html_escape(repo_full.lower())}">
                <span class="repo-setup-inventory-name">{html_escape(repo_full)}</span>
                <span class="repo-setup-inventory-action">{action}</span>
            </article>
            '''
        )
    return "".join(rendered)


def render_repo_onboarding_metrics(onboarded_summaries: list[dict[str, object]]) -> str:
    onboarded_count = len(onboarded_summaries)
    approved_count = sum(1 for summary in onboarded_summaries if str(summary.get("onboarding_status") or "").lower() == "baseline_approved")
    tracked_artifacts = sum(int(summary.get("discovered_artifact_count") or 0) for summary in onboarded_summaries)
    historical_checkpoints = sum(int(summary.get("historical_version_count") or 0) for summary in onboarded_summaries)
    cards = [
        ("Onboarded repos", onboarded_count, "Repositories with a stored onboarding record in this workspace."),
        ("Baseline approved", approved_count, "Repos whose current onboarding baseline is already locked."),
        ("Tracked artifacts", tracked_artifacts, "Control surfaces currently captured across onboarded repositories."),
        ("History checkpoints", historical_checkpoints, "Historical snapshots materialized across onboarded repositories."),
    ]
    return "".join(
        f'''
        <article class="repo-setup-metric-card">
            <span class="repo-setup-note-label">{html_escape(label)}</span>
            <strong>{value}</strong>
            <span class="repo-setup-metric-foot">{html_escape(detail)}</span>
        </article>
        '''
        for label, value, detail in cards
    )


def render_repo_onboarded_summary_cards(onboarded_summaries: list[dict[str, object]]) -> str:
    if not onboarded_summaries:
        return '<article class="repo-setup-card repo-setup-card-empty"><div class="repo-setup-card-label">Onboarded repo summaries</div><h3>No onboarded repositories yet</h3><p>Allocate a repository and finish the first onboarding pass to unlock rollout and posture summaries here.</p></article>'

    rendered: list[str] = []
    for summary in sorted(onboarded_summaries, key=lambda item: str(item.get("repo_full") or "").lower()):
        state_label, state_class = _repo_setup_state_label(None, {"allocation_status": summary.get("allocation_status")}, summary)
        state_key = _repo_setup_state_key(None, {"allocation_status": summary.get("allocation_status")}, summary)
        last_onboarded_value = summary.get("last_onboarded_at") if isinstance(summary.get("last_onboarded_at"), (int, float)) else 0
        rendered.append(
            f'''
            <article class="repo-setup-card repo-setup-card-strong repo-setup-summary-card" data-repo-summary-card="true" data-status="{html_escape(state_key)}" data-repo-full="{html_escape(str(summary['repo_full']).lower())}" data-artifacts="{int(summary.get('discovered_artifact_count') or 0)}" data-history="{int(summary.get('historical_version_count') or 0)}" data-last-onboarded="{last_onboarded_value}">
                <div class="repo-setup-card-top">
                    <div class="repo-setup-card-label">Onboarded repository</div>
                    <span class="repo-setup-chip {state_class}">{html_escape(state_label)}</span>
                </div>
                <h3><a class="repo-setup-card-link" href="{html_escape(_repo_dashboard_href(str(summary['repo_full'])))}">{html_escape(str(summary['repo_full']))}</a></h3>
                <div class="repo-setup-stat-row repo-setup-stat-row-tight">
                    <div class="repo-setup-stat"><span class="repo-setup-meta-label">Default branch</span><span class="repo-setup-meta-value">{html_escape(str(summary.get('default_branch') or 'unknown'))}</span></div>
                    <div class="repo-setup-stat"><span class="repo-setup-meta-label">Artifacts</span><span class="repo-setup-meta-value">{int(summary.get('discovered_artifact_count') or 0)}</span></div>
                    <div class="repo-setup-stat"><span class="repo-setup-meta-label">History</span><span class="repo-setup-meta-value">{int(summary.get('historical_version_count') or 0)}</span></div>
                    <div class="repo-setup-stat"><span class="repo-setup-meta-label">Last onboarded</span><span class="repo-setup-meta-value">{html_escape(_format_timestamp(summary.get('last_onboarded_at') if isinstance(summary.get('last_onboarded_at'), (int, float)) else None))}</span></div>
                </div>
                <p>{html_escape(_repo_setup_summary_copy(summary))}</p>
                <a class="repo-setup-secondary-link" href="{html_escape(_repo_dashboard_href(str(summary['repo_full'])))}">Open audit page</a>
            </article>
            '''
        )
    return "".join(rendered)


def render_repo_connection_cards(connections: list[dict[str, str]], *, csrf_token: str) -> str:
    return render_repo_inventory_cards(connections)


def render_repo_allocation_cards(allocations: list[dict[str, str]]) -> str:
    return render_repo_onboarded_summary_cards(allocations)
def render_control_plane_app_page(
    state: str | None = None,
    resolution: WorkspaceAccessResolution | None = None,
    *,
    profile_url: str | None = None,
    admin_url: str | None = None,
) -> str:
    resolved = resolution or _resolution_for_preview_state(state)
    template = _load_template("control_plane_app.html")
    primary_action = _render_action_chip(resolved.primary_cta, _state_primary_action_url(resolved), fallback="No action required")
    secondary_action = _render_action_chip(resolved.secondary_cta, _state_secondary_action_url(resolved), fallback="Workspace shell preview")
    next_action = _render_action_chip(resolved.required_next_action, _state_next_action_url(resolved), fallback="Continue to dashboard")
    return (
        template.replace("{{STATE_NAME}}", html_escape(resolved.state.replace("_", " ")))
        .replace("{{UI_TITLE}}", html_escape(resolved.ui_title))
        .replace("{{UI_BODY}}", html_escape(resolved.ui_body))
        .replace("{{PRIMARY_CTA}}", primary_action)
        .replace("{{SECONDARY_CTA}}", secondary_action)
        .replace("{{NEXT_ACTION}}", next_action)
        .replace("{{DASHBOARD_ACCESS}}", "Enabled" if resolved.can_access_dashboard else "Blocked")
        .replace("{{ACCESS_MODE}}", "Read only" if resolved.is_read_only else "Interactive")
        .replace("{{QUICK_LINKS}}", _render_quick_links(profile_url=profile_url, admin_url=admin_url))
        .replace("{{STATE_LINKS}}", _render_state_links(resolved.state))
        .replace("{{CHECKLIST_ITEMS}}", _render_checklist(resolved))
    )


def render_control_plane_profile_page(
    *,
    display_name: str,
    theme_preference: str,
    github_login: str,
    github_user_id: str,
    primary_email: str | None,
    workspace_name: str,
    workspace_role: str,
    plan_label: str,
    next_payment_at: float | None,
    status_note: str | None,
    resolution: WorkspaceAccessResolution,
    admin_url: str | None,
    csrf_token: str,
) -> str:
    template = _load_template("control_plane_profile.html")
    admin_control = ""
    if admin_url:
        admin_control = f'''<a class="control-page-admin-link" href="{html_escape(admin_url)}">Open system admin</a>'''
    return (
        template.replace("{{DISPLAY_NAME}}", html_escape(display_name))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{THEME_DARK_CHECKED}}", "checked" if theme_preference == "dark" else "")
        .replace("{{THEME_LIGHT_CHECKED}}", "checked" if theme_preference == "light" else "")
        .replace("{{CSRF_INPUT}}", _csrf_input(csrf_token))
        .replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{GITHUB_LOGIN}}", html_escape(github_login))
        .replace("{{GITHUB_USER_ID}}", html_escape(github_user_id))
        .replace("{{PRIMARY_EMAIL}}", html_escape(primary_email or "Unavailable"))
        .replace("{{WORKSPACE_PERMISSION}}", html_escape(_permission_label(workspace_role)))
        .replace("{{NEXT_PAYMENT_AT}}", html_escape(_format_timestamp(next_payment_at)))
        .replace("{{STATUS_NOTE}}", html_escape(status_note or "User settings apply to your identity inside the current workspace. GitHub account details remain read-only."))
        .replace("{{ADMIN_CONTROL}}", admin_control)
    )


def render_control_plane_settings_page(
    *,
    workspace_name: str,
    plan_label: str,
    theme_preference: str,
    status_note: str | None,
    resolution: WorkspaceAccessResolution,
    admin_url: str | None,
    csrf_token: str,
    pr_comments_allowed_by_plan: bool,
    pr_comments_setting_enabled: bool,
    can_manage: bool,
    workspace_role: str,
    workspace_members: list[dict[str, object]],
    repo_rows: list[dict[str, object]],
    next_payment_at: float | None,
    subscription_status: str | None,
    setup_state: str,
    installation_account_login: str | None,
    repo_limit: int | None,
    seat_limit: int | None,
    invite_enabled: bool,
) -> str:
    template = _load_template("control_plane_settings.html")
    effective_status = pr_comments_allowed_by_plan and pr_comments_setting_enabled
    status_copy = status_note or "Manage workspace-wide comment behavior for pull requests."
    if not pr_comments_allowed_by_plan:
        status_copy = "Your current plan does not permit PR comments, so this setting will not take effect until comments are included in the workspace entitlement."
    manage_note = "Owners and admins can change this setting." if can_manage else "Only workspace owners and admins can change this setting."
    admin_control = ""
    if admin_url:
        admin_control = f'''<a class="control-page-admin-link" href="{html_escape(admin_url)}">Open system admin</a>'''
    return (
        template.replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{CSRF_INPUT}}", _csrf_input(csrf_token))
        .replace("{{ADMIN_CONTROL}}", admin_control)
        .replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{STATUS_NOTE}}", html_escape(status_copy))
        .replace("{{CHECKLIST_ITEMS}}", _render_checklist(resolution))
        .replace("{{WORKSPACE_NAME_INPUT}}", html_escape(workspace_name))
        .replace("{{PR_COMMENTS_ON_CHECKED}}", "checked" if pr_comments_setting_enabled else "")
        .replace("{{PR_COMMENTS_OFF_CHECKED}}", "checked" if not pr_comments_setting_enabled else "")
        .replace("{{PR_COMMENTS_DISABLED}}", "disabled" if not can_manage else "")
        .replace("{{PLAN_PR_COMMENTS_STATUS}}", "Included" if pr_comments_allowed_by_plan else "Unavailable")
        .replace("{{WORKSPACE_PR_COMMENTS_STATUS}}", "On" if pr_comments_setting_enabled else "Off")
        .replace("{{EFFECTIVE_PR_COMMENTS_STATUS}}", "Active" if effective_status else "Paused")
        .replace("{{MANAGE_NOTE}}", html_escape(manage_note))
        .replace("{{WORKSPACE_PERMISSION}}", html_escape(_permission_label(workspace_role)))
        .replace("{{WORKSPACE_MEMBER_ACTIONS}}", _render_workspace_member_invite_form(csrf_token=csrf_token, invite_enabled=invite_enabled))
        .replace("{{WORKSPACE_MEMBERS}}", _render_workspace_members_list(workspace_members))
        .replace("{{WORKSPACE_REPOS}}", _render_workspace_repos_table(repo_rows))
        .replace("{{NEXT_PAYMENT_AT}}", html_escape(_format_timestamp(next_payment_at)))
        .replace("{{SUBSCRIPTION_STATUS}}", html_escape((subscription_status or "unknown").replace("_", " ").title()))
        .replace("{{SETUP_STATE}}", html_escape(_setup_state_label(setup_state)))
        .replace("{{INSTALLATION_ACCOUNT_LOGIN}}", html_escape(installation_account_login or "Not linked"))
        .replace("{{REPO_LIMIT}}", html_escape(str(repo_limit or 0)))
        .replace("{{SEAT_LIMIT}}", html_escape(str(seat_limit or 0)))
    )


def render_control_plane_placeholder_page(
    *,
    page_title: str,
    page_kicker: str,
    page_copy: str,
    workspace_name: str,
    plan_label: str,
    theme_preference: str,
    admin_url: str | None,
    active_nav: str,
) -> str:
    template = _load_template("control_plane_placeholder.html")
    admin_control = ""
    if admin_url:
        admin_control = f'''<a class="control-page-admin-link" href="{html_escape(admin_url)}">Open system admin</a>'''
    return (
        template.replace("{{PAGE_TITLE}}", html_escape(page_title))
        .replace("{{PAGE_KICKER}}", html_escape(page_kicker))
        .replace("{{PAGE_COPY}}", html_escape(page_copy))
        .replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{ADMIN_CONTROL}}", admin_control)
        .replace("{{COMPLIANCE_ACTIVE}}", " sidebar-nav-item-active" if active_nav == "compliance" else "")
        .replace("{{POLICIES_ACTIVE}}", " sidebar-nav-item-active" if active_nav == "policies" else "")
        .replace("{{HELP_ACTIVE}}", " sidebar-nav-item-active" if active_nav == "help" else "")
    )


def render_control_plane_help_page(
    *,
    workspace_name: str,
    plan_label: str,
    theme_preference: str,
    admin_url: str | None,
    repo_rows: list[dict[str, object]],
    repo_summaries: list[object],
    export_ready_count: int,
    export_pending_count: int,
) -> str:
    template = _load_template("control_plane_help.html")
    admin_control = ""
    if admin_url:
        admin_control = f'''<a class="control-page-admin-link" href="{html_escape(admin_url)}">Open system admin</a>'''
    help_context = _build_help_context(repo_rows, repo_summaries, export_ready_count, export_pending_count)
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{ADMIN_CONTROL}}", admin_control)
        .replace("{{HELP_CONTEXT_SUMMARY}}", html_escape(help_context["summary"]))
        .replace("{{HELP_START_HERE_COPY}}", html_escape(help_context["start_here_copy"]))
        .replace("{{HELP_NEXT_STEP_LABEL}}", html_escape(help_context["next_step_label"]))
        .replace("{{HELP_STATUS_CARDS}}", help_context["status_cards_html"])
        .replace("{{HELP_NEXT_STEP_PANEL}}", help_context["next_step_panel_html"])
    )


def render_control_plane_mcp_page(
    *,
    workspace_name: str,
    plan_label: str,
    theme_preference: str,
    admin_url: str | None,
    active_tab: str,
    download_url: str,
    broker_host: str,
    config_snippet: str,
    principals: list,
    audit_logs: list,
    csrf_token: str,
    can_manage: bool,
    entitlement_allows: bool,
    one_time_secret: str | None,
    max_principals: int,
    new_client_id: str | None = None,
) -> str:
    template = _load_template("control_plane_mcp.html")
    admin_control = ""
    if admin_url:
        admin_control = f'''<a class="control-page-admin-link" href="{html_escape(admin_url)}">Open system admin</a>'''

    tab_urls = {"overview": "/app/integrations/mcp?tab=overview"}
    tab_labels = {"overview": "Overview"}
    if can_manage:
        tab_urls.update(
            {
                "api-keys": "/app/integrations/mcp?tab=api-keys",
                "activity": "/app/integrations/mcp?tab=activity",
            }
        )
        tab_labels.update({"api-keys": "API keys", "activity": "Activity"})
    tab_bar = "".join(
        f'''<a class="control-page-tab-link" href="{html_escape(tab_urls[tab_key])}"{' aria-current="page"' if tab_key == active_tab else ''}>{html_escape(tab_labels[tab_key])}</a>'''
        for tab_key in tab_urls
    )

    if one_time_secret:
        secret_block = f"""
        <section class="control-page-flash-secret" role="alert" aria-live="polite">
            <div class="control-page-flash-secret-inner">
                <p class="control-page-flash-secret-title">&#128274; API key created — copy your secret now</p>
                <p class="control-page-copy">This secret will not be shown again. Store it securely.</p>
                <div class="control-page-secret-reveal">
                    <code id="api-key-secret" class="control-page-monospace">{html_escape(one_time_secret)}</code>
                    <button type="button" class="control-page-copy-btn"
                            onclick="navigator.clipboard.writeText(document.getElementById('api-key-secret').textContent)">
                        Copy
                    </button>
                </div>
            </div>
        </section>"""
    else:
        secret_block = ""

    active_principal_count = sum(1 for principal in principals if getattr(principal, "status", "") == "active") if can_manage else None
    api_keys_section = _render_api_keys_section(
        principals=principals,
        can_manage=can_manage,
        entitlement_allows=entitlement_allows,
        csrf_token=csrf_token,
        max_principals=max_principals,
        new_client_id=new_client_id,
    )

    tool_cards = "".join(
        f'''<article class="help-page-topic-card"><strong>{html_escape(tool["name"])}</strong><p>{html_escape(tool["description"])}</p><p><span class="control-page-badge">{html_escape(tool["required_scope"])}</span></p></article>'''
        for tool in MCP_BROKER_TOOLS
    )

    activity_rows = ""
    for entry in audit_logs:
        try:
            payload = json.loads(getattr(entry, "payload_json", "") or "{}")
        except (ValueError, TypeError):
            payload = {}
        details: list[str] = []
        if isinstance(payload, dict):
            if payload.get("source"):
                details.append(f"source={payload['source']}")
            scopes = payload.get("scopes")
            if isinstance(scopes, list) and scopes:
                details.append("scopes=" + ", ".join(str(scope) for scope in scopes))
            if payload.get("tool_name"):
                details.append(f"tool={payload['tool_name']}")
        detail_text = " | ".join(details) if details else "Workspace automation activity"
        activity_rows += f"""
        <tr>
            <td>{html_escape(_format_timestamp(getattr(entry, 'created_at', None)))}</td>
            <td><code>{html_escape(getattr(entry, 'event_type', 'unknown'))}</code></td>
            <td>{html_escape(f"{getattr(entry, 'subject_type', 'workspace')}:{getattr(entry, 'subject_id', 'n/a')}")}</td>
            <td>{html_escape(detail_text)}</td>
        </tr>"""

    if activity_rows:
        activity_section = f"""
        <article class="control-page-section control-page-section-wide">
            <div class="secondary-panel-title">Workspace activity</div>
            <h2 class="control-page-section-title">Recent integration and API-key events</h2>
            <p class="control-page-copy">This feed keeps connector setup, key rotation, and broker actions together on one page.</p>
            <div class="control-page-table-wrap">
                <table class="control-page-table">
                    <thead>
                        <tr><th>When</th><th>Event</th><th>Subject</th><th>Details</th></tr>
                    </thead>
                    <tbody>{activity_rows}</tbody>
                </table>
            </div>
        </article>"""
    else:
        activity_section = """
        <article class="control-page-section control-page-section-wide">
            <div class="secondary-panel-title">Workspace activity</div>
            <h2 class="control-page-section-title">Recent integration and API-key events</h2>
            <p class="control-page-copy">No integration activity has been recorded for this workspace yet.</p>
        </article>"""

    if active_tab == "api-keys" and can_manage:
        active_panel = api_keys_section
    elif active_tab == "activity" and can_manage:
        active_panel = activity_section
    else:
        workspace_principal_card = """
        <article class="control-page-section">
            <div class="secondary-panel-title">Workspace machine principals</div>
            <h2 class="control-page-section-title">API-key posture</h2>
            <p class="control-page-copy">Workspace machine-principal inventory and API-key management stay restricted to workspace owners and admins.</p>
        </article>"""
        if can_manage:
            workspace_principal_card = f"""
        <article class="control-page-section">
            <div class="secondary-panel-title">Workspace machine principals</div>
            <h2 class="control-page-section-title">API-key posture</h2>
            <p class="control-page-copy">{html_escape(str(active_principal_count or 0))} active workspace API key(s) are currently available for connector setup. Use the API keys tab to review scopes, create a new key, or revoke an old one.</p>
            <a class="control-page-button" href="{html_escape(tab_urls['api-keys'])}">Open API keys</a>
        </article>"""

        activity_card = """
        <article class="control-page-section">
            <div class="secondary-panel-title">Operational visibility</div>
            <h2 class="control-page-section-title">Recent integration activity</h2>
            <p class="control-page-copy">Recent integration and API-key activity stays visible only to workspace owners and admins.</p>
        </article>"""
        if can_manage:
            activity_card = f"""
        <article class="control-page-section">
            <div class="secondary-panel-title">Operational visibility</div>
            <h2 class="control-page-section-title">Recent integration activity</h2>
            <p class="control-page-copy">Keep connector rollout, key changes, and broker actions together by reviewing the Activity tab before handing the package to a customer host.</p>
            <a class="control-page-button" href="{html_escape(tab_urls['activity'])}">Open Activity</a>
        </article>"""

        workflow_card_two = """
                <div class="help-page-action-card"><span class="help-page-action-step">2</span><strong>Request API-key access</strong><p>Workspace owners and admins manage machine principals, scope selection, and one-time secret handoff for connector setup.</p></div>
                <div class="help-page-action-card"><span class="help-page-action-step">3</span><strong>Coordinate rollout</strong><p>Ask an owner or admin to verify recent integration activity before the connector is handed to a customer host.</p></div>"""
        if can_manage:
            workflow_card_two = f"""
                <a class="help-page-action-card" href="{html_escape(tab_urls['api-keys'])}"><span class="help-page-action-step">2</span><strong>Review API keys</strong><p>Use the API keys tab for the workspace machine principal list, scope choices, and one-time secret handoff.</p></a>
                <a class="help-page-action-card" href="{html_escape(tab_urls['activity'])}"><span class="help-page-action-step">3</span><strong>Check activity</strong><p>Recent integration activity stays on this page so connector rollout, key rotation, and broker actions can be reviewed together.</p></a>"""

        active_panel = f"""
        <article class="control-page-section control-page-section-wide">
            <div class="secondary-panel-title">Download</div>
            <h2 class="control-page-section-title">Customer MCP connector package</h2>
            <p class="control-page-copy">This downloadable package is meant for authenticated customers only. It runs as a thin local MCP server in the customer environment, exchanges workspace-scoped machine-principal credentials for a short-lived broker token, and forwards allowed tool calls to the hosted PromptDrift broker.</p>
            <div class="help-page-workflow-grid">
                <a class="help-page-action-card" href="{html_escape(download_url)}"><span class="help-page-action-step">1</span><strong>Download connector</strong><p>Includes the local MCP server script, dependency list, environment template, and example host configuration.</p></a>
                {workflow_card_two}
            </div>
        </article>

        <article class="control-page-section">
            <div class="secondary-panel-title">Quickstart</div>
            <h2 class="control-page-section-title">Host configuration</h2>
            <pre class="help-page-flow">{html_escape(config_snippet)}</pre>
            <p class="control-page-copy">The connector never receives internal PromptDrift bearer tokens. It uses the machine-principal credentials you create for this workspace only to obtain a short-lived broker token.</p>
        </article>

        <article class="control-page-section">
            <div class="secondary-panel-title">Safety model</div>
            <h2 class="control-page-section-title">Trust boundary</h2>
            <pre class="help-page-flow">Your AI agent
  -&gt; customer MCP connector
  -&gt; PromptDrift broker
  -&gt; curated PromptDrift control-plane reads</pre>
            <p class="control-page-copy">One connector session maps to one workspace. The connector package is thin on purpose so PromptDrift can keep product semantics, output shaping, and credential handling server-side.</p>
        </article>

        <article class="control-page-section control-page-section-wide">
            <div class="secondary-panel-title">Available tools</div>
            <h2 class="control-page-section-title">Read-first MCP surface</h2>
            <div class="help-page-card-grid">{tool_cards}</div>
        </article>

        {workspace_principal_card}

        {activity_card}"""

    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{ADMIN_CONTROL}}", admin_control)
        .replace("{{BROKER_HOST}}", html_escape(broker_host))
        .replace("{{TOOL_COUNT}}", html_escape(str(len(MCP_BROKER_TOOLS))))
        .replace("{{ACTIVE_API_KEY_LABEL}}", html_escape("Active API keys" if can_manage else "API-key access"))
        .replace("{{ACTIVE_API_KEY_COUNT}}", html_escape(str(active_principal_count or "Restricted")))
        .replace("{{ONE_TIME_SECRET_BLOCK}}", secret_block)
        .replace("{{MCP_TAB_BAR}}", tab_bar)
        .replace("{{MCP_ACTIVE_PANEL}}", active_panel)
    )


def _build_help_context(
    repo_rows: list[dict[str, object]],
    repo_summaries: list[object],
    export_ready_count: int,
    export_pending_count: int,
) -> dict[str, str]:
    visible_repo_count = len(repo_rows)
    repo_summary_by_full = {
        str(getattr(summary, "repo_full", "")): summary
        for summary in repo_summaries
        if str(getattr(summary, "repo_full", ""))
    }
    baseline_approved = [
        summary for summary in repo_summaries if str(getattr(summary, "onboarding_status", "")).lower() == "baseline_approved"
    ]
    baseline_pending = [
        summary for summary in repo_summaries if str(getattr(summary, "onboarding_status", "")).lower() == "pending_baseline_approval"
    ]
    onboarding_active = [
        summary
        for summary in repo_summaries
        if str(getattr(summary, "onboarding_status", "")).lower() not in {"baseline_approved", "pending_baseline_approval"}
    ]
    connected_only_rows = [row for row in repo_rows if str(row.get("repo_full") or "") not in repo_summary_by_full]

    summary = (
        f"This workspace currently has {visible_repo_count} visible repos, "
        f"{len(repo_summaries)} onboarded repos, and {len(baseline_approved)} approved baselines."
    )

    if not repo_rows:
        start_here_copy = "This workspace does not have any visible repositories yet. Open Repositories first and confirm the GitHub installation is connected to the right account or org."
        next_step_label = "Connect a repository"
        next_title = "No repositories are visible yet"
        next_body = "Help should start with the actual blocking state. Right now the blocker is before drift review: the workspace has nothing in inventory to onboard or review."
        next_href = "/app/repos"
        next_cta = "Open Repositories"
    elif connected_only_rows:
        target_repo = str(connected_only_rows[0].get("repo_full") or "your repo")
        start_here_copy = f"Start in Repositories. {target_repo} is visible to the workspace, but at least one visible repo has not been onboarded into review workflows yet."
        next_step_label = "Finish onboarding"
        next_title = "A visible repo still needs onboarding"
        next_body = f"{target_repo} is connected, but it does not yet have stored onboarding state. Until onboarding runs, Help, Dashboard, and Compliance can only explain so much because there is no review-ready baseline context."
        next_href = "/app/repos"
        next_cta = "Go to Repositories"
    elif baseline_pending:
        target_repo = str(getattr(baseline_pending[0], "repo_full", "your repo"))
        start_here_copy = f"You already have onboarded repositories. The next useful move is baseline review, because {target_repo} is waiting for approval before drift becomes fully grounded."
        next_step_label = "Review pending baseline"
        next_title = "A repo is waiting for baseline approval"
        next_body = f"{target_repo} has completed artifact discovery, but its latest baseline is still pending approval. That usually explains why the system feels partially ready instead of fully trustworthy."
        next_href = f"/dashboard/{quote(target_repo, safe='')}"
        next_cta = "Open pending repo"
    elif onboarding_active:
        target_repo = str(getattr(onboarding_active[0], "repo_full", "your repo"))
        start_here_copy = f"Your workspace has onboarding in progress. Check {target_repo} next to confirm discovery finished and baseline creation did not stall."
        next_step_label = "Check onboarding progress"
        next_title = "Onboarding is still maturing"
        next_body = f"{target_repo} has onboarding state, but it is not baseline-approved yet. That usually means Help should point you to repository setup and the repo dashboard before any compliance workflow."
        next_href = f"/dashboard/{quote(target_repo, safe='')}"
        next_cta = "Open repo dashboard"
    elif export_pending_count > 0:
        start_here_copy = "Review state looks healthy enough to move into evidence work. The next check is whether pending exports are still running or need follow-up in Compliance."
        next_step_label = "Check pending exports"
        next_title = "Compliance work is active"
        next_body = f"This workspace has {len(baseline_approved)} approved baselines and {export_pending_count} pending export jobs. The most useful help now is operational: verify evidence generation is finishing cleanly."
        next_href = "/app/compliance"
        next_cta = "Open Compliance"
    else:
        start_here_copy = "This workspace already has review-ready repos. The next useful place to work is the Dashboard, where approved baselines and current repo state can be compared directly."
        next_step_label = "Review drift"
        next_title = "The workspace is ready for review"
        next_body = f"This workspace has {len(baseline_approved)} approved baselines and {export_ready_count} completed exports. Help should now point people toward active review and evidence follow-up, not setup."
        next_href = "/dashboard"
        next_cta = "Open Dashboard"

    status_cards = [
        ("Visible repos", str(visible_repo_count), "Repositories visible to this workspace through its current GitHub connection."),
        ("Onboarded repos", str(len(repo_summaries)), "Repositories with stored onboarding state that Help can reason about."),
        ("Baselines approved", str(len(baseline_approved)), "Repos whose current baseline is already trusted for drift comparison."),
        ("Exports ready / pending", f"{export_ready_count} / {export_pending_count}", "Compliance export jobs completed versus still in progress."),
    ]
    status_cards_html = "".join(
        f'''
        <article class="control-page-stat-card">
            <span class="control-page-stat-label">{html_escape(label)}</span>
            <strong>{html_escape(value)}</strong>
            <span class="control-page-microcopy">{html_escape(detail)}</span>
        </article>
        '''
        for label, value, detail in status_cards
    )

    spotlight_items: list[tuple[str, str]] = []
    if connected_only_rows:
        spotlight_items.extend((str(row.get("repo_full") or "repo"), "Connected only") for row in connected_only_rows[:2])
    if baseline_pending:
        spotlight_items.extend((str(getattr(item, "repo_full", "repo")), "Baseline pending") for item in baseline_pending[:2])
    if baseline_approved and not spotlight_items:
        spotlight_items.extend((str(getattr(item, "repo_full", "repo")), "Ready for drift review") for item in baseline_approved[:2])

    spotlight_html = (
        "".join(
            f'<div class="help-page-spotlight-row"><strong>{html_escape(repo_full)}</strong><span>{html_escape(label)}</span></div>'
            for repo_full, label in spotlight_items
        )
        or '<div class="control-page-empty">No repo-specific guidance is available yet because the workspace has no visible repositories.</div>'
    )

    next_step_panel_html = f'''
        <div class="help-page-next-grid">
            <article class="control-page-section help-page-rich-text help-page-next-card">
                <p class="secondary-panel-title">Recommended next move</p>
                <h2 class="control-page-section-title">{html_escape(next_title)}</h2>
                <p>{html_escape(next_body)}</p>
                <a class="control-page-button help-page-inline-button" href="{html_escape(next_href)}">{html_escape(next_cta)}</a>
            </article>
            <article class="control-page-section help-page-rich-text help-page-next-card">
                <p class="secondary-panel-title">Repo spotlight</p>
                <h2 class="control-page-section-title">What Help is reacting to right now</h2>
                <div class="help-page-spotlight-list">{spotlight_html}</div>
            </article>
        </div>
    '''

    return {
        "summary": summary,
        "start_here_copy": start_here_copy,
        "next_step_label": next_step_label,
        "status_cards_html": status_cards_html,
        "next_step_panel_html": next_step_panel_html,
    }


def _compliance_tone_class(tone: str) -> str:
    normalized = (tone or "muted").strip().lower()
    if normalized in {"success", "warning", "danger", "muted"}:
        return f"tone-{normalized}"
    return "tone-muted"


def _render_compliance_tab_bar(active_tab: str) -> str:
    items = (
        ("readiness", "Readiness", "/app/compliance"),
        ("frameworks", "Frameworks", "/app/compliance/frameworks"),
        ("exports", "Exports", "/app/compliance/exports"),
        ("evidence", "Evidence", "/app/compliance/evidence"),
    )
    return "".join(
        f'<a class="control-page-tab-link" href="{html_escape(href)}"{(" aria-current=\"page\"" if key == active_tab else "")}>{html_escape(label)}</a>'
        for key, label, href in items
    )


def _render_compliance_metrics(view: ComplianceWorkspaceView) -> str:
    return "".join(
        f'''
        <article class="control-page-stat-card compliance-metric-card">
            <span class="control-page-stat-label">{html_escape(metric.label)}</span>
            <strong>{html_escape(metric.value)}</strong>
            <span class="control-page-microcopy">{html_escape(metric.detail)}</span>
        </article>
        '''
        for metric in view.metrics
    )


def _render_compliance_verdict(view: ComplianceWorkspaceView) -> str:
    verdict = view.verdict
    return f'''
        <article class="control-page-section compliance-verdict-card {_compliance_tone_class(verdict.tone)}">
            <p class="secondary-panel-title">Readiness verdict</p>
            <h2 class="control-page-section-title">{html_escape(verdict.headline)}</h2>
            <p>{html_escape(verdict.detail)}</p>
            <a class="control-page-button" href="{html_escape(verdict.cta_href)}">{html_escape(verdict.cta_label)}</a>
        </article>
    '''


def _render_compliance_gaps(gaps: tuple[ComplianceGapItem, ...]) -> str:
    if not gaps:
        return '<div class="control-page-empty">No blocking readiness gaps are open right now.</div>'
    return "".join(
        f'''
        <article class="control-page-section compliance-gap-card">
            <div class="compliance-gap-head">
                <div>
                    <p class="secondary-panel-title">Top gap</p>
                    <h3 class="control-page-section-title">{html_escape(item.title)}</h3>
                </div>
                <span class="compliance-status-pill tone-warning">{item.affected_count} repos</span>
            </div>
            <p>{html_escape(item.detail)}</p>
            <p class="control-page-microcopy">{html_escape(', '.join(item.repo_fulls))}</p>
            <a class="subtle-link" href="{html_escape(item.cta_href)}">{html_escape(item.cta_label)}</a>
        </article>
        '''
        for item in gaps
    )


def _render_compliance_repo_table(rows: tuple[ComplianceRepoReadinessRow, ...]) -> str:
    if not rows:
        return '<div class="control-page-empty">No repositories are visible to this workspace yet.</div>'
    body = "".join(
        f'''
        <tr>
            <td>
                <div class="stack compact-stack">
                    <a class="link" href="{html_escape(row.repo_href)}">{html_escape(row.repo_full)}</a>
                    <span class="control-page-microcopy">{html_escape(row.connection_status)} · default branch {html_escape(row.default_branch)}</span>
                </div>
            </td>
            <td><span class="compliance-status-pill {_compliance_tone_class(row.overall_tone)}">{html_escape(row.overall_label)}</span></td>
            <td><span class="compliance-status-pill {_compliance_tone_class(row.baseline_tone)}">{html_escape(row.baseline_label)}</span></td>
            <td><span class="compliance-status-pill {_compliance_tone_class(row.governance_tone)}">{html_escape(row.governance_label)}</span></td>
            <td><span class="compliance-status-pill {_compliance_tone_class(row.freshness_tone)}">{html_escape(row.freshness_label)}</span></td>
            <td>
                <div class="stack compact-stack">
                    <a class="subtle-link" href="{html_escape(row.action_href)}">{html_escape(row.action_label)}</a>
                    <span class="control-page-microcopy">{html_escape(row.action_detail)}</span>
                </div>
            </td>
        </tr>
        '''
        for row in rows
    )
    return (
        '<div class="table-shell"><table class="data-table"><thead><tr>'
        '<th>Repository</th><th>Status</th><th>Baseline</th><th>Governance</th><th>Freshness</th><th>Next action</th>'
        f'</tr></thead><tbody>{body}</tbody></table></div>'
    )


def _render_compliance_export_summary(summary: ComplianceExportSummary) -> str:
    download_markup = ""
    if summary.latest_download_href:
        download_markup = f'<a class="subtle-link" href="{html_escape(summary.latest_download_href)}">Download latest export</a>'
    return f'''
        <article class="control-page-section compliance-export-summary-card">
            <div class="compliance-export-summary-grid">
                <div>
                    <p class="secondary-panel-title">Export readiness</p>
                    <h2 class="control-page-section-title">{summary.ready_repo_count} repos can export immediately</h2>
                    <p>{html_escape(summary.latest_detail)}</p>
                </div>
                <div class="control-page-stat-grid compliance-inline-stat-grid">
                    <article class="control-page-stat-card"><span class="control-page-stat-label">Completed</span><strong>{summary.completed_count}</strong></article>
                    <article class="control-page-stat-card"><span class="control-page-stat-label">Pending</span><strong>{summary.pending_count}</strong></article>
                    <article class="control-page-stat-card"><span class="control-page-stat-label">Failed</span><strong>{summary.failed_count}</strong></article>
                </div>
            </div>
            <div class="compliance-export-summary-actions">
                <span class="compliance-status-pill {_compliance_tone_class('success' if summary.ready_repo_count else 'warning')}">{html_escape(summary.latest_status_label)}</span>
                <a class="control-page-button" href="/app/compliance/exports#new-export">Generate export</a>
                {download_markup}
            </div>
        </article>
    '''


def _render_compliance_framework_cards(cards: tuple[ComplianceFrameworkCard, ...]) -> str:
    return "".join(
        f'''
        <article class="compliance-framework-card compliance-framework-detail-card">
            <div class="compliance-gap-head">
                <div>
                    <p class="secondary-panel-title">Framework</p>
                    <h3 class="control-page-section-title">{html_escape(card.title)}</h3>
                </div>
                <span class="compliance-status-pill tone-muted">{html_escape(card.status_label)}</span>
            </div>
            <p>{html_escape(card.detail)}</p>
            <ul class="control-page-checklist compliance-framework-list">
                {''.join(f'<li class="checklist-item"><span>{html_escape(bullet)}</span></li>' for bullet in card.bullets)}
            </ul>
        </article>
        '''
        for card in cards
    )


def _render_compliance_evidence_rows(rows: tuple[ComplianceRepoReadinessRow, ...]) -> str:
    if not rows:
        return '<div class="control-page-empty">No evidence rows are available yet.</div>'
    cards: list[str] = []
    for row in rows:
        if not row.gap_keys and row.freshness_tone == "success":
            summary = "Evidence is current and governance-backed."
            next_step = "Keep this repo inside the regular review cadence."
        elif "needs_setup" in row.gap_keys:
            summary = "No onboarding record is stored for this repo yet."
            next_step = "Run onboarding to create the first evidence pack."
        elif "baseline_review" in row.gap_keys:
            summary = "A baseline exists, but the approval decision is still pending."
            next_step = "Approve or reject the pending baseline before export."
        elif "missing_governance" in row.gap_keys:
            summary = "Governance or policy evidence is missing from the stored artifact set."
            next_step = "Attach governance artifacts to strengthen the review trail."
        elif "stale_evidence" in row.gap_keys:
            summary = "Stored evidence has moved outside the fresh review window."
            next_step = "Refresh onboarding output before the next evidence pack."
        else:
            summary = "Evidence is usable, but it is aging out of the fresh window."
            next_step = "Schedule a refresh before the stale threshold is crossed."
        chips = ''.join(f'<span class="compliance-status-pill tone-warning">{html_escape(gap.replace("_", " ").title())}</span>' for gap in row.gap_keys) or '<span class="compliance-status-pill tone-success">Healthy evidence</span>'
        cards.append(
            f'''
            <article class="compliance-assessment-card">
                <div class="compliance-assessment-head">
                    <strong>{html_escape(row.repo_full)}</strong>
                    <span class="compliance-status-pill {_compliance_tone_class(row.freshness_tone)}">{html_escape(row.freshness_label)}</span>
                </div>
                <div class="tag-row">{chips}</div>
                <p>{html_escape(summary)}</p>
                <p class="control-page-microcopy">{html_escape(next_step)}</p>
                <a class="subtle-link" href="{html_escape(row.repo_href)}">Open audit page</a>
            </article>
            '''
        )
    return f'<div class="compliance-assessment-grid">{"".join(cards)}</div>'


def _render_compliance_evidence_filter_note(gap_filter: str | None, repo_filter: str | None, filtered_count: int, total_count: int) -> str:
    active_filter = normalize_compliance_gap_filter(gap_filter)
    active_repo = normalize_compliance_repo_filter(repo_filter)
    if active_filter is None and active_repo is None:
        return ""
    repo_label = "repo" if filtered_count == 1 else "repos"
    fragments: list[str] = [f"Showing {filtered_count} of {total_count} {repo_label}"]
    if active_filter is not None:
        fragments.append(f"for <strong>{html_escape(active_filter.replace('_', ' ').title())}</strong>")
    if active_repo is not None:
        fragments.append(f"for <strong>{html_escape(active_repo)}</strong>")
    return (
        f'<div class="control-page-inline-note">'
        f"{' '.join(fragments)}. "
        f'<a class="subtle-link" href="/app/compliance/evidence">Show all evidence</a>'
        f'</div>'
    )


def _render_compliance_export_scope_rows(rows: tuple[ComplianceRepoReadinessRow, ...]) -> str:
    if not rows:
        return '<div class="control-page-empty">No repositories are connected to this workspace yet.</div>'
    rendered: list[str] = []
    for row in rows:
        eligibility_chips = []
        if row.export_ready:
            eligibility_chips.append('<span class="compliance-status-pill tone-success">Review-ready preset</span>')
        else:
            eligibility_chips.append('<span class="compliance-status-pill tone-warning">Needs readiness work</span>')
        eligibility_chips.append(f'<span class="compliance-status-pill {_compliance_tone_class(row.freshness_tone)}">{html_escape(row.freshness_label)}</span>')
        rendered.append(
            f'''
            <label class="compliance-repo-row">
                <input type="checkbox" name="repo_fulls" value="{html_escape(row.repo_full)}" />
                <div class="compliance-repo-main">
                    <div class="compliance-repo-copy">
                        <strong>{html_escape(row.repo_full)}</strong>
                        <span>{html_escape(row.connection_status)} · default branch {html_escape(row.default_branch)}</span>
                        <div class="tag-row">{"".join(eligibility_chips)}</div>
                        <span>{html_escape(row.action_detail)}</span>
                    </div>
                    <a class="subtle-link" href="{html_escape(row.repo_href)}">Open audit page</a>
                </div>
            </label>
            '''
        )
    return "".join(rendered)


def _render_compliance_export_history(jobs: tuple[ExportJob, ...] | list[ExportJob]) -> str:
    if not jobs:
        return '<div class="control-page-empty">No compliance exports have been generated for this workspace yet.</div>'
    rows: list[str] = []
    for job in jobs:
        range_label = (
            f"{datetime.fromtimestamp(job.from_ts).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(job.to_ts).strftime('%Y-%m-%d')}"
        )
        if job.status == "completed" and job.download_token and job.result_blob:
            download_markup = f'<a class="link" href="/api/export/{job.id}/download?token={quote(job.download_token)}">Download</a>'
        else:
            download_markup = html_escape(job.status.replace("_", " ").title())
        rows.append(
            f'''
            <tr>
                <td>{html_escape(job.repo_full)}</td>
                <td>{html_escape(job.export_mode.replace('_', ' ').title())}</td>
                <td>{html_escape(range_label)}</td>
                <td>{html_escape(job.status.replace('_', ' ').title())}</td>
                <td>{download_markup}</td>
            </tr>
            '''
        )
    return (
        '<div class="table-shell"><table class="data-table"><thead><tr>'
        '<th>Repository</th><th>Mode</th><th>Date range</th><th>Status</th><th>Download</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _render_compliance_export_form(view: ComplianceWorkspaceView, csrf_token: str) -> str:
    return f'''
        <article id="new-export" class="control-page-section stack compact-stack">
            <div>
                <p class="secondary-panel-title">Generate evidence pack</p>
                <h2 class="control-page-section-title">Run compliance exports</h2>
                <p>Generate a focused export for selected repositories or let the server-side presets choose the repos that are already ready.</p>
            </div>
            <form method="post" action="/app/compliance/export" class="stack compact-stack">
                {_csrf_input(csrf_token)}
                <label class="settings-field">
                    <span>Server-side repo preset</span>
                    <select name="export_preset">
                        <option value="none">No preset</option>
                        <option value="review_ready">Review-ready repos</option>
                        <option value="fresh_review_ready">Fresh review-ready repos</option>
                    </select>
                </label>
                <fieldset class="settings-field">
                    <legend>Scope</legend>
                    <label><input type="radio" name="export_scope" value="all_visible" checked /> All visible repos</label>
                    <label><input type="radio" name="export_scope" value="selected" /> Selected repos only</label>
                </fieldset>
                <fieldset class="settings-field">
                    <legend>Export mode</legend>
                    <label><input type="radio" name="export_mode" value="compliance" checked /> Compliance evidence bundle</label>
                    <label><input type="radio" name="export_mode" value="compliance_plus_drift" /> Compliance plus drift context</label>
                </fieldset>
                <div class="control-page-meta-grid">
                    <label class="settings-field"><span>From</span><input type="date" name="from_date" required /></label>
                    <label class="settings-field"><span>To</span><input type="date" name="to_date" required /></label>
                </div>
                <label><input type="checkbox" name="include_artifact_content" value="true" checked /> Include artifact content when available</label>
                <div class="compliance-repo-list">{_render_compliance_export_scope_rows(view.repo_rows)}</div>
                <button class="control-page-button" type="submit">Generate export</button>
            </form>
        </article>
    '''


def _render_compliance_page_content(
    active_tab: str,
    view: ComplianceWorkspaceView,
    csrf_token: str,
    export_jobs: tuple[ExportJob, ...],
    evidence_filter: str = "",
    evidence_repo: str = "",
) -> str:
    if active_tab == "frameworks":
        return f'''
            <section class="control-page-section stack compact-stack">
                <h2 class="control-page-section-title">Framework coverage</h2>
                <p>Use this view when you need the framework-oriented narrative rather than the day-to-day readiness summary.</p>
                <div class="compliance-framework-grid">{_render_compliance_framework_cards(view.framework_cards)}</div>
            </section>
        '''
    if active_tab == "exports":
        return f'''
            {_render_compliance_export_summary(view.export_summary)}
            {_render_compliance_export_form(view, csrf_token)}
            <section class="control-page-section stack compact-stack">
                <div>
                    <p class="secondary-panel-title">Recent activity</p>
                    <h2 class="control-page-section-title">Export history</h2>
                </div>
                {_render_compliance_export_history(export_jobs)}
            </section>
        '''
    if active_tab == "evidence":
        active_filter, active_repo, evidence_rows, repo_rows = filter_compliance_evidence_view(view, evidence_filter, evidence_repo)
        return f'''
            <section class="control-page-section stack compact-stack">
                <div>
                    <p class="secondary-panel-title">Evidence detail</p>
                    <h2 class="control-page-section-title">Repository evidence posture</h2>
                    <p>Inspect missing governance artifacts, stale evidence, and pending approvals without the export form competing for attention.</p>
                </div>
                {_render_compliance_evidence_filter_note(active_filter, active_repo, len(evidence_rows), len(view.evidence_rows))}
                {_render_compliance_evidence_rows(repo_rows)}
            </section>
        '''
    return f'''
        <div class="control-page-stat-grid">{_render_compliance_metrics(view)}</div>
        <div class="compliance-readiness-grid">
            {_render_compliance_verdict(view)}
            <section class="control-page-section stack compact-stack">
                <div>
                    <p class="secondary-panel-title">Priority gaps</p>
                    <h2 class="control-page-section-title">What needs attention next</h2>
                </div>
                <div class="compliance-gap-grid">{_render_compliance_gaps(view.top_gaps)}</div>
            </section>
        </div>
        <section class="control-page-section stack compact-stack">
            <div>
                <p class="secondary-panel-title">Repository view</p>
                <h2 class="control-page-section-title">Readiness by repository</h2>
            </div>
            {_render_compliance_repo_table(view.repo_rows)}
        </section>
        {_render_compliance_export_summary(view.export_summary)}
    '''


def render_control_plane_compliance_page(
    *,
    workspace_name: str,
    audit_href: str,
    plan_label: str,
    theme_preference: str,
    status_note: str,
    active_tab: str,
    page_title: str,
    page_description: str,
    page_note: str,
    view: ComplianceWorkspaceView,
    export_jobs: tuple[ExportJob, ...] | None = None,
    csrf_token: str = "",
    evidence_filter: str = "",
    evidence_repo: str = "",
) -> str:
    template = _load_template("control_plane_compliance.html")
    export_job_items = export_jobs or tuple()
    status_markup = f'<div class="control-page-inline-note">{html_escape(status_note)}</div>' if status_note else ""
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{AUDIT_HREF}}", html_escape(audit_href))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{STATUS_NOTE}}", status_markup)
        .replace("{{PAGE_TITLE}}", html_escape(page_title))
        .replace("{{PAGE_DESCRIPTION}}", html_escape(page_description))
        .replace("{{PAGE_NOTE}}", html_escape(page_note))
        .replace("{{COMPLIANCE_TAB_BAR}}", _render_compliance_tab_bar(active_tab))
        .replace("{{COMPLIANCE_CONTENT}}", _render_compliance_page_content(active_tab, view, csrf_token, tuple(export_job_items), evidence_filter, evidence_repo))
    )


def render_control_plane_admin_page(
    *,
    actor_github_login: str,
    admin_rows: list[dict[str, object]],
    unclaimed_installations: list[dict[str, object]],
    billing_claims: list[dict[str, object]],
    audit_logs: list[dict[str, object]],
    csrf_token: str,
    status_note: str | None = None,
) -> str:
    template = _load_template("control_plane_admin.html")

    unique_users: list[dict[str, object]] = []
    seen_user_ids: set[int] = set()
    unique_workspaces: list[dict[str, object]] = []
    seen_workspace_ids: set[int] = set()
    for row in admin_rows:
        user_id = row.get("user_id")
        if isinstance(user_id, int) and user_id not in seen_user_ids:
            seen_user_ids.add(user_id)
            unique_users.append(row)
        workspace_id = row.get("workspace_id")
        if isinstance(workspace_id, int) and workspace_id not in seen_workspace_ids:
            seen_workspace_ids.add(workspace_id)
            unique_workspaces.append(row)

    user_options = "".join(
        f'<option value="{int(row.get("user_id") or 0)}">{html_escape(str(row.get("user_display_name") or "Unknown"))}#{int(row.get("user_id") or 0)}</option>'
        for row in unique_users
    ) or '<option value="">Create a user first</option>'
    workspace_options = "".join(
        f'<option value="{int(row.get("workspace_id") or 0)}">{html_escape(str(row.get("workspace_display_name") or "Workspace"))}</option>'
        for row in unique_workspaces
    ) or '<option value="">Create a workspace first</option>'
    status_markup = f'<div class="admin-status-note">{html_escape(status_note)}</div>' if status_note else ""

    add_toolbar = f'''
        <div class="admin-toolbar">
            <details class="admin-disclosure">
                <summary>Add user</summary>
                <form method="post" action="/app/admin/users/create" class="action-form admin-form-grid">
                    {_csrf_input(csrf_token)}
                    <input class="field-input" name="display_name" maxlength="120" placeholder="Display name" />
                    <input class="field-input" name="primary_email" maxlength="320" placeholder="Primary email" />
                    <button type="submit" class="button">Create user</button>
                </form>
            </details>
            <details class="admin-disclosure">
                <summary>Add workspace</summary>
                <form method="post" action="/app/admin/workspaces/create" class="action-form admin-form-grid">
                    {_csrf_input(csrf_token)}
                    <input class="field-input" name="display_name" maxlength="120" placeholder="Workspace name" />
                    <input class="field-input" name="slug" maxlength="120" placeholder="workspace-slug" />
                    <select class="field-input" name="billing_owner_user_id">{user_options}</select>
                    <button type="submit" class="button">Create workspace</button>
                </form>
            </details>
            <details class="admin-disclosure">
                <summary>Assign membership</summary>
                <form method="post" action="/app/admin/memberships/upsert" class="action-form admin-form-grid">
                    {_csrf_input(csrf_token)}
                    <select class="field-input" name="user_id">{user_options}</select>
                    <select class="field-input" name="workspace_id">{workspace_options}</select>
                    <select class="field-input" name="role">
                        <option value="owner">Owner</option>
                        <option value="admin">Edit</option>
                        <option value="viewer">Read</option>
                    </select>
                    <button type="submit" class="button">Save membership</button>
                </form>
            </details>
        </div>
    '''

    admin_table_rows = []
    for row in admin_rows:
        user_id = int(row.get("user_id") or 0)
        workspace_id = int(row.get("workspace_id") or 0) if row.get("workspace_id") is not None else 0
        workspace_name = str(row.get("workspace_display_name") or "Unassigned")
        workspace_slug = str(row.get("workspace_slug") or _slugify_value(workspace_name))
        profile_bits = []
        if row.get("primary_email"):
            profile_bits.append(html_escape(str(row.get("primary_email"))))
        if row.get("github_company"):
            profile_bits.append(html_escape(str(row.get("github_company"))))
        if row.get("github_location"):
            profile_bits.append(html_escape(str(row.get("github_location"))))
        if row.get("github_blog"):
            profile_bits.append(html_escape(str(row.get("github_blog"))))
        if row.get("github_twitter_username"):
            profile_bits.append(f'@{html_escape(str(row.get("github_twitter_username")))}')
        if row.get("github_bio"):
            profile_bits.append(html_escape(str(row.get("github_bio"))))
        github_login = str(row.get("github_login") or "Unavailable")
        github_profile_url = str(row.get("github_profile_url") or "").strip()
        github_markup = (
            f'<a class="subtle-link admin-inline-link" href="{html_escape(github_profile_url)}">@{html_escape(github_login)}</a>'
            if github_profile_url and github_login != "Unavailable"
            else html_escape(github_login)
        )
        counts_markup = (
            f'Installs {html_escape(str(int(row.get("installation_count") or 0)))} | '
            f'Connected {html_escape(str(int(row.get("connected_repo_count") or 0)))} | '
            f'Onboarded {html_escape(str(int(row.get("onboarded_repo_count") or 0)))}'
        )
        user_edit = f'''
            <details class="admin-row-disclosure">
                <summary>Edit user</summary>
                <form method="post" action="/app/admin/users/{user_id}/update" class="action-form admin-form-grid">
                    {_csrf_input(csrf_token)}
                    <input class="field-input" name="display_name" maxlength="120" value="{html_escape(str(row.get('user_display_name') or ''))}" />
                    <input class="field-input" name="primary_email" maxlength="320" value="{html_escape(str(row.get('primary_email') or ''))}" />
                    <label class="admin-checkbox-row"><input type="checkbox" name="active" value="1" {'checked' if bool(row.get('user_active')) else ''} /> Active user</label>
                    <button type="submit" class="button">Save user</button>
                </form>
            </details>
        '''
        workspace_edit = ""
        membership_delete = ""
        workspace_delete = ""
        if workspace_id:
            workspace_edit = f'''
                <details class="admin-row-disclosure">
                    <summary>Edit workspace</summary>
                    <form method="post" action="/app/admin/workspaces/{workspace_id}/update" class="action-form admin-form-grid">
                        {_csrf_input(csrf_token)}
                        <input class="field-input" name="display_name" maxlength="120" value="{html_escape(workspace_name)}" />
                        <input class="field-input" name="slug" maxlength="120" value="{html_escape(workspace_slug)}" />
                        <button type="submit" class="button">Save workspace</button>
                    </form>
                </details>
            '''
            membership_delete = f'''
                <form method="post" action="/app/admin/memberships/{workspace_id}/{user_id}/delete" class="action-form" onsubmit="return confirm('Remove this user from the workspace?');">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="button admin-danger-button">Remove member</button>
                </form>
            '''
            workspace_delete = f'''
                <form method="post" action="/app/admin/workspaces/{workspace_id}/delete" class="action-form" onsubmit="return confirm('Delete this workspace and all linked records?');">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="button admin-danger-button">Delete workspace</button>
                </form>
            '''
        user_delete = f'''
            <form method="post" action="/app/admin/users/{user_id}/delete" class="action-form" onsubmit="return confirm('Delete this user and any linked workspace memberships?');">
                {_csrf_input(csrf_token)}
                <button type="submit" class="button admin-danger-button">Delete user</button>
            </form>
        '''
        actions_markup = f'<div class="admin-action-stack">{user_edit}{workspace_edit}{membership_delete}{workspace_delete}{user_delete}</div>'
        admin_table_rows.append(
            [
                html_escape(workspace_name),
                html_escape(str(row.get("user_display_name") or "Unknown")),
                github_markup,
                "<br />".join(profile_bits) if profile_bits else "<span class=\"page-note\">No extra profile data</span>",
                html_escape(str(row.get("membership_role") or "none")),
                html_escape(counts_markup),
                html_escape(_setup_state_label(str(row.get("setup_state") or "none"))),
                html_escape(_format_timestamp(row.get("last_login_at") if isinstance(row.get("last_login_at"), (int, float)) else None)),
                actions_markup,
            ]
        )

    audit_rows = _render_table(
        ["When", "Event", "Subject", "Workspace", "Actor", "Details"],
        [
            [
                html_escape(_format_timestamp(row.get("created_at") if isinstance(row.get("created_at"), (int, float)) else None)),
                html_escape(str(row.get("event_type") or "")),
                html_escape(f"{row.get('subject_type') or 'subject'}:{row.get('subject_id') or ''}"),
                html_escape(str(row.get("workspace_id") or "Global")),
                html_escape(str(row.get("actor_user_id") or "System")),
                html_escape(str(row.get("payload_json") or "{}")),
            ]
            for row in audit_logs
        ],
    )

    def _render_installation_summary(row: dict[str, object]) -> str:
        installation_login = str(row.get("installation_account_login") or row.get("installation_id") or "none")
        installation_count = int(row.get("installation_count") or 0)
        if installation_count > 1:
            return f"{installation_login} ({installation_count} installs)"
        return installation_login

    user_rows = _render_table(
        ["Workspace", "User", "GitHub", "Profile", "Role", "Counts", "Setup", "Last login", "Actions"],
        admin_table_rows,
    )
    install_rows = _render_table(
        ["Installation id", "Account", "Type", "Status", "Repos", "Last sync", "Updated"],
        [
            [
                html_escape(str(row.get("installation_id") or "")),
                html_escape(str(row.get("account_login") or "")),
                html_escape(f"{row.get('account_type') or ''}/{row.get('target_type') or ''}"),
                html_escape(str(row.get("status") or "")),
                html_escape(str(row.get("repo_count") or 0)),
                html_escape(_format_timestamp(row.get("last_synced_at") if isinstance(row.get("last_synced_at"), (int, float)) else None)),
                html_escape(_format_timestamp(row.get("updated_at") if isinstance(row.get("updated_at"), (int, float)) else None)),
            ]
            for row in unclaimed_installations
        ],
    )
    claim_rows = _render_table(
        ["Provider", "Purchase", "Plan", "Status", "Email", "Next payment", "Claimed workspace", "Consumed", "Updated"],
        [
            [
                html_escape(str(row.get("provider") or "")),
                html_escape(str(row.get("external_purchase_id") or "")),
                html_escape(str(row.get("plan_code") or "")),
                html_escape(str(row.get("billing_status") or "")),
                html_escape(str(row.get("billing_email") or "")),
                html_escape(_format_timestamp(row.get("next_payment_at") if isinstance(row.get("next_payment_at"), (int, float)) else None)),
                html_escape(str(row.get("claimed_workspace_id") or "pending")),
                html_escape(_format_timestamp(row.get("consumed_at") if isinstance(row.get("consumed_at"), (int, float)) else None)),
                html_escape(_format_timestamp(row.get("updated_at") if isinstance(row.get("updated_at"), (int, float)) else None)),
            ]
            for row in billing_claims
        ],
    )
    return (
        template.replace("{{ACTOR_GITHUB_LOGIN}}", html_escape(actor_github_login))
        .replace("{{QUICK_LINKS}}", _render_quick_links(profile_url="/app/profile", admin_url="/app/admin"))
        .replace("{{STATUS_NOTE}}", status_markup)
        .replace("{{ADD_TOOLBAR}}", add_toolbar)
        .replace("{{USER_ROWS}}", user_rows)
        .replace("{{AUDIT_ROWS}}", audit_rows)
        .replace("{{UNCLAIMED_INSTALL_ROWS}}", install_rows)
        .replace("{{CLAIM_ROWS}}", claim_rows)
    )


def _render_api_keys_section(
    principals: list,
    can_manage: bool,
    entitlement_allows: bool,
    csrf_token: str,
    max_principals: int,
    new_client_id: str | None = None,
) -> str:
    import json as _json
    from datetime import datetime as _dt, timezone as _timezone

    def _fmt_date(ts: float | None) -> str:
        if not ts:
            return "-"
        try:
            return _dt.fromtimestamp(ts, tz=_timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return "-"

    rows_html = ""
    for p in principals:
        try:
            scopes = _json.loads(p.scopes_json)
        except (ValueError, TypeError):
            scopes = []
        scope_badges = "".join(
            f'<span class="control-page-badge">{html_escape(s)}</span>' for s in scopes
        )
        revoke_form = ""
        if can_manage and p.status == "active":
            revoke_form = f"""
            <form method="post" action="/app/settings/api-keys/{html_escape(p.client_id)}/revoke"
                  style="display:inline"
                  onsubmit="return confirm('Revoke this API key? This cannot be undone.')">
                <input type="hidden" name="csrf_token" value="{html_escape(csrf_token)}" />
                <button type="submit" class="control-page-danger-btn">Revoke</button>
            </form>"""
        status_class = "control-page-badge-active" if p.status == "active" else "control-page-badge-revoked"
        # Highlight the newly-created row
        row_class = ' class="control-page-table-row-new"' if (new_client_id and p.client_id == new_client_id) else ""
        # Show first 16 chars of client_id; not secret, but enough to match CI config.
        client_id_display = html_escape(p.client_id[:16]) + "&#8230;"
        created_label = _fmt_date(getattr(p, "created_at", None))
        rows_html += f"""
        <tr{row_class}>
            <td><code>{html_escape(p.display_name)}</code></td>
            <td><code class="control-page-monospace" title="{html_escape(p.client_id)}">{client_id_display}</code></td>
            <td>{scope_badges}</td>
            <td><span class="control-page-badge {status_class}">{html_escape(p.status)}</span></td>
            <td>{created_label}</td>
            <td>{revoke_form}</td>
        </tr>"""

    table_html = ""
    if principals:
        table_html = f"""
        <div class="control-page-table-wrap">
            <table class="control-page-table">
                <thead>
                    <tr>
                        <th>Name</th><th>Client ID</th><th>Scopes</th><th>Status</th><th>Created</th><th>Actions</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>"""
    else:
        table_html = '<p class="control-page-copy">No API keys created yet.</p>'

    create_form = ""
    if can_manage and entitlement_allows:
        active_count = sum(1 for p in principals if p.status == "active")
        at_limit = active_count >= max_principals
        disabled_attr = ' disabled' if at_limit else ''
        limit_note = (
            f'<p class="control-page-copy control-page-warning">Workspace limit of {max_principals} active API keys reached.</p>'
            if at_limit else ""
        )
        create_form = f"""
        <div class="control-page-form-divider"></div>
        <div class="secondary-panel-title">Create API key</div>
        {limit_note}
        <form method="post" action="/app/settings/api-keys" class="control-page-form">
            <input type="hidden" name="csrf_token" value="{html_escape(csrf_token)}" />
            <label class="control-page-label" for="api-key-name">Key name</label>
            <input class="control-page-input" id="api-key-name" name="display_name"
                   placeholder="e.g. CI pipeline" maxlength="120" required{disabled_attr} />
            <fieldset class="control-page-checkbox-group" aria-labelledby="api-key-scopes-title">
                <legend class="control-page-label" id="api-key-scopes-title">Scopes</legend>
                <label class="control-page-checkbox-option">
                    <input type="checkbox" name="scope_drift_read" value="1"{disabled_attr} />
                    <span class="control-page-checkbox-copy"><strong>drift.read</strong> &mdash; Read drift data and dashboards</span>
                </label>
                <label class="control-page-checkbox-option">
                    <input type="checkbox" name="scope_drift_write_low" value="1"{disabled_attr} />
                    <span class="control-page-checkbox-copy"><strong>drift.write.low</strong> &mdash; Initiate compliance exports</span>
                </label>
                <label class="control-page-checkbox-option">
                    <input type="checkbox" name="scope_drift_write_high" value="1"{disabled_attr} />
                    <span class="control-page-checkbox-copy"><strong>drift.write.high</strong> &mdash; Approve baselines
                        <em class="control-page-warning">(Baseline approval is a permanent governance action &mdash; assign only to trusted automation)</em>
                    </span>
                </label>
            </fieldset>
            <button type="submit" class="control-page-submit"{disabled_attr}>Create API key</button>
        </form>"""
    elif not entitlement_allows:
        create_form = '<p class="control-page-copy">Control plane API access is not enabled for this workspace. Contact support to enable it.</p>'
    else:
        create_form = '<p class="control-page-copy">Only workspace owners and admins can create API keys.</p>'

    return f"""
    <article class="control-page-section">
        <div class="secondary-panel-title">Workspace API keys</div>
        <h2 class="control-page-section-title">Machine principal credentials</h2>
        <p class="control-page-copy">
            API keys let trusted automation access the DriftGuard control plane API without user credentials.
            Each key is scoped to this workspace. Store secrets securely &mdash; they are shown only once.
        </p>
        {table_html}
        {create_form}
    </article>"""


def render_api_keys_settings_page(
    *,
    workspace_name: str,
    plan_label: str,
    theme_preference: str,
    admin_url: str | None,
    csrf_token: str,
    can_manage: bool,
    entitlement_allows: bool,
    principals: list,
    one_time_secret: str | None,
    max_principals: int,
    new_client_id: str | None = None,
) -> str:
    template = _load_template("control_plane_api_keys.html")
    admin_control = ""
    if admin_url:
        admin_control = f'<a class="control-page-admin-link" href="{html_escape(admin_url)}">Open system admin</a>'

    if one_time_secret:
        secret_block = f"""
        <section class="control-page-flash-secret" role="alert" aria-live="polite">
            <div class="control-page-flash-secret-inner">
                <p class="control-page-flash-secret-title">&#128274; API key created - copy your secret now</p>
                <p class="control-page-copy">This secret will not be shown again. Store it securely.</p>
                <div class="control-page-secret-reveal">
                    <code id="api-key-secret" class="control-page-monospace">{html_escape(one_time_secret)}</code>
                    <button type="button" class="control-page-copy-btn"
                            onclick="navigator.clipboard.writeText(document.getElementById('api-key-secret').textContent)">
                        Copy
                    </button>
                </div>
            </div>
        </section>"""
    else:
        secret_block = ""

    api_keys_section = _render_api_keys_section(
        principals=principals,
        can_manage=can_manage,
        entitlement_allows=entitlement_allows,
        csrf_token=csrf_token,
        max_principals=max_principals,
        new_client_id=new_client_id,
    )

    return (
        template.replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{ADMIN_CONTROL}}", admin_control)
        .replace("{{API_KEYS_SECTION}}", api_keys_section)
        # Replace ONE_TIME_SECRET_BLOCK last; its content contains the raw
        # secret and must not be interpretable as another placeholder.
        .replace("{{ONE_TIME_SECRET_BLOCK}}", secret_block)
    )
