# Issue #86 Execution Slice 01

This document records the first evidence-backed QA slice for issue `#86`.

## Scope

- public entry routes and unauthenticated setup gate
- runtime liveness and readiness
- focused automated coverage for dashboard, compliance, MCP, customer control plane, operator APIs, and runtime guardrails
- automated PR lifecycle and landed-drift coverage aligned to the `dummyAI` rehearsal goal

## Environment

- commit under review: `1d32b47`
- app shape: local monolith
- base URL: `http://127.0.0.1:8011`
- working tree note: issue-86 docs are new and uncommitted; no product code changed in this slice

## Automated Results

### Focused high-risk release pack

- command scope:
  - `tests/test_control_plane_ui.py`
  - `tests/test_dashboard_api.py`
  - `tests/test_dashboard_control_tower.py`
  - `tests/test_compliance_api.py`
  - `tests/test_mcp_package.py`
  - `tests/test_mcp_broker.py`
  - `tests/test_customer_control_plane.py`
  - `tests/test_operator_api.py`
  - `tests/test_runtime_guardrails.py`
- result: `166 passed, 1 skipped in 191.93s`
- conclusion: no regressions found in the highest-risk backend and route-contract surfaces exercised by this pack

### PR lifecycle and landed-drift pack

- command scope:
  - `tests/test_repo_journey.py`
  - `tests/test_pr_merge_onboarding_sync.py`
  - `tests/test_audit_worker.py`
- result: `44 passed in 67.34s`
- conclusion: merged-PR journey handling, onboarding sync, and reopen-state handling passed in the current build

### Proposal-only dashboard evidence slice

- command scope: `tests/test_dashboard_views.py -k proposal`
- result: `2 passed in 7.19s`
- key evidence: the dashboard view suite asserts `proposal-only evidence` in risk reasoning, which matches the intended separation between PR lifecycle evidence and landed drift

## Live Runtime Results

### Public route checks

- `/` rendered the Vipari marketing page with title `Vipari Control Plane`
- `/pricing` rendered with title `Pricing | Vipari`
- `/login` rendered with title `Login | Vipari`
- `/app` as an unauthenticated visitor resolved to the login flow

### Runtime checks

- `/health` returned `{"status":"ok","service_role":"monolith"}`
- `/health/ready` returned a healthy local readiness payload covering config, persistence, and migrations

### Restart log

- uvicorn startup completed successfully
- the restart log recorded repeated `200 OK` traffic on product routes including:
  - `/dashboard`
  - `/api/dashboard/overview`
  - `/api/dashboard/escalation-queue`
  - `/api/repos/doria90/dummyAI/dashboard`
  - `/app/repos`
  - `/app/compliance`
  - `/app/integrations/mcp`
  - `/app/help`
  - `/app/profile`
  - `/app/settings`
  - `/app/policies`

## Local Operator Shell Behavior

- live checks against `/dashboard` and `/dashboard/doria90/dummyAI` without an authenticated session rendered the dashboard shell but surfaced `401`-backed unavailable states for overview and repo data
- live checks against `/app/profile`, `/app/settings`, `/app/integrations/mcp`, and `/app/compliance` without a session returned `{"detail":"Authentication required."}`
- nearby automated evidence confirms this is intentional in local operator mode rather than a regression:
  - `tests/test_control_plane_ui.py` expects `/dashboard` to return `200` in local mode while `/api/dashboard/overview` and `/api/repos` still return `401`
  - `tests/test_operator_api.py` expects dashboard HTML pages to render static shells independently of authenticated API hydration
- current assessment: this is an expected local-development affordance, not a release defect, but it should remain clearly documented because it can look like a broken login gate during manual QA

## DummyAI Alignment

- reference PR reviewed: `doria90/dummyAI` PR `#43`, titled `test: PR lifecycle and landed drift separation`
- expected story from the PR description:
  - audit runs on PR open
  - PR evidence remains proposal-only
  - close and reopen update PR state without contaminating landed-drift views
- current supporting evidence in PromptDrift:
  - `tests/test_dashboard_views.py` covers proposal-only evidence messaging
  - `tests/test_audit_worker.py` covers reopen-state timestamp clearing behavior
  - `tests/test_repo_journey.py` covers merged-PR journey behavior
  - `tests/test_pr_merge_onboarding_sync.py` covers merge-driven onboarding sync expectations

