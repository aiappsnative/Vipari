# Advanced Analysis Budgeting Design

## Purpose

This document defines the planned `advanced_analysis_units` budgeting model for the detection engine. It exists so rollout, pricing, and operational enforcement can all refer to the same mechanism instead of relying on scattered implementation details.

This document now covers both the live implementation and the remaining design direction. The current implementation is intentionally partial: ingress micro-classifier calls plus worker-side semantic review and active verifier execution are budget-aware, while scenario evaluation, hybrid analysis, and richer reporting remain follow-on work.

## Why the budget is unit-based

Vipari has two kinds of expensive work:

- direct LLM calls where provider token usage is the best cost signal
- non-LLM advanced analysis work where the cost shows up as CPU, GitHub API usage, snapshot processing, repository IO, and package generation

A token-only limiter would undercount scenario evaluation and hybrid analysis. A flat per-job limiter would overcount small classifier calls and undercount large review calls. The budget should therefore be one workspace-visible unit budget backed by feature-specific accounting.

## Current cost-bearing paths

The main current cost centers are:

1. Relevance micro-classifier in `engine/relevance.py`
   - one LLM call per uncertain changed file
   - fan-out risk on large noisy pull requests
2. Main semantic review in `services/audit_worker.py`
   - one larger LLM call for the reviewer-facing semantic analysis path
   - currently the largest direct model-spend path
3. Scenario evaluation in `services/scenario_execution.py` and `services/oss_eval_harness.py`
   - mostly non-token cost from repository analysis, onboarding-like work, artifact packaging, and related persistence
4. Hybrid analysis in `services/hybrid_execution.py`
   - mostly bounded CPU and snapshot-processing cost

## Verifier status

Phase B implemented verifier contracts, invocation decisions, bounded request planning, durable metadata storage, and evaluation-harness reporting. The current branch now also supports live verifier execution behind an active rollout mode.

What is implemented today:

- `engine/verifier.py` decides whether verifier review should be invoked
- `engine/models.py` defines verifier request and decision contracts
- `services/audit_worker.py` computes verifier plans, can execute a second verifier model pass when active, and persists verifier metadata on audit records
- `services/oss_eval_harness.py` emits synthetic verifier release-gate reporting

What is not yet fully implemented:

- richer verifier budget accounting and durable storage of verifier verdict details beyond the current metadata footprint
- scenario evaluation and hybrid-analysis budget enforcement

In other words, the verifier is now a real second-pass execution path, and the first budgeting slice covers it when active, but the broader budgeting surface still needs to be extended.

## Budget model

The product-visible budget should be `advanced_analysis_units` at the workspace level.

Internally, units should be derived from:

- actual LLM token usage where the provider returns usage
- deterministic surcharges for non-LLM expensive operations

This gives Vipari one budget to explain to operators while still accounting for the real shape of system cost.

## Current live slice

The current implementation enforces advanced-analysis budget checks for:

- relevance micro-classifier calls at shared ingress in `services/cloud_common.py`
- semantic review in `services/audit_worker.py`
- active verifier execution in `services/audit_worker.py`

The current live mechanics are:

- workspace budget configuration is read from `entitlements.feature_flags_json`
- `advanced_analysis_units_limit` sets the active-window unit cap
- `advanced_analysis_window_seconds` optionally overrides the default rolling window size
- micro-classifier reservations occur before each uncertain-file classifier call and conservatively keep the artifact relevant when the reservation is denied
- semantic review reserves units before the LLM call and falls back to deterministic review if the reservation is denied
- active verifier execution reserves units before the second-pass call and skips verifier execution, while preserving proposer output, if the reservation is denied
- usage is reconciled after completion into `workspace_analysis_budget_windows` and `workspace_analysis_budget_events`

This live slice intentionally still leaves scenario and hybrid charging for the next iteration.

## Unit calculation

### Token-derived units

Token-bearing calls should be normalized into units with a provider-agnostic formula. The exact constants can be tuned, but the shape should stay simple and stable.

Example:

$$
\text{token_units} = \left\lceil \frac{\text{prompt_tokens}}{1000} \right\rceil + 2 \cdot \left\lceil \frac{\text{completion_tokens}}{1000} \right\rceil
$$

This keeps the model simple, weights output tokens a bit more heavily, and avoids encoding vendor pricing tables directly into worker logic.

### Feature-specific charging

Recommended initial charging model:

- micro-classifier: `max(1 unit, token_units)`
- semantic review: `max(5 units, token_units)`
- verifier: charge like semantic review when the active verifier path actually executes
- scenario evaluation: `base fee + per-artifact surcharge + token component if present`
- hybrid analysis: `small fixed surcharge per analyzer request or scanned artifact`

Example starting values:

- micro-classifier: `max(1, token_units)`
- semantic review: `max(5, token_units)`
- scenario evaluation: `15 + 3 * artifact_count + token_units`
- hybrid analysis: `1` or `2` units per artifact or analyzer request

The exact constants are operational tuning values, not product contracts.

## Reservation and reconciliation

Budget enforcement should be reservation-based, not purely retrospective.

Before an optional expensive step runs:

1. resolve the workspace
2. estimate units for the step
3. reserve units transactionally
4. skip the step if the reservation cannot be satisfied
5. persist the reason for skipping
6. reconcile the reservation to actual consumed units after execution completes

This is especially important for LLM paths because actual token usage is only known after the provider responds.

## Persistence model

The first implementation should use two durable concepts:

- workspace budget windows
- budget events

Suggested tables:

- `workspace_analysis_budget_windows`
- `workspace_analysis_budget_events`

Suggested event fields:

- `workspace_id`
- `audit_id` or `audit_job_id`
- `feature_key`
- `reservation_key` or idempotency key
- `units_reserved`
- `units_consumed`
- `prompt_tokens`
- `completion_tokens`
- `provider`
- `model`
- `window_start`
- `status`

This supports both enforcement and later reporting.

## Rollout order

To keep the first implementation small and defensible, the recommended order is:

1. extend deterministic-cost gating to scenario evaluation and hybrid analysis
2. expose read-side reporting after enforcement is stable
3. add operator-facing budget surfaces and alerts

## Architecture constraints

The budgeting mechanism must preserve the existing detection-engine guardrails:

- workspace isolation stays authoritative
- off-mode and shadow-mode behavior must still skip expensive work early
- retries must be idempotent from a budget perspective
- reviewer-facing comments must not expose rollout-internal budget bookkeeping
- production rollout must remain fail-closed when required configuration is absent

## Open follow-ons

The first slice does not need to solve everything. The likely follow-ons are:

- operator-facing budget dashboards and alerts
- monthly or plan-bound budget windows tied to workspace entitlements
- richer per-feature trend reporting for calibration and pricing
