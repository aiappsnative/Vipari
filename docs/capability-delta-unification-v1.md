# Capability Delta Unification v1

This note captures the current unified path for capability-delta interpretation across onboarding, PR review, governance, and control-plane surfaces.

## Canonical signal model

- Source helper: `services/capability_inference.py::build_capability_delta_signal`
- Canonical fields:
  - `delta` (float)
  - `direction` (`expanded` | `reduced` | `stable`)
  - `material` (bool)
  - `summary` (short operator-facing sentence)
- Material threshold: `abs(delta) >= 0.03`

## Pipeline integration

1. Onboarding and baseline comparison
- Baseline comparison in `services/dashboard_views.py::_build_pr_review_baseline_comparison` emits `capability_delta_signal`.

2. PR route payloads
- `services/dashboard_views.py::build_repo_pr_review_routes_payload` attaches `capability_delta_signal` to route entries and selected route.

3. Governance rationale
- `services/governance_policy.py::evaluate_governance_decision` accepts an optional `capability_delta_signal` input.
- Material capability expansion now emits rationale code `material_capability_expansion` and contributes to escalation.
- Material capability reduction emits rationale code `material_capability_reduction` as contextual info.

4. PR comments and worker governance checks
- `services/audit_worker.py` derives capability signal from attribute-profile deltas.
- Worker governance status/check-run evaluation passes the signal into governance decision logic.

5. Repo and overview governance summaries
- Repo governance summaries and overview attention/card payloads carry capability signal context via `governance_capability_delta_signal`.

6. Control-plane repo setup cards
- `/repos` onboarding summary cards render `Capability delta: ...` copy and aggregate material-shift count.

## Operator and CI visibility

- Governance decision API includes expanded rationale list with capability-based reason codes when present.
- `scripts/governance_gate.py` check-run text renders rationale items and evidence, including material capability expansion evidence when returned.
- Escalation queue payloads now include governance context fields:
  - `governance_decision_lane`
  - `governance_rationale_codes`
  - `governance_capability_delta_signal`

## Guardrails

- Additive schema: new fields are additive and backward-compatible for existing consumers.
- Fallback behavior: when measurement is unavailable, a stable/unavailable signal is emitted with explicit summary text instead of omitting the field.
