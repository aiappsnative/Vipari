# Issue 88: Audit Experience Plan

This document turns GitHub issue `#88` into an execution plan for reshaping the repository audit experience into a tighter, decision-first review workflow.

## Goal

Make the repo audit experience answer one reviewer question first:

**What changed, how risky is it, and what should I do now?**

The current repo dashboard already contains most of the right backend primitives. The work in this issue is to reorganize them into a clearer audit mode, a cleaner backend contract, and a more operational review flow.

## Customer Outcome

An operator opening a repo audit should be able to understand the trigger, top evidence, baseline comparison, and recommended action without first parsing a wide repo-posture dashboard.

## Product Hypothesis

If Vipari separates transactional PR audit work from longitudinal repo posture analysis, and if the audit surface is driven by one canonical review object, reviewers will reach a defensible decision faster with less cognitive overhead and less UI branching.

## Current Problem

The current repo case-file experience is strong in raw capability but diffuse in presentation:

- repo review currently lives inside a broader repo dashboard mental model
- the current tab set mixes drift, version control, baseline, compliance, and reports into one surface
- the frontend assembles the audit narrative from many independent global state buckets
- baseline actions are still tied to browser prompt and alert primitives
- the UI makes reviewers infer causality by comparing multiple sections instead of walking one review story

In practical terms, the product already has evidence, provenance, baseline context, and follow-up actions. The missing piece is a dedicated audit information architecture.

## Non-Goals

- no rewrite of the core audit engine or scoring model in this issue
- no removal of repo posture, compliance, or reports capabilities
- no broad route renaming outside the repo audit surface
- no expansion of MCP mutation authority beyond the existing read-first governance posture
- no schema churn unless a dedicated audit contract clearly needs persistence support in a later slice

## Current Owning Surfaces

### Routes and page entrypoints

- `main.py`
  - `dashboard_repo_page(...)`
  - current valid repo tab set: `drift`, `version-control`, `baseline`, `compliance`, `reports`
- `routers/dashboard.py`
  - repo dashboard JSON route
  - repo page route wiring
  - pending proposals route wiring

### Frontend shell and state

- `templates/dashboard_repo.html`
  - current repo audit page shell
  - current tab bar and section layout
- `static/dashboard-repo.js`
  - repo-page state assembly
  - current `window.__*` global state surface
  - tab visibility and deep-link behavior
- `static/dashboard.css`
  - repo page layout and tab styling

### Read models and render helpers

- `services/dashboard_frontend.py`
  - repo page render helper
- `services/dashboard_views.py`
  - repo dashboard view assembly
- `services/dashboard_api_payloads.py`
  - payload shaping for repo and storyline surfaces

### Related workflow surfaces

- `services/baseline_approval_service.py`
  - baseline action primitives and review panels
- `services/mcp_broker.py`
  - current MCP broker tool dispatch and namespace compatibility
- `customer_mcp_server/tool-manifest.json`
  - current shipped MCP surface

### Existing regression anchors

- `tests/test_dashboard_views.py`
- `tests/test_dashboard_api.py`
- `tests/test_dashboard_control_tower.py`
- `tests/test_control_plane_ui.py`
- `tests/test_operator_api.py`

## Design Principles For Issue 88

1. Decision-first beats dashboard-first.
2. PR audit and repo posture are related but not the same job.
3. A single canonical audit object should replace ad hoc frontend assembly where practical.
4. Review actions should be contextual and inline, not browser-prompt driven.
5. MCP parity should follow the same audit contract, but remain read-first and governance-safe.

## Proposed User Model

Issue `#88` should establish two clearly separate review entry points:

| Surface | Primary question | Default evidence | Primary actions |
| --- | --- | --- | --- |
| Audit | Should this change proceed? | PR-linked evidence, diff-to-baseline, top findings, recommendation | approve, flag, request follow-up, request rebaseline |
| Repo posture | Where is this repo drifting over time? | artifact inventory, baseline freshness, governance anomalies, repeated drift | inspect artifacts, refresh baseline, enter audit queue |

The audit page should be optimized for a current trigger; the repo posture surface should remain optimized for longitudinal context.

## Proposed Phase Order

### Phase 1: Define the audit contract

Deliverables:

- add a canonical `audit_brief` read model on the backend
- keep the first version additive so current repo payloads do not break existing UI immediately
- define a minimal shape that the frontend can render with low branching