## Current Assessment

- no release-blocking defects were found in this slice
- automated confidence is strong for backend behavior and scope isolation
- live confidence is strong for public entry and runtime health only
- the biggest remaining gaps are still authenticated browser UX, full role-and-entitlement walkthroughs, MCP connector-host manual validation, and the full manual `dummyAI` operator narrative

## Authenticated Owner Bypass And Results

- for local QA only, a temporary owner session was created directly in the local SQLite control-plane database and injected into the browser as the normal `promptdrift_session` cookie
- this bypass avoided OAuth interaction but did not change application code or weaken authorization logic inside the app
- the authenticated session resolved to:
  - user: `doria90`
  - workspace: `Wow Team`
  - role: `owner`
  - access state: `active`

### Protected routes verified under the temporary owner session

- `/app/profile` rendered correctly with editable display name, theme controls, and owner workspace summary
- `/app/settings` rendered correctly with workspace controls, membership invite UI, repo footprint, setup checklist, and MCP link
- `/app/integrations/mcp` rendered correctly with connector quickstart, `vipari.*` tool list, API-key tab, and activity tab
- `/app/settings/api-keys` resolved to `/app/integrations/mcp?tab=api-keys` and showed the machine-principal credentials surface
- `/app/integrations/mcp/download` returned `200` with `application/zip`
- `/app/compliance`, `/app/compliance/frameworks`, `/app/compliance/exports`, and `/app/compliance/evidence` all returned `200`
- `/dashboard` rendered a hydrated overview with real counts, escalations, and repository map data
- `/dashboard/doria90/dummyAI` rendered a hydrated case file with decision summary, recommendation, attribute profile, and drift storyline

### Supporting authenticated API checks

- `/api/auth/session` returned `authenticated: true` and `access.state: active`
- `/api/dashboard/overview` returned `200` with a nontrivial payload
- `/api/dashboard/escalation-queue` returned `200`
- `/api/repos/doria90/dummyAI/dashboard` returned `200`
- `/api/repos` returned `200`

### Updated assessment

- live confidence is now strong for owner-path control-plane, compliance, MCP, dashboard overview, and `dummyAI` casefile behavior in local QA
- the biggest remaining live-browser gaps are edit-role and read-role walkthroughs, plus full MCP connector-host and end-to-end `dummyAI` operator rehearsal

## Admin And Viewer Role Checks

- two additional temporary local sessions were created against the real active workspace:
  - `qa-admin` with workspace role `admin`
  - `qa-viewer` with workspace role `viewer`
- both sessions resolved to `authenticated: true` with `access.state: active`

### Admin role results

- `/app/profile`, `/app/settings`, `/app/integrations/mcp`, `/app/compliance`, `/dashboard`, and `/dashboard/doria90/dummyAI` all returned `200`
- `/app/settings/api-keys` redirected to `/app/integrations/mcp?tab=api-keys`
- posting to `/app/settings/api-keys` with `drift.read` returned `303`, confirming that the editable non-owner path can use workspace API-key management
- `/app/admin` returned `403`, confirming that workspace admin is not equivalent to system-owner access

### Viewer role results

- `/app/profile`, `/app/settings`, `/app/integrations/mcp`, `/app/compliance`, `/dashboard`, and `/dashboard/doria90/dummyAI` all returned `200`
- `/app/settings/api-keys` redirected to `/app/integrations/mcp?tab=api-keys`, but the resulting page fell back to the overview-style restricted view
- the live viewer MCP page omitted `Machine principal credentials` and instead showed the restriction copy:
  - `Workspace machine-principal inventory and API-key management stay restricted to workspace owners and admins.`
  - `Recent integration and API-key activity stays visible only to workspace owners and admins.`
- the live viewer settings page showed disabled workspace controls, disabled invite inputs, and explicit owner/admin-only messaging
- viewer mutation attempts were denied with `403` for:
  - `/app/billing/checkout?plan=team`
  - `/app/setup/install/link`
  - `/app/settings/api-keys`

### System-owner confirmation

- the existing local owner session for `doria90` exposed `href="/app/admin"` from both `/app/profile` and `/app/settings`
- `/app/admin` returned `200`, confirming that the configured local owner identity retains system-owner access in this environment

## MCP Package And Broker Follow-Up

