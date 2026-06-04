# Pause advanced-analysis budgeting and preserve implementation base

## Summary

This issue captures the budgeting design and implementation work completed so far for the detection engine so it can be resumed later without rediscovery.

## Status update (June 2026)

This note is now historical rather than current planning state.

The parked budgeting slice has since been resumed on a dedicated feature branch and extended to cover:

- scenario evaluation budget gating
- hybrid analysis budget gating
- branch-scan budget gating for workspace-bound repositories
- current-window budget reporting in control-plane, dashboard, and MCP read surfaces
- estimated provider cost reporting from entitlement-configured provider/model rate tables
- operator alerts for budget exhaustion, high utilization, single-event cost spikes, and current-window estimated spend spikes

One deliberate policy choice remains: provider price thresholds are currently alert-only, not hard execution gates. The current implementation surfaces anomalies to operators while preserving conservative analysis behavior.

Budgeting is not required for current detection-engine merge readiness. The immediate priority is keeping the roadmap branch focused on the detection engine itself. The budget slice should therefore be parked on a dedicated branch and treated as follow-on work.

## Why this was explored

The detection engine now has multiple optional expensive paths with materially different cost shapes:

- direct LLM calls in the relevance micro-classifier
- direct LLM calls in semantic review
- direct LLM calls in active verifier execution
- bounded but non-token-heavy scenario evaluation work
- bounded but non-token-heavy hybrid analysis work

A pure token limiter looked too narrow because it would undercount scenario and hybrid work. A flat per-job limiter looked too coarse because it would overcount small classifier calls and undercount larger reviewer calls. That led to the workspace-scoped `advanced_analysis_units` model.

## Design conclusion reached

The strongest design found so far is:

- budget at the workspace level, not repo or PR level
- store policy in `entitlements.feature_flags_json`
- use `advanced_analysis_units_limit`
- optionally override the rolling window with `advanced_analysis_window_seconds`
- derive units from real token usage where available
- apply deterministic feature-specific floors or surcharges where token usage is absent or not representative
- use reservation/reconciliation rather than retrospective-only accounting

Provider-agnostic token normalization used in the implementation draft:

- `ceil(prompt_tokens / 1000) + 2 * ceil(completion_tokens / 1000)`

Initial charging guidance documented in the implementation draft:

- micro-classifier: `max(1, token_units)`
- semantic review: `max(5, token_units)`
- verifier: same as semantic review when the active second pass runs
- scenario evaluation: `base fee + per-artifact surcharge + token component if present`
- hybrid analysis: `small fixed surcharge per artifact or analyzer request`

## What is already implemented locally

### Ledger and persistence foundation

A new budget ledger was implemented in `services/analysis_budget.py` with:

- policy resolution from workspace entitlement flags
- windowed reservation and consumption tracking
- reservation release for failed calls
- token usage extraction helpers
- feature-unit estimation helpers
- budget event listing helpers

A migration was added in `services/schema_migrations.py`:

- `0013_add_analysis_budget_tables`

Tables introduced:

- `workspace_analysis_budget_windows`
- `workspace_analysis_budget_events`

### Worker-side live enforcement

Budget enforcement was added in `services/audit_worker.py` for:

- semantic review
- active verifier execution

Behavior implemented:

- reserve before the expensive call
- reconcile to actual consumed units using returned token usage
- release reservations when the call fails before completion
- fall back conservatively when budget is exhausted

Current conservative behavior:

- semantic review falls back to deterministic review rather than failing the audit
- active verifier skips its second pass while preserving proposer output

### Ingress micro-classifier live enforcement

Shared ingress enforcement was added in `services/cloud_common.py`, with callers wired through:

- `main.py`
- `services/cloud_worker.py`

Behavior implemented:

- resolve workspace allocation at ingress
- wrap micro-classifier calls in a reservation/consume/release flow
- if budget is exhausted, skip the classifier call entirely
- preserve conservative safety by treating the uncertain artifact as relevant and queueing audit rather than skipping it

### Verifier context

This work was built on top of the already-completed live verifier path:

- `engine/verifier.py`
- `engine/models.py`
- `services/audit_worker.py`

The active verifier second pass is already functional and budget-aware in the local slice.

## Files carrying the local budget slice

Core implementation:

- `services/analysis_budget.py`
- `services/schema_migrations.py`
- `services/audit_worker.py`
- `services/cloud_common.py`
- `services/cloud_worker.py`
- `main.py`
- `engine/relevance.py`
- `engine/models.py`
- `engine/verifier.py`

Tests:

- `tests/test_audit_worker.py`
- `tests/test_main.py`
- `tests/test_cloud_deployment.py`
- `tests/test_persistence.py`
- `tests/test_verifier.py`

Documentation:

- `docs/advanced-analysis-budgeting-design.md`
- `docs/detection-engine-plan.md`
- `docs/detection-engine-roadmap-parent-plan.md`
- `Plan.MD`

## Validated behavior from the local slice

The local slice was validated before parking with:

- focused ingress regressions showing budget-exhausted micro-classifier paths queue audits conservatively instead of skipping them
- worker/verifier regressions showing semantic review and active verifier degrade gracefully under budget pressure
- migration coverage for the new budget tables
- a broader mixed validation run that passed with `189 passed`
- a broader detection-engine readiness run that passed with `348 passed`

## Problems solved during implementation

Important lessons already paid for:

- budget exhaustion at ingress must be conservative; false negatives are worse than extra queued audits
- worker-side budget checks need reservation/reconciliation because actual token usage is only known after the provider responds
- specific budget exceptions must be handled before generic exceptions in the micro-classifier path
- cloud-worker Postgres-locator tests needed control-plane DB patching once ingress started resolving workspace allocation earlier in the flow
- readiness tests that pin migration floors must be kept in sync when new additive migrations are introduced

## Why the work is paused

This is likely more mechanism than is necessary for immediate detection-engine rollout.

The budget design is coherent, but finishing the full surface now would expand scope into:

- scenario evaluation charging
- hybrid analysis charging
- operator-visible reporting
- budget dashboards or alerts
- plan and entitlement packaging decisions

That makes it reasonable to pause here, preserve the work, and keep the main roadmap branch centered on detection-engine merge readiness.

## Recommended resume order

When this is picked back up later, the next steps should be:

1. Decide whether `advanced_analysis_units` is still the desired product abstraction.
2. If yes, keep the existing ledger and worker/ingress slice rather than redesigning from scratch.
3. Extend deterministic-cost gating to:
   - `services/scenario_execution.py`
   - `services/hybrid_execution.py`
4. Add read-side reporting for:
   - current workspace usage
   - recent budget events
   - budget exhaustion counts by feature
5. Add operator-facing surfaces only after enforcement and reporting feel stable.
6. Revisit pricing and plan packaging only after real usage data exists.

## Acceptance criteria for future completion

A future budgeting completion pass should at minimum include:

- scenario evaluation budget enforcement
- hybrid analysis budget enforcement
- durable reporting/read-side access to current window usage and recent events
- explicit tests for retry/idempotency behavior
- explicit tests proving reviewer-facing comments do not leak budget bookkeeping
- clear docs on rollout, entitlement configuration, and fallback behavior

## Non-goals for the next pass

The next pass should avoid turning budgeting into a broader billing system. It only needs to be a bounded operational control for expensive optional analysis.

## Parking plan

This issue is paired with a dedicated remote branch carrying the current local budget implementation so the work can be resumed later without polluting the main detection-engine branch.
