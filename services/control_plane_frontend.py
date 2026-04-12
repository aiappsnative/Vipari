from __future__ import annotations

from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from urllib.parse import quote

from .access_state import WorkspaceAccessResolution, WorkspaceAccessSnapshot, resolve_workspace_access_state
from .entitlements import PLAN_DEFINITIONS


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


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '<div class="empty-state">No records yet.</div>'
    head_html = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
    row_html = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f'<div class="table-shell"><table class="data-table"><thead><tr>{head_html}</tr></thead><tbody>{row_html}</tbody></table></div>'


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


def render_control_plane_login_page(*, auth_start_url: str, context_note: str | None = None) -> str:
    template = _load_template("control_plane_login.html")
    return template.replace("{{AUTH_START_URL}}", html_escape(auth_start_url)).replace(
        "{{CONTEXT_NOTE}}", html_escape(context_note or "GitHub identity anchors workspace membership, install authority, and repository allocation.")
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


def render_control_plane_repo_setup_page(*, workspace_name: str, inventory_cards: str, onboarding_metrics: str, onboarding_summary_cards: str, audit_href: str) -> str:
    template = _load_template("control_plane_repo_setup.html")
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{INVENTORY_CARDS}}", inventory_cards)
        .replace("{{ONBOARDING_METRICS}}", onboarding_metrics)
        .replace("{{ONBOARDING_SUMMARY_CARDS}}", onboarding_summary_cards)
        .replace("{{AUDIT_HREF}}", html_escape(audit_href))
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


