# Detection Engine Roadmap Parent Plan

## Purpose

This document turns GitHub issue `#18` into a parent-feature execution plan for the next detection-engine program. It covers the parent branch, the four stacked phase branches, the detailed execution checklist for each phase, the expected git workflow, and the test gates required before each merge.

This plan is intentionally execution-focused. Use it with:

- [docs/detection-engine-plan.md](detection-engine-plan.md) for engine architecture
- [Plan.MD](../Plan.MD) for repo-level roadmap status
- GitHub issues `#18` through `#22` for the strategic intent and scope boundaries

## Delivery Model

This program ships as one parent feature with four ordered child phases.

Current implementation status on `feature/detection-engine-phase-a-signal-fusion`:

- parent roadmap planning doc added and linked from [Plan.MD](../Plan.MD)
- Phase A branch created from the parent branch
- contract-protection regressions added for deterministic prompt formatting, empty-analysis output, and micro-classifier override behavior
- deterministic rule coverage expanded with `orchestration_drift` and `sensitive_tooling_drift`
- initial relevance noise reduction added for generic docs/tests/fixtures paths that do not look like real AI control-surface artifacts
- standalone policy scaffolding added in `engine/policy.py` with default policy rules and a policy-floor hook in fusion
- live audit-worker fusion now derives policy floors from normalized attribute-profile deltas and deterministic findings, and surfaces concise policy-impact rationale in reviewer comments
- static profile extraction now discounts example blocks so capability, autonomy, and sampling signals are driven by live instructions rather than illustrative examples
- focused Phase A validation is currently green for engine, rules, policy, drift profile, signal fusion, audit-worker fusion, and the OSS eval-harness regression slice

Current implementation status on `feature/detection-engine-phase-b-verifier-eval`:

- Phase B branch created from the updated parent branch after Phase A merged into `feature/detection-engine-roadmap`
- explicit verifier contracts added in `engine/models.py` for invocation decisions, request payloads, and review results
- new `engine/verifier.py` module added to separate verifier gating and request-building from proposer comment generation
- live audit-worker review generation now computes bounded verifier plans with selective triggers for high-impact, low-confidence, disagreement, and merge-blocking cases
- verifier rollout is now configurable and safe by default through shadow-mode planning plus per-review request caps in `services/audit_worker.py`
- durable PR audit records now persist verifier mode, trigger, and request-count metadata so feedback and PR outcomes can calibrate future verifier policy safely
- OSS evaluation packages now include a synthetic verifier release-gate block with trigger precision/recall, disagreement counts, and budgeted cost/latency reporting
- focused Phase B validation is currently green across verifier, worker, persistence, and eval-harness slices
- Phase B merged into `feature/detection-engine-roadmap` at `3887bd5`, and the parent branch is now the canonical base for Phase C

Branch topology:

1. `main`
2. `feature/detection-engine-roadmap` from `main`
3. `feature/detection-engine-phase-a-signal-fusion` from `feature/detection-engine-roadmap`
4. `feature/detection-engine-phase-b-verifier-eval` from `feature/detection-engine-roadmap` after Phase A merges into the parent branch
5. `feature/detection-engine-phase-c-governance-cicd` from `feature/detection-engine-roadmap` after Phase B merges into the parent branch
6. `feature/detection-engine-phase-d-scenario-hybrid-analysis` from `feature/detection-engine-roadmap` after Phase C merges into the parent branch

The parent branch is the integration spine for the full roadmap. Each phase still gets its own branch, PR, review gate, validation pass, and cleanup flow.

## Non-Negotiable Constraints

Across all phases:

1. Vipari remains static-first and GitHub-native.
2. No runtime traffic inspection or live inference telemetry is introduced.
3. LLMs remain advisory; deterministic rules and policy floors remain the bounding layer.
4. Existing auth, workspace scoping, and four-eyes boundaries must remain intact.
5. New risk behavior should ship behind shadow-mode or dry-run gates before enforcement.
6. Engine changes must be evaluation-gated before merge.

## General Git Operations

