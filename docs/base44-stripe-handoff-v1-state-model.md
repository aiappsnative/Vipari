# DriftGuard Base44 Billing Handoff V1 State Model

This document defines the canonical user-access and workspace-setup state model for issue `#25`.

## Principle

The app should not scatter setup and access checks across unrelated routes. Every authenticated page and relevant API should resolve behavior from the same workspace access snapshot.

## Inputs to the resolver

- valid session exists
- authenticated user exists
- selected workspace exists
- membership exists and has sufficient role
- subscription record exists
- billing state is active / pending / failed / expired
- entitlement package is active
- GitHub App installation is linked
- repo allocation count
- onboarded repo count

## Required states

### `unauthenticated`

- meaning: no valid DriftGuard session exists
- dashboard access: no
- primary action: `Continue with GitHub`

### `authenticated_no_workspace`

- meaning: identity exists but no workspace is selected or linked
- dashboard access: no
- primary action: `Create workspace`

### `invited_pending_acceptance`

- meaning: a matching invitation exists but is not accepted yet
- dashboard access: no
- primary action: `Accept invitation`

### `forbidden`

- meaning: session is valid but user lacks membership/role for the workspace
- dashboard access: no
- primary action: `Switch workspace`

### `workspace_no_subscription`

- meaning: workspace exists but billing has not started
- dashboard access: no
- primary action: `Choose plan`

### `billing_pending_confirmation`

- meaning: checkout was initiated or completed in browser, but webhook-confirmed activation is not finished yet
- dashboard access: no
- primary action: `Refresh status`

### `payment_failed`

- meaning: the active billing provider indicates payment failure, entitlement loss, or past due state
- dashboard access: no
- primary action: `Fix billing`

### `awaiting_github_install`

- meaning: billing is valid but the workspace has no linked GitHub App installation
- dashboard access: no
- primary action: `Install DriftGuard`

### `awaiting_repo_onboarding`

- meaning: installation exists but no licensed repo is both allocated and onboarded
- dashboard access: no
- primary action: `Select repositories`

### `active_comments_only`

- meaning: the workspace is entitled for PR comments but not for dashboard access
- dashboard access: no
- primary action: `Upgrade to Starter`

### `active`

- meaning: subscription, installation, and onboarding prerequisites are complete
- dashboard access: yes

### `canceled_active_until_period_end`

- meaning: subscription will end, but paid access is still currently valid
- dashboard access: yes
- primary action: `Resume subscription`

### `expired_read_only`

- meaning: paid period ended or active entitlement is no longer valid
- dashboard access: limited read-only only
- primary action: `Reactivate`

## Resolver precedence

The resolver should short-circuit in this order:

1. `unauthenticated`
2. `authenticated_no_workspace`
3. `invited_pending_acceptance`
4. `forbidden`
5. `workspace_no_subscription`
6. `billing_pending_confirmation`
7. `payment_failed`
8. `awaiting_github_install`
9. `awaiting_repo_onboarding`
10. `active_comments_only`
11. `expired_read_only`
12. `canceled_active_until_period_end`
13. `active`

## Checklist contract

The UI checklist should be derived, not hard-coded per route.

Minimum checklist items:

- GitHub connected
- workspace linked
- plan active
- GitHub App installed
- repository allocated
- first onboarding completed

Each checklist item should expose:

- `label`
- `status`: `complete`, `current`, `blocked`, or `pending`
- `detail`
- `cta`

## Route behavior rules

### Public acquisition routes

- accessible without session
- may optionally personalize if a session exists

### Auth routes

- start login when unauthenticated
- redirect to app bootstrap when already authenticated

### Billing routes

- workspace owner/admin only in v1
- other roles should be denied explicitly

### Install and repo-setup routes

- owner/admin mutates provisioning state
- blocked users may view status but not allocate or install

### Dashboard routes

- `active`: full access
- `canceled_active_until_period_end`: full access with renewal warning
- `expired_read_only`: read-only shell with reactivation CTA
- all incomplete setup states: setup-aware shell only

## Non-negotiable rule

No route should independently decide that checkout success means active access. Only webhook-confirmed subscription projection may move a workspace into an active paid state.
Free plans are the exception: local free-plan activation may move a workspace into the comments-only path without an external billing callback.

## Implementation status on 2026-04-09

This state model is now merged into `main`.

Implemented usages:

- `/app` renders a setup-aware shell from the resolved workspace state
- `/api/auth/session` returns the current access resolution to the frontend
- `/api/workspaces/current/access-state` exposes the same resolver directly for workspace-aware clients
- `/dashboard` and `/dashboard/{owner/repo}` redirect to `/login` or `/app` when the workspace is not dashboard-eligible
- dashboard JSON routes now apply the same gating when control-plane workspaces exist
- `/app/billing/checkout`, `/app/billing/portal`, `/app/setup/install/link`, and `/app/setup/repos/allocate` enforce the owner/admin mutation rule documented above
- `/webhook` now refuses to queue PR audits/comments for installed repos that are not allocated or whose workspace lacks comment entitlement

Validated transitions covered by focused tests:

- unauthenticated -> login redirect
- authenticated without workspace -> workspace bootstrap
- workspace without subscription -> billing entry
- billing pending until provider-confirmed activation
- billing active but no install -> install required
- install linked but no onboarded allocation -> repo onboarding required
- onboarded allocation with comments entitlement but no dashboard entitlement -> `active_comments_only`
- onboarded allocation -> active dashboard access
- viewer role denied for billing/install mutation paths
- active workspace shell exposes a clickable dashboard continuation path instead of a dead-end status card

Live tunnel-backed validation confirmed the GitHub OAuth, install-link, repo-sync, allocation, and dashboard-unlock path for the same state machine. The final merge hardening also ensures Stripe webhook activation resolves through stored customer/subscription ownership instead of trusting workspace metadata alone.