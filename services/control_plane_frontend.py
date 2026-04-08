from __future__ import annotations

from html import escape as html_escape
from pathlib import Path

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
    return template.replace("/static/control-plane.css", _asset_url("/static/control-plane.css"))


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
        "awaiting_repo_onboarding": "/app/setup/repos",
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
        "canceled_active_until_period_end": "/app/billing",
        "expired_read_only": "/app/billing",
    }
    return mapping.get(resolution.state)


def _checklist_cta_links(resolution: WorkspaceAccessResolution) -> dict[str, str]:
    links = {
        "billing": "/app/billing",
        "workspace": "/app/workspaces/new",
        "github_login": "/login",
        "installation": "/app/setup/install",
        "repo_allocation": "/app/setup/repos",
        "first_scan": "/app/setup/repos",
    }
    if resolution.state == "active":
        links["repo_allocation"] = "/dashboard"
        links["first_scan"] = "/dashboard"
    return links


def _render_action_chip(label: str | None, href: str | None, *, fallback: str) -> str:
    text = label or fallback
    if href:
        return f'<a class="button" href="{html_escape(href)}">{html_escape(text)}</a>'
    return f'<span>{html_escape(text)}</span>'


def _render_state_links(active_state: str) -> str:
    states = [
        "unauthenticated",
        "authenticated_no_workspace",
        "workspace_no_subscription",
        "billing_pending_confirmation",
        "payment_failed",
        "awaiting_github_install",
        "awaiting_repo_onboarding",
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


def render_control_plane_workspace_new_page(*, selected_plan_label: str | None = None, source_label: str | None = None) -> str:
    template = _load_template("control_plane_workspace_new.html")
    context_lines: list[str] = []
    if selected_plan_label:
        context_lines.append(f"Selected plan: {selected_plan_label}.")
    if source_label:
        context_lines.append(f"Entry source: {source_label}.")
    context_message = " ".join(context_lines) if context_lines else "Create the first DriftGuard workspace before billing and GitHub installation continue."
    return template.replace("{{WORKSPACE_CONTEXT}}", html_escape(context_message))


def render_control_plane_billing_page(
    *,
    workspace_name: str,
    current_plan_label: str,
    subscription_status: str,
    selected_plan_code: str,
    checkout_status_note: str | None,
    flow_context: dict[str, str],
    portal_url: str | None,
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
) -> str:
    template = _load_template("control_plane_install.html")
    install_action = (
        f'<a class="button" href="{html_escape(install_url)}">Start GitHub App install</a>' if install_url else '<span class="subtle-link">GitHub App install URL unavailable</span>'
    )
    manual_link_form = f'''
        <form method="post" action="/app/setup/install/link" class="action-form">
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


def render_control_plane_repo_setup_page(*, workspace_name: str, repo_cards: str, allocation_cards: str) -> str:
    template = _load_template("control_plane_repo_setup.html")
    return (
        template.replace("{{WORKSPACE_NAME}}", html_escape(workspace_name))
        .replace("{{REPO_CARDS}}", repo_cards)
        .replace("{{ALLOCATION_CARDS}}", allocation_cards)
    )


def render_repo_connection_cards(connections: list[dict[str, str]]) -> str:
    if not connections:
        return '<article class="action-card"><div class="eyebrow">No synced repositories</div><h2>Link an installation first</h2><p>No repository connections are available for allocation yet.</p></article>'
    rendered: list[str] = []
    for connection in connections:
        rendered.append(
            f'''
            <article class="action-card">
                <div class="eyebrow">Available repository</div>
                <h2>{html_escape(connection["repo_full"])}</h2>
                <p>Default branch: {html_escape(connection.get("default_branch") or "unknown")}</p>
                <form action="/app/setup/repos/allocate?repo_full={html_escape(connection['repo_full'])}" method="post">
                    <button type="submit" class="button">Allocate and onboard</button>
                </form>
            </article>
            '''
        )
    return "".join(rendered)


def render_repo_allocation_cards(allocations: list[dict[str, str]]) -> str:
    if not allocations:
        return '<article class="action-card"><div class="eyebrow">Licensed repositories</div><h2>No allocations yet</h2><p>Allocated repositories will appear here as soon as a workspace starts onboarding.</p></article>'
    rendered: list[str] = []
    for allocation in allocations:
        rendered.append(
            f'''
            <article class="action-card action-card-strong">
                <div class="eyebrow">Allocated repository</div>
                <h2>{html_escape(allocation["repo_full"])}</h2>
                <p>Status: {html_escape(allocation["allocation_status"])}.</p>
            </article>
            '''
        )
    return "".join(rendered)


def render_control_plane_app_page(state: str | None = None, resolution: WorkspaceAccessResolution | None = None) -> str:
    resolved = resolution or _resolution_for_preview_state(state)
    template = _load_template("control_plane_app.html")
    primary_action = _render_action_chip(resolved.primary_cta, _state_primary_action_url(resolved), fallback="No action required")
    secondary_action = _render_action_chip(resolved.secondary_cta, _state_secondary_action_url(resolved), fallback="Workspace shell preview")
    return (
        template.replace("{{STATE_NAME}}", html_escape(resolved.state.replace("_", " ")))
        .replace("{{UI_TITLE}}", html_escape(resolved.ui_title))
        .replace("{{UI_BODY}}", html_escape(resolved.ui_body))
        .replace("{{PRIMARY_CTA}}", primary_action)
        .replace("{{SECONDARY_CTA}}", secondary_action)
        .replace("{{NEXT_ACTION}}", html_escape(resolved.required_next_action or "Continue to dashboard"))
        .replace("{{DASHBOARD_ACCESS}}", "Enabled" if resolved.can_access_dashboard else "Blocked")
        .replace("{{ACCESS_MODE}}", "Read only" if resolved.is_read_only else "Interactive")
        .replace("{{STATE_LINKS}}", _render_state_links(resolved.state))
        .replace("{{CHECKLIST_ITEMS}}", _render_checklist(resolved))
    )