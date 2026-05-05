# Issue #86 Pre-Launch QA Overpass Plan

This document turns issue `#86` into a release-readiness QA execution plan for Vipari.

The goal is not a generic smoke pass. The goal is a deliberate pre-release overpass that pressure-tests the current product surface, validates the intended operating model, catches UI regressions before release, and produces decision-quality evidence for go / no-go.

## Goal

Confirm that the current release candidate is ready for launch across:

- public control-plane entry points
- authenticated control-plane and dashboard flows
- repository onboarding and drift review workflows
- compliance and export workflows
- billing, entitlement, and access-gating behavior
- MCP package, broker, and customer-host setup flow
- PR-audit behavior against the `dummyAI` test surface
- health, readiness, runtime, and operational guardrails
- automation coverage for the above

The issue is complete only when the team can answer all of the following with evidence:

- what paths are working as designed
- what paths are intentionally blocked by access or setup state
- what visual or UX defects remain
- what risks remain for release
- whether the build is ready for production deployment without additional stabilizing work

## Planning Principles

- Use the current repo and current product as the source of truth, not old assumptions.
- Prefer state-aware testing over route-only testing. Many Vipari routes intentionally vary by auth, workspace, plan, install, and onboarding state.
- Treat UI polish as a release criterion, not a cosmetic follow-up.
- Capture evidence as the work runs so findings are reproducible.
- Separate product defects from expected setup-aware gating.
- Do not treat passing tests alone as sufficient. Dynamic runtime and browser validation are required.

## Non-Negotiable Scope

This QA pass must cover all major product surfaces currently visible in the repo.

### Public surfaces

- `/`
- `/pricing`
- `/login`
- GitHub auth entry and callback handoff behavior

### Authenticated control-plane surfaces

- `/app`
- `/app/workspaces/new`
- `/app/profile`
- `/app/settings`
- `/app/settings/api-keys` redirect behavior into MCP page
- `/app/integrations/mcp`
- `/app/integrations/mcp/download`
- `/app/policies`
- `/app/compliance`
- `/app/compliance/frameworks`
- `/app/compliance/exports`
- `/app/compliance/evidence`
- `/app/help`
- `/app/billing`
- `/app/billing/claim`
- `/app/billing/portal`
- `/app/setup/install`
- `/app/repos`
- `/app/admin`

### Dashboard surfaces

- `/dashboard`
- `/dashboard/{repo_full}`
- deep-link behavior via `artifact`, `pr`, and `head_sha` query params
- repo-level tabs: `drift`, `version-control`, `baseline`, `compliance`, `reports`

### Product APIs that materially affect the release surface

- `/api/auth/session`
- `/api/workspaces/current/access-state`
- dashboard JSON routes under `/api/dashboard/*` and `/api/repos/*`
- compliance APIs under `/api/compliance/*`
- export APIs under `/api/export/*` and `/api/repos/{repo}/export/*`
- onboarding and backfill APIs under `/api/repos/{repo}/onboard` and `/api/repos/{repo}/backfill`
- MCP broker APIs:
  - `/api/agent-integrations/mcp/token`
  - `/api/agent-integrations/mcp/tools`
  - `/api/agent-integrations/mcp/invoke`
- customer control-plane APIs under `/cp/*`
- billing handoff webhook-facing or provisioning-adjacent paths that affect entitlement state

### Runtime and operational surfaces

- `/health`
- `/health/ready`
- `/webhook`
- `/webhooks/stripe`
- production-preflight and migration tooling behavior relevant to launch readiness

### Customer MCP package surface

- `customer_mcp_server/README.md`
- `customer_mcp_server/vipari_mcp_server.py`
- `customer_mcp_server/tool-manifest.json`
- `customer_mcp_server/vipari.env.example`
- `customer_mcp_server/claude-desktop-config.json.example`
- compatibility expectations for legacy PromptDrift aliases

### Reference repo and audit flow surface

- `dummyAI/`
- GitHub installation and repo connection behavior for a known test repo
- PR audit / review path against a controlled change in `dummyAI`

## Test Environments

The QA pass should use more than one environment because this release has both local product surfaces and production-shape assumptions.

### Environment A: local deterministic validation

Purpose:

- fast iteration
- targeted route and API validation
- fixture-backed UI verification
- regression confirmation while fixing defects

Expected shape:

- local run from current repo
- local DB and queue as appropriate for deterministic runs
- controllable session and fixture state

### Environment B: production-like rehearsal