Each feature in this parent program should follow the same git workflow:

1. Start from the latest `feature/detection-engine-roadmap`.
2. Create the dedicated phase branch from the parent branch.
3. Keep commits scoped to the current phase only.
4. Push the phase branch and open a PR targeting `feature/detection-engine-roadmap`.
5. Run the required scoped test gates for that phase.
6. Merge the phase PR only when the branch is reviewed and green.
7. Delete the remote and local phase branch after merge.
8. Refresh the parent branch to the merged state before creating the next phase branch.
9. After all phases are merged into the parent branch, open a final PR from `feature/detection-engine-roadmap` to `main`.
10. Delete the parent branch after the final PR is merged.

Each phase checklist below ends with an explicit planning-file update step so this document stays current.

## Parent Branch Checklist

Branch: `feature/detection-engine-roadmap`

1. Create the parent branch from `main`.
2. Add this planning document to the repo and keep it updated throughout the program.
3. Normalize roadmap references in [Plan.MD](../Plan.MD) so issue `#18` and phases `#19` through `#22` are visible in repo docs.
4. Preserve the stable engine contracts across the full roadmap:
   - changed-file relevance
   - structured change extraction
   - deterministic findings
   - attribute profiles and drift deltas
   - semantic review packages
   - fused risk outputs
   - durable audit storage
5. Track which phase has landed, what changed, and what the next phase must inherit.
6. Run integrated validation on the parent branch after all phase branches are merged.
7. Open the final PR from the parent branch to `main` only after the full stacked feature is validated.
8. End-of-parent update step: update this document with the final merged SHAs, validation summary, and any deferred follow-up work before opening the PR to `main`.

## Phase A

Issue: `#19`
Branch: `feature/detection-engine-phase-a-signal-fusion`
PR target: `feature/detection-engine-roadmap`
Goal: strengthen core static signals, deterministic rules, policy scaffolding, relevance, and fusion.

### Phase A Execution Checklist

1. Create `feature/detection-engine-phase-a-signal-fusion` from `feature/detection-engine-roadmap`.
2. Lock core engine contracts before adding new behavior.
   Files:
   - `engine/analysis.py`
   - `engine/models.py`
   - `tests/test_engine.py`
3. Expand deterministic rule families.
   Files:
   - `engine/rules.py`
   - `engine/diff_parser.py`
   - `tests/test_rules.py`
   Slices:
   - orchestration and autonomy expansion rules
   - sensitive-system-access-without-guardrail rules
   - richer tool-permission drift rules
   - better evidence spans for added versus removed lines
4. Introduce a dedicated policy evaluation contract.
   Files:
   - `engine/policy.py` or equivalent new engine module
   - `engine/models.py`
   - targeted new tests under `tests/`
   Slices:
   - workspace default policies
   - optional repo overrides
   - safe built-in templates
   - shadow-mode only evaluation
5. Harden static profiling and attribute signals.
   Files:
   - `engine/drift_profile.py`
   - `tests/test_drift_profile.py`
6. Harden relevance and context selection.
   Files:
   - `engine/relevance.py`
   - `engine/context_selector.py`
   - `tests/test_engine.py`
   Slices:
   - negative rules for obvious non-AI noise
   - broader real-repo heuristics
   - more artifact-aware thresholds
   - lower unnecessary micro-classifier usage
7. Upgrade fusion behavior.
   Files:
   - `services/signal_fusion.py`
   - `services/audit_worker.py`
   - `tests/test_signal_fusion.py`
   Slices:
   - policy-aware risk floors
   - selected history-aware weighting
   - better semantic-confidence handling
   - explicit fused rationale
8. Expose only the necessary Phase A reviewer-surface changes.
   Outcome:
   - concise policy impact summaries
   - dominant-rule explanation
   - no broad UI redesign
9. Update roadmap and architecture docs.
   Files:
   - [docs/detection-engine-plan.md](detection-engine-plan.md)
   - [Plan.MD](../Plan.MD)
10. Run Phase A test gates.
   Gates:
   - focused engine tests
   - rule/profile/fusion regression suite
   - representative eval-harness comparison
   - no token-cost blowout relative to the baseline expectation
