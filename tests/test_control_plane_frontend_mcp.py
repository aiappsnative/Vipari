from __future__ import annotations

from types import SimpleNamespace

from services.control_plane_frontend import MCP_BROKER_TOOLS, render_control_plane_mcp_page


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
    assert '>Tools</a>' in html
    assert principal.client_id in html
    assert "Client ID" in html
    assert 'data-filter-scope="activity"' in html
    assert 'data-filter-target="event"' in html
    assert 'data-filter-target="client"' in html
    assert 'data-filter-status="activity"' in html
    assert 'data-filter-row="activity"' in html


def test_render_control_plane_mcp_overview_uses_header_badge_and_moves_tools_to_tools_tab():
    principal = SimpleNamespace(
        display_name="qa-admin-temp",
        client_id="f93336c1-d36f-4f71-ab02-8f19dfc9b5f9",
        scopes_json='["drift.read"]',
        status="active",
        created_at=1_777_000_000.0,
    )

    overview_html = render_control_plane_mcp_page(
        workspace_name="Wow Team",
        audit_href="/dashboard",
        plan_label="Team",
        theme_preference="dark",
        admin_url=None,
        active_tab="overview",
        download_url="/app/integrations/mcp/download",
        broker_host="http://127.0.0.1:8011/api/agent-integrations/mcp",
        config_snippet="VIPARI_MCP_BROKER_URL=http://127.0.0.1:8011/api/agent-integrations/mcp",
        principals=[principal],
        audit_logs=[],
        csrf_token="csrf-token",
        can_manage=True,
        entitlement_allows=True,
        one_time_secret=None,
        max_principals=5,
        new_client_id=None,
    )
    tools_html = render_control_plane_mcp_page(
        workspace_name="Wow Team",
        audit_href="/dashboard",
        plan_label="Team",
        theme_preference="dark",
        admin_url=None,
        active_tab="tools",
        download_url="/app/integrations/mcp/download",
        broker_host="http://127.0.0.1:8011/api/agent-integrations/mcp",
        config_snippet="VIPARI_MCP_BROKER_URL=http://127.0.0.1:8011/api/agent-integrations/mcp",
        principals=[principal],
        audit_logs=[],
        csrf_token="csrf-token",
        can_manage=True,
        entitlement_allows=True,
        one_time_secret=None,
        max_principals=5,
        new_client_id=None,
    )

    assert 'class="control-page-header-meta-link"' in overview_html
    assert 'href="/integrations/mcp?tab=api-keys"' in overview_html
    assert '1 active workspace API key is available.' in overview_html
    assert "Workspace machine principals" not in overview_html
    assert "Operational visibility" not in overview_html
    assert "Scoped MCP surface" in tools_html
    assert "vipari.list_available_tools" in tools_html
    assert "The downloaded connector ships the full contract" in tools_html
    assert "drift.write.low" in tools_html
    assert MCP_BROKER_TOOLS[0]["name"] in tools_html


def test_render_control_plane_mcp_activity_makes_denied_broker_events_operator_readable():
    principal = SimpleNamespace(
        display_name="qa-admin-temp",
        client_id="f93336c1-d36f-4f71-ab02-8f19dfc9b5f9",
        scopes_json='["drift.read"]',
        status="active",
        created_at=1_777_000_000.0,
    )
    denied_entry = SimpleNamespace(
        created_at=1_777_000_000.0,
        event_type="mcp_broker.tool_denied",
        subject_type="machine_principal",
        subject_id=principal.client_id,
        payload_json='{"error":"insufficient_scope","message":"Missing required scope: drift.write.low.","required_scope":"drift.write.low","granted_scopes":["drift.read"],"tool_name":"vipari.add_audit_feedback"}',
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
        audit_logs=[denied_entry],
        csrf_token="csrf-token",
        can_manage=True,
        entitlement_allows=True,
        one_time_secret=None,
        max_principals=5,
        new_client_id=None,
    )

    assert "Broker tool denied" in html
    assert "mcp_broker.tool_denied" in html
    assert "Missing required scope: drift.write.low." in html
    assert "required scope=drift.write.low" in html
    assert "granted=drift.read" in html
    assert "tool=vipari.add_audit_feedback" in html


def test_render_control_plane_mcp_activity_shows_denial_summary_counts():
    principal = SimpleNamespace(
        display_name="qa-admin-temp",
        client_id="f93336c1-d36f-4f71-ab02-8f19dfc9b5f9",
        scopes_json='["drift.read"]',
        status="active",
        created_at=1_777_000_000.0,
    )
    audit_logs = [
        SimpleNamespace(
            created_at=1_777_000_000.0,
            event_type="mcp_broker.tool_denied",
            subject_type="machine_principal",
            subject_id=principal.client_id,
            payload_json='{"error":"insufficient_scope","message":"Missing required scope: drift.write.low."}',
        ),
        SimpleNamespace(
            created_at=1_777_000_010.0,
            event_type="mcp_broker.auth_denied",
            subject_type="machine_principal",
            subject_id="other-client-id",
            payload_json='{"error":"invalid_token","message":"Machine principal is not active."}',
        ),
        SimpleNamespace(
            created_at=1_777_000_020.0,
            event_type="mcp_broker.tool_invoked",
            subject_type="machine_principal",
            subject_id=principal.client_id,
            payload_json='{"tool_name":"vipari.list_repos"}',
        ),
    ]

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
        audit_logs=audit_logs,
        csrf_token="csrf-token",
        can_manage=True,
        entitlement_allows=True,
        one_time_secret=None,
        max_principals=5,
        new_client_id=None,
    )

    assert "Denied broker events" in html
    assert "Denied clients" in html
    assert "Tool denials" in html
    assert "Top denied client" in html
    assert "Top denied tool" in html
    assert ">2</strong><span class=\"control-page-copy\">Recent token, auth, and tool denials recorded in this activity window." in html
    assert ">2</strong><span class=\"control-page-copy\">Distinct machine principals that hit a denied MCP action." in html
    assert ">1</strong><span class=\"control-page-copy\">Denied broker tool calls after token issuance and request auth succeeded." in html
    assert ">f93336c1-d36f-4f71-ab02-8f19dfc9b5f9</strong><span class=\"control-page-copy\">Machine principal with the most denied MCP activity in this view." in html
    assert ">None</strong><span class=\"control-page-copy\">Broker tool most often denied after request auth succeeded." in html


def test_render_control_plane_mcp_activity_flags_high_denial_volume():
    principal = SimpleNamespace(
        display_name="qa-admin-temp",
        client_id="client-a",
        scopes_json='["drift.read"]',
        status="active",
        created_at=1_777_000_000.0,
    )
    audit_logs = [
        SimpleNamespace(
            created_at=1_777_000_000.0 + index,
            event_type="mcp_broker.tool_denied",
            subject_type="machine_principal",
            subject_id=f"client-{index}",
            payload_json='{"error":"insufficient_scope","message":"Missing required scope: drift.write.low.","tool_name":"vipari.add_audit_feedback"}',
        )
        for index in range(5)
    ]

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
        audit_logs=audit_logs,
        csrf_token="csrf-token",
        can_manage=True,
        entitlement_allows=True,
        one_time_secret=None,
        max_principals=5,
        new_client_id=None,
    )

    assert "Investigation recommended" in html
    assert ">vipari.add_audit_feedback</strong><span class=\"control-page-copy\">Broker tool most often denied after request auth succeeded." in html