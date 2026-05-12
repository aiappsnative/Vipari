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
        "authenticated_no_workspace": "/workspaces/new",
        "invited_pending_acceptance": "/workspace",
        "forbidden": "/workspace",
        "workspace_no_subscription": "/billing",
        "billing_pending_confirmation": "/billing",
        "payment_failed": "/billing",
        "awaiting_github_install": "/setup/install",
        "awaiting_repo_onboarding": "/repos",
        "active_comments_only": "/billing?plan=starter",
        "active": "/dashboard",
        "canceled_active_until_period_end": "/billing",
        "expired_read_only": "/billing",
    }
    return mapping.get(resolution.state)


def _state_secondary_action_url(resolution: WorkspaceAccessResolution) -> str | None:
    mapping = {
        "billing_pending_confirmation": "/billing",
        "payment_failed": "/billing/portal",
        "awaiting_github_install": "/setup/install",
        "active_comments_only": "/repos",
        "canceled_active_until_period_end": "/billing",
        "expired_read_only": "/billing",
    }
    return mapping.get(resolution.state)


def _state_next_action_url(resolution: WorkspaceAccessResolution) -> str | None:
    mapping = {
        "unauthenticated": "/login",
        "authenticated_no_workspace": "/workspaces/new",
        "workspace_no_subscription": "/billing",
        "billing_pending_confirmation": "/billing",
        "payment_failed": "/billing/portal",
        "awaiting_github_install": "/setup/install",
        "awaiting_repo_onboarding": "/repos",
        "active_comments_only": "/repos",
        "active": "/dashboard",
        "canceled_active_until_period_end": "/dashboard",
        "expired_read_only": "/billing",
    }
    return mapping.get(resolution.state)


def _checklist_cta_links(resolution: WorkspaceAccessResolution) -> dict[str, str]:
    links = {
        "billing": "/billing",
        "workspace": "/workspaces/new",
        "github_login": "/login",
        "installation": "/setup/install",
        "repo_allocation": "/repos",
        "first_scan": "/repos",
    }
    if resolution.state == "active":
        links["repo_allocation"] = "/dashboard"
        links["first_scan"] = "/dashboard"
    if resolution.state == "active_comments_only":
        links["repo_allocation"] = "/repos"
        links["first_scan"] = "/repos"
    return links


def _render_action_chip(label: str | None, href: str | None, *, fallback: str) -> str:
    text = label or fallback
    if href:
        return f'<a class="button" href="{html_escape(href)}">{html_escape(text)}</a>'
    return f'<span>{html_escape(text)}</span>'


def _control_page_shell_notice_html(*, shell_title: str, shell_body: str, shell_cta_href: str | None, shell_cta_label: str | None) -> str:
    cta_markup = ""
    if shell_cta_href and shell_cta_label:
        cta_markup = f'<a class="filter-add" href="{html_escape(shell_cta_href, quote=True)}">{html_escape(shell_cta_label)}</a>'
    return (
        '<section class="card-shell dashboard-shell-notice" id="dashboard-shell-notice" aria-label="Upgrade notice">'
        f'<div class="secondary-panel-title">{html_escape(shell_title)}</div>'
        f'<div class="muted">{html_escape(shell_body)}</div>'
        f'{cta_markup}'
        '</section>'
    )


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
            <form method="post" action="/settings/invite" class="member-toolbar-form">
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
        f'<a class="state-pill {"state-pill-active" if state == active_state else ""}" href="/workspace?state={html_escape(state)}">{html_escape(state.replace("_", " "))}</a>'
        for state in states
    )


def render_control_plane_marketing_page() -> str:
    return _load_template("control_plane_marketing.html")


