# DriftGuard Base44 Billing Handoff V1 Plan

This document turns GitHub issue `#25` into the implementation plan for DriftGuard's first production-facing acquisition-to-product handoff.

## Goal

Create a coherent customer journey where Base44 handles public acquisition and DriftGuard handles every authenticated, stateful, or security-sensitive step after the CTA click.

Target journey:

1. Visitor lands on Base44.
2. Visitor clicks `Start with GitHub`.
3. DriftGuard authenticates the user with GitHub.
4. DriftGuard creates or resumes a workspace.
5. DriftGuard selects or preselects a plan.
6. Free users activate locally, while paid users complete checkout in Base44/Wix first.
7. Base44/Wix sends a signed billing handoff to DriftGuard, which creates a claim token.
8. DriftGuard consumes the claim after login/workspace bootstrap, activates entitlements, and guides the user through GitHub App install and repo setup.
9. DriftGuard gates dashboard and webhook behavior until setup and entitlements are complete.

## Scope boundary

### Base44 owns

- public landing page
- pricing and positioning
- CTA surfaces
- visual storytelling
- public FAQ and trust framing

### DriftGuard owns

- GitHub web login
- user identity and session state
- workspace and membership state
- plan selection and entitlement linkage
- free-tier activation
- signed billing claim creation/consumption
- optional Stripe Checkout / portal fallback integration
- authoritative billing activation handling
- workspace entitlements
- GitHub App installation linkage
- repo allocation and onboarding
- setup-aware app shell and dashboard gating

### Out of scope for this branch

- building the Base44 marketing site inside this repo
- enterprise SSO / SAML
- usage-based billing
- custom card collection UI
- broad dashboard redesign unrelated to access gating

## Current baseline on `main`

DriftGuard already has:

- GitHub App integration primitives
- onboarding and historical backfill flows
- dashboard pages and APIs
- durable SQLite-backed operational and history persistence
- split webhook / worker / API deployment shape

DriftGuard does not yet have:

- GitHub OAuth login
- user / workspace / session persistence
- provider-neutral billing activation handling
- entitlement enforcement
- customer-facing GitHub installation and repo allocation flow
- central access-state gating

## Reuse strategy

The prior branch `feature/customer-control-plane-v1` should be used as a selective source for:

- `services/control_plane_records.py`
- `services/auth_service.py`
- `services/billing_service.py`
- `services/entitlements.py`
- `services/access_state.py`
- `services/github_provisioning.py`
- related focused tests

It should not be merged wholesale. Port modules in slices onto current `main` so the rename and latest dashboard/audit work remain intact.

## Branch

- `feature/driftguard-base44-stripe-handoff-v1` (merged to `main` on 2026-04-09)

## Implementation snapshot on 2026-04-09

This implementation is now merged to `main`.

Delivered on this branch:

- control-plane persistence for users, sessions, workspaces, memberships, subscriptions, entitlements, installations, repo connections, repo allocations, and webhook receipts
- GitHub OAuth start/callback flow with encrypted token storage and session issuance
- workspace bootstrap and app-shell access resolution
- canonical plan catalog with `free`, `starter`, `team`, and `enterprise`, plus legacy `business -> enterprise` compatibility
- provider-neutral entitlement capabilities via `dashboard_enabled` and `pr_comments_enabled`
- signed billing handoff claim persistence and activation flow for external providers
- free-tier activation plus Stripe checkout, billing portal support, and authoritative webhook-driven subscription/entitlement projection for fallback paid flows
- GitHub App install linkage, repository sync, repository allocation, and handoff into the existing onboarding engine
- setup-aware customer pages for landing, login, pricing, workspace creation, billing, install, repo setup, and app state
- actionable app-shell CTAs for incomplete setup states, the free comments-only terminal state, and the final active workspace state
- additive legacy SQLite migrations for control-plane columns plus repaired foreign-key rebuilds for `repo_connections` and `repo_allocations`
- dashboard JSON/API gating for incomplete or non-dashboard states when the control plane is active
- webhook allocation and entitlement gating for PR comment generation
- owner/admin protection on billing and provisioning mutations
- Stripe webhook ownership validation against stored customer/subscription records
- worker-side allocation and entitlement revalidation for queued PR audits
- stale webhook-delivery reclaim for crash-safe GitHub redelivery handling

Validated at the current checkpoint:

- targeted billing/control-plane/webhook/worker regression slice: `73 passed`
- targeted control-plane/access-state coverage now includes free-tier activation, signed claim creation/consumption, free-workspace dashboard denial, and webhook allocation enforcement
- live tunnel-backed validation for GitHub OAuth login/callback, workspace bootstrap, GitHub App installation linking, repo sync, repo allocation/onboarding, and dashboard unlock after simulated Team billing

Remaining operational follow-up outside this merged implementation:

- one Base44/Wix-backed live pass with real signed handoff activation instead of simulated billing state
- optional confirmation of the Stripe fallback path with real webhook forwarding

## Execution sequence

### Phase 1: Architecture lock

Status on 2026-04-07: complete.

Deliverables:

- this plan document
- access-state model document
- copy-ready issue analysis draft

Why first:

