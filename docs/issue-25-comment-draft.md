# Issue 25 Analysis Draft

This is the analysis I intended to post back to GitHub issue `#25`. The environment here cannot submit issue comments directly, so this file preserves the same content for documentation and later copy/paste if needed.

## Summary

Issue `#25` is primarily a DriftGuard control-plane and integration problem, not a Base44 implementation problem inside this repository.

Base44 should remain the public acquisition surface:

- public landing page
- positioning and pricing presentation
- CTA entry points
- visual storytelling

DriftGuard must own everything fragile or stateful after the CTA click:

- GitHub identity
- user session
- workspace creation and membership
- plan selection
- Stripe Checkout session creation
- authoritative Stripe webhook handling
- workspace subscription and entitlements
- GitHub App installation linkage
- repo allocation and onboarding
- setup-aware dashboard access gating

## What current `main` already has

Current `main` already provides:

- GitHub App auth primitives
- durable persistence for audits, onboarding, dashboard read models, and operator workflows
- dashboard routes and APIs
- repo onboarding and historical backfill flows
- split webhook / worker / API deployment shape

Current `main` does **not** yet provide:

- GitHub OAuth web login
- user/workspace/session records
- subscription/customer/entitlement records
- Stripe Checkout or portal flows
- Stripe webhook-driven access activation
- GitHub App installation linkage per workspace
- repo allocation against plan limits
- a central access-state resolver for setup-aware gating

## Important finding from prior work

This is not greenfield. The old branch `feature/customer-control-plane-v1` already contains a substantial foundation for the work required by issue `#25`, including:

- `services/control_plane_records.py`
- `services/auth_service.py`
- `services/billing_service.py`
- `services/entitlements.py`
- `services/access_state.py`
- `services/github_provisioning.py`
- setup-aware app routes and templates
- focused tests for control-plane behavior

That branch should be treated as a selective source of reusable work, not blindly merged wholesale.

## Recommended branch

All issue `#25` work should start from current `main` on a fresh branch:

- `feature/driftguard-base44-stripe-handoff-v1`

This avoids dragging older branch drift into the new implementation while still allowing selective reuse.

## Recommended execution plan

1. Document the canonical architecture and state machine first.
2. Port the control-plane persistence model from the old branch into current `main` shape.
3. Add GitHub OAuth login and DB-backed session handling.
4. Add workspace bootstrap and plan selection.
5. Add Stripe Checkout and billing portal creation.
6. Add authoritative Stripe webhook processing and entitlement projection.
7. Add GitHub App install linkage and repo allocation.
8. Gate dashboard/app access through a single access-state resolver.
9. Reuse the existing onboarding engine after repo allocation instead of replacing it.
10. Document the Base44 integration contract so the landing page can integrate against stable URLs and plan codes.

## Architectural rules to preserve

- Base44 does not own auth or billing state.
- Stripe webhooks, not browser redirects, change access.
- DriftGuard owns user, workspace, entitlement, install, and dashboard state.
- The dashboard must show setup-aware blocked states instead of blank or broken views.
- Existing audit, onboarding, and dashboard logic should be integrated, not rewritten unnecessarily.

## Primary risks

- duplicating earlier control-plane work instead of reusing it
- spreading gating logic across routes instead of centralizing it
- unlocking access from checkout success redirect instead of webhook-confirmed subscription state
- letting Base44 and DriftGuard plan codes drift apart
- breaking the existing dashboard or onboarding domain while adding the control plane

## Immediate next step

Begin implementation on `feature/driftguard-base44-stripe-handoff-v1` with architecture docs and the access-state model before porting backend modules.

## Addendum on 2026-04-07

The implementation proposed above is now largely landed on `feature/driftguard-base44-stripe-handoff-v1`.

Completed on the branch:

- control-plane persistence and schema version bump
- GitHub OAuth login, encrypted token storage, and session management
- workspace bootstrap and access-state resolution
- Stripe checkout, portal support, webhook verification, and entitlement projection
- GitHub App install linkage, repo sync, repo allocation, and dashboard gating
- setup-aware customer pages plus focused route-flow and service tests

Validated locally:

- focused control-plane route suite passed: `11 passed`
- full suite passed: `148 passed`

Remaining practical step before merge prep:

- complete one provider-backed tunnel-based pass with real GitHub OAuth and Stripe test mode to verify the external callbacks and webhook wiring end to end