def render_control_plane_login_page(*, auth_start_url: str, context_note: str | None = None, auth_available: bool = True) -> str:
    template = _load_template("control_plane_login.html")
    action_markup = (
        f'''
        <a class="button login-cta-button" href="{html_escape(auth_start_url)}">
            <span class="login-cta-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" focusable="false"><path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.57.11.78-.25.78-.56 0-.27-.01-1.01-.02-1.98-3.2.69-3.88-1.54-3.88-1.54-.52-1.33-1.28-1.68-1.28-1.68-1.05-.72.08-.7.08-.7 1.15.08 1.76 1.19 1.76 1.19 1.03 1.76 2.69 1.25 3.35.96.1-.75.4-1.25.72-1.54-2.55-.29-5.24-1.27-5.24-5.67 0-1.25.45-2.27 1.18-3.07-.12-.29-.51-1.47.11-3.06 0 0 .97-.31 3.17 1.17a10.96 10.96 0 0 1 5.77 0c2.19-1.48 3.16-1.17 3.16-1.17.63 1.59.24 2.77.12 3.06.74.8 1.18 1.82 1.18 3.07 0 4.41-2.69 5.37-5.25 5.66.41.35.77 1.03.77 2.08 0 1.5-.01 2.72-.01 3.09 0 .31.2.68.79.56A11.51 11.51 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z" fill="currentColor"/></svg>
            </span>
            <span>Continue with GitHub</span>
        </a>
        '''
        if auth_available
        else f'<a class="button button-disabled login-cta-button" aria-disabled="true" href="{html_escape(auth_start_url)}"><span class="login-cta-icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.57.11.78-.25.78-.56 0-.27-.01-1.01-.02-1.98-3.2.69-3.88-1.54-3.88-1.54-.52-1.33-1.28-1.68-1.28-1.68-1.05-.72.08-.7.08-.7 1.15.08 1.76 1.19 1.76 1.19 1.03 1.76 2.69 1.25 3.35.96.1-.75.4-1.25.72-1.54-2.55-.29-5.24-1.27-5.24-5.67 0-1.25.45-2.27 1.18-3.07-.12-.29-.51-1.47.11-3.06 0 0 .97-.31 3.17 1.17a10.96 10.96 0 0 1 5.77 0c2.19-1.48 3.16-1.17 3.16-1.17.63 1.59.24 2.77.12 3.06.74.8 1.18 1.82 1.18 3.07 0 4.41-2.69 5.37-5.25 5.66.41.35.77 1.03.77 2.08 0 1.5-.01 2.72-.01 3.09 0 .31.2.68.79.56A11.51 11.51 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z" fill="currentColor"/></svg></span><span>Continue with GitHub</span></a>'
    )
    return (
        template.replace("{{AUTH_ACTION}}", action_markup)
        .replace("{{AUTH_START_URL}}", html_escape(auth_start_url))
        .replace("{{CONTEXT_NOTE}}", html_escape(context_note or ""))
        .replace("{{CONTEXT_NOTE_VISIBILITY}}", "" if context_note else " login-context-note-hidden")
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
    context_message = " ".join(context_lines) if context_lines else "Create the first Vipari workspace before billing and GitHub installation continue."
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
    theme_preference: str = "dark",
    sidebar_profile_initial: str = "V",
) -> str:
    template = _load_template("control_plane_billing.html")
    selected_plan = PLAN_DEFINITIONS.get(selected_plan_code)
    selected_plan_label = selected_plan.label if selected_plan is not None else selected_plan_code.replace("_", " ").title()
    checkout_state_label = "Ready to start"
    if flow_context.get("checkout_session_id"):
        checkout_state_label = "Checkout in progress"
    elif flow_context.get("canceled"):
        checkout_state_label = "Checkout canceled"
    portal_block = (
        f'<a class="subtle-link" href="{html_escape(portal_url)}">Open billing portal</a>' if portal_url else '<span class="subtle-link">Portal unavailable</span>'
    )
    flow_query = ""
    if flow_context:
        flow_query = "&" + "&".join(
            f"{html_escape(key)}={html_escape(value)}"
            for key, value in flow_context.items()
            if key != "plan"
        )
        if flow_query == "&":
            flow_query = ""
    plan_copy = {
        "free": {
            "tagline": "Try Vipari on a single repo.",
            "price": "Free forever",
            "price_suffix": "",
            "features": (
                "1 repository",
                "Limited automated PR comments on detected AI drift",
                "Read-only dashboard and audit details",
                "No version history",
            ),
            "button_label": "Start with GitHub",
        },
        "starter": {
            "tagline": "For pilots and small teams.",
            "price": "$40",
            "price_suffix": "/mo",
            "features": (
                "Up to 5 repositories",
                "Unlimited PR drift comments",
                "Core dashboard",
                "Baseline comparisons",
                "Version history",
                "Review queue",
                "Repo posture radar",
                "Data export for SOC2 & ISO27001 compliance",
                "Email support",
            ),
            "button_label": "Start with GitHub",
            "badge": "Most popular",
        },
        "team": {
            "tagline": "For active product teams.",
            "price": "$150",
            "price_suffix": "/mo",
            "features": (
                "Up to 20 repositories",
                "Everything in Starter",
                "Governance coverage views",
                "Team access & roles",
                "Priority support",
            ),
            "button_label": "Start with GitHub",
        },
        "enterprise": {
            "tagline": "For larger organizations.",
            "price": "Custom",
            "price_suffix": "",
            "features": (
                "Custom repo limits",
                "Everything in Team",
                "Advanced governance",
                "Custom onboarding",
                "SSO & enterprise features",
                "Dedicated support",
            ),
            "button_label": "Talk to us",
        },
    }
    plan_cards = []
    for code, plan in PLAN_DEFINITIONS.items():
        card_copy = plan_copy.get(code, {})
        button_label = str(card_copy.get("button_label") or f"Choose {plan.label}")
        button_disabled = code in {"starter", "team"}
        features = "".join(
            f'<li class="billing-tier-feature-item">{html_escape(feature)}</li>'
            for feature in card_copy.get("features", ())
        )
        badge_markup = ""
        if card_copy.get("badge"):
            badge_markup = f'<span class="billing-tier-badge">{html_escape(str(card_copy["badge"]))}</span>'
        plan_cards.append(
            f'''
            <article class="billing-tier-card{' billing-tier-card-featured' if code == 'starter' else ''}{' billing-tier-card-current' if code == selected_plan_code else ''}">
                {badge_markup}
                <div class="billing-tier-header">
                    <p class="billing-tier-name">{html_escape(plan.label)}</p>
                    <p class="billing-tier-tagline">{html_escape(str(card_copy.get("tagline") or ''))}</p>
                </div>
                <div class="billing-tier-price-row">
                    <strong class="billing-tier-price">{html_escape(str(card_copy.get("price") or plan.label))}</strong>
                    <span class="billing-tier-price-suffix">{html_escape(str(card_copy.get("price_suffix") or ''))}</span>
                </div>
                <ul class="billing-tier-feature-list">{features}</ul>
                <form method="post" action="/billing/checkout?plan={html_escape(code)}{flow_query}" class="billing-tier-form">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="button billing-tier-button{' billing-tier-button-primary' if code == 'starter' else ''}"{' disabled' if button_disabled else ''}>{html_escape(button_label)}</button>
                </form>
            </article>
            '''
        )
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{CURRENT_PLAN_LABEL}}", html_escape(current_plan_label))
        .replace("{{SUBSCRIPTION_STATUS}}", html_escape(subscription_status))
        .replace("{{SELECTED_PLAN_LABEL}}", html_escape(selected_plan_label))
        .replace("{{CHECKOUT_STATE_LABEL}}", html_escape(checkout_state_label))
        .replace("{{CHECKOUT_STATUS_NOTE}}", html_escape(checkout_status_note or "Choose a plan to create or resume Stripe checkout."))
        .replace("{{PLAN_CARDS}}", "".join(plan_cards))
        .replace("{{PORTAL_ACTION}}", portal_block)
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
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
        <form method="post" action="/setup/install/link" class="action-form">
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


def _repo_allocate_href(repo_full: str) -> str:
    return f'/repos/allocate?repo_full={quote(repo_full, safe="/")}'


def _repo_disconnect_href(repo_full: str) -> str:
    return f'/repos/disconnect?repo_full={quote(repo_full, safe="/")}'


def render_control_plane_repo_setup_page(*, workspace_name: str, inventory_summary: str, inventory_cards: str, onboarding_metrics: str, onboarding_summary_cards: str, audit_href: str, theme_preference: str = "dark", sidebar_profile_initial: str = "V") -> str:
    template = _load_template("control_plane_repo_setup.html")
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{INVENTORY_SUMMARY}}", html_escape(inventory_summary))
        .replace("{{INVENTORY_CARDS}}", inventory_cards)
        .replace("{{ONBOARDING_METRICS}}", onboarding_metrics)
        .replace("{{ONBOARDING_SUMMARY_CARDS}}", onboarding_summary_cards)
        .replace("{{AUDIT_HREF}}", html_escape(audit_href))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
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
            return "The repository is onboarded, but its latest baseline still needs approval before Vipari treats it as the authoritative posture checkpoint."
        return "The repository has been allocated and partially onboarded, so Vipari is collecting artifacts and building its first stable baseline."
    if allocation is not None:
        return "This repository is already attached to the workspace and ready for its next onboarding or baseline pass."
    return "Allocate this repository to start onboarding, baseline capture, and repo-level journey tracking."


def _repo_setup_summary_copy(summary: dict[str, object]) -> str:
    onboarding_status = str(summary.get("onboarding_status") or "").lower()
    if onboarding_status == "baseline_approved":
        return "Stable baseline coverage is in place and the repo is contributing full posture tracking."
    if onboarding_status == "pending_baseline_approval":
        return "Artifact discovery is complete, but the baseline still needs approval before it becomes the reference posture."
    return "Vipari has started collecting artifacts and history for this repo, but onboarding is still maturing."


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


