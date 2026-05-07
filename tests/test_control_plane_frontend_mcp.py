from __future__ import annotations

from types import SimpleNamespace

from services.control_plane_frontend import render_control_plane_mcp_page


def test_render_control_plane_mcp_page_shows_audit_link_full_client_ids_and_filters():
    principal = SimpleNamespace(
        display_name="qa-admin-temp",
        client_id="f93336c1-d36f-4f71-ab02-8f19dfc9b5f9",
        scopes_json='["drift.read"]',
        status="active",
        created_at=1_777_000_000.0,
    )
    audit_entry = SimpleNamespace(
        created_at=1_777_000_000.0,
        event_type="mcp_broker.token_issued",
        subject_type="machine_principal",
        subject_id=principal.client_id,
        payload_json='{"source":"self_service"}',
    )

    html = render_control_plane_mcp_page(
        workspace_name="Wow Team",
        audit_href="/dashboard",
        plan_label="Team",
        theme_preference="dark",
        admin_url=None,
        active_tab="activity",
        download_url="/app/integrations/mcp/download",
        broker_host="http://127.0.0.1:8011/api/agent-integrations/mcp",
        config_snippet="VIPARI_MCP_BROKER_URL=http://127.0.0.1:8011/api/agent-integrations/mcp",
        principals=[principal],
        audit_logs=[audit_entry],
        csrf_token="csrf-token",
        can_manage=True,
        entitlement_allows=True,
        one_time_secret=None,
        max_principals=5,
        new_client_id=None,
    )

    assert 'href="/dashboard" class="sidebar-nav-item" aria-label="Audit Logs"' in html
    assert principal.client_id in html
    assert "Client ID" in html
    assert 'data-filter-scope="activity"' in html
    assert 'data-filter-target="event"' in html
    assert 'data-filter-target="client"' in html
    assert 'data-filter-status="activity"' in html
    assert 'data-filter-row="activity"' in html