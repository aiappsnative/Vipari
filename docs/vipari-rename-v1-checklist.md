# Vipari Rename V1 Checklist

This checklist narrows GitHub issue `#82` into a systematic rename audit so visible branding can be updated without touching risky internal contracts.

Read this together with [vipari-rename-v1-plan.md](./vipari-rename-v1-plan.md).

## Recommended Branch

- `feature/issue-82-vipari-rename`

## Rename Rules

- rename visible product identity to `Vipari`
- keep route names, env vars, DB names, queue identifiers, and MCP tool namespaces unchanged
- add the name explanation only in low-frequency surfaces such as Help and README
- preserve historical docs where changing old names would distort the historical record

## Must Rename Now: User-Facing Product Surfaces

### Dashboard

- [x] `templates/dashboard_index.html`
- [x] `templates/dashboard_repo.html`

### Logged-out / setup / access-state pages

- [x] `templates/control_plane_marketing.html`
- [x] `templates/control_plane_login.html`
- [x] `templates/control_plane_app.html`
- [x] `templates/control_plane_install.html`
- [x] `templates/control_plane_workspace_new.html`

### Logged-in control-plane pages

- [x] `templates/control_plane_repo_setup.html`
- [x] `templates/control_plane_compliance.html`
- [x] `templates/control_plane_settings.html`
- [x] `templates/control_plane_profile.html`
- [x] `templates/control_plane_api_keys.html`
- [x] `templates/control_plane_mcp.html`
- [x] `templates/control_plane_help.html`

### Python-emitted visible copy

- [x] `services/access_state.py`
- [x] audited remaining visible product strings emitted directly from current Python/UI surfaces

## Should Rename Now: Contributor / Product Docs

- [x] `README.md`
- [x] `customer_mcp_server/README.md`
- [x] `docs/ai-act-readiness-faq.md`
- [x] `docs/ai-act-capability-map.md`
- [x] current customer-visible MCP runtime copy in `customer_mcp_server/promptdrift_mcp_server.py`

## Add Light-Touch Explanation

- [x] add one short “Why the name?” note to Help or equivalent low-frequency in-app surface
- [x] add one short “Why the name?” note to README or primary product doc
- [x] keep the explanation concise, product-relevant, and non-philosophical in tone

## Leave For Follow-Up: Internal-Only Legacy Names

- [x] `customer_mcp_server/tool-manifest.json` tool namespace entries such as `promptdrift.list_repos`
- [x] connector env vars like `PROMPTDRIFT_MCP_BROKER_URL`
- [x] compose / DB identifiers such as `driftguard` and `promptdrift.db`
- [x] internal JS/meta keys such as `driftguard-repo-full`
- [x] API paths and route naming

## Ambiguous Items Requiring Manual Review

- [x] sidebar logo mark updated from `D` to `V` across current app templates
- [x] placeholder workspace default updated to `Vipari Team` in `templates/control_plane_workspace_new.html`
- [x] archived or historical docs intentionally left unchanged when they serve as historical record

## Suggested First Implementation Slice

- [x] rename app-shell and dashboard visible strings to `Vipari`
- [x] rename logged-out and setup pages to `Vipari`
- [x] update help and MCP page copy to `Vipari`
- [x] add one short Help-page explanation of the name
- [x] add focused rendering assertions for `Vipari` on key pages

## Validation Checklist

- [x] logged-out pages do not show mixed branding
- [x] logged-in dashboard and repo pages do not show mixed branding
- [x] settings/help/integrations pages do not show mixed branding
- [x] README and customer MCP docs align with `Vipari`
- [x] no risky internal identifiers were renamed unintentionally