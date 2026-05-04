# Compliance Readiness V1 Slice 1 Checklist

This checklist narrows issue `#76` into the first code-bearing implementation slice.

Read this together with [compliance-readiness-v1-plan.md](./compliance-readiness-v1-plan.md).

## Recommended Branch

- `feature/compliance-readiness-v1`

## Customer Outcome

Compliance users can open `/app/compliance` and understand readiness, top gaps, and next actions without scanning framework cards, dense evidence grids, or export history first.

## Product Hypothesis

If the compliance page is reorganized around a verdict, top gaps, and repo actions, compliance and GRC users will be able to answer "Are we ready, what is missing, and what do I fix next?" faster than they can on the current page.

## Non-Goals For Slice 1

- no persistence schema migration
- no export package format changes
- no dashboard overview or repo-case-file redesign
- no fully interactive compliance frontend application
- no hard dependency on a new blocking-risk signal if no trustworthy source is available

## Locked Defaults For Slice 1

Use these defaults unless a product decision overrides them before coding begins:

- scope = visible repos with stored onboarding state
- freshness buckets stay `Fresh < 7d`, `Aging 7-29d`, `Stale >= 30d`
- top gaps = pending baseline approval, missing governance, stale evidence
- risk column may render `Unknown` in slice 1 if no canonical blocking-risk signal is available
- main page export content = compact summary only, with detailed history moved off the primary view

## Deliverables

### 1. Add a compliance readiness read model

- create `services/compliance_readiness.py`
- define dataclasses for workspace verdict, top gaps, repo rows, and export summary
- centralize readiness computation outside `main.py`

### 2. Refactor `/app/compliance` to use the new read model

- keep the route at `/app/compliance`
- replace scattered helper-driven sections with one readiness-oriented payload
- stop assembling the page directly from evidence-specific HTML fragments

### 3. Redesign the main compliance page layout

- keep the hero or page intro minimal
- add KPI row:
  - repos in scope
  - review-ready repos
  - repos missing governance
  - repos with stale evidence
- add a verdict card with:
  - status
  - concise summary
  - 2-3 bullets
  - one recommended CTA
- add top gaps section capped at three items
- add repo readiness table with:
  - repo
  - baseline
  - governance
  - evidence freshness
  - risk
  - status
  - action
- add compact export summary with:
  - last export state
  - pending count
  - generate export CTA
  - link to detailed export history

### 4. De-emphasize or remove secondary detail from the first screen

- remove framework cards from the main above-the-fold area
- remove detailed AI Act evidence grid from the main readiness flow
- remove detailed evidence freshness card grid from the main readiness flow
- remove long export history table from the main readiness flow
- keep those surfaces available for a later route or secondary section, but not at equal weight with readiness

### 5. Preserve export behavior

- keep `POST /app/compliance/export` working
- keep current preset behavior working
- do not change export job creation semantics in slice 1

## File Checklist

### Backend

- `main.py`
- `services/control_plane_frontend.py`
- `services/compliance_readiness.py` (new)

### Templates

- `templates/control_plane_compliance.html`

### Styles

- `static/dashboard.css`

### Tests

- `tests/test_control_plane_ui.py`
- add new readiness-model tests, likely under `tests/`

## Suggested Task Order

1. create the read-model dataclasses and builder functions
2. write unit tests for readiness aggregation before touching the template
3. switch `/app/compliance` to consume the new read model
4. replace the main template layout with the new readiness-first structure
5. update control-plane UI tests to the new page contract
6. rerun export-flow tests to ensure the existing submit behavior still works

## Readiness Rules To Implement In Slice 1

### Repo status

`Ready` when:

- onboarding baseline is approved
- governance artifact family is present
- freshness is not stale

`Needs work` when:

- no blocking condition exists, but the repo is not fully ready
- likely examples: aging evidence or incomplete but non-blocking state

`Blocked` when:

- baseline approval is missing
- governance artifact family is missing
- evidence is stale

### Workspace verdict

Initial guidance:

- `Review-ready` when the majority of in-scope repos are `Ready` and no severe concentration of blocked repos exists
- `Needs work` when the workspace has manageable remediation gaps but not a severe blocked state
- `High-risk evidence gap` when blocked repos materially dominate readiness

The exact thresholds should be implemented in one place inside the read-model builder and covered by tests.

## Top Gap Ranking Rules

The first slice should rank only the three clearest remediation categories:

1. pending baseline approvals
2. missing governance artifacts
3. stale evidence

Ranking should sort by affected repo count descending and then by a stable priority order when counts tie.

## Copy Constraints

- use operational language: `Readiness`, `Top gaps`, `Affected repos`, `Next action`
- avoid leading with framework jargon on the first screen
- keep each gap explanation to one or two sentences max
- keep the verdict CTA singular and actionable

## Validation Plan

### Unit tests

- repo readiness = approved baseline + governance + fresh evidence
- repo blocked = missing governance
- repo blocked = pending baseline approval
- repo blocked = stale evidence
- workspace verdict from mixed repo sets
- top-gap cap and ordering

### UI tests

- `/app/compliance` renders KPI row, verdict card, top gaps, and repo table
- old framework-heavy sections are no longer primary assertions on the main page
- export CTA still exists
- selected-repo and preset export submission still works
- failed export state remains retryable

### Focused regression commands

- `pytest tests/test_control_plane_ui.py -k "compliance_page or compliance_export"`
- readiness-model test module once added

## Exit Criteria

- `/app/compliance` answers readiness, top gaps, and next actions without requiring users to parse secondary evidence sections first
- compliance readiness is computed by a dedicated service module rather than scattered helper functions
- export submission behavior remains intact
- the first-screen UI is visibly less noisy than the current page
- tests cover the new readiness rules and updated UI contract

## Follow-On Work After Slice 1

- add `/app/compliance/frameworks`, `/app/compliance/exports`, and `/app/compliance/evidence`
- add compact `/api/compliance/*` endpoints
- add canonical blocking-risk integration if a trustworthy source is identified
- add filtered drill-downs from top-gap CTAs