- downloaded `/app/integrations/mcp/download` again into `artifacts/issue86-mcp-check/vipari-mcp.zip` and unpacked it successfully
- shipped package contents matched the expected six-file connector artifact:
  - `README.md`
  - `requirements.txt`
  - `vipari.env.example`
  - `claude-desktop-config.json.example`
  - `tool-manifest.json`
  - `vipari_mcp_server.py`
- the shipped manifest still matched the expected four-tool `vipari.*` surface:
  - `vipari.list_repos`
  - `vipari.get_repo_posture`
  - `vipari.get_repo_casefile`
  - `vipari.list_escalations`
- the packaged `vipari_mcp_server.py` remained the intended thin broker client and accepted both `VIPARI_*` and legacy `PROMPTDRIFT_*` environment variables
- attempted a true end-to-end broker path by creating a temporary workspace machine principal and calling `/api/agent-integrations/mcp/token`
- that live token exchange failed with `503` and body `{"detail":"Internal JWT auth is not configured."}`
- conclusion: the connector package itself is consistent, but full live connector-host validation is blocked in this local environment by missing internal JWT broker configuration rather than by a package or route-shape defect

## DummyAI Operator Rehearsal

- the live `dummyAI` casefile is currently anchored to PR `#48`, not PR `#43`
- this matched the current connected repo evidence rather than a stale dashboard read:
  - local `dummyAI` checkout was on branch `promptdrift-live-risky-20260503`
  - local HEAD was `31db2a3e62d597bc4a7c3e8bafeeb3a71d2db173`
  - GitHub PR `#48` exists and matches that risky live prompt branch
  - the repo dashboard payload returned `review_target: PR #48`, `review_head_sha: 31db2a3...`, and a `review_url` pointing at `/pull/48`
- conclusion: the original issue-86 rehearsal target of PR `#43` has been superseded in the current live fixture by PR `#48`; the dashboard and API remained internally consistent with the connected repository state

### Dashboard overview and repo casefile results

- workspace dashboard overview still surfaced `dummyAI` as a primary escalation with proposal-plus-history evidence and an explicit baseline anchor
- the default `dummyAI` drift tab remained coherent and operator-usable:
  - top finding: `Capability expansion needs review`
  - rationale: `The current PR proposal increases capability risk relative to the current baseline.`
  - recommendation quality remained specific and actionable
  - provenance stayed explicit through `PR #48`, `proposal + history`, and `main @ ca51d71`

### Repo tab walkthrough

- `version-control` hydrated after network idle and rendered:
  - posture radar
  - snapshot journey with approved baseline, historical commit, and branch-head checkpoints
  - baseline-vs-current comparison with `+0.000` drift delta and `0 changed artifacts`
- `baseline` hydrated after network idle and rendered:
  - review target card pointing back to the flagged PR-driven change
  - baseline review summary with `1 approved` artifact and recent `rebaseline` decisions
  - artifact registry with `system_prompt.md` as the single approved prompt artifact
- `compliance` rendered correctly with repo-level AI-surface counts, audit cues, and no lower-confidence conflicts
- `reports` rendered the export form and related-audits handoff path correctly; export history remained in loading state until a real history row is available

### Deep-link behavior

- deep-linking `/dashboard/doria90/dummyAI?artifact=system_prompt.md&pr=48&head_sha=31db2a3...` preserved the selected artifact and PR context across repo tabs
- the deep-linked drift view hydrated to the expected `system_prompt.md` casefile, including:
  - `Capability expansion needs review`
  - `PR #48 drift detected in guardrails and capability.`
  - storyline, attribute profile, and recommendation content aligned with the live repo dashboard payload

### Revised remaining gaps

- live confidence is now strong for system-owner, owner, editable non-owner, and read-only role behavior on the core control-plane and dashboard routes
- the biggest remaining live gaps are:
  - rerunning MCP connector-host end-to-end execution in an environment where internal JWT broker config is enabled
  - deciding whether issue-86 should refresh its controlled PR reference from `#43` to `#48` for the current live fixture, or re-stage the repo back to the original lifecycle-specific PR scenario

## Next Slice Priorities

1. Run authenticated browser walkthroughs for owner, edit, and read roles across `/app/*` and `/dashboard*`.
2. Execute the full manual `dummyAI` PR-audit rehearsal and record screenshots plus operator-facing narrative quality.
3. Validate MCP package download, unzip contents, connector configuration, and live `vipari.*` tool invocation.