11. Push the branch and open a PR to `feature/detection-engine-roadmap`.
12. Merge the PR when reviewed and green.
13. Delete the Phase A branch locally and remotely.
14. Refresh the parent branch from the merged Phase A result.
15. End-of-phase update step: update this document with Phase A status, merged commit, major landed changes, validation summary, and any Phase B prerequisite adjustments.

## Phase B

Issue: `#20`
Branch: `feature/detection-engine-phase-b-verifier-eval`
PR target: `feature/detection-engine-roadmap`
Goal: add a proposer-verifier stack and turn evaluation into a measurable release gate.

### Phase B Execution Checklist

1. Create `feature/detection-engine-phase-b-verifier-eval` from the updated parent branch after Phase A is merged.
2. Define verifier contracts separate from proposer logic.
   Files:
   - `engine/semantic_review.py`
   - new verifier module under `engine/` or `services/`
   - `engine/models.py`
3. Add selective verifier invocation.
   Files:
   - `services/audit_worker.py`
   - verifier module files
   Slices:
   - high-impact gating
   - ambiguous/low-confidence gating
   - budget caps and call filtering
4. Expand the evaluation harness into a release gate.
   Files:
   - `services/oss_eval_harness.py`
   - `fixtures/eval-harness/`
   - `tests/test_oss_eval_harness.py`
   Slices:
   - synthetic traps
   - curated OSS scenarios
   - precision/recall by risk level
   - verifier disagreement tracking
   - cost and latency reporting
5. Integrate feedback and PR outcomes into calibration storage.
   Files:
   - `services/audit_records.py`
   - related feedback/reaction services
6. Add feature flags and shadow-mode rollout for verifier behavior.
7. Update roadmap and architecture docs.
   Files:
   - [docs/detection-engine-plan.md](detection-engine-plan.md)
   - [Plan.MD](../Plan.MD)
8. Run Phase B test gates.
   Gates:
   - verifier contract tests
   - evaluation-harness regressions
   - disagreement and budget-cap tests
   - shadow-mode verification
9. Push the branch and open a PR to `feature/detection-engine-roadmap`.
10. Merge the PR when reviewed and green.
11. Delete the Phase B branch locally and remotely.
12. Refresh the parent branch from the merged Phase B result.
13. End-of-phase update step: update this document with Phase B status, merged commit, metric deltas, verifier rollout state, and any Phase C prerequisite adjustments.

Phase B merge update:

- merged PR: `#23` (`Complete Phase B verifier shadow gate`)
- merged parent commit: `3887bd5`
- rollout state: verifier planning remains shadow-mode only in the worker, with reviewer-facing PR comments intentionally kept free of rollout-internal verifier notes
- implementation note: Phase B landed verifier contracts, gating, persisted calibration metadata, and eval-harness reporting; the current parent branch has since extended that foundation with an active verifier execution mode in the worker while keeping rollout control explicit
- validation summary before merge: `104 passed` across `tests/test_verifier.py`, `tests/test_engine.py`, `tests/test_audit_worker.py`, `tests/test_oss_eval_harness.py`, and `tests/test_persistence.py`
- Phase C prerequisite adjustment: governance decision consumers should treat stored verifier metadata as calibration input and rollout evidence, not as an end-user policy decision on its own

Phase C merge update:

- merged PR: `#24` (`Complete Phase C governance CI/CD lifecycle`)
- merged parent commit: `967cbc2`
- rollout state: governance decisions now have read-side consumers across dashboard, admin-token, and control-plane surfaces, with runner-side and worker-side GitHub status/check-run actuation still opt-in via dry-run/warn/enforce rollout settings
- validation summary before merge: `247 passed` across `tests/test_github_integration.py`, `tests/test_audit_worker.py`, `tests/test_cloud_deployment.py`, `tests/test_dashboard_api.py`, `tests/test_control_plane_auth.py`, `tests/test_governance_gate_script.py`, and `tests/test_governance_policy.py`
- Phase D prerequisite adjustment: optional scenario or hybrid-analysis follow-ons should preserve the new governance lifecycle as a downstream consumer of bounded audit evidence rather than turning experimental analysis into an implicit merge gate