def render_repo_inventory_cards(
    connections: list[dict[str, object]],
    allocations: list[dict[str, object]],
    onboarded_summaries: list[dict[str, object]],
    *,
    csrf_token: str,
) -> str:
    connection_by_full = {str(connection["repo_full"]): connection for connection in connections}
    allocation_by_full = {str(allocation["repo_full"]): allocation for allocation in allocations}
    summary_by_full = {str(summary["repo_full"]): summary for summary in onboarded_summaries}

    repo_fulls = set(connection_by_full) | set(allocation_by_full) | set(summary_by_full)
    if not repo_fulls:
        return '<article class="repo-setup-card repo-setup-card-empty"><div class="repo-setup-card-label">Repository inventory</div><h3>Link an installation first</h3><p>No repository connections are available for allocation yet.</p></article>'

    def sort_key(repo_full: str) -> tuple[int, str]:
        connection = connection_by_full.get(repo_full)
        allocation = allocation_by_full.get(repo_full)
        summary = summary_by_full.get(repo_full)
        rank = 2
        if summary is not None:
            rank = 0
        elif allocation is not None:
            rank = 1
        elif connection is not None:
            rank = 2
        return (rank, repo_full.lower())

    rendered: list[str] = []
    for repo_full in sorted(repo_fulls, key=sort_key):
        connection = connection_by_full.get(repo_full)
        allocation = allocation_by_full.get(repo_full)
        summary = summary_by_full.get(repo_full)
        visibility = "Private" if connection and connection.get("is_private") else "Public"
        default_branch = (
            (summary.get("default_branch") if summary is not None else None)
            or (connection.get("default_branch") if connection is not None else None)
            or "unknown"
        )
        tracked_artifacts = int(summary.get("discovered_artifact_count") or 0) if summary is not None else 0
        history_count = int(summary.get("historical_version_count") or 0) if summary is not None else 0
        allocation_label = str(allocation.get("allocation_status") or "not_allocated").replace("_", " ") if allocation is not None else "not allocated"
        state_label, state_class = _repo_setup_state_label(connection, allocation, summary)
        state_key = _repo_setup_state_key(connection, allocation, summary)
        primary_action = ""
        if allocation is None and connection is not None:
            primary_action = f'''
                <form action="/app/repos/allocate?repo_full={html_escape(repo_full)}" method="post">
                    {_csrf_input(csrf_token)}
                    <button type="submit" class="repo-setup-button">Allocate and onboard</button>
                </form>
            '''
        else:
            primary_action = f'<div class="repo-setup-card-note">Workspace state: {html_escape(allocation_label)}</div>'

        rendered.append(
            f'''
            <article class="repo-setup-card repo-setup-card-compact{' repo-setup-card-strong' if summary is not None else ''}" data-repo-inventory-card="true" data-status="{html_escape(state_key)}" data-repo-full="{html_escape(repo_full.lower())}">
                <div class="repo-setup-card-top">
                    <div class="repo-setup-card-label">Repository inventory</div>
                    <div class="repo-setup-status-stack">
                        <span class="repo-setup-chip {state_class}">{html_escape(state_label)}</span>
                        <span class="repo-setup-chip">{html_escape(visibility)}</span>
                    </div>
                </div>
                <h3><a class="repo-setup-card-link" href="{html_escape(_repo_dashboard_href(repo_full))}">{html_escape(repo_full)}</a></h3>
                <div class="repo-setup-stat-row">
                    <div class="repo-setup-stat"><span class="repo-setup-meta-label">Default branch</span><span class="repo-setup-meta-value">{html_escape(str(default_branch))}</span></div>
                    <div class="repo-setup-stat"><span class="repo-setup-meta-label">Tracked artifacts</span><span class="repo-setup-meta-value">{tracked_artifacts}</span></div>
                    <div class="repo-setup-stat"><span class="repo-setup-meta-label">History</span><span class="repo-setup-meta-value">{history_count}</span></div>
                </div>
                <p>{html_escape(_repo_setup_inventory_copy(connection, allocation, summary))}</p>
                <div class="repo-setup-card-actions">
                    <a class="repo-setup-secondary-link" href="{html_escape(_repo_dashboard_href(repo_full))}">Open audit page</a>
                    {primary_action}
                </div>
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
    return render_repo_inventory_cards(connections, [], [], csrf_token=csrf_token)


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
    workspace_name: str,
    plan_label: str,
    next_payment_at: float | None,
    status_note: str | None,
    resolution: WorkspaceAccessResolution,
    admin_url: str | None,
    csrf_token: str,
) -> str:
    template = _load_template("control_plane_profile.html")
    admin_nav_item = ""
    if admin_url:
        admin_nav_item = f'<a href="{html_escape(admin_url)}" class="sidebar-nav-item" aria-label="Admin" data-tooltip="Admin">A</a>'
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
        .replace("{{NEXT_PAYMENT_AT}}", html_escape(_format_timestamp(next_payment_at)))
        .replace("{{STATUS_NOTE}}", html_escape(status_note or "Update how your name appears inside the control plane. GitHub identity details remain read-only."))
        .replace("{{ADMIN_NAV_ITEM}}", admin_nav_item)
        .replace("{{CHECKLIST_ITEMS}}", _render_checklist(resolution))
    )


def render_control_plane_admin_page(
    *,
    actor_github_login: str,
    admin_rows: list[dict[str, object]],
    unclaimed_installations: list[dict[str, object]],
    billing_claims: list[dict[str, object]],
) -> str:
    template = _load_template("control_plane_admin.html")

    def _render_installation_summary(row: dict[str, object]) -> str:
        installation_login = str(row.get("installation_account_login") or row.get("installation_id") or "none")
        installation_count = int(row.get("installation_count") or 0)
        if installation_count > 1:
            return f"{installation_login} ({installation_count} installs)"
        return installation_login

    user_rows = _render_table(
        [
            "Workspace",
            "User",
            "GitHub",
            "Role",
            "Plan",
            "Dashboard",
            "PR comments",
            "Next payment",
            "Installation",
            "Connected repos",
            "Onboarded repos",
            "Setup",
            "Last login",
        ],
        [
            [
                html_escape(str(row.get("workspace_display_name") or "Unassigned")),
                html_escape(str(row.get("user_display_name") or "Unknown")),
                html_escape(str(row.get("github_login") or "Unavailable")),
                html_escape(str(row.get("membership_role") or "none")),
                html_escape(str(row.get("plan_code") or "none")),
                "yes" if bool(row.get("dashboard_enabled")) else "no",
                "yes" if bool(row.get("pr_comments_enabled")) else "no",
                html_escape(_format_timestamp(row.get("next_payment_at") if isinstance(row.get("next_payment_at"), (int, float)) else None)),
                html_escape(_render_installation_summary(row)),
                html_escape(str(int(row.get("connected_repo_count") or 0))),
                html_escape(str(int(row.get("onboarded_repo_count") or 0))),
                html_escape(str(row.get("setup_state") or "none")),
                html_escape(_format_timestamp(row.get("last_login_at") if isinstance(row.get("last_login_at"), (int, float)) else None)),
            ]
            for row in admin_rows
        ],
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
        .replace("{{USER_ROWS}}", user_rows)
        .replace("{{UNCLAIMED_INSTALL_ROWS}}", install_rows)
        .replace("{{CLAIM_ROWS}}", claim_rows)
    )