Recommended `audit_brief` fields:

- trigger source: PR, repo drift alert, pending baseline review, or compliance follow-up
- review target: repo, PR number, head SHA, artifact count
- recommendation: safe to merge, review before merge, rebaseline needed, governance gap, potentially unsafe expansion
- risk summary: severity, confidence, affected dimensions, changed artifact count
- why_now copy: the smallest sufficient escalation explanation
- baseline summary: approved reference, freshness, pending review, repeated drift state
- ordered evidence list: finding title, plain-language explanation, artifact path, provenance links, evidence snippets when available
- next actions: allowed actions for the current role and workspace state

Primary implementation anchors:

- `services/dashboard_views.py`
- `services/dashboard_api_payloads.py`
- repo dashboard JSON route in `routers/dashboard.py`

Validation:

- additive backend payload tests in `tests/test_dashboard_api.py`
- view-model tests in `tests/test_dashboard_views.py`

### Phase 2: Introduce a dedicated Audit route or mode

Deliverables:

- introduce a first-class audit route or equivalent route-sharp mode for repo review
- preserve current deep-link semantics for `artifact`, `pr`, and `head_sha`
- keep repo posture available without forcing all audit work through the current tab model

Recommended route direction:

- preferred: `/dashboard/{repo_full}/audit?pr=...&head_sha=...`
- fallback if route churn is too risky for first slice: dedicated `audit` tab with route-compatible semantics that can later graduate into its own route

Primary implementation anchors:

- `main.py`
- `routers/dashboard.py`
- `services/dashboard_frontend.py`
- `templates/dashboard_repo.html`
- `static/dashboard-repo.js`

Validation:

- repo-page routing and deep-link tests in `tests/test_control_plane_ui.py`
- operator shell and unauthenticated local-mode expectations in `tests/test_operator_api.py`

### Phase 3: Redesign the page around one review story

Deliverables:

- replace the current broad dashboard-first repo layout inside Audit with four fixed blocks:
  - decision header
  - evidence summary
  - change narrative
  - actions
- push broader repo posture and inventory context behind secondary panels or disclosures

Decision header should include:

- repo
- PR number and head SHA when available
- current recommendation
- severity and confidence
- changed artifact count
- plain-language `why you are seeing this`

Evidence summary should include:

- top 3 to 5 ordered findings
- clear artifact links
- supporting provenance

Change narrative should group by decision-relevant dimensions:

- guardrails
- tools and permissions
- autonomy
- model/config
- governance

Primary implementation anchors:

- `templates/dashboard_repo.html`
- `static/dashboard-repo.js`
- `static/dashboard.css`

Validation:

- focused HTML assertions in `tests/test_control_plane_ui.py`
- live browser walkthrough against `/dashboard/{repo}` and the new audit entry path

### Phase 4: Replace prompt and alert baseline actions

Deliverables:

- remove browser `window.prompt` and `window.alert` from baseline governance actions in the audit flow
- replace them with inline review components such as drawers, action sheets, or embedded approval forms
- make reviewers see the target object, rationale, and consequence before mutation

Required UX elements:

- object being approved or rejected
- preset rationale choices plus optional free text
- visible result preview
- validation and error states inline
- audit-trail framing

Primary implementation anchors:

- `static/dashboard-repo.js`
- `templates/dashboard_repo.html`
- baseline action helpers and related mutation routes

Validation:

- focused UI tests for mutation affordances
- negative-path checks for rejected permissions and invalid rationale state

### Phase 5: Introduce disposition state for audit work

Deliverables:

- define explicit audit disposition states: `new`, `in_review`, `waiting_on_owner`, `needs_baseline_decision`, `accepted_risk`, `resolved`
- decide whether the first slice is frontend-only projection or persisted state
- add queue semantics that bridge dashboard triage and repo audit action

Recommended first slice:

- start with persisted state only if it can reuse existing low-risk or proposal-style append-only patterns
- otherwise start with read-model shaping and visible status chips before adding mutations

Primary implementation anchors:

- `services/dashboard_views.py`
- potentially `services/audit_feedback_records.py` or a new append-only audit-disposition record module
- queue and repo detail surfaces

Validation:

- queue ordering and state transition tests
- workspace isolation checks

### Phase 6: Extend MCP with audit-first read contracts

