# Session Handoff: `approved-baseline-v1` (Archived)

> Archive note: this slice has already been merged into `main`. This document is preserved as historical implementation context only. For the current roadmap, see [Plan.MD](../Plan.MD). For shipped outcomes, see [CHANGELOG.md](../CHANGELOG.md).

## Archived state

As of 2026-03-22:

- `feature/pr-escalation-v1` has been merged into `main`
- `main` now includes:
  - comment + label escalation
  - richer semantic reviewer detail
  - a canonical PR comment format baseline
- live validation succeeded on `dummyAI`

Current `main` head after merge:

- `6cb394b` — merge of `feature/pr-escalation-v1`

## Historical planned slice

Name:

- `feature/approved-baseline-v1`

Purpose:

- make baseline-relative drift trustworthy by defining one authoritative baseline model across PRs, dashboards, and history views

Strategic source:

- [Plan.MD](../Plan.MD)
- [docs/drift-profile-design-spec.md](drift-profile-design-spec.md)

## Why this is next

The PR wedge is now materially real:

- PromptDrift can detect risky AI control-surface changes
- it can escalate in the PR itself
- it can label high-confidence cases
- it now presents comments in a stable canonical format

The next trust gap is baseline ambiguity.

Right now PromptDrift can show drift, but the product becomes much more credible when it can clearly answer:

- drift relative to what?
- was that baseline explicitly approved?
- is this comparison using an onboarding fallback, current main, or a true approved reference?

## Product target for this slice

PromptDrift should move from:

- "this changed from an earlier stored version"

to:

- "this changed from the latest explicitly approved version of this control surface"

with transparent fallback behavior when no approved baseline exists yet.

## Baseline model to introduce

At minimum, the system should distinguish:

- `approved_baseline`
- `onboarding_baseline`
- `historical_reference`
- `current_main_reference` if still needed during transition

The user-visible requirement is that PR comments, dashboard views, and history views should not imply equal authority across those baseline types.

## Recommended implementation order

### 1. Define the baseline provenance contract

Add an explicit model for baseline provenance that can be reused by:

- audit persistence
- static drift profile comparisons
- dashboard read models
- repo-detail views

The first pass should answer:

- baseline type
- baseline source version/profile id
- whether the baseline is authoritative or fallback
- a short reviewer-facing label

### 2. Trace current baseline selection paths

Inspect how baselines are currently chosen in:

- static drift profile persistence
- audit record persistence
- dashboard read models
- onboarding/backfill flows

Goal:

- identify every place where the product currently assumes "previous version" means baseline

### 3. Introduce explicit approved-baseline selection

First pass behavior should likely be:

- if an approved baseline exists for an artifact, use it
- otherwise fall back to onboarding baseline or historical reference
- persist which path was used

Avoid a heavy approval workflow UI in this slice.

### 4. Surface provenance in read models

Update reviewer-facing outputs so they can say things like:

- compared to approved baseline
- compared to onboarding baseline
- compared to historical fallback

This should be visible in:

- PR drift summaries where feasible
- repo detail pages
- artifact history views

### 5. Add regression tests before UI widening

Tests should lock in:

- approved baseline wins over generic previous-version selection
- fallback behavior is explicit and stable
- dashboard/read-side labels match persisted provenance
- old data degrades gracefully when baseline provenance fields are absent

## Likely files to inspect first at the time

Core candidates:

- `engine/drift_profile.py`
- `services/audit_records.py`
- `services/dashboard_views.py`
- `services/onboarding.py`
- `services/onboarding_records.py`
- `tests/test_audit_worker.py`
- `tests/test_dashboard_views.py`
- `tests/test_onboarding.py`
- `tests/test_audit_history.py`

## First concrete questions captured at the time

1. Where is the effective baseline selected today for static drift deltas?
2. What persistence model is the smallest change that can carry baseline provenance cleanly?
3. Which existing user-facing surfaces already imply a baseline, and how should they label it?
4. What is the smallest explicit approval concept that creates trust without adding workflow bloat yet?

## Suggested first move at the time

1. create `feature/approved-baseline-v1`
2. map all current baseline-selection code paths
3. design the persisted baseline-provenance shape
4. implement the authoritative baseline selection logic
5. then update read models and tests

## Definition of a good first pass

A successful first pass should:

- make baseline choice explicit in the code and persisted records
- prefer approved baselines when present
- preserve stable behavior when no approved baseline exists yet
- expose provenance clearly enough that reviewers understand the authority of the comparison
- avoid introducing a heavyweight governance workflow too early
