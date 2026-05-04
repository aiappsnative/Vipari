# Dev Fallback Inventory

This document tracks runtime fallbacks and environment-sensitive shortcuts that
must remain strictly local-only or be removed before production release.

## Scope

Focus on behaviors that can affect authentication, authorization, or exposure of
control-plane surfaces.

## Inventory

### Control-plane activation gate

- File: `main.py`
- Surface: `_control_plane_active()`
- Current activation:
  - always active in production
  - active when workspaces exist
  - active for non-monolith roles
  - active when `APP_ENV` is not `local`
  - otherwise depends on `APP_BASE_URL` resolving away from localhost
- Risk:
  - controls whether some dashboard helpers return real auth decisions or local
    operator behavior
  - can make behavior depend on deploy shape rather than an explicit security
    mode
- Target state:
  - no protected API route should ever weaken auth because this helper returns
    false
  - if retained, this helper should only shape bootstrap UX, not API access

### Local debug workspace fallback

- File: `main.py`
- Surface: `_local_debug_workspace_context()` via `_current_workspace_context(..., allow_local_debug=True)`
- Current activation:
  - `APP_ENV=local`
  - `LOCAL_DEBUG_DISABLE_LOGIN=true`
  - localhost `APP_BASE_URL`
  - at least one workspace exists
- Risk:
  - injects workspace context without a session for browser flows
  - dangerous if environment or host detection drifts in staging or production
- Target state:
  - local-only, localhost-only, explicit opt-in
  - never available to JSON or REST routes
  - preferably replaced by fixtures/scripts over time

### Dashboard read fallback when control plane is inactive

- File: `main.py`
- Surface: `_require_dashboard_read_access()`
- Current activation:
  - returns `{}` instead of enforcing access when `_control_plane_active()` is false
- Risk:
  - protected callers can accidentally treat an empty context as successful access
  - behavior depends on runtime state rather than explicit authorization
- Target state:
  - protected routes must return `401`, `403`, or `503`
  - no auth-bearing route should receive a fake-success empty context

### Local owner fallback

- File: `main.py`
- Surface: `_has_local_owner_fallback()`
- Current activation:
  - owner config absent
  - not production
  - `APP_ENV=local`
  - localhost `APP_BASE_URL`
  - current user is the workspace billing owner
- Risk:
  - can elevate a user to owner/admin-equivalent control-plane access
  - `test` support increases the chance of fallback bleed-through into broader contexts
- Target state:
  - local-only, localhost-only, explicit opt-in
  - remove `test` from runtime activation
  - long-term preference is fixture/script replacement

### CP API entitlement shortcut outside production

- File: `main.py`
- Surface: `_has_cp_api_access()`
- Current activation:
  - returns `True` only when `APP_ENV=local`
- Risk:
  - broad non-production entitlement bypass for CP API access
  - acceptable for tests today, but staging must not inherit this behavior
- Target state:
  - staging should behave like production
  - tests should rely on explicit fixtures or entitlements where possible

### Worker authorization when no workspaces exist

- File: `services/cloud_worker.py`
- Surface: `_message_still_authorized()`
- Current activation:
  - returns `True` when `_control_plane_active(db_path)` is false
- Risk:
  - push/PR work can remain authorized just because workspace state is absent
  - this is the worker-side equivalent of a permissive inactive-control-plane shortcut
- Target state:
  - no production-like service should broaden authorization because workspace
    state is empty
  - bootstrap handling should be explicit, not permissive by default

### Local default bind addresses

- Files:
  - `run_api.py`
  - `run_webhook.py`
- Current activation:
  - both bind to `0.0.0.0` by default
- Risk:
  - local and preview runs can expose dev-only behavior on shared hosts
- Target state:
  - bind to `127.0.0.1` by default for local runs
  - require explicit override for externally reachable deployments

## Hardening Order

1. Config model and environment enum
2. Startup guardrails that fail fast for dev fallback misuse
3. Protected route helper hardening in `main.py`
4. Worker/webhook authorization hardening
5. Replace convenience fallbacks with fixtures or scripts where practical

## Current Status

- `AppEnv` enum and staging support added
- startup guardrails now reject dev auth fallbacks in staging/production-like environments
- preflight accepts `staging` and validates the stricter contract
- protected dashboard APIs no longer receive fake-success empty access contexts
- worker PR-event authorization now fails closed when control-plane state is absent
- local billing-owner elevation is restricted to true local runs
- staging now respects CP API entitlement flags instead of inheriting a blanket non-production bypass
- `run_api.py` and `run_webhook.py` now bind to loopback by default with explicit host overrides
- dashboard bootstrap fallback is restricted to `APP_ENV=local`
- local debug workspace fallback is still present for browser flows and remains the main convenience path left to replace or further constrain