def render_repo_inventory_cards(
    repositories: list[dict[str, object]],
    *,
    csrf_token: str,
    install_start_href: str,
    install_disabled: bool = False,
    install_disabled_href: str = "/billing?plan=starter",
) -> str:
    if not repositories:
        return '<article class="repo-setup-card repo-setup-card-empty"><div class="repo-setup-card-label">Repository inventory</div><h3>No repositories available yet</h3><p>Reconnect GitHub if repository enumeration has not been granted for this workspace identity.</p></article>'

    rendered: list[str] = []
    for repository in sorted(repositories, key=lambda item: str(item.get("repo_full") or "").lower()):
        repo_full = str(repository.get("repo_full") or "")
        if not repo_full:
            continue
        is_connected = bool(repository.get("is_connected"))
        is_allocated = bool(repository.get("is_allocated"))
        is_onboarded = bool(repository.get("is_onboarded"))
        can_restore = bool(repository.get("can_restore"))
        onboarding_status = str(repository.get("onboarding_status") or "").lower()
        if is_onboarded:
            if onboarding_status == "baseline_approved":
                action = f'''
                <div class="repo-setup-inventory-action-group">
                    <a class="repo-setup-button repo-setup-button-link" href="{html_escape(_repo_dashboard_href(repo_full))}">Open audit</a>
                    <form method="post" action="{html_escape(_repo_disconnect_href(repo_full))}" class="repo-setup-inline-form">
                        {_csrf_input(csrf_token)}
                        <button type="submit" class="repo-setup-button repo-setup-button-link">Disconnect repo</button>
                    </form>
                </div>
                '''
                status = "onboarded"
            else:
                action = f'''
                <div class="repo-setup-inventory-action-group">
                    <span class="repo-setup-chip repo-setup-chip-cool">Onboarding active</span>
                    <form method="post" action="{html_escape(_repo_disconnect_href(repo_full))}" class="repo-setup-inline-form">
                        {_csrf_input(csrf_token)}
                        <button type="submit" class="repo-setup-button repo-setup-button-link">Disconnect repo</button>
                    </form>
                </div>
                '''
                status = "onboarding"
        elif is_allocated:
            action = f'''
            <div class="repo-setup-inventory-action-group">
                <span class="repo-setup-chip repo-setup-chip-cool">Allocation saved</span>
                <form method="post" action="{html_escape(_repo_disconnect_href(repo_full))}" class="repo-setup-inline-form">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="repo-setup-button repo-setup-button-link">Disconnect repo</button>
                </form>
            </div>
            '''
            status = "allocated"
        elif can_restore:
            if install_disabled:
                action = (
                    f'<a class="repo-setup-button repo-setup-button-link" href="{html_escape(install_disabled_href)}" '
                    'aria-disabled="true" data-upgrade-required="repo-limit">Upgrade to restore repo</a>'
                )
                status = "upgrade_required"
            else:
                action = f'''
                <form method="post" action="{html_escape(_repo_allocate_href(repo_full))}" class="repo-setup-inline-form">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="repo-setup-button">Restore repo</button>
                </form>
                '''
                status = "restorable"
        elif is_connected:
            if install_disabled:
                action = (
                    f'<a class="repo-setup-button repo-setup-button-link" href="{html_escape(install_disabled_href)}" '
                    'aria-disabled="true" data-upgrade-required="repo-limit">Upgrade to add repo</a>'
                )
                status = "upgrade_required"
            else:
                action = f'''
                <form method="post" action="{html_escape(_repo_allocate_href(repo_full))}" class="repo-setup-inline-form">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="repo-setup-button">Allocate and onboard</button>
                </form>
                '''
                status = "connected"
        else:
            if install_disabled:
                action = (
                    f'<a class="repo-setup-button repo-setup-button-link" href="{html_escape(install_disabled_href)}" '
                    'aria-disabled="true" data-upgrade-required="repo-limit">Upgrade to add repo</a>'
                )
                status = "upgrade_required"
            else:
                action = f'<a class="repo-setup-button repo-setup-button-link" href="{html_escape(install_start_href)}">Install app</a>'
                status = "available"
        rendered.append(
            f'''
            <article class="repo-setup-inventory-row" data-repo-inventory-card="true" data-status="{html_escape(status)}" data-repo-full="{html_escape(repo_full.lower())}">
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
    return render_repo_inventory_cards(connections, csrf_token=csrf_token, install_start_href="/setup/install/start")


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
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape((display_name or github_login or "V")[:1].upper()))
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
    sidebar_profile_initial: str = "V",
) -> str:
    template = _load_template("control_plane_settings.html")
    effective_status = pr_comments_allowed_by_plan and pr_comments_setting_enabled
    status_copy = status_note or "Manage workspace-wide comment behavior for pull requests."
    if not pr_comments_allowed_by_plan:
        status_copy = "Your current plan does not permit PR comments, so this setting will not take effect until comments are included in the workspace entitlement."
    manage_note = "Owners and admins can change this setting." if can_manage else "Only workspace owners and admins can change this setting."
    billing_link = '<a class="control-page-button" href="/billing">Open billing</a>'
    return (
        template.replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
        .replace("{{CSRF_INPUT}}", _csrf_input(csrf_token))
        .replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{STATUS_NOTE}}", html_escape(status_copy))
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
        .replace("{{BILLING_LINK}}", billing_link)
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
    sidebar_profile_initial: str = "V",
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
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
        .replace("{{ADMIN_CONTROL}}", admin_control)
        .replace("{{COMPLIANCE_ACTIVE}}", " sidebar-nav-item-active" if active_nav == "compliance" else "")
        .replace("{{POLICIES_ACTIVE}}", " sidebar-nav-item-active" if active_nav == "policies" else "")
        .replace("{{HELP_ACTIVE}}", " sidebar-nav-item-active" if active_nav == "help" else "")
    )


def _render_policies_summary_cards(cards: list[dict[str, str]]) -> str:
    return "".join(
        f'''
        <article class="secondary-panel secondary-panel-flat">
            <div class="secondary-panel-title">{html_escape(card["label"])}</div>
            <p><strong>{html_escape(card["value"])}</strong></p>
            <p class="muted">{html_escape(card["detail"])}</p>
        </article>
        '''
        for card in cards
    )


def _render_policies_classification_form(
    system: dict[str, object],
    *,
    csrf_token: str,
    can_manage: bool,
    compact: bool = False,
) -> str:
    risk_options = [
        ("unclassified", "Unclassified"),
        ("minimal-risk", "Minimal risk"),
        ("limited-risk", "Limited risk"),
        ("high-risk", "High risk"),
        ("prohibited", "Prohibited"),
    ]
    domain_options = [
        ("", "Not set"),
        ("general_purpose", "General purpose AI"),
        ("employment", "Employment and worker management"),
        ("education", "Education and vocational training"),
        ("essential_services", "Essential private or public services"),
        ("biometric", "Biometrics or identity verification"),
        ("law_enforcement", "Law enforcement or public authority support"),
        ("internal_productivity", "Internal productivity support"),
    ]

    def _option_markup(options: list[tuple[str, str]], selected: str | None) -> str:
        return "".join(
            f'<option value="{html_escape(value)}"{' selected' if value == (selected or "") else ''}>{html_escape(label)}</option>'
            for value, label in options
        )

    risk_level = str(system.get("risk_level") or "unclassified")
    domain_value = str(system.get("eu_ai_act_domain") or "")
    purpose_summary = str(system.get("purpose_summary") or "")
    last_reviewed_at = str(system.get("last_reviewed_at") or "Not reviewed")
    provenance_note = (
        "Auto-prefilled from deterministic repository evidence. Review and confirm before relying on it for compliance decisions."
        if last_reviewed_at == "Not reviewed"
        else "Reviewer-confirmed classification stored in the workspace registry."
    )
    form_class = "control-page-inline-form policies-review-form" if compact else "control-page-inline-form"

    if not can_manage:
        return (
            '<p class="muted">Owners and admins can update classification and policy context.</p>'
            f'<p class="muted">{html_escape(provenance_note)}</p>'
        )

    return f'''
        <form method="post" action="/policies/systems/{int(system["id"])}" class="{form_class}">
            {_csrf_input(csrf_token)}
            <label class="policies-form-field">
                <span class="secondary-panel-title">Risk classification</span>
                <select class="control-page-select policies-form-select policies-form-select-risk" name="risk_level">{_option_markup(risk_options, risk_level)}</select>
            </label>
            <label class="policies-form-field">
                <span class="secondary-panel-title">Domain</span>
                <select class="control-page-select policies-form-select" name="eu_ai_act_domain">{_option_markup(domain_options, domain_value)}</select>
            </label>
            <label class="policies-form-field">
                <span class="secondary-panel-title">System purpose</span>
                <input class="control-page-input policies-form-input" type="text" name="purpose_summary" value="{html_escape(purpose_summary)}" maxlength="280" />
            </label>
            <p class="muted">{html_escape(provenance_note)}</p>
            <button type="submit" class="button">Save classification</button>
        </form>
    '''


def _render_policies_review_queue(system_rows: list[dict[str, object]], *, csrf_token: str, can_manage: bool) -> str:
    review_rows = [row for row in system_rows if str(row.get("last_reviewed_at") or "Not reviewed") == "Not reviewed"]
    if not review_rows:
        return (
            '<article class="secondary-panel secondary-panel-flat">'
            '<div class="secondary-panel-title">Needs review now</div>'
            '<p class="muted">Every registered AI system in this workspace already has a reviewer-confirmed classification.</p>'
            '</article>'
        )

    remaining_count = len(review_rows)
    review_copy = (
        f'{remaining_count} system still relies on auto-prefilled registry context and should be confirmed before it is used in compliance decisions.'
        if remaining_count == 1
        else f'{remaining_count} systems still rely on auto-prefilled registry context and should be confirmed before they are used in compliance decisions.'
    )
    cards = []
    for system in review_rows:
        display_name = str(system.get("display_name") or system.get("repo_full") or "Unknown")
        repo_full = str(system.get("repo_full") or "Unknown")
        evidence_summary = str(system.get("evidence_summary") or "GitHub repository evidence")
        onboarding_status = str(system.get("onboarding_status") or "Unknown")
        risk_label = str(system.get("risk_level_label") or "Unclassified")
        if str(system.get("risk_level") or "unclassified") == "high-risk":
            review_priority = "High-risk prefill should be confirmed before evidence exports or formal review follow-up."
        elif onboarding_status == "Baseline Approved":
            review_priority = "Baseline evidence is approved, so this system is ready for reviewer confirmation now."
        else:
            review_priority = "Confirm this registry entry before relying on it in downstream compliance decisions."
        dashboard_href = f'/dashboard/{quote(repo_full, safe="")}'
        cards.append(
            f'''
            <article class="secondary-panel policies-review-card">
                <div class="stack compact-stack">
                    <div class="policies-review-head">
                        <div class="stack compact-stack">
                            <div class="secondary-panel-title">Needs confirmation</div>
                            <h3 class="control-page-section-title policies-review-title">{html_escape(display_name)}</h3>
                        </div>
                        <span class="compliance-status-pill tone-warning">Auto-prefilled</span>
                    </div>
                    <p class="muted policies-review-repo">{html_escape(repo_full)}</p>
                    <div class="policies-review-meta">
                        <span>{html_escape(evidence_summary)}</span>
                        <span>Onboarding: {html_escape(onboarding_status)}</span>
                        <span>Risk: {html_escape(risk_label)}</span>
                    </div>
                    <p class="muted policies-review-priority">{html_escape(review_priority)}</p>
                    <div class="policies-review-actions">
                        <a class="subtle-link" href="{html_escape(dashboard_href)}">Open repo dashboard</a>
                        <a class="subtle-link" href="/compliance">Open compliance workspace view</a>
                    </div>
                </div>
                {_render_policies_classification_form(system, csrf_token=csrf_token, can_manage=can_manage, compact=True)}
            </article>
            '''
        )
    return (
        '<article class="control-page-section control-page-section-wide">'
        '<div class="secondary-panel-title">Needs review now</div>'
        '<h2 class="control-page-section-title">Clear confirmation debt before export or audit follow-up</h2>'
        f'<p class="control-page-copy">{html_escape(review_copy)}</p>'
        f'<div class="policies-review-grid">{"".join(cards)}</div>'
        '</article>'
    )


def _render_policies_system_rows(system_rows: list[dict[str, object]], *, csrf_token: str, can_manage: bool) -> str:
    if not system_rows:
        return (
            '<div class="empty-state">'
            '<strong>No registered AI systems yet.</strong>'
            '<p>Attach and onboard a repository from Repositories to create the first registry entry for this workspace.</p>'
            '</div>'
        )

    rows: list[str] = []
    for system in system_rows:
        repo_full = str(system.get("repo_full") or "Unknown")
        display_name = str(system.get("display_name") or repo_full)
        evidence_summary = str(system.get("evidence_summary") or "GitHub repository evidence")
        onboarding_status = str(system.get("onboarding_status") or "Unknown")
        risk_level = str(system.get("risk_level") or "unclassified")
        risk_label = str(system.get("risk_level_label") or risk_level)
        domain_label = str(system.get("eu_ai_act_domain_label") or "Not set")
        last_reviewed_at = str(system.get("last_reviewed_at") or "Not reviewed")
        form_markup = _render_policies_classification_form(system, csrf_token=csrf_token, can_manage=can_manage)
        rows.append(
            f'''
            <tr>
                <td>
                    <strong>{html_escape(display_name)}</strong>
                    <div class="muted">{html_escape(repo_full)}</div>
                </td>
                <td>
                    <div>{html_escape(evidence_summary)}</div>
                    <div class="muted">Last review: {html_escape(last_reviewed_at)}</div>
                </td>
                <td>
                    <div>{html_escape(onboarding_status)}</div>
                    <div class="muted">Risk: {html_escape(risk_label)} · Domain: {html_escape(domain_label)}</div>
                </td>
                <td>{form_markup}</td>
            </tr>
            '''
        )
    return "".join(rows)


def render_control_plane_policies_page(
    *,
    workspace_name: str,
    audit_href: str,
    plan_label: str,
    theme_preference: str,
    admin_url: str | None,
    summary_cards: list[dict[str, str]],
    system_rows: list[dict[str, object]],
    status_note: str | None,
    can_manage: bool,
    csrf_token: str,
    sidebar_profile_initial: str = "V",
) -> str:
    template = _load_template("control_plane_policies.html")
    status_markup = f'<div class="secondary-panel"><p>{html_escape(status_note)}</p></div>' if status_note else ""
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{AUDIT_HREF}}", html_escape(audit_href))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
        .replace("{{STATUS_NOTE}}", status_markup)
        .replace("{{SUMMARY_CARDS}}", _render_policies_summary_cards(summary_cards))
        .replace("{{REVIEW_QUEUE}}", _render_policies_review_queue(system_rows, csrf_token=csrf_token, can_manage=can_manage))
        .replace("{{SYSTEM_ROWS}}", _render_policies_system_rows(system_rows, csrf_token=csrf_token, can_manage=can_manage))
    )


def render_control_plane_help_page(
    *,
    workspace_name: str,
    plan_label: str,
    theme_preference: str,
    admin_url: str | None,
    resolution: WorkspaceAccessResolution,
    repo_rows: list[dict[str, object]],
    repo_summaries: list[object],
    export_ready_count: int,
    export_pending_count: int,
    sidebar_profile_initial: str = "V",
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
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
        .replace("{{ADMIN_CONTROL}}", admin_control)
        .replace("{{HELP_CONTEXT_SUMMARY}}", html_escape(help_context["summary"]))
        .replace("{{HELP_START_HERE_COPY}}", html_escape(help_context["start_here_copy"]))
        .replace("{{HELP_NEXT_STEP_LABEL}}", html_escape(help_context["next_step_label"]))
        .replace("{{HELP_STATUS_CARDS}}", help_context["status_cards_html"])
        .replace("{{HELP_NEXT_STEP_PANEL}}", help_context["next_step_panel_html"])
        .replace("{{CHECKLIST_ITEMS}}", _render_checklist(resolution))
        .replace("{{CHECKLIST_ITEMS}}", "")
    )


def render_control_plane_mcp_page(
    *,
    workspace_name: str,
    audit_href: str,
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
    sidebar_profile_initial: str = "V",
) -> str:
    template = _load_template("control_plane_mcp.html")
    admin_control = ""
    if admin_url:
        admin_control = f'''<a class="control-page-admin-link" href="{html_escape(admin_url)}">Open system admin</a>'''

    tab_urls = {
        "overview": "/integrations/mcp?tab=overview",
        "tools": "/integrations/mcp?tab=tools",
    }
    tab_labels = {"overview": "Overview", "tools": "Tools"}
    if can_manage:
        tab_urls.update(
            {
                "api-keys": "/integrations/mcp?tab=api-keys",
                "activity": "/integrations/mcp?tab=activity",
            }
        )
        tab_labels.update({"api-keys": "API keys", "activity": "Activity"})
    tab_bar = "".join(
        f'''<a class="control-page-tab-link" href="{html_escape(tab_urls[tab_key])}"{' aria-current="page"' if tab_key == active_tab else ''}>{html_escape(tab_labels[tab_key])}</a>'''
        for tab_key in tab_urls
    )
    tab_intro_content = {
        "overview": (
            "Customer MCP connector",
            "Download the broker-backed connector, verify the trust boundary, and prepare the workspace rollout path.",
            "Keep package handoff, host setup, and rollout guardrails together before issuing or rotating keys.",
        ),
        "tools": (
            "Hosted broker tools",
            "Review the current read-first tool surface exposed to customer agents through the hosted broker.",
            "Use this tab to confirm the allowed surface before distributing credentials or connector config.",
        ),
        "api-keys": (
            "Workspace API keys",
            "Manage machine principals, scope selection, and one-time secret handoff for this workspace.",
            "Prefer the narrowest scope set that still supports the automation path you are enabling.",
        ),
        "activity": (
            "Integration activity",
            "Inspect recent API-key and broker events for rollout verification, troubleshooting, and audit follow-up.",
            "This feed is meant to answer who changed access, when they changed it, and what the broker was asked to do.",
        ),
    }
    tab_intro_title, tab_intro_description, tab_intro_note = tab_intro_content.get(
        active_tab,
        tab_intro_content["overview"],
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

    active_principal_count = sum(1 for principal in principals if getattr(principal, "status", "") == "active") if can_manage else 0
    active_key_bullet = ""
    if can_manage:
        active_key_bullet = (
            f'<a class="control-page-header-meta-link" href="{html_escape(tab_urls["api-keys"])}" '
            f'aria-label="Open API keys for {html_escape(str(active_principal_count))} active workspace API keys">'
            f'{html_escape(str(active_principal_count))} active workspace API {"key is" if active_principal_count == 1 else "keys are"} available.</a>'
        )
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
    tools_section = f"""
        <article class="control-page-section control-page-section-wide">
            <div class="secondary-panel-title">Available tools</div>
            <h2 class="control-page-section-title">Read-first MCP surface</h2>
            <p class="control-page-copy">This is the full tool surface exposed by the hosted broker for customer agents.</p>
            <div class="help-page-card-grid">{tool_cards}</div>
        </article>"""

    activity_rows = ""
    for entry in audit_logs:
        try:
            payload = json.loads(getattr(entry, "payload_json", "") or "{}")
        except (ValueError, TypeError):
            payload = {}
        event_type = str(getattr(entry, "event_type", "unknown") or "unknown")
        subject_type = str(getattr(entry, "subject_type", "workspace") or "workspace")
        subject_id = str(getattr(entry, "subject_id", "n/a") or "n/a")
        created_at = _format_timestamp(getattr(entry, "created_at", None))
        details: list[str] = []
        if isinstance(payload, dict):
            if payload.get("source"):
                details.append(f"source={payload['source']}")
            scopes = payload.get("scopes")
            if isinstance(scopes, list) and scopes:
                details.append("scopes=" + ", ".join(str(scope) for scope in scopes))
            if payload.get("tool_name"):
                details.append(f"tool={payload['tool_name']}")
        subject_label = f"{subject_type}:{subject_id}"
        detail_text = " | ".join(details) if details else "Workspace automation activity"
        activity_rows += f"""
        <tr data-filter-row="activity"
            data-filter-date="{html_escape(created_at.lower())}"
            data-filter-event="{html_escape(event_type.lower())}"
            data-filter-client="{html_escape(subject_id.lower())}"
            data-filter-details="{html_escape((subject_label + ' ' + detail_text).lower())}">
            <td>{html_escape(created_at)}</td>
            <td><code>{html_escape(event_type)}</code></td>
            <td><code class="control-page-monospace control-page-monospace-break">{html_escape(subject_label)}</code></td>
            <td>{html_escape(detail_text)}</td>
        </tr>"""

    if activity_rows:
        activity_section = f"""
        <article class="control-page-section control-page-section-wide control-page-section-table-wide">
            <div class="secondary-panel-title">Workspace activity</div>
            <h2 class="control-page-section-title">Recent integration and API-key events</h2>
            <p class="control-page-copy">This feed keeps connector setup, key rotation, and broker actions together on one page.</p>
            <div class="control-page-filter-bar" data-filter-scope="activity">
                <label class="control-page-filter-field">
                    <span class="control-page-filter-label">Date</span>
                    <input class="control-page-input" type="search" placeholder="YYYY-MM-DD" data-filter-target="date" />
                </label>
                <label class="control-page-filter-field">
                    <span class="control-page-filter-label">Event</span>
                    <input class="control-page-input" type="search" placeholder="principal.created" data-filter-target="event" />
                </label>
                <label class="control-page-filter-field">
                    <span class="control-page-filter-label">Client ID</span>
                    <input class="control-page-input" type="search" placeholder="client id" data-filter-target="client" />
                </label>
                <label class="control-page-filter-field control-page-filter-field-wide">
                    <span class="control-page-filter-label">Details</span>
                    <input class="control-page-input" type="search" placeholder="subject, scope, source, or tool" data-filter-target="details" />
                </label>
                <button type="button" class="control-page-filter-reset" data-filter-reset="activity">Clear filters</button>
            </div>
            <div class="control-page-filter-summary" data-filter-status="activity" aria-live="polite">Showing all events.</div>
            <div class="control-page-table-wrap control-page-table-wrap-wide">
                <table class="control-page-table control-page-table-wide">
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
    elif active_tab == "tools":
        active_panel = tools_section
    else:
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
            <p class="control-page-copy">This downloadable package is meant for authenticated customers only. It runs as a thin local MCP server in the customer environment, exchanges workspace-scoped machine-principal credentials for a short-lived broker token, and forwards allowed tool calls to the hosted Vipari broker.</p>
            <div class="help-page-workflow-grid">
                <a class="help-page-action-card" href="{html_escape(download_url)}"><span class="help-page-action-step">1</span><strong>Download connector</strong><p>Includes the local MCP server script, dependency list, environment template, and example host configuration.</p></a>
                {workflow_card_two}
            </div>
        </article>

        <article class="control-page-section">
            <div class="secondary-panel-title">Quickstart</div>
            <h2 class="control-page-section-title">Host configuration</h2>
            <pre class="help-page-flow">{html_escape(config_snippet)}</pre>
            <p class="control-page-copy">The connector never receives internal Vipari bearer tokens. It uses the machine-principal credentials you create for this workspace only to obtain a short-lived broker token.</p>
        </article>

        <article class="control-page-section">
            <div class="secondary-panel-title">Safety model</div>
            <h2 class="control-page-section-title">Trust boundary</h2>
            <pre class="help-page-flow">Your AI agent
  -&gt; customer MCP connector
    -&gt; Vipari broker
    -&gt; curated Vipari control-plane reads</pre>
                        <p class="control-page-copy">One connector session maps to one workspace. The connector package is thin on purpose so Vipari can keep product semantics, output shaping, and credential handling server-side.</p>
        </article>
"""

    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{AUDIT_HREF}}", html_escape(audit_href))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{MCP_TAB_TITLE}}", html_escape(tab_intro_title))
        .replace("{{MCP_TAB_DESCRIPTION}}", html_escape(tab_intro_description))
        .replace("{{MCP_TAB_NOTE}}", html_escape(tab_intro_note))
        .replace("{{ADMIN_CONTROL}}", admin_control)
        .replace("{{BROKER_HOST}}", html_escape(broker_host))
        .replace("{{TOOL_COUNT}}", html_escape(str(len(MCP_BROKER_TOOLS))))
        .replace("{{ACTIVE_API_KEY_LABEL}}", html_escape("Active API keys" if can_manage else "API-key access"))
        .replace("{{ACTIVE_API_KEY_COUNT}}", html_escape(str(active_principal_count if can_manage else "Restricted")))
        .replace("{{MCP_ACTIVE_KEY_BULLET}}", active_key_bullet)
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
        next_href = "/repos"
        next_cta = "Open Repositories"
    elif connected_only_rows:
        target_repo = str(connected_only_rows[0].get("repo_full") or "your repo")
        start_here_copy = f"Start in Repositories. {target_repo} is visible to the workspace, but at least one visible repo has not been onboarded into review workflows yet."
        next_step_label = "Finish onboarding"
        next_title = "A visible repo still needs onboarding"
        next_body = f"{target_repo} is connected, but it does not yet have stored onboarding state. Until onboarding runs, Help, Dashboard, and Compliance can only explain so much because there is no review-ready baseline context."
        next_href = "/repos"
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
        next_href = "/compliance"
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
        ("readiness", "Readiness", "/compliance"),
        ("frameworks", "Frameworks", "/compliance/frameworks"),
        ("exports", "Exports", "/compliance/exports"),
        ("evidence", "Evidence", "/compliance/evidence"),
    )
    return "".join(
        f'''<a class="control-page-tab-link" href="{html_escape(href)}"{' aria-current="page"' if key == active_tab else ''}>{html_escape(label)}</a>'''
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
            <td>
                <div class="stack compact-stack">
                    <span class="compliance-status-pill {_compliance_tone_class(row.ai_act_status_tone)}">{html_escape(row.ai_act_status_label)}</span>
                    <span class="control-page-microcopy {_compliance_tone_class(row.ai_act_provenance_tone)}">{html_escape(row.ai_act_provenance_label)}</span>
                    <span class="control-page-microcopy">{html_escape(row.ai_act_review_detail)}</span>
                </div>
            </td>
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
        '<th>Repository</th><th>Status</th><th>Baseline</th><th>Governance</th><th>AI Act</th><th>Freshness</th><th>Next action</th>'
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
                <a class="control-page-button" href="/compliance/exports#new-export">Generate export</a>
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
        f'<a class="subtle-link" href="/compliance/evidence">Show all evidence</a>'
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
                        <span class="control-page-microcopy">AI system: {html_escape(row.ai_act_provenance_label)} · {html_escape(row.ai_act_review_detail)}</span>
                        <span>{html_escape(row.action_detail)}</span>
                    </div>
                    <a class="subtle-link" href="{html_escape(row.repo_href)}">Open audit page</a>
                </div>
            </label>
            '''
        )
    return "".join(rendered)


