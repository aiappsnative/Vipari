# Base44 Billing Handoff V1 Handoff

This note is the current restart point for `feature/driftguard-base44-stripe-handoff-v1`.

## Branch status

The first implementation slice for issue `#25` is in place, locally validated, and partially provider-validated through a live tunnel-backed GitHub flow.

Implemented today and preserved on the branch:

- GitHub OAuth login and session handling
- Base44 source/plan passthrough across auth, workspace bootstrap, and billing continuation
- workspace bootstrap and access-state-driven app shell
- a first-class `free` plan with comments-only access and one-repository entitlement
- provider-neutral entitlement projection separating dashboard access from PR-comment access
- signed billing handoff claims plus paid-plan claim activation after login/workspace bootstrap
- Stripe checkout, portal support, and webhook-driven subscription projection retained as a paid-path fallback
- GitHub App installation linkage, setup-URL callback handling, and repository allocation into the existing onboarding engine
- actionable `/app` state cards for both blocked setup states and the fully active workspace state
- actionable `/app` state cards for the free comments-only terminal state
- additive SQLite migrations for older local databases, including rebuilt `repo_connections` and `repo_allocations` foreign keys
- setup-state persistence refresh from entitlement/install/allocation facts
- dashboard gating for incomplete setup states plus dashboard JSON-route gating when the control plane is active
- webhook gating so installed-but-unallocated repos do not receive PR audits/comments
- owner/admin protection for billing and provisioning mutations
- provider-setup preflight tooling via `python scripts/control_plane_preflight.py`
- updated README, roadmap, changelog, and architecture docs reflecting the branch state

## Validation status

Validated at the current checkpoint:

- `python -m pytest -q` -> `162 passed`
- targeted control-plane/access-state coverage now includes free-tier activation, signed billing handoff claims, dashboard denial for free workspaces, and webhook allocation enforcement
- live tunnel-backed flow confirmed GitHub OAuth callback, workspace creation, GitHub App install linking, repo sync, repo allocation/onboarding for `doria90/dummyAI`, and dashboard unlock after simulated Team billing

Known non-blocking signal:

- Starlette emits existing `TestClient` cookie deprecation warnings in tests; behavior is otherwise green

## What is still open

The main remaining work item is real Base44/Wix-backed validation.

Specifically:

- keep using `python scripts/control_plane_preflight.py` before the live billing pass
- wire Base44/Wix to `POST /api/billing/handoff/base44` with `BILLING_HANDOFF_SECRET`
- complete one payment -> signed handoff -> claim -> install -> repo allocation -> dashboard run without local billing simulation
- optionally validate the Stripe fallback path by forwarding Stripe test-mode events to `/webhooks/stripe`

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
Then either run the real Base44/Wix-backed E2E flow from [README.md](../README.md) or validate the Stripe fallback path.