## Phase C

Issue: `#21`
Branch: `feature/detection-engine-phase-c-governance-cicd`
PR target: `feature/detection-engine-roadmap`
Goal: turn calibrated engine outputs into auditable governance and CI/CD decisions.

### Phase C Execution Checklist

1. Create `feature/detection-engine-phase-c-governance-cicd` from the updated parent branch after Phase B is merged.
2. Build policy-decision consumers outside the engine core.
   Files:
   - new governance or policy-consumer services under `services/`
   - `services/audit_records.py`
   Slices:
   - `should_block_merge`
   - `should_require_escalation`
   - structured explanation payloads
3. Add GitHub and CI integration paths.
   Files:
   - GitHub integration services
   - control-plane or API surfaces exposing policy decisions
   - workflow or CI example docs if stored in-repo
4. Expand governance and control-tower read models.
   Files:
   - `services/dashboard_views.py`
   - `services/dashboard_api_payloads.py`
   - `services/governance_signals.py`
   - related tests
5. Add full auditability and versioning for governance behavior.
6. Keep default rollout safe with warn and dry-run modes first.
7. Update roadmap and architecture docs.
   Files:
   - [docs/detection-engine-plan.md](detection-engine-plan.md)
   - [Plan.MD](../Plan.MD)
8. Run Phase C test gates.
   Gates:
   - policy-decision integration tests
   - GitHub/CI flow tests
   - dashboard/control-tower payload tests
   - dry-run versus enforce-mode regression tests
9. Push the branch and open a PR to `feature/detection-engine-roadmap`.
10. Merge the PR when reviewed and green.
11. Delete the Phase C branch locally and remotely.
12. Refresh the parent branch from the merged Phase C result.
13. End-of-phase update step: update this document with Phase C status, merged commit, dry-run/enforcement readiness, and any Phase D prerequisite adjustments.

## Phase D

Issue: `#22`
Branch: `feature/detection-engine-phase-d-execution-read-models`
PR target: `feature/detection-engine-roadmap`
Goal: add optional scenario evals and hybrid static-analysis enrichments without changing the validity of the core engine when disabled.

Current implementation status on `feature/detection-engine-phase-d-execution-read-models`:

- the first two Phase D slices are already merged into `feature/detection-engine-roadmap` at `dae81b2`, covering disabled-by-default scenario participation planning plus shadow-only hybrid static-analysis planning and persistence
- the scenario execution slice is already merged into `feature/detection-engine-roadmap` at `ed8b807`, reusing the existing OSS eval-harness seeded-scenario surface instead of inventing a second scenario runtime
- shadow-mode scenario execution now runs only when the worker-selected scenario plan is eligible and the repo has a seeded scenario registered in the eval harness
- scenario execution writes bounded execution summaries back into durable audit records, including scenario key, selected artifact paths, package/comparison paths, and assertion summary metadata
- the hybrid execution slice already merged into `feature/detection-engine-roadmap` at `9cafadb` runs bounded in-memory analyzer passes over already-fetched artifact snapshots, keyed by the persisted hybrid-analysis plan rather than by a second repository crawl
- hybrid execution now persists summary results for each selected artifact, including analyzer key, finding count, highest severity, and bounded finding metadata, while keeping rollout shadow-only and production-off
- the Phase D read-model slice merged into `feature/detection-engine-roadmap` at `b801b4a`, completing the downstream exposure of persisted execution summaries for dashboard and machine-consumable read paths
- the current read-model slice exposes scenario and hybrid execution summaries on the repo PR-review routes payload, the shared repo audit brief, the machine-auth control-plane repo dashboard response, the standalone machine-auth control-plane audit list/detail responses with state/severity/artifact/recency filtering, and the MCP repo casefile response so both dashboard and machine-consumable read paths can inspect the shadow-mode evidence already stored on the audit record without adding a new route family
- runtime guardrails continue to keep `SCENARIO_EVAL_ROLLOUT_MODE` and `HYBRID_STATIC_ANALYSIS_ROLLOUT_MODE` off in production and limited to `worker` or `monolith` roles elsewhere
- latest Phase D gate on this branch is green at `228 passed` across `tests/test_audit_worker.py`, `tests/test_hybrid_analysis.py`, `tests/test_hybrid_execution.py`, `tests/test_dashboard_api.py`, `tests/test_mcp_broker.py`, `tests/test_customer_control_plane.py`, `tests/test_cp_low_risk_actions.py`, and `tests/test_control_plane_auth.py`, covering disabled-path behavior, shadow-mode execution persistence, workspace-scoped read surfaces, and the standalone machine-auth control-plane audit list/detail filters
- next design follow-on: optional deeper analysis is now partially governed by the live workspace-scoped `advanced_analysis_units` mechanism documented in [advanced-analysis-budgeting-design.md](advanced-analysis-budgeting-design.md), covering shared-ingress micro-classifier calls plus worker-side semantic review and active verifier execution, with scenario and hybrid execution still pending deterministic surcharge enforcement
- the parent branch now satisfies the integrated validation prerequisite for the final PR to `main`

