# Vipari Rename V1 Plan

This document turns GitHub issue `#82` into an execution plan for the product-side rename from `PromptDrift` / `DriftGuard` to `Vipari`.

## Goal

Present the product consistently as `Vipari` across the application and current product-facing repository docs without turning the change into a risky internal refactor.

## Customer Outcome

Logged-in and logged-out users should encounter one clear product identity across dashboard, setup, help, settings, and integration surfaces, with no major mixed branding left in the product experience.

## Product Hypothesis

If the rename is executed as a visible-surface copy pass rather than a deep technical rename, the product can adopt the new identity quickly without destabilizing routes, integrations, persistence, or deployment conventions.

## Non-Goals

- no Base44 marketing-site changes
- no API path renames for branding reasons
- no env var, database, queue, or package renames
- no MCP tool namespace rename in this issue
- no broad UX redesign beyond copy and light-touch identity updates
- no wholesale symbol/import/module renaming unless tiny and obviously safe

## Current Reality On `main`

The current branch is heavily mixed across visible product surfaces:

- dashboard and control-plane templates still use `DriftGuard` in page titles, sidebar home labels, onboarding/setup copy, and integration/help/settings language
- some surfaces still use `PromptDrift`, especially MCP integration/help text and older control-plane marketing copy
- contributor-facing docs such as `README.md` and customer MCP docs still present the product as `DriftGuard` or `PromptDrift`
- internal-only identifiers still intentionally use legacy naming in routes, env vars, DB paths, compose settings, MCP tool names, and some JS metadata keys

That means the issue should be implemented as a systematic visible-copy rename with an explicit do-not-touch boundary around internal contracts.

## Scope

### In Scope

- browser-visible product names in templates and rendered copy
- dashboard page titles and visible dashboard labels
- control-plane page titles, onboarding/setup/login/install/help/settings/profile/API key pages
- user-facing MCP and integration copy in the app
- current product-facing docs in this repo
- one short explanation of the name in low-frequency help/docs surfaces

### Out Of Scope

- `/dashboard`, `/cp/*`, broker, or webhook route renames
- `PROMPTDRIFT_*` / `driftguard` env vars and compose settings
- database table names and migration identifiers
- MCP tool IDs such as `promptdrift.list_repos`
- internal JS/meta keys used only for DOM wiring or tests
- archived or historical docs whose value depends on preserving historical wording

## Audit Summary

### User-Facing: Must Rename In This Issue

- `templates/dashboard_index.html`
- `templates/dashboard_repo.html`
- `templates/control_plane_marketing.html`
- `templates/control_plane_login.html`
- `templates/control_plane_app.html`
- `templates/control_plane_install.html`
- `templates/control_plane_workspace_new.html`
- `templates/control_plane_repo_setup.html`
- `templates/control_plane_compliance.html`
- `templates/control_plane_settings.html`
- `templates/control_plane_profile.html`
- `templates/control_plane_api_keys.html`
- `templates/control_plane_mcp.html`
- `templates/control_plane_help.html`
- selected copy emitted from `services/access_state.py`

### Contributor-Facing / Docs: Should Rename In This Issue

- `README.md`
- `customer_mcp_server/README.md`
- current product guidance docs such as `docs/ai-act-readiness-faq.md`
- current product guidance docs such as `docs/ai-act-capability-map.md`

### Internal-Only: Leave For Follow-Up

- `customer_mcp_server/tool-manifest.json` tool IDs using `promptdrift.*`
- env vars such as `PROMPTDRIFT_MCP_BROKER_URL`
- compose/database values like `driftguard` and `promptdrift.db`
- internal JS/meta keys such as `driftguard-repo-full`
- internal route and API naming

### Ambiguous: Manual Review Required

- single-letter sidebar logo marks currently rendered as `D`
- placeholder workspace defaults such as `PromptDrift Team`
- historical docs or changelog entries that should stay historically accurate rather than rewritten

## Recommended Implementation Shape

Do not do a blind global search-and-replace.

Use a staged implementation with three naming buckets:

1. `Vipari` for all current user-facing product identity
2. short explanatory copy only in low-frequency help/about/doc surfaces
3. legacy internal identifiers left in place until a separate cleanup issue

