# Session Handoff: `repo-provenance-v1`

## Current state

As of 2026-03-23:

- `feature/pr-escalation-v1` has been merged into `main`
- `feature/approved-baseline-v1` has been merged into `main`
- `main` now includes:
  - managed PR comments with explicit escalation guidance
  - GitHub escalation label support for high-confidence before-merge cases
  - reviewer-focused PR comments without internal drift metrics
  - approved-baseline provenance persisted for PR and historical profile records
  - dashboard/history provenance exposure for approved vs fallback baseline context
- cleanup is complete:
  - merged feature branches were deleted locally and remotely
  - roadmap/docs were refreshed on `main`

Current `main` head:

- `e3c05bc` — `docs: refresh roadmap after baseline merge`

## Next planned slice

Name:

- `feature/repo-provenance-v1`

Purpose:

- enrich repo-detail and history views with stronger reviewer context about where drift came from, who changed it, and under what review context

Strategic source:

- [Plan.MD](../Plan.MD)
- [SOUL.md](../SOUL.md)
- [docs/detection-engine-plan.md](detection-engine-plan.md)

## Why this is next

The product wedge is now materially real:

- PromptDrift can detect risky AI control-surface changes in PRs
- it can recommend escalation before merge
- it can apply a GitHub label for high-confidence escalation cases
- it can compare against an approved baseline model rather than only implicit prior versions

The next trust gap is reviewer context.

Right now the system can say a control surface drifted from an approved or fallback baseline, but repo-detail views still need to answer the practical reviewer questions more directly:

- who changed this?
- when did it change?
- did this come from historical backfill or an actual PR review event?
- what review path or provenance context produced the current posture?

## Product target for this slice

PromptDrift should move from:

- "this artifact drifted from baseline"

to:

- "this artifact drifted from baseline, and here is the concrete review/history context for that drift"

The goal is not more top-level metrics. The goal is better reviewer decision support.

## In-scope outcomes

The first pass should focus on repo-detail and history read models.

Target outcomes:

1. clearer provenance labels in repo-detail design cards
2. artifact timeline points that tell reviewers whether a point came from:
   - historical backfill
   - pull-request audit
   - approved baseline context
3. where available, attach source metadata such as:
   - PR number
   - commit SHA/date
   - author or actor if already available cheaply
4. improve narratives so a reviewer can answer:
   - what changed
   - when it changed
   - in what review context it changed

## Out of scope

Do not let this slice sprawl into workflow or platform work.

Explicitly out of scope:

- a full baseline approval workflow or approval UI
- merge blocking or branch protection integration
- major dashboard redesign or new top-level panels
- runtime telemetry or observability work
- signal-fusion algorithm redesign
- production persistence/deployment hardening

## Recommended implementation order

### 1. Map existing provenance fields

Inspect where current provenance already exists in:

- dashboard read models
- timeline builders
- PR audit persistence
- onboarding and historical backfill records

Goal:

- identify what metadata is already available versus what is only implied

### 2. Define the minimal provenance read contract

Add or normalize a compact model that can express:

- source type (`pull_request`, `historical`, `approved_baseline`)
- human-readable label
- created-at timestamp
- source review reference where available (`PR #`, commit, actor)

Keep it read-model focused.

### 3. Improve repo-detail rendering semantics

Update repo-detail views so artifact posture cards and timelines tell a cleaner story:

- current posture relative to approved baseline
- most recent source event
- whether the current posture came from a PR audit or only historical backfill

### 4. Add regression coverage before widening scope

Tests should lock in:

- stable provenance labels in dashboard payloads
- graceful behavior when older records lack newer provenance fields
- repo-detail history points remain readable and deterministic

## Likely files to inspect first

Core candidates:

- `services/dashboard_views.py`
- `services/audit_records.py`
- `services/onboarding_records.py`
- `tests/test_dashboard_views.py`
- `tests/test_dashboard_api.py`
- `tests/test_audit_history.py`
- `tests/test_operator_api.py`

## First concrete questions to answer next session

1. What provenance metadata do we already persist but not surface?
2. Which repo-detail cards are most important for reviewer trust?
3. Can we add actor/author context cheaply from current data, or should that wait?
4. What is the smallest provenance payload that improves reviewer understanding without adding UI debt?

## Suggested first move next session

1. create `feature/repo-provenance-v1`
2. map current provenance fields and read paths
3. define the minimal repo-detail provenance contract
4. update dashboard/read models
5. add regression coverage

## Definition of a good first pass

A successful first pass should:

- make repo-detail provenance more concrete and reviewer-usable
- improve trust without adding PR noise
- keep the change read-model focused
- avoid starting the explicit approval workflow too early
- leave `baseline-approval-v1` smaller and clearer as the next step after this one