def _render_compliance_export_history(
    jobs: tuple[ExportJob, ...] | list[ExportJob],
    repo_rows: tuple[ComplianceRepoReadinessRow, ...],
) -> str:
    if not jobs:
        return '<div class="control-page-empty">No compliance exports have been generated for this workspace yet.</div>'
    repo_row_by_full = {row.repo_full: row for row in repo_rows}
    rows: list[str] = []
    for job in jobs:
        range_label = (
            f"{datetime.fromtimestamp(job.from_ts).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(job.to_ts).strftime('%Y-%m-%d')}"
        )
        repo_markup = html_escape(job.repo_full)
        repo_row = repo_row_by_full.get(job.repo_full)
        provenance_label = job.ai_system_provenance_label
        review_detail = job.ai_system_review_detail
        if (not provenance_label or not review_detail) and repo_row is not None:
            provenance_label = repo_row.ai_act_provenance_label
            review_detail = repo_row.ai_act_review_detail
        error_detail = ""
        if job.status == "failed" and job.last_error:
            error_detail = f'<span class="control-page-microcopy">Failure: {html_escape(job.last_error)}</span>'
        if repo_row is not None:
            repo_markup = (
                f'<div class="stack compact-stack">'
                f'<span>{html_escape(job.repo_full)}</span>'
                f'<span class="control-page-microcopy">AI system: {html_escape(provenance_label or "No registry entry")} · {html_escape(review_detail or "Last review: Not yet reviewed")}</span>'
                f'{error_detail}'
                f'</div>'
            )
        elif error_detail:
            repo_markup = (
                f'<div class="stack compact-stack">'
                f'<span>{html_escape(job.repo_full)}</span>'
                f'{error_detail}'
                f'</div>'
            )
        if job.status == "completed" and job.download_token and job.result_blob:
            download_markup = f'<a class="link" href="/api/export/{job.id}/download?token={quote(job.download_token)}">Download</a>'
        else:
            download_markup = html_escape(job.status.replace("_", " ").title())
        rows.append(
            f'''
            <tr>
                <td>{repo_markup}</td>
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
            <form method="post" action="/compliance/export" class="control-page-form compliance-export-form">
                {_csrf_input(csrf_token)}
                <label class="compliance-export-field">
                    <span class="control-page-label">Server-side repo preset</span>
                    <select class="control-page-select" name="export_preset">
                        <option value="none">No preset</option>
                        <option value="review_ready">Review-ready repos</option>
                        <option value="fresh_review_ready">Fresh review-ready repos</option>
                    </select>
                </label>
                <fieldset class="control-page-choice-group">
                    <legend class="control-page-label">Scope</legend>
                    <div class="control-page-choice-grid compliance-export-choice-grid">
                        <label class="control-page-choice">
                            <input type="radio" name="export_scope" value="all_visible" checked />
                            <span class="control-page-choice-card compliance-export-choice-card">
                                <span class="control-page-choice-title">All visible repos</span>
                                <span class="control-page-choice-copy">Run the export across every repository currently visible in this workspace.</span>
                            </span>
                        </label>
                        <label class="control-page-choice">
                            <input type="radio" name="export_scope" value="selected" />
                            <span class="control-page-choice-card compliance-export-choice-card">
                                <span class="control-page-choice-title">Selected repos only</span>
                                <span class="control-page-choice-copy">Override the preset and include only the repositories checked below.</span>
                            </span>
                        </label>
                    </div>
                </fieldset>
                <fieldset class="control-page-choice-group">
                    <legend class="control-page-label">Export mode</legend>
                    <div class="control-page-choice-grid compliance-export-choice-grid">
                        <label class="control-page-choice">
                            <input type="radio" name="export_mode" value="compliance" checked />
                            <span class="control-page-choice-card compliance-export-choice-card">
                                <span class="control-page-choice-title">Compliance evidence bundle</span>
                                <span class="control-page-choice-copy">Generate the governance and readiness evidence package without extra drift review context.</span>
                            </span>
                        </label>
                        <label class="control-page-choice">
                            <input type="radio" name="export_mode" value="compliance_plus_drift" />
                            <span class="control-page-choice-card compliance-export-choice-card">
                                <span class="control-page-choice-title">Compliance plus drift context</span>
                                <span class="control-page-choice-copy">Attach the readiness evidence package plus the current drift narrative for follow-up review.</span>
                            </span>
                        </label>
                    </div>
                </fieldset>
                <div class="compliance-export-date-grid">
                    <label class="compliance-export-field">
                        <span class="control-page-label">From</span>
                        <input class="control-page-input" type="date" name="from_date" required />
                    </label>
                    <label class="compliance-export-field">
                        <span class="control-page-label">To</span>
                        <input class="control-page-input" type="date" name="to_date" required />
                    </label>
                </div>
                <label class="control-page-checkbox-option compliance-export-checkbox">
                    <input type="checkbox" name="include_artifact_content" value="true" checked />
                    <span class="control-page-checkbox-copy">
                        <strong>Include artifact content</strong>
                        <span>Attach stored artifact content whenever it is available so the export bundle is ready for governance review.</span>
                    </span>
                </label>
                <div class="control-page-form-divider"></div>
                <div class="stack compact-stack">
                    <div>
                        <span class="control-page-label">Repository selection</span>
                        <p class="control-page-copy">Use the checklist below only when you want to override the preset and target a smaller export set.</p>
                    </div>
                    <div class="compliance-repo-list">{_render_compliance_export_scope_rows(view.repo_rows)}</div>
                </div>
                <div class="compliance-export-submit-row">
                    <button class="control-page-button" type="submit">Generate export</button>
                </div>
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
                {_render_compliance_export_history(export_jobs, view.repo_rows)}
            </section>
        '''
    if active_tab == "evidence":
        active_filter, active_repo, evidence_rows, repo_rows = filter_compliance_evidence_view(view, evidence_filter, evidence_repo)
        return f'''
            <section class="control-page-section stack compact-stack">
                {_render_compliance_evidence_filter_note(active_filter, active_repo, len(evidence_rows), len(view.evidence_rows))}
                {_render_compliance_evidence_rows(repo_rows)}
            </section>
        '''
    return f'''
        <div class="control-page-stat-grid compliance-stat-grid">{_render_compliance_metrics(view)}</div>
        <section class="control-page-section stack compact-stack">
            <div>
                <p class="secondary-panel-title">Priority gaps</p>
                <h2 class="control-page-section-title">What needs attention next</h2>
            </div>
            <div class="compliance-gap-grid">{_render_compliance_gaps(view.top_gaps)}</div>
        </section>
        <div class="compliance-overview-grid">
            <section class="control-page-section stack compact-stack compliance-repo-view-shell">
                <div>
                    <p class="secondary-panel-title">Repository view</p>
                    <h2 class="control-page-section-title">Readiness by repository</h2>
                </div>
                {_render_compliance_repo_table(view.repo_rows)}
            </section>
            <div class="compliance-side-rail">
                {_render_compliance_export_summary(view.export_summary)}
                {_render_compliance_verdict(view)}
            </div>
        </div>
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
    shell_state: str = "active",
    shell_title: str = "",
    shell_body: str = "",
    shell_cta_href: str | None = None,
    shell_cta_label: str | None = None,
    sidebar_profile_initial: str = "V",
) -> str:
    template = _load_template("control_plane_compliance.html")
    export_job_items = export_jobs or tuple()
    show_status_note = bool(status_note)
    status_markup = f'<div class="control-page-inline-note control-page-inline-note-compact compliance-inline-note">{html_escape(status_note)}</div>' if show_status_note else ""
    blocked_class = " dashboard-shell-blocked" if shell_state != "active" else ""
    shell_notice = ""
    if shell_state != "active":
        shell_notice = _control_page_shell_notice_html(
            shell_title=shell_title,
            shell_body=shell_body,
            shell_cta_href=shell_cta_href,
            shell_cta_label=shell_cta_label,
        )
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{AUDIT_HREF}}", html_escape(audit_href))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
        .replace("{{STATUS_NOTE}}", status_markup)
        .replace("{{PAGE_TITLE}}", html_escape(page_title))
        .replace("{{PAGE_DESCRIPTION}}", html_escape(page_description))
        .replace("{{PAGE_NOTE}}", html_escape(page_note))
        .replace("{{DASHBOARD_BLOCKED_CLASS}}", blocked_class)
        .replace("{{DASHBOARD_SHELL_NOTICE}}", shell_notice)
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
    active_tab: str = "overview",
    logs_view: dict[str, object] | None = None,
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
                <form method="post" action="/admin/users/create" class="action-form admin-form-grid">
                    {_csrf_input(csrf_token)}
                    <input class="field-input" name="display_name" maxlength="120" placeholder="Display name" />
                    <input class="field-input" name="primary_email" maxlength="320" placeholder="Primary email" />
                    <button type="submit" class="button">Create user</button>
                </form>
            </details>
            <details class="admin-disclosure">
                <summary>Add workspace</summary>
                <form method="post" action="/admin/workspaces/create" class="action-form admin-form-grid">
                    {_csrf_input(csrf_token)}
                    <input class="field-input" name="display_name" maxlength="120" placeholder="Workspace name" />
                    <input class="field-input" name="slug" maxlength="120" placeholder="workspace-slug" />
                    <select class="field-input" name="billing_owner_user_id">{user_options}</select>
                    <button type="submit" class="button">Create workspace</button>
                </form>
            </details>
            <details class="admin-disclosure">
                <summary>Assign membership</summary>
                <form method="post" action="/admin/memberships/upsert" class="action-form admin-form-grid">
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

    def _tier_label(plan_code: object) -> str:
        normalized = str(plan_code or "").strip().lower()
        plan = PLAN_DEFINITIONS.get(normalized)
        return plan.label if plan is not None else "No plan"

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
                <form method="post" action="/admin/users/{user_id}/update" class="action-form admin-form-grid">
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
            selected_plan_code = str(row.get("plan_code") or "starter").strip().lower() or "starter"
            workspace_plan_options = "".join(
                f'<option value="{html_escape(plan.code)}" {"selected" if plan.code == selected_plan_code else ""}>{html_escape(plan.label)}</option>'
                for plan in PLAN_DEFINITIONS.values()
            )
            workspace_edit = f'''
                <details class="admin-row-disclosure">
                    <summary>Edit workspace</summary>
                    <form method="post" action="/admin/workspaces/{workspace_id}/update" class="action-form admin-form-grid">
                        {_csrf_input(csrf_token)}
                        <input class="field-input" name="display_name" maxlength="120" value="{html_escape(workspace_name)}" />
                        <input class="field-input" name="slug" maxlength="120" value="{html_escape(workspace_slug)}" />
                        <select class="field-input" name="plan_code">{workspace_plan_options}</select>
                        <button type="submit" class="button">Save workspace</button>
                    </form>
                </details>
            '''
            membership_delete = f'''
                <form method="post" action="/admin/memberships/{workspace_id}/{user_id}/delete" class="action-form" onsubmit="return confirm('Remove this user from the workspace?');">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="button admin-danger-button">Remove member</button>
                </form>
            '''
            workspace_delete = f'''
                <form method="post" action="/admin/workspaces/{workspace_id}/delete" class="action-form" onsubmit="return confirm('Delete this workspace and all linked records?');">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="button admin-danger-button">Delete workspace</button>
                </form>
            '''
        user_delete = f'''
            <form method="post" action="/admin/users/{user_id}/delete" class="action-form" onsubmit="return confirm('Delete this user and any linked workspace memberships?');">
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
                html_escape(_tier_label(row.get("plan_code"))),
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
        ["Workspace", "User", "GitHub", "Profile", "Role", "Tier", "Counts", "Setup", "Last login", "Actions"],
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
    overview_content = f'''
        <section class="admin-section">
            <div class="section-heading">
                <p class="eyebrow">Mutations</p>
                <h2>Manage the control plane</h2>
            </div>
            {add_toolbar}
        </section>
        <section class="admin-section">
            <div class="section-heading">
                <p class="eyebrow">Registered users and workspaces</p>
                <h2>Aggregated workspace accounts</h2>
            </div>
            {user_rows}
        </section>
        <section class="admin-section">
            <div class="section-heading">
                <p class="eyebrow">Audit</p>
                <h2>Recent admin activity</h2>
            </div>
            {audit_rows}
        </section>
        <section class="admin-section">
            <div class="section-heading">
                <p class="eyebrow">GitHub app installs</p>
                <h2>Unclaimed public installations</h2>
            </div>
            {install_rows}
        </section>
        <section class="admin-section">
            <div class="section-heading">
                <p class="eyebrow">Billing handoff</p>
                <h2>Stored billing claims</h2>
            </div>
            {claim_rows}
        </section>
    '''

    logs_state = logs_view or {}
    logs_filters = logs_state.get("filters") or {}
    logs_rows = _render_table(
        ["When", "Source", "Event", "Workspace", "Actor", "Subject", "Details"],
        [
            [
                html_escape(_format_timestamp(row.get("occurred_at") if isinstance(row.get("occurred_at"), (int, float)) else None)),
                html_escape(str(row.get("source") or "")),
                html_escape(str(row.get("event_type") or "")),
                html_escape(str(row.get("workspace_label") or "Global")),
                html_escape(str(row.get("actor_label") or "System")),
                html_escape(str(row.get("subject") or "")),
                html_escape(str(row.get("details") or "")),
            ]
            for row in logs_state.get("rows") or []
        ],
    )
    event_options_markup = "".join(
        f'<option value="{html_escape(option)}" {"selected" if option == logs_filters.get("event_type") else ""}>{html_escape(option)}</option>'
        for option in logs_state.get("event_options") or []
    )
    workspace_options_markup = "".join(
        f'<option value="{html_escape(str(option.get("value") or ""))}" {"selected" if str(option.get("value") or "") == str(logs_filters.get("workspace") or "") else ""}>{html_escape(str(option.get("label") or ""))}</option>'
        for option in logs_state.get("workspace_options") or []
    )
    actor_options_markup = "".join(
        f'<option value="{html_escape(option)}" {"selected" if option == logs_filters.get("actor") else ""}>{html_escape(option)}</option>'
        for option in logs_state.get("actor_options") or []
    )
    logs_content = f'''
        <section class="admin-section">
            <div class="section-heading">
                <p class="eyebrow">Operational activity</p>
                <h2>Unified logs</h2>
            </div>
            <form method="get" action="/admin" class="admin-log-filters">
                <input type="hidden" name="tab" value="logs" />
                <select class="field-input" name="event_type">
                    <option value="">All event types</option>
                    {event_options_markup}
                </select>
                <select class="field-input" name="workspace">
                    <option value="">All workspaces</option>
                    {workspace_options_markup}
                </select>
                <select class="field-input" name="actor">
                    <option value="">All actors</option>
                    {actor_options_markup}
                </select>
                <input class="field-input" type="date" name="from_date" value="{html_escape(str(logs_filters.get("from_date") or ""))}" />
                <input class="field-input" type="date" name="to_date" value="{html_escape(str(logs_filters.get("to_date") or ""))}" />
                <input class="field-input admin-log-search" type="search" name="query" value="{html_escape(str(logs_filters.get("query") or ""))}" placeholder="Search event, subject, or details" />
                <button type="submit" class="button">Apply filters</button>
                <a class="subtle-link" href="/admin?tab=logs">Reset</a>
            </form>
            <div class="admin-log-summary">
                <span>{int(logs_state.get("result_count") or 0)} matching log rows</span>
                <span>Page {int(logs_state.get("page") or 1)}</span>
            </div>
            {logs_rows}
            <div class="admin-log-pagination">
                {f'<a class="subtle-link" href="{html_escape(str(logs_state.get("prev_href") or ""))}">Previous</a>' if logs_state.get("has_prev") and logs_state.get("prev_href") else '<span class="page-note">Previous</span>'}
                {f'<a class="subtle-link" href="{html_escape(str(logs_state.get("next_href") or ""))}">Next</a>' if logs_state.get("has_next") and logs_state.get("next_href") else '<span class="page-note">Next</span>'}
            </div>
        </section>
    '''
    tabs_markup = f'''
        <nav class="admin-tabs" aria-label="Admin navigation">
            <a href="/admin?tab=overview" class="admin-tab-link {"admin-tab-link-active" if active_tab == "overview" else ""}">Overview</a>
            <a href="/admin?tab=logs" class="admin-tab-link {"admin-tab-link-active" if active_tab == "logs" else ""}">Logs</a>
        </nav>
    '''

    return (
        template.replace("{{ACTOR_GITHUB_LOGIN}}", html_escape(actor_github_login))
        .replace("{{QUICK_LINKS}}", _render_quick_links(profile_url="/profile", admin_url="/admin"))
        .replace("{{ADMIN_TABS}}", tabs_markup)
        .replace("{{STATUS_NOTE}}", status_markup)
        .replace("{{ADMIN_CONTENT}}", logs_content if active_tab == "logs" else overview_content)
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
            <form method="post" action="/settings/api-keys/{html_escape(p.client_id)}/revoke"
                  style="display:inline"
                  onsubmit="return confirm('Revoke this API key? This cannot be undone.')">
                <input type="hidden" name="csrf_token" value="{html_escape(csrf_token)}" />
                <button type="submit" class="control-page-danger-btn">Revoke</button>
            </form>"""
        status_class = "control-page-badge-active" if p.status == "active" else "control-page-badge-revoked"
        # Highlight the newly-created row
        row_class = ' class="control-page-table-row-new"' if (new_client_id and p.client_id == new_client_id) else ""
        client_id_display = html_escape(p.client_id)
        created_label = _fmt_date(getattr(p, "created_at", None))
        rows_html += f"""
        <tr{row_class}>
            <td><code>{html_escape(p.display_name)}</code></td>
            <td><code class="control-page-monospace control-page-monospace-break">{client_id_display}</code></td>
            <td>{scope_badges}</td>
            <td><span class="control-page-badge {status_class}">{html_escape(p.status)}</span></td>
            <td>{created_label}</td>
            <td>{revoke_form}</td>
        </tr>"""

    table_html = ""
    if principals:
        table_html = f"""
        <div class="control-page-table-wrap control-page-table-wrap-wide">
            <table class="control-page-table control-page-table-wide">
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
        <form method="post" action="/settings/api-keys" class="control-page-form">
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
    <article class="control-page-section control-page-section-wide control-page-section-table-wide">
        <div class="secondary-panel-title">Workspace API keys</div>
        <h2 class="control-page-section-title">Machine principal credentials</h2>
        <p class="control-page-copy">
            API keys let trusted automation access the Vipari control plane API without user credentials.
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
    sidebar_profile_initial: str = "V",
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
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
        .replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{PLAN_LABEL}}", html_escape(plan_label))
        .replace("{{ADMIN_CONTROL}}", admin_control)
        .replace("{{API_KEYS_SECTION}}", api_keys_section)
        # Replace ONE_TIME_SECRET_BLOCK last; its content contains the raw
        # secret and must not be interpretable as another placeholder.
        .replace("{{ONE_TIME_SECRET_BLOCK}}", secret_block)
    )
