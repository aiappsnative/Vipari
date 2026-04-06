# Dashboard Redesign V2 Plan

This document turns GitHub issue `#20` into an execution plan for the next frontend-only PromptDrift slice.

## Goal

Replace the current document-style dashboard with a triage-first operator shell that answers one question immediately:

- what needs attention right now, and why

The redesign should reduce scroll depth, remove the current `Triage` / `Coverage` mode split, and make the next action obvious on both the portfolio view and the per-repo case-file view.

## Scope

Issue `#20` is explicitly a frontend-only redesign.

Files in scope:

- `templates/dashboard_index.html`
- `templates/dashboard_repo.html`
- `static/dashboard.css`
- `static/dashboard-index.js`
- `static/dashboard-repo.js`

Files out of scope unless a blocker is discovered:

- Python route handlers
- services / read-model logic
- API response shapes
- database or persistence code

## Current Reality On `main`

The current dashboard already has stronger triage content than the older version, but its structure is still page-like rather than app-like:

- the index page still starts with wrapper + hero copy + mode switch
- the repo page still uses card stacks and collapsible secondary context
- the CSS contains a large legacy surface for wraps, hero sections, mode switching, and multiple overlapping layout systems
- the JS data layer is good enough to keep, but the render layer is tightly coupled to the old DOM structure

That means the right plan is to preserve data loading and rewrite the shell and render targets in phases.

## Product Intent

The redesign should behave like an operator tool, not a narrative report.

The primary interaction should be:

1. user lands on the page
2. first triage row is already selected
3. detail panel already explains the risk
4. one clear action is visually emphasized

Everything else should be secondary context, reachable without dominating the primary view.

## Design Constraints

- Keep the redesign dark, dense, and restrained rather than decorative.
- Use the token system defined in issue `#20` as the new CSS source of truth.
- Preserve API compatibility and existing fetch flows.
- Keep icons decorative only; labels and severity text must stay explicit.
- Make keyboard navigation first-class for triage rows.
- Avoid a half-migrated UI where old hero/mode/collapsible patterns survive beside the new shell.

## Execution Phases

### Phase 1: Shared shell and tokens

Deliverables:

- introduce shared `app-shell`, `sidebar`, `main-content`, and `posture-strip` structure in both templates
- load Inter and Lucide in both templates
- replace top-level page wrappers and remove the old hero / topbar / mode-switch framing
- install the issue-defined design tokens at the top of `static/dashboard.css`

Why first:

- this creates the structural base for both dashboards before any rendering logic is moved
- it isolates the highest-risk CSS reset work early

### Phase 2: Dashboard index migration

Deliverables:

- replace the current triage-mode layout with the new fixed two-column split
- render the ranked queue into the new `.triage-row` list structure
- auto-select the first row on load
- populate the right-side detail panel from existing index-page data
- condense coverage and risk views into the new secondary row and repo table

Notes:

- keep the current data-fetching and risk-state logic
- rewrite render targets rather than trying to reuse the old card markup directly

### Phase 3: Repo case-file migration

Deliverables:

- apply the same sidebar and posture strip shell to the repo page
- convert repo insights into the same triage-row selection pattern used on the index
- move selected-item explanation into the right-side detail panel
- relocate secondary context into the new secondary row and artifact table
- remove the old lower-confidence and history `<details>` card patterns from the primary view

Notes:

- the recently added storyline work must remain reachable in the redesign, but not necessarily in the same card layout
- artifact inventory and deep history should move into lower-emphasis sections consistent with the issue

### Phase 4: CSS deletion and interaction hardening

Deliverables:

- delete obsolete classes called out in the issue: wraps, hero classes, mode switch, inbox/casefile layouts, section kickers, hints, collapsible-card patterns, info strip, and related variants
- add skeleton loading states instead of text-only loading placeholders
- ensure hover, selected, focus-visible, and keyboard states are complete
- make the sidebar tooltips and stub navigation items behave consistently

Why last:

- deleting old CSS too early makes it harder to migrate safely
- by this phase the new DOM structure should already be stable

## Real Code Touchpoints

Primary files to edit:

- `templates/dashboard_index.html`
- `templates/dashboard_repo.html`
- `static/dashboard.css`
- `static/dashboard-index.js`
- `static/dashboard-repo.js`

Likely no-change but must be respected:

- `main.py`
- `services/api_service.py`
- `services/dashboard_views.py`

## Known Risks

- `static/dashboard.css` is already broad, so a token reset can easily create regressions across both pages if done without phased deletion
- the index and repo pages currently use different component vocabularies, so forcing a shared triage/detail pattern will need careful DOM normalization
- the repo page now contains storyline-specific interactions that must survive the redesign rather than being accidentally dropped
- issue `#20` asks for zero backend changes, so any missing frontend data must be handled by reuse or composition, not by casually extending APIs

## Recommended Branch

- `feature/dashboard-redesign-v2`

## Exit Criteria For First Implementation Slice

The first code-bearing slice should be narrow but vertical:

- new shell and posture strip exist on both pages
- dashboard index renders the new triage list and right-side detail panel
- first item auto-select works
- old mode switch is removed
- CSS token system is in place
- no backend files are changed

That gets the redesign moving without trying to land the entire issue in one jump.

## Suggested Validation

- manual browser pass on `/dashboard`
- manual browser pass on `/dashboard/{owner}/{repo}`
- verify first-row auto-selection and keyboard selection behavior
- verify existing API calls still populate the new DOM targets
- run any frontend-focused regression checks already used in the dashboard workflow

## Planning Note

The numbered roadmap entry in `Plan.MD` previously used `20` for a different optimization item. For upcoming work, refer to this explicitly as GitHub issue `#20` or `feature/dashboard-redesign-v2` to avoid ambiguity.