Purpose:

- validate the blessed topology shape
- confirm split-service assumptions and operational readiness behavior
- confirm launch-day instructions are still accurate

Expected shape:

- Docker-based service split
- Postgres
- Redis
- production-like env vars and readiness contract

### Environment C: browser-driven operator pass

Purpose:

- visual polish verification
- navigation continuity
- responsive layout checks
- theme behavior and state continuity
- shell consistency across product areas

## Preconditions

Before executing the QA overpass, establish the following:

1. Identify the exact commit under review.
2. Confirm the repo is clean and the working branch is stable.
3. Confirm required local secrets and service configuration for auth, GitHub App, and any payment or MCP flows that will be exercised.
4. Confirm whether real external providers will be used or whether the pass is split between mocked and live-provider slices.
5. Prepare one workspace in each major state needed for validation.
6. Prepare one known test repo for onboarding and PR audit, with `dummyAI` as the canonical controlled repo.
7. Decide where screenshots, route notes, and defect evidence will be stored.

## Required Workspace States

This release has setup-aware routing, so QA must explicitly validate multiple states rather than only the happy path.

At minimum, validate these states:

1. Unauthenticated visitor
2. Authenticated user without workspace
3. Workspace without active subscription
4. Billing pending confirmation
5. Payment failed
6. Active workspace without GitHub install
7. Installed workspace without onboarded repo
8. Active comments-only workspace
9. Fully active workspace with dashboard access
10. Canceled-but-still-active workspace
11. Expired read-only workspace
12. Forbidden or reduced-role user inside an existing workspace

For each state, capture:

- expected landing route
- expected CTA
- expected blocked areas
- whether the UI explains the state clearly
- whether the state wording is accurate and non-confusing

## Role Matrix

The pass must verify behavior by role, not only by route.

At minimum:

- unauthenticated user
- workspace owner
- workspace edit user
- workspace read user
- customer MCP machine principal
- system owner for `/app/admin`

For each role, verify:

- allowed routes
- denied routes
- visible controls
- hidden or disabled controls
- mutation permissions
- copy quality when access is denied or reduced

## Route And UX Matrix

The QA execution sheet derived from this plan should include one row per route or route family with the following fields:

- route
- surface type
- auth required
- workspace state required
- role expectations
- primary user job
- expected CTA or next action
- key visual expectations
- API dependencies
- evidence captured
- result
- defect IDs

### Public entry routes

Validate:

- landing page copy, hierarchy, and calls to action
- pricing page plan naming and compatibility wording
- login flow language and handoff-state messaging
- no broken links or shell regressions
- no stale PromptDrift branding in primary user-facing surfaces

### Setup and onboarding routes

Validate:

- `/app` redirects correctly for every setup state
- workspace bootstrap form and copy
- billing entry, claim flow, and portal routing
- GitHub install flow and manual fallback copy
- repo inventory and allocation flow
- onboarding readiness cues and route continuity

### Control-plane workspace routes

Validate:

- profile
- settings
- API-key or agent-integration handoff
- policies placeholder behavior
- help content
- compliance routes
- admin page restrictions

For each, verify:

- shell consistency
- nav highlighting
- sidebar continuity
- theme toggle behavior
- empty states
- state messaging
- disabled or hidden actions for non-admin users

### Dashboard routes

Validate:

- overview page first-load behavior
- repo list load states
- escalation queue rendering
- default selection behavior
- repo detail page tab switching
- evidence and storyline rendering
- deep-link handling
- shell-blocked states for non-eligible workspaces

UI-specific checks:

- no collapsed layout regressions
- no broken skeleton states
- no duplicated sections
- no dead-end navigation items
- keyboard focus and obvious active state on major controls

### Compliance routes

Validate:

- readiness summary
- frameworks tab
- exports tab
- evidence tab
- filtering behavior
- export CTA clarity
- evidence freshness and governance labeling

### MCP routes and package flows

Validate:

- integrations page content and role-based controls
- API-key generation and revoke flows
- package download
- broker URL correctness
- tool-count and manifest consistency
- one-time-secret handling language
- trust-boundary explanation clarity

## UI Quality Matrix

The release bar for issue #86 must explicitly include UI quality.

For each primary page family, validate on desktop and mobile-width layouts:

- visual hierarchy
- typography consistency
- sidebar and shell alignment
- spacing consistency
- overflow handling
- empty-state quality
- button states
- disabled-state clarity
- success and error message styling
- no placeholder or stub copy where the product now claims readiness

Theme checks:

- dark and light mode rendering on pages that support theme preference
- theme persistence through page transitions where intended
- no unreadable contrast combinations

Accessibility-adjacent checks:

- keyboard navigation on major interactive elements
- visible focus states
- semantic button vs link use on major actions
- loading states that do not strand the operator

## DummyAI And PR-Audit Validation Plan

This issue must include a controlled end-to-end audit rehearsal using `dummyAI`.

### Goal

Prove that a representative repository change flows cleanly through onboarding, baseline, diff analysis, and operator review surfaces.

### Required steps

1. Confirm `dummyAI` can be connected or is already available in the test installation.
2. Ensure the repo is allocated to the test workspace.
3. Run onboarding and, where useful, history backfill.
4. Confirm baseline and artifact inventory are visible in product surfaces.
5. Create or identify a controlled PR that changes a known AI-relevant artifact.
6. Verify queueing, processing, and storage of the audit result.
7. Verify visibility in:
   - dashboard overview
   - repo case-file page
   - any relevant compliance or evidence path
   - MCP broker surfaces if read access exposes the resulting state
8. Validate the operator narrative quality:
   - is the finding understandable
   - is the recommended action useful
   - is provenance visible enough to trust the result

### Failure conditions to watch

- repo appears connected but not review-ready without explanation
- onboarding appears complete but dashboard remains empty
- PR audit result exists but is absent from user-facing views
- posture or severity labels feel inconsistent across views
- deep links into case-file context fail or lose state

## MCP End-To-End Validation Plan

The MCP package is product-critical and must be treated as a first-class release surface.

### Documentation validation

Confirm that the package README, env example, config example, and manifest agree on:

- broker URL variable names
- client credential names
- canonical tool names
- compatibility note for legacy names
- setup order

### Package integrity validation

Confirm the downloaded package contains the expected files and only the expected files.

### Functional validation

Run the customer connector in a controlled environment and verify:

- client credentials can exchange for a broker token
- tool discovery works
- each documented `vipari.*` tool invokes successfully for an authorized workspace
- scoped failures are understandable for unauthorized requests

### Compatibility validation

Confirm legacy PromptDrift environment variables and aliases still behave as documented where compatibility is intended.

## API And Security Validation Plan

The QA overpass must include explicit checks for security-sensitive seams.

### Auth and session

- GitHub login handoff correctness
- session creation and logout
- state and context preservation
- unauthenticated redirects
- no route leaks across setup-aware boundaries

### Access control

- owner-only billing and install mutations
- admin-only control-plane actions
- read-only user restrictions
- workspace isolation for customer control-plane APIs
- machine principal scope isolation for MCP and `/cp/*`

### Sensitive operations

- API-key creation, one-time secret display, and revoke
- billing handoff flows
- export downloads and token access
- admin actions under `/app/admin`

### Negative-path validation

- missing secret / misconfigured auth conditions
- malformed requests to MCP broker endpoints
- revoked principal behavior
- wrong-workspace access attempts
- expired or reduced-entitlement workspace behavior

## Runtime And Operational Validation Plan

This issue is pre-release, so it must confirm that the runtime story remains coherent.

### Required checks

- health endpoint behavior
- readiness endpoint behavior
- production-like preflight behavior
- migration workflow correctness against non-SQLite production assumptions
- webhook service expectations
- worker queue expectations
- split-role assumptions for blessed deployment model

### What to verify

- misconfiguration fails clearly
- healthy services report ready only when dependencies are reachable
- local helper scripts are still clearly non-production
- docs and tooling still describe the same blessed path

## Automation Coverage Audit

Issue #86 is not only about manual QA. It must also measure where automated coverage is strong and where it is thin.

### Existing suites to audit against plan scope

Primary high-value suites include:

- `tests/test_control_plane_ui.py`
- `tests/test_control_plane_auth.py`
- `tests/test_customer_control_plane.py`
- `tests/test_dashboard_control_tower.py`
- `tests/test_dashboard_api.py`
- `tests/test_compliance_api.py`
- `tests/test_compliance_readiness.py`
- `tests/test_mcp_package.py`
- `tests/test_mcp_broker.py`
- `tests/test_runtime_guardrails.py`
- `tests/test_railway_preflight.py`
- `tests/test_main.py`

### Coverage audit questions

For each major route family, record:

- whether an automated test exists
- whether it validates positive path only or negative path too
- whether it validates product copy or only status codes
- whether it validates access-state transitions
- whether it validates browser-visible UI composition
- whether it leaves a manual-only gap that should be closed before release

### Deliverable

Produce a gap table with three categories:

- adequately automated
- partially automated, still needs manual release pass
- not meaningfully automated and should be considered for follow-up

## Evidence Capture Format

Every QA slice should leave evidence. Use one consistent format.

### For each checked route or workflow

Capture:

- environment
- commit SHA
- actor / role
- workspace state
- route or API
- steps performed
- expected result
- actual result
- screenshots where UI is relevant
- logs or payload excerpts where runtime is relevant
- defect ID if failed

### Suggested evidence buckets

- public-pages
- auth-and-onboarding
- dashboard
- compliance
- mcp
- dummyai-pr-audit
- runtime-and-ops
- regression-summary

## Defect Taxonomy

Use severity levels that make release decisions easier.

### Severity 0

- data loss or security exposure
- broken access isolation
- broken entitlement gating
- broken production-readiness behavior

### Severity 1

- core workflow broken for a release-critical path
- dashboard, onboarding, compliance, or MCP path unusable
- high-visibility UI regression on main operator flows

### Severity 2

- degraded but usable workflow
- inaccurate or confusing copy on important setup-aware pages
- visual regression that harms operator trust but does not fully block work

### Severity 3

- cosmetic or isolated polish issue with no operational impact

The final release call for issue #86 should document not just open bugs, but also why any remaining Severity 2 or Severity 3 items are acceptable or not acceptable for launch.

## Execution Phases

### Phase 1: QA harness and environment preparation

Deliverables:

- commit under test identified
- required envs and secrets confirmed
- evidence location created
- workspace-state matrix prepared
- `dummyAI` validation repo prepared

### Phase 2: Static route and automation audit

Deliverables:

- route inventory completed
- role/state matrix completed
- automated test coverage mapped to route families
- gaps identified before live testing begins

### Phase 3: Public and setup-aware control-plane pass

Deliverables:

- public page validation complete
- login and setup-aware redirect behavior validated
- workspace bootstrap, billing, install, and repo setup flows validated
- UI consistency notes captured

### Phase 4: Authenticated product-shell pass

Deliverables:

- profile, settings, help, policies, MCP, compliance, and admin surfaces checked
- nav, shell, theme, and responsive behavior verified
- role-based control visibility verified

### Phase 5: Dashboard and case-file pass

Deliverables:

- overview and repo dashboards checked across intended states
- deep links and tab navigation verified
- queue, storyline, and evidence rendering checked
- UI polish and readability findings captured

### Phase 6: Compliance and export pass

Deliverables:

- readiness, frameworks, exports, and evidence flows validated
- export generation and download behavior checked
- evidence freshness and governance labeling reviewed

### Phase 7: MCP and customer package pass

Deliverables:

- download package inspected
- docs and manifest consistency confirmed
- token exchange and tool invocation tested
- compatibility behavior validated

### Phase 8: DummyAI PR-audit rehearsal

Deliverables:

- onboarding and baseline state confirmed
- representative PR exercised
- audit result visible in operator surfaces
- narrative quality reviewed

### Phase 9: Runtime and deployment-readiness pass

Deliverables:

- health and readiness checks validated
- production-shape tooling verified
- migration and preflight contract rechecked against docs

### Phase 10: Release decision pack

Deliverables:

- route matrix completed
- defect list triaged by severity
- automation gap summary completed
- go / no-go recommendation written
- explicit follow-ups separated into must-fix vs post-launch

## Exit Criteria

Issue #86 is complete only when all of the following are true:

1. Every major release surface listed in this document has been exercised or explicitly waived with rationale.
2. All Severity 0 and Severity 1 issues found during the pass are fixed or the release is blocked.
3. The UI on primary operator flows is visually coherent and free of obvious release-grade regressions.
4. MCP package and broker flows are verified end to end.
5. `dummyAI` PR-audit rehearsal demonstrates the intended review story end to end.
6. Runtime and deployment guidance still align with the blessed Docker split-service model.
7. The team has a written go / no-go summary grounded in captured evidence.

## Immediate Next Deliverables After This Plan

The first execution slice derived from this plan should produce:

1. a route and state matrix worksheet
2. a role and entitlement matrix worksheet
3. a manual QA checklist ordered by phase
4. an automation coverage gap table
5. an evidence folder structure for screenshots, notes, and logs

That will make the actual QA run operational rather than aspirational.