- prevents Base44 and DriftGuard from implementing different assumptions
- keeps auth, billing, install, and dashboard responsibilities explicit

### Phase 2: Control-plane persistence

Status on 2026-04-07: complete for the SQLite-first implementation slice.

Implement DB-backed records for:

- users
- github_identities
- user_sessions
- workspaces
- workspace_memberships
- billing_customers
- subscriptions
- entitlements
- github_installations
- repo_connections
- repo_allocations
- webhook_event_receipts

Rules:

- SQLite-first, PostgreSQL-ready naming and constraints
- additive schema evolution only
- keep current audit/onboarding persistence untouched

### Phase 3: Auth and session foundation

Status on 2026-04-07: complete for GitHub OAuth, session issuance, logout, and workspace bootstrap/resume.

Add:

- GitHub OAuth start and callback handling
- session issuance, validation, and logout
- query-param passthrough from Base44, including `source=base44` and optional `plan`
- workspace bootstrap / resume logic

### Phase 4: Plan catalog and billing entry

Status on 2026-04-07: complete for plan definitions, checkout, and portal flow entry points.

Add:

- canonical plan-code definitions
- plan-to-entitlement mapping
- in-app plan selection or preselection from CTA params
- local free-tier activation for non-billed plans
- signed handoff claim entry points for external paid providers
- Stripe Checkout session creation with workspace/user/plan metadata as a fallback path
- Stripe billing portal session creation

### Phase 5: Authoritative billing projection

Status on 2026-04-09: complete for external handoff claims, webhook verification, idempotent receipts, entitlement projection, and stored-owner Stripe workspace resolution.

Add billing activation surfaces with:

- `POST /api/billing/handoff/base44` with signed purchase activation handling and claim creation
- `GET /claim/{token}` plus `/app/billing/claim` for post-login claim consumption
- `POST /webhooks/stripe` with Stripe signature verification and fallback paid-plan projection
- event receipt storage for idempotency where webhook-driven providers are used
- projection of subscription/customer state into internal records
- entitlement updates only from verified billing-provider inputs

### Phase 6: GitHub install and repo allocation

Status on 2026-04-07: complete for installation linkage, repository sync, allocation, and onboarding handoff.

Add:

- GitHub App install URL generation
- installation callback/linking to workspace
- accessible repo sync
- repo allocation under entitlement limits
- handoff into existing onboarding engine for selected repos

### Phase 7: Access-state gating

Status on 2026-04-07: complete for central resolver use in setup-aware app/API routes and dashboard redirects.

Introduce a single resolver that determines whether the user is:

- unauthenticated
- missing workspace
- missing subscription
- waiting on billing confirmation
- payment failed
- waiting on GitHub install
- waiting on repo onboarding
- active comments only
- active
- canceled but still active
- expired read-only
- forbidden

Use this resolver for both HTML routes and app APIs.

### Phase 8: Setup-aware app surfaces

Status on 2026-04-07: complete for the v1 surfaces.

Add or adapt app-owned pages for:

- login entry
- workspace bootstrap
- billing
- install
- repo setup
- setup-aware app shell

These are not the Base44 public site. They are the authenticated product handoff and onboarding surfaces.

### Phase 9: Base44 integration contract

Status on 2026-04-08: documented and partially provider-validated; GitHub-side live validation is complete while real Stripe confirmation remains open.

Document stable entry points such as:

- `/auth/github/start?source=base44`
- `/auth/github/start?source=base44&plan=starter`

Also document:

- plan-code contract
- success/cancel behavior
- onboarding sequence after payment
- the rule that access changes only on webhook-confirmed billing state

### Phase 10: Validation

Status on 2026-04-09: merged to `main`; local validation complete; GitHub-side tunnel validation complete; real Base44/Wix and optional real Stripe provider confirmation remain operational follow-up items rather than merge blockers.

Required tests:

- control-plane records
- auth service
- billing service
- access-state resolver
- GitHub provisioning
- setup-aware UI routes
- regression coverage for existing onboarding and dashboard behavior

Validation rule:

- run focused suites per slice
- run full suite before PR

Observed results on this branch:

- `python -m pytest tests/test_control_plane_ui.py -q` -> `11 passed`
- `python -m pytest` -> `148 passed`

## Post-merge operational checklist

1. Start DriftGuard locally and expose it through a public tunnel.
2. Register the tunnel URL in the GitHub OAuth app and GitHub App webhook settings.
3. Forward Stripe test events to `/webhooks/stripe` if validating the fallback path.
4. Walk the Base44-style entry flow and capture any provider-backed defects.
5. Record provider-backed results in [CHANGELOG.md](../CHANGELOG.md) and [README.md](../README.md).

## Primary failure modes to avoid

1. Splitting logic between Base44 and DriftGuard ambiguously.
2. Trusting Stripe success redirect instead of webhooks.
3. Gating dashboard access ad hoc per route.
4. Rewriting existing onboarding logic instead of integrating it.
5. Letting Base44 pricing labels diverge from backend plan codes.

## Near-term implementation priority

The first code slice after documentation should be:

1. control-plane records
2. entitlements and config
3. auth/session service
4. access-state resolver

That sequence establishes the minimal backbone before Stripe and GitHub installation flows are layered on top.