Centralize only minimal branding primitives where it reduces repeated copy safely. A small helper or constant is acceptable for repeated visible strings, but most template copy should remain explicit and readable.

## Execution Phases

### Phase 1: Audit And Guardrails

Deliverables:

- complete a repo-wide audit for `PromptDrift`, `DriftGuard`, and visible title strings
- maintain a categorized checklist in `docs/vipari-rename-v1-checklist.md`
- identify explicit out-of-scope legacy names that must remain untouched

Why first:

- this issue is more likely to miss visible surfaces than to break code
- the checklist keeps the PR systematic and reviewable

### Phase 2: Visible App-Shell Rename

Deliverables:

- update page titles, sidebar home labels, visible product references, onboarding/setup/login/install copy, and integration/help/settings copy to `Vipari`
- ensure a logged-in user does not encounter mixed branding in the primary app flow
- keep routes, API shapes, and persistence untouched

Primary files:

- template files listed in the audit summary
- `services/access_state.py`
- any current render helpers in `main.py` or related service modules that still emit legacy visible strings

### Phase 3: Light-Touch Name Explanation

Deliverables:

- add a short explanation in the Help page and README
- keep the wording practical and product-relevant
- avoid placing the explanation in dashboard triage or review-heavy surfaces

Suggested tone:

> Vipari is named from *viparinamadhamma* — the idea that systems we treat as fixed are in fact subject to change.

### Phase 4: Product-Facing Docs And MCP Copy

Deliverables:

- update README and current product-facing docs to `Vipari`
- update customer MCP connector documentation and in-app integration copy
- keep tool namespaces and env vars unchanged in this issue

### Phase 5: Cleanup And Follow-Up Boundary

Deliverables:

- verify no major mixed branding remains in user-facing product surfaces
- explicitly document leftover internal-only legacy identifiers in the PR
- create a follow-up issue if deeper internal naming normalization is still desired

## Design Constraints

- keep the dashboard focused on review and posture, not philosophy
- keep the name explanation short and low-frequency
- preserve current template structure and route behavior unless copy changes require a tiny helper extraction
- prefer narrow UI regression tests over invasive structural changes

## Real Code Touchpoints

Primary application surfaces:

- `templates/dashboard_index.html`
- `templates/dashboard_repo.html`
- `templates/control_plane_*.html`
- `services/access_state.py`
- `services/dashboard_frontend.py` only if a tiny shared helper becomes worthwhile

Docs and integration surfaces:

- `README.md`
- `customer_mcp_server/README.md`
- selected docs under `docs/`

Likely no-change except deliberate out-of-scope review:

- `customer_mcp_server/tool-manifest.json`
- compose files
- env var names
- route definitions and API paths

## Known Risks

- mixed branding can linger in less obvious pages such as help, install, API keys, and repo setup if the audit is not checklist-driven
- an overly aggressive rename could accidentally touch MCP tool IDs, env vars, compose settings, or tests that intentionally rely on internal legacy names
- historical docs can be rewritten incorrectly if current-product guidance and historical record are not separated carefully

## Recommended Branch

- `feature/issue-82-vipari-rename`

## Suggested Validation

### Automated

- targeted UI tests covering dashboard and control-plane page rendering
- targeted tests for help/settings/integration pages if they currently assert visible strings
- add branding regression assertions for key pages that should render `Vipari`

### Manual

- `/login`
- `/app`
- `/dashboard`
- `/dashboard/{owner}/{repo}`
- `/app/help`
- `/app/settings`
- `/app/profile`
- `/app/integrations/mcp`
- `/app/repos`
- `/app/compliance`

## Exit Criteria

- primary application surfaces consistently present the product as `Vipari`
- no major user-facing mixed branding remains in the product repo
- a short name explanation exists only in appropriate low-frequency surfaces
- docs visible to customers or contributors are updated to the new name
- internal-only identifiers remain stable unless a trivial safe rename is explicitly justified

## Follow-On Work After This Issue

- internal identifier harmonization if still desired
- MCP tool namespace rename if product strategy requires it later
- env var / deployment identifier normalization
- historical-document cleanup only if preserving original wording is no longer important