# Compliance Readiness V1 Plan

This document turns GitHub issue `#76` into an implementation-ready plan for redesigning DriftGuard's compliance surface around readiness, gaps, and next actions.

## Goal

Replace the current mixed compliance workspace page with a readiness-first operating surface that answers three questions immediately:

- are we review-ready
- what is missing
- what should we fix next

The redesigned page should feel like a compliance decision surface rather than a generic GRC wall or a combined export console plus evidence report.

## Scope

Issue `#76` is not just a visual refresh. It requires:

- a new compliance readiness read model
- a simplified primary compliance page at `/app/compliance`
- progressive disclosure through secondary compliance routes or tabs
- preservation of the existing export workflow while moving it out of the primary visual focus
- test coverage updates for the new page contract and readiness logic

Files likely in scope:

- `main.py`
- `services/control_plane_frontend.py`
- `templates/control_plane_compliance.html`
- `static/dashboard.css`
- a new compliance read-model service, likely `services/compliance_readiness.py` or `services/compliance_views.py`
- `tests/test_control_plane_ui.py`
- new or expanded tests for compliance readiness aggregation and compact compliance APIs

Files likely out of scope unless a blocker is found:

- repository onboarding persistence schema
- export package file formats in `services/compliance_export_service.py`
- dashboard repo and overview frontend files
- customer control-plane auth and billing flows unrelated to compliance routing

## Current Reality On `main`

The current compliance page already contains useful signals, but they are assembled as a long, equal-weight control-plane page.

Current route and render path:

- `GET /app/compliance` in `main.py`
- `render_control_plane_compliance_page(...)` in `services/control_plane_frontend.py`
- `templates/control_plane_compliance.html`
- compliance styling embedded in `static/dashboard.css`

Current primary sections on the page:

- hero copy and scope note
- four summary stats: plan, tracked repos, approved baselines, exports ready or pending
- standards framework cards
- EU AI Act relevance assessment grid
- evidence gaps grid
- evidence freshness grid
- export creation form
- workspace export history table

Current supporting helper logic in `main.py`:

- `_render_compliance_ai_act_assessment(...)`
- `_render_compliance_evidence_gaps(...)`
- `_render_compliance_evidence_freshness(...)`
- `_render_compliance_repo_rows(...)`
- `_render_compliance_export_history(...)`
- `_compliance_export_preset_repo_fulls(...)`

Current readiness logic is implicit rather than modeled explicitly:

- approved baseline comes from onboarding status
- governance coverage comes from onboarded artifact families
- freshness comes from onboarding timestamps with the current `fresh`, `aging`, and `stale` buckets
- export readiness comes from per-workspace export job state

The page therefore already contains the raw ingredients for readiness, but not a single readiness verdict or prioritized remediation model.

## Product Intent

The new compliance experience should behave like a readiness console for GRC, security, legal, and engineering leads.

The first screen should answer, without scrolling:

1. are we ready for review or export
2. what are the top gaps blocking readiness
3. which repositories should we fix first

Everything else should become secondary detail.

## Design Constraints

- Keep the main page operational and compact rather than narrative-heavy.
- Preserve the current `/app/compliance/export` flow during the first implementation slice.
- Prefer server-rendered HTML for the initial redesign unless a clear interaction requires client-side filtering.
- Keep URLs stable where possible: `/app/compliance` should remain the primary entry.
- Use progressive disclosure rather than deleting framework or evidence detail.
- Do not invent legal classification language on the main screen.
- Use one severity vocabulary consistently: `Ready`, `Needs work`, `High-risk gap` or an equivalent final set.

## Proposed Information Architecture

### Primary route

- `/app/compliance`

This route becomes the readiness-first main view.

### Secondary routes

- `/app/compliance/frameworks`
- `/app/compliance/exports`
- `/app/compliance/evidence`

These may initially be separate server-rendered pages sharing one renderer family, or a tabbed view if that is materially cheaper. Separate routes are preferred because the current page already carries enough density that route-level separation is cleaner and more testable.

## What Stays On The Main Page

Readiness-critical content only:

- a compact KPI row
- a readiness verdict card
- a top gaps section capped at three items
- a repo readiness table with one primary action per row
- a compact export summary with last export state and one primary export CTA

## What Moves Off The Main Page

Supporting detail:

- framework-specific narrative cards
- AI Act-specific explanatory grids
- detailed evidence freshness cards
- detailed evidence-gap narratives by repo
- long export history
- verbose repo selection and preset explanation text

## Readiness Model

The redesign needs an explicit compliance read model instead of scattered HTML helper logic.

Recommended repo readiness rules for the first implementation:

### `Ready`

A repo is `Ready` when all of the following are true:

- approved baseline exists
- governance artifact coverage exists
- evidence freshness is within the configured fresh or acceptable window
- no blocking risk signal is present

### `Needs work`

A repo is `Needs work` when:

- the repo is not fully blocked, but one readiness dimension is weak
- examples: aging evidence, baseline approved but governance weak, or evidence present but recent risk needs follow-up

### `Blocked`

A repo is `Blocked` when at least one blocking condition is present:

- no approved baseline
- missing governance artifact
- stale evidence beyond the threshold
- blocking risk signal present

### Workspace verdict

The main verdict should be derived from the repo set and exposed as one of:

- `Review-ready`
- `Needs work`
- `High-risk evidence gap`

The verdict card should include:

- one status label
- two or three explanatory bullets
- one recommended next step

## Read Model Proposal

Add a new service module for compliance aggregation.

Preferred location:

- `services/compliance_readiness.py`

Suggested dataclasses:

- `ComplianceWorkspaceView`
- `ComplianceVerdict`
- `ComplianceGapItem`
- `ComplianceRepoReadinessRow`
- `ComplianceExportSummary`
- `ComplianceFrameworkDetailView`
- `ComplianceEvidenceDetailView`

Suggested workspace payload shape:

- `kpis`
  - repos_in_scope
  - review_ready_repos
  - repos_missing_governance
  - repos_with_stale_evidence
- `verdict`
  - status
  - headline
  - bullets
  - recommended_action
- `top_gaps`
  - key
  - title
  - explanation
  - affected_repo_count
  - cta_label
  - filter_key
- `repo_rows`
  - repo_full
  - baseline_status
  - governance_status
  - evidence_freshness_status
  - blocking_risk_status
  - overall_status
  - recommended_action
  - target_href
- `export_summary`
  - last_export_at
  - last_export_status
  - pending_export_count
  - history_href

## Blocking Risk Signal

This is the only major readiness dimension not already modeled explicitly on the compliance page.

The implementation should not guess here. It needs a canonical source, ideally one of:

- latest repo governance posture from dashboard read models
- latest unresolved high-priority repo insight
- latest active escalation or merge-block recommendation from PR audit state

If no stable source exists yet, the first implementation slice may:

- ship readiness without blocking-risk in the verdict formula
- expose risk as `unknown` in the repo table
- add blocking-risk integration as a second slice

That is preferable to baking in an untrustworthy risk heuristic.

## API Plan

Issue `#76` explicitly calls for compact compliance APIs.

Recommended endpoints:

- `GET /api/compliance/readiness`
- `GET /api/compliance/repos?filter=missing_governance|pending_baseline|stale_evidence|blocking_risk`
- `GET /api/compliance/exports`
- `GET /api/compliance/frameworks`
- `GET /api/compliance/evidence`

Why add them even if the first UI remains server-rendered:

- they isolate readiness computation from HTML assembly
- they make top-gap drill-downs cheap and predictable
- they create a durable contract for later frontend evolution
- they keep `main.py` from accumulating more compliance-specific business logic

## UI Structure For `/app/compliance`

Recommended order:

1. KPI row
2. readiness verdict card
3. top gaps section
4. repo readiness table
5. compact export summary

### KPI row

Values:

- repos in scope
- review-ready repos
- repos missing governance
- repos with stale evidence

### Verdict card

Contents:

- one readiness label
- concise summary copy
- two or three bullets based on current workspace state
- one recommended CTA

### Top gaps section

Constraints:

- maximum of three rows or cards
- each one should contain count, short explanation, and a CTA
- CTA should open the filtered repo subset or corresponding remediation page

Suggested initial gap categories:

- missing governance artifacts
- pending baseline approvals
- stale evidence

Optional fourth category if blocking risk is available:

- blocking risk requiring review

### Repo readiness table

Columns:

- repo
- baseline
- governance
- evidence freshness
- risk
- status
- action

The action should be singular and operational:

- approve baseline
- add governance artifact
- refresh evidence
- review risk

### Export summary

Keep on the main page:

- last export status
- last export time
- pending export count
- primary button: generate export
- secondary link: view export history

Move full export history off the main page.

## Frameworks, Exports, and Evidence Detail Pages

### Frameworks page

Purpose:

- explain framework-specific views such as AI Act, SOC 2, and ISO 27001
- show mappings and detailed posture breakdowns
- preserve regulatory and standards detail without crowding the readiness page

### Exports page

Purpose:

- host full workspace export history
- show per-export status, time range, mode, and download links
- optionally keep the advanced export form here after the first slice

### Evidence page

Purpose:

- show detailed evidence freshness narratives
- show repo-by-repo evidence gaps in full detail
- preserve the current informational cards in a less intrusive place

## Execution Phases

### Phase 1: Introduce readiness model and simplify the main page

Deliverables:

- add compliance readiness aggregation service and dataclasses
- refactor `/app/compliance` to use one workspace readiness view
- replace the current long-page structure with KPI row, verdict, top gaps, repo table, and export summary
- keep existing export POST flow working
- move existing framework and evidence sections out of the primary page content

Why first:

- this lands the core value of the issue immediately
- it avoids getting stuck in secondary-detail migrations before the primary question is solved

### Phase 2: Add secondary compliance routes

Deliverables:

- add Frameworks, Exports, and Evidence routes
- move current secondary content into those routes
- add simple navigation across compliance sub-pages

Why second:

- once the main page is clear, the secondary material can be reorganized without slowing the readiness redesign

### Phase 3: Add filtered drill-downs and compact compliance APIs

Deliverables:

- add dedicated compliance JSON endpoints
- wire top-gap CTAs to filtered repo subsets
- reduce coupling between compliance HTML and `main.py` helper strings

### Phase 4: Integrate blocking-risk readiness and polish copy

Deliverables:

- wire in the canonical blocking-risk source if available
- tighten wording for compliance, GRC, and security stakeholders
- refine responsive table and mobile layout behavior

## Real Code Touchpoints

Primary backend touchpoints:

- `main.py`
- `services/control_plane_frontend.py`
- new compliance read-model module under `services/`

Primary frontend touchpoints:

- `templates/control_plane_compliance.html`
- likely new templates for compliance secondary pages
- `static/dashboard.css`

Primary tests to update:

- `tests/test_control_plane_ui.py`
- new tests for compliance readiness aggregation
- API tests for compliance JSON endpoints if added in the same slice

Existing tests that must be preserved conceptually:

- compliance page renders for a workspace with repo and export state
- compliance export submission works for selected repos
- compliance export failure remains retryable and visible
- review-ready export preset still narrows correctly

## Proposed Test Plan

### Readiness aggregation tests

Add focused unit tests covering:

- repo readiness status with approved baseline plus governance plus fresh evidence
- repo blocked by missing governance
- repo blocked by pending baseline
- repo blocked by stale evidence
- workspace verdict calculation from mixed repo states
- top-gap ranking and cap at three items

### UI tests

Update control-plane UI coverage to assert:

- main compliance page renders verdict card and top gaps
- full framework and export-history content is no longer on the primary page when moved
- repo table includes expected action labels
- filtered or secondary links point to the correct compliance routes

### API tests

If APIs land in the same branch:

- readiness payload shape
- filtered repo payload shape
- workspace-scoped export summary payload
- workspace isolation for all compliance endpoints

## Known Risks

- the current compliance page is built from HTML helpers in `main.py`, so partial refactors can leave the route in an awkward half-old, half-new state if aggregation is not moved cleanly into a service module
- export creation is currently embedded in the main page, so moving it visually without breaking the POST flow requires careful route and template separation
- blocking risk is not yet a clearly named compliance input, so the redesign can overpromise if that signal is improvised
- `tests/test_control_plane_ui.py` currently asserts on current copy and section names, so the redesign will need deliberate test rewriting rather than superficial string tweaks

## Recommended Branch

- `feature/compliance-readiness-v1`

## Exit Criteria For The First Code Slice

The first implementation slice should be considered complete when:

- `/app/compliance` answers readiness, top gaps, and next actions without scrolling past secondary detail
- a dedicated compliance readiness read model exists outside `main.py`
- the current export flow still works
- the repo readiness table exists with baseline, governance, freshness, and action columns
- the previous framework or evidence-heavy sections no longer dominate the first screen

## Open Product Decisions To Resolve Before Coding

These choices should be made explicitly before implementation starts:

1. What counts as `repos in scope`: visible repos, onboarded repos, or only allocated repos with stored evidence?
2. Should the current freshness thresholds remain `fresh < 7d`, `aging 7-29d`, `stale >= 30d`?
3. What is the canonical blocking-risk signal for compliance readiness?
4. Is `missing model/config artifact` still a top-level gap, or should it move into supporting detail?
5. Should the main page show workspace-wide export history summary, requester-scoped summary, or only the last export state?

## Suggested Defaults If No New Product Decision Is Made

- scope = visible repos with stored onboarding state for readiness calculations
- keep the existing freshness buckets for the first slice
- top gaps = pending baseline approval, missing governance, stale evidence
- blocking risk = deferred to phase 4 unless a stable existing source is identified
- export summary on the main page = last export state plus pending count, with full history moved to `/app/compliance/exports`

## Planning Note

This issue should be treated as a compliance information-architecture and read-model slice, not only a visual redesign. The right implementation is a readiness-first page backed by an explicit compliance aggregation layer, with the current page's framework, evidence, and export details redistributed into secondary surfaces.