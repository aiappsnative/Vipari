# Drift Storyline V1 Plan

This document turns issue `#17` into an implementation plan for `feature/drift-storyline-v1`.

## Goal

Make repo case files explain how an AI control surface evolved over time, not just what its current posture looks like.

The first release should help a reviewer answer:

- what the approved baseline was
- which major drift episodes happened after that baseline
- which attributes changed in each episode
- whether the artifact is repeatedly weakening, expanding, or oscillating
- what changed most recently and what deserves attention now

## Acceptance Mapping

Issue `#17` requires five concrete outcomes:

1. Featured artifacts on repo case-file pages show a visible `Drift storyline`.
2. Each storyline includes baseline, major episodes, and current posture.
3. Episode items show date, type, top attribute deltas, and provenance when available.
4. Sparse-history repos degrade safely with explicit disclosure.
5. Repo-level history cues help prioritize repeated or stale-drift artifacts.

The branch plan below is organized to satisfy those outcomes directly.

## Current Groundwork Already Present

The codebase already has useful primitives that make this feature incremental rather than greenfield:

- persisted static profile history for PR audits and historical backfill
- approved-baseline provenance carried through read models
- repo case-file dashboard payloads built in `services/dashboard_views.py`
- a lightweight artifact history timeline already rendered on the repo dashboard
- episode-aware PR comment lifecycle keyed by `head_sha`
- provenance links for PRs and commits in repo/detail views

What is still missing is a normalized read model that turns raw history points into a reviewer-facing storyline.

## Proposed Read Model

Introduce a `DriftEpisode` read-model concept on the dashboard/read side.

Recommended fields:

- `artifact_path`
- `artifact_type`
- `repo_full`
- `episode_timestamp`
- `source_type` (`pr`, `baseline_promotion`, `historical_backfill`, `unknown`)
- `source_ref`
- `source_url`
- `episode_type` (`guardrail_regression`, `guardrail_improvement`, `capability_expansion`, `capability_reduction`, `autonomy_increase`, `governance_shift`, `mixed`)
- `attribute_deltas`
- `top_attributes`
- `episode_summary`
- `severity`
- `confidence`
- `limited_history`

This should be a read-model abstraction, not a new core engine dependency. The first slice should derive episodes from persisted profile history rather than introduce a new write path.

## Product Surfaces

### Repo case file

Add a `Drift storyline` section for the featured artifact.

It should render:

- baseline promotion or baseline reference milestone
- major subsequent episodes in chronological order
- latest current posture marker
- a compact story summary above the timeline
- explicit `limited history available` disclosure when continuity is sparse

### Artifact-level inspection

Expose fuller episode history behind an artifact expansion path or dedicated API-backed detail panel.

The initial repo page should stay compact. Full episode history should be lazy-loaded or separately fetched.

### Repo-level history cues

Add small prioritization blocks, not a metrics wall:

- top repeated-drift artifacts
- longest time since approved baseline
- most mixed-direction artifacts
- artifacts whose latest episode is high severity

## Implementation Plan

### Phase 1: Normalize episodes on the read side

- add `DriftEpisode` and any supporting summary dataclasses in `services/dashboard_views.py` or a small adjacent read-model module
- build a helper that merges history from PR-side profile records, historical backfill records, and baseline promotions into ordered episodes
- classify `episode_type` from attribute deltas and fallback to `mixed` when multiple important dimensions move together
- collapse repeated low-signal same-dimension events when they are temporally close and materially similar

### Phase 2: Extend repo dashboard payloads

- add storyline payloads for the featured artifact to `RepoDashboardView`
- add repo-level history cue aggregates
- keep the existing lightweight timelines available until the frontend transition is complete
- preserve safe fallback when history is sparse or provenance is incomplete

### Phase 3: Expose artifact episode API

- add a dedicated endpoint under `services/api_service.py` for artifact episode history
- return ordered episodes with provenance URLs and limited-history disclosure
- keep the repo dashboard payload compact by avoiding full-history fanout in the main response

### Phase 4: Frontend rendering

- update `templates/dashboard_repo.html` to include a `Drift storyline` section in the featured case-file area
- update `static/dashboard-repo.js` to render storyline cards or timeline items with date, type, top deltas, summary, and provenance links
- keep the visual language consistent with the current case-file design instead of adding chart-heavy chrome

### Phase 5: Tests and performance hardening

- extend dashboard read-model tests for sparse, repeated-drift, mixed-history, and baseline-refresh scenarios
- extend API tests for artifact episode responses
- verify repo detail rendering still degrades gracefully on sparse local datasets
- review whether additional indexes or lightweight read-side caching are needed before shipping on large OSS histories

## Recommended Code Touchpoints

- `services/dashboard_views.py`
- `services/api_service.py`
- `static/dashboard-repo.js`
- `templates/dashboard_repo.html`
- `services/audit_records.py`
- `services/onboarding_records.py`
- `tests/test_dashboard_views.py`
- `tests/test_dashboard_api.py`
- `tests/test_audit_history.py`

## Design Constraints

- Do not redesign PR comments in this issue.
- Do not mix proposal-only PR audit evidence into landed-history posture without clear labeling.
- Do not render raw event dumps; storyline items should group low-signal history when appropriate.
- Do not fabricate continuity when provenance or history is incomplete.
- Do not move dashboard product logic back into route handlers or inline HTML strings.

## Open Decisions

These questions should be resolved during implementation, not before starting:

1. Whether `DriftEpisode` stays entirely read-side or earns durable persistence in a later slice.
2. Whether baseline promotion events should be synthesized from existing baseline rows or given a new explicit event representation.
3. How aggressive the first grouping heuristic should be for repeated low-signal edits.
4. Whether the artifact detail expansion lives inside the current repo case-file page or behind a dedicated route/API pattern.

## First Commit Target On This Branch

The first code-bearing implementation should be small and vertically coherent:

- define the read-model objects
- generate storyline data for one featured artifact
- render a minimal `Drift storyline` block on the repo case-file page
- add sparse-history regression tests

That gives a usable customer-facing slice before deeper aggregation and episode drill-down work.