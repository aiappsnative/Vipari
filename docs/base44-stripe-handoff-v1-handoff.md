# Base44 + Stripe Handoff V1 Handoff

This note is the current restart point for `feature/driftguard-base44-stripe-handoff-v1`.

## Branch status

The first implementation slice for issue `#25` is in place, locally validated, and partially provider-validated through a live tunnel-backed GitHub flow.

Implemented today and preserved on the branch:

- GitHub OAuth login and session handling
- Base44 source/plan passthrough across auth, workspace bootstrap, and billing continuation
- workspace bootstrap and access-state-driven app shell
- Stripe checkout, portal support, and webhook-driven subscription projection
- GitHub App installation linkage, setup-URL callback handling, and repository allocation into the existing onboarding engine
- actionable `/app` state cards for both blocked setup states and the fully active workspace state
- additive SQLite migrations for older local databases, including rebuilt `repo_connections` and `repo_allocations` foreign keys
- dashboard gating for incomplete setup states
- owner/admin protection for billing and provisioning mutations
- provider-setup preflight tooling via `python scripts/control_plane_preflight.py`
- updated README, roadmap, changelog, and architecture docs reflecting the branch state

## Validation status

Validated at the current checkpoint:

- `python -m pytest tests/test_control_plane_ui.py -q` -> `17 passed`
- `python -m pytest tests/test_control_plane_foundation.py -q` -> `7 passed`
- `python -m pytest -q` -> `157 passed`
- live tunnel-backed flow confirmed GitHub OAuth callback, workspace creation, GitHub App install linking, repo sync, repo allocation/onboarding for `doria90/dummyAI`, and dashboard unlock after simulated Team billing

Known non-blocking signal:

- Starlette emits existing `TestClient` cookie deprecation warnings in tests; behavior is otherwise green
- the persisted `workspaces.setup_state` row can remain at `awaiting_repo_onboarding` even when the derived resolver/UI correctly report `active`

## What is still open

The main remaining work item is real Stripe-backed validation.

Specifically:

- keep using `python scripts/control_plane_preflight.py` before the live billing pass
- forward Stripe test-mode events to `/webhooks/stripe`
- complete one checkout -> webhook -> install -> repo allocation -> dashboard run without local billing simulation
- optionally clean up the persisted `setup_state` sync gap once the billing path is verified

## Files to re-open first tomorrow

- [README.md](../README.md)
- [Plan.MD](../Plan.MD)
- [docs/base44-stripe-handoff-v1-plan.md](base44-stripe-handoff-v1-plan.md)
- [docs/base44-stripe-handoff-v1-state-model.md](base44-stripe-handoff-v1-state-model.md)
- [main.py](../main.py)
- [tests/test_control_plane_ui.py](../tests/test_control_plane_ui.py)

## Suggested first command next

```bash
python -m pytest -q
```

Then either run the real Stripe-backed E2E flow from [README.md](../README.md) or fix the smaller `setup_state` persistence gap.