### Phase D Execution Checklist

1. Create `feature/detection-engine-phase-d-scenario-hybrid-analysis` from the updated parent branch after Phase C is merged.
2. Build scenario-eval execution as an additive module.
   Files:
   - new scenario-eval services/modules
   - existing evaluation surfaces
3. Add artifact and repo participation controls.
4. Add hybrid static-analyzer integration points.
5. Keep formal-methods-style integration narrow and opt-in.
6. Ensure storage, auth, and workspace scoping match the existing audit-evidence model.
7. Update roadmap and architecture docs.
   Files:
   - [docs/detection-engine-plan.md](detection-engine-plan.md)
   - [Plan.MD](../Plan.MD)
8. Run Phase D test gates.
   Gates:
   - disabled-path parity tests
   - non-prod constraint tests
   - execution budget tests
   - static-analyzer mapping tests
   - workspace-scoping and evidence-storage tests
9. Push the branch and open a PR to `feature/detection-engine-roadmap`.
10. Merge the PR when reviewed and green.
11. Delete the Phase D branch locally and remotely.
12. Refresh the parent branch from the merged Phase D result.
13. End-of-phase update step: update this document with Phase D status, merged commit, rollout state, and final parent-branch readiness for the PR to `main`.

## Integrated Parent PR Gate

1. After Phases A through D are all merged into `feature/detection-engine-roadmap`, run integrated validation across the full parent branch.
2. Confirm no stacked-branch regressions exist between phases.
3. Confirm roadmap docs and architecture docs reflect the full landed parent feature.
4. Open the final PR from `feature/detection-engine-roadmap` to `main`.
5. Merge the parent PR when reviewed and green.
6. Delete the parent branch locally and remotely.
7. Final update step: update this document with the parent PR status, final merge commit, full validation summary, and any deferred follow-up issues.

### Parent PR Readiness Update

- parent branch: `feature/detection-engine-roadmap`
- parent PR status: ready to open against `main`
- merged phase SHAs on the parent branch: Phase A `a7a0f79`, Phase B `3887bd5`, Phase C `967cbc2`, Phase D planning/persistence `dae81b2`, Phase D scenario execution `ed8b807`, Phase D hybrid execution `9cafadb`, Phase D read-model exposure `b801b4a`
- integrated validation summary: `228 passed` across `tests/test_audit_worker.py`, `tests/test_hybrid_analysis.py`, `tests/test_hybrid_execution.py`, `tests/test_dashboard_api.py`, `tests/test_mcp_broker.py`, `tests/test_customer_control_plane.py`, `tests/test_cp_low_risk_actions.py`, and `tests/test_control_plane_auth.py`
- deferred follow-up issues after the parent PR: `#46` Railway launch hardening, `#14` attribute-profile UI, and `#15` governance signals