Deliverables:

- expose audit-first read tools that mirror the human audit surface
- keep the design read-first, propose-second, mutate-last

Recommended MCP additions:

- `vipari.get_audit_brief`
- `vipari.list_audit_findings`
- `vipari.get_baseline_diff`
- optional later: `vipari.submit_audit_note`
- optional later: `vipari.request_rebaseline`

Primary implementation anchors:

- `services/mcp_broker.py`
- `customer_mcp_server/tool-manifest.json`
- `customer_mcp_server/vipari_mcp_server.py`
- control-plane MCP integration page and package copy if the shipped surface changes

Validation:

- `tests/test_mcp_broker.py`
- `tests/test_mcp_package.py`
- live package/download sanity checks if the shipped connector changes

## Recommended First Delivery Slice

Do not attempt the entire issue in one pass.

The highest-leverage first slice is:

1. add additive backend `audit_brief` payload support
2. add a dedicated audit route or route-sharp audit mode
3. redesign the main review area into decision header, evidence summary, storyline, and actions
4. preserve existing repo posture tabs for context, but demote them relative to the dedicated audit workflow

This slice gives immediate reviewer clarity without requiring the full queue-state and MCP expansion work in the same PR.

## Recommended Out-Of-Scope For Slice 1

- persistence-backed disposition states if they require new schema and policy design
- full suppression model
- owner handoff workflow
- decision memo export
- full audit timeline experience
- MCP mutation tools

These are valuable follow-ons, but they should not block the first decision-first audit surface.

## Risks And Watchpoints

### 1. Route churn risk

Changing repo audit entry semantics can break deep links or bookmarked review URLs if not handled carefully.

Mitigation:

- preserve current query params
- keep backward-compatible redirects where practical
- maintain existing repo route behavior until the new route is stable

### 2. Frontend state coupling risk

The current repo page relies on many `window.__*` globals.

Mitigation:

- introduce an audit-scoped store before adding more state
- keep drift, baseline, compliance, and reports context isolated from audit-only state

### 3. Audit-vs-posture ambiguity risk

If slice 1 still mixes transactional audit and longitudinal posture too heavily, the UI will improve cosmetically but not operationally.

Mitigation:

- require every new primary panel to justify the current review decision
- push non-decision context behind secondary disclosures

### 4. Governance-action trust risk

Replacing prompts with inline actions must not weaken permission checks or audit trails.

Mitigation:

- keep all current server-side authorization and append-only record expectations intact
- treat the UI change as a presentation upgrade, not a trust-boundary change

### 5. MCP surface creep risk

Audit-first MCP support is useful, but expanding mutation authority too early would create governance risk.

Mitigation:

- keep MCP additions read-first in the first audit slice

## Validation Plan

### Automated

- `pytest tests/test_dashboard_views.py`
- `pytest tests/test_dashboard_api.py`
- `pytest tests/test_dashboard_control_tower.py`
- `pytest tests/test_control_plane_ui.py -k "dashboard or repo"`
- `pytest tests/test_operator_api.py`
- if MCP surface changes: `pytest tests/test_mcp_broker.py tests/test_mcp_package.py`

### Live manual

- load `/dashboard`
- open the repo audit for `doria90/dummyAI`
- verify deep links using `artifact`, `pr`, and `head_sha`
- verify audit-first layout answers trigger, evidence, baseline comparison, and recommendation before broader repo context
- verify owner/admin/viewer role behavior stays intact for any surfaced actions
- verify local operator shell behavior remains understandable in unauthenticated local mode

### Review criteria

Slice 1 is successful when:

- the audit page reads as a review workspace instead of a broad repo dashboard
- a reviewer can explain why the item was escalated without scanning multiple unrelated sections
- baseline actions feel trustworthy and contextual
- existing repo posture, compliance, and reports surfaces remain available without dominating the audit path
- no access-control or deep-link regressions are introduced

## Suggested Branch Name

- `feature/audit-experience-v1`

## Immediate Next Step

Start with a thin design-and-contract PR:

1. add `audit_brief` backend shaping
2. introduce route semantics for a dedicated audit entry point
3. migrate the first page block into a decision header and evidence summary

That keeps the first implementation falsifiable, reviewable, and small enough to validate before pulling baseline action UX and MCP follow-ons into the same branch.