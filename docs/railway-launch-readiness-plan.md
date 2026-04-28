# Railway Launch Readiness Plan

Branch: `feature/railway-launch-hardening`

Issue: `#36 [Launch Blocker] Production-hardening and Railway deployment readiness for DriftGuard`

## Executive summary

This issue is not a pure deployment/configuration task. At the time this plan was written, the codebase was still **SQLite-native** at the persistence layer and **SQLite/SQS-native** at the queue layer. The implementation on this branch has since added Redis queue support and a PostgreSQL-capable persistence adapter, but the engineering principle remains the same: production readiness must be earned with explicit contracts rather than deployment theater.

1. immediate launch-hardening work that can and should land now
2. deeper persistence work required before the issue can honestly be called fully complete

The critical engineering principle for this branch is:

- do not fake production readiness with unsafe fallbacks
- do not hide architectural blockers behind env-var indirection
- do make the deployment shape, readiness behavior, queue choice, and secret handling production-grade and explicit
- do fail closed when production-critical assumptions are missing or unsafe

## Current-state audit findings

Status refresh: the original audit captured a pre-hardening snapshot. The findings below reflect the current branch state after PostgreSQL-capable persistence, Redis queue support, split-service packaging, and readiness guardrails landed.

### 1. Production persistence is PostgreSQL-capable, with mixed adapter-level and simulated lifecycle coverage

Observed facts:

- `config.py` still defaults `DATABASE_URL` to `sqlite:///...`
- `services/persistence.py` now resolves SQLite and PostgreSQL locators and exposes a PostgreSQL-capable connection adapter
- application bootstrap and schema repair now flow through `services.schema_migrations` and `scripts/db_migrate.py`
- runtime guardrails fail closed in production when SQLite is configured
- local/dev still defaults to SQLite and many persistence call sites continue to rely on SQLite-compatible semantics exercised through the adapter layer
- `tests/test_persistence.py` covers `PostgresConnection` SQL translation behavior directly
- `tests/test_cloud_deployment.py` now covers restart and idempotency seams while the app is configured with a PostgreSQL locator, but those higher-level lifecycle proofs still use patched SQLite backing stores rather than a live Postgres service

Implication:

- the branch now supports a PostgreSQL-backed production path with stronger executable coverage on the highest-risk persistence seams, but real PostgreSQL integration confidence still depends on explicit environment-backed validation rather than these simulated lifecycle proofs alone

### 2. Queueing is aligned for production, but operational validation still matters

Observed facts:

- webhook and worker now support `QUEUE_BACKEND=redis`
- production runtime validation requires Redis for `webhook` and `worker` roles
- local/dev compatibility still preserves SQLite queue behavior for non-production workflows

Implication:

- Railway queue topology is now aligned with the intended production architecture, and the remaining concern is operational verification rather than missing Redis support

### 3. Split cloud service shape is now aligned with the intended public surface

Observed facts:

- `run_api.py` serves the real app from `main.py`
- `Dockerfile.api` copies `main.py`, `run_api.py`, and the required runtime tree
- webhook and worker remain split services with dedicated entrypoints

Implication:

- the deployment shape is now materially aligned with the intended Railway topology, so remaining work is about production hardening rather than correcting the service boundary itself

### 4. Readiness is now meaningful, but only as strong as the contracts it checks

Observed facts:

- `main.py`, `services/api_service.py`, and `services/webhook_service.py` expose `/health/ready`
- readiness validates runtime configuration, persistence reachability, and queue reachability when relevant
- readiness now fails when schema migrations have not been applied, rather than treating bare database connectivity as sufficient

Implication:

- Railway can now use readiness to reject misconfigured or partially bootstrapped services, but operators still need to run the migration entrypoint explicitly before cutting traffic

### 5. Secret and production-safety rules are not explicit enough

Observed facts:

- runtime guardrails centrally validate production-critical config in `services.runtime_guardrails`
- production rejects insecure cookie settings, non-HTTPS app URLs, SQLite persistence, and non-Redis webhook/worker queue configuration
- production continues to reject local-file-only GitHub private key assumptions in favor of inline key material

Implication:

- production startup is now materially safer and more explicit, though the broader issue still depends on continued PostgreSQL-backed operational validation before it can honestly be called fully complete

## Delivery strategy

This branch will execute in two tracks.

### Track A: launch-hardening that must land now

This track is implementable on this branch and materially improves deploy safety.

Scope:

- define Railway topology and service contracts
- align the public API service with the real app surface
- implement production startup validation and fail-fast rules
- add readiness-aware health endpoints
- implement Redis queue backend and worker/webhook Redis wiring
- produce Railway docs, env matrix, launch checklist, rollback guidance
- add preflight validation tooling and tests

### Track B: persistence completion required for honest issue closure

This track is the real architectural blocker.

Required scope:

- introduce Postgres-backed persistence adapter(s)
- define migration/bootstrap strategy for first deploy and repeat deploys
- move critical state off SQLite/file semantics
- validate redeploy/restart durability against Postgres

Status update:

- the branch now has targeted restart/reclaim/idempotency proofs for the main production persistence seams, plus direct adapter-level PostgresConnection tests
- the remaining Track B work is real PostgreSQL environment validation and release confidence, not the original adapter/contract gap

Important note:

- Track B is required before the umbrella issue can be called fully complete
- if Track B cannot be finished safely on this branch, the branch must still leave production incapable of silently launching on SQLite in “production” mode

## Implementation order

### Phase 1. Production contracts and fail-fast behavior

Changes:

- add explicit runtime environment settings such as `APP_ENV` and `SERVICE_ROLE`
- centralize validation for production-critical config
- require HTTPS `APP_BASE_URL` in production
- require `SESSION_COOKIE_SECURE=true` in production for public app/API surfaces
- require GitHub webhook secret for webhook service
- require inline `GITHUB_APP_PRIVATE_KEY` for production deployments; keep `GITHUB_PRIVATE_KEY_PATH` local-dev only
- require safe queue configuration for worker/webhook in production
- prevent silent SQLite production launch unless an explicit emergency override exists

Risks:

- overly strict validation can break local dev if applied universally

Mitigation:

- scope strict checks to `APP_ENV=production`
- keep local/dev defaults unchanged

Acceptance criteria:

- missing production-critical config fails at startup with a clear message
- local dev still runs without production-only requirements

### Phase 2. Correct service shape for Railway

Changes:

- make the public API service run the real customer control plane app from `main.py`
- ensure Dockerfile packaging matches runtime imports
- keep webhook and worker split as separate services
- document public/private service exposure rules

Risks:

- Docker image may miss files imported by `main.py`

Mitigation:

- audit imports and copy the full required runtime tree into the image
- cover the service entrypoint in tests/docs

Acceptance criteria:

- `api` maps to the real product surface
- Docker build inputs are explicit and reproducible

### Phase 3. Readiness and health checks

Changes:

- add liveness and readiness endpoints for public services
- readiness must check config sufficiency, persistence reachability, and queue reachability when relevant
- keep endpoints fast and non-sensitive
- document Railway health-check paths

Risks:

- readiness checks can become slow or flaky if they do too much

Mitigation:

- use shallow connectivity checks only
- avoid expensive GitHub or model calls in health endpoints

Acceptance criteria:

- unhealthy/misconfigured deploys fail visibly
- healthy deploys report ready only when core dependencies are reachable

### Phase 4. Redis queue productionization

Changes:

- implement `RedisQueue`
- allow `QUEUE_BACKEND=redis`
- use Redis in production webhook/worker guidance and validation
- preserve existing SQLite queue for local/dev tests

Risks:

- queue semantics may diverge from current retry/DLQ expectations

Mitigation:

- match current queue interface precisely
- add focused enqueue/dequeue/ack/nack/DLQ tests

Acceptance criteria:

- webhook and worker can use Redis as the production queue backend
- retries and DLQ semantics remain diagnosable

### Phase 5. Railway documentation and preflight tooling

Changes:

- write Railway deployment guide
- add production env-var matrix by service
- add launch-day checklist and rollback notes
- add production preflight script validating required env and service-role rules
- optionally add `railway.json` if it improves reproducibility without hiding platform details

Risks:

- docs drift from implementation

Mitigation:

- derive docs directly from runtime settings and service-role contracts

Acceptance criteria:

- an operator can stand up the Railway project without guessing
- launch-day setup is checklist-driven rather than tribal knowledge
- the database bootstrap path is explicit, scriptable, and recorded in a runbook

### Phase 6. Persistence completion

Changes:

- implement a PostgreSQL-backed persistence path without regressing the SQLite developer workflow
- keep production mode fail-closed on SQLite so Railway cannot silently launch on file-backed persistence
- verify that runtime readiness checks actual database connectivity rather than a placeholder capability flag

Risks:

- pressure to ship with unsafe SQLite semantics on Railway

Mitigation:

- fail fast in production mode
- document the blocker clearly in code and docs

Acceptance criteria:

- the repo cannot accidentally present itself as Postgres-ready when it is not

## Problems likely to occur and how they will be handled

### Problem: `main.py` packaging breaks in the API container

Response:

- update `Dockerfile.api` to copy `main.py` and all runtime dependencies it imports
- validate imports with targeted tests and startup checks

### Problem: Redis queue behavior differs from SQLite queue timing

Response:

- retain the current queue interface and retry contract
- add dedicated queue tests for ack/nack/retry/DLQ behavior
- keep SQLite queue for dev/test fallback only

### Problem: production validation blocks existing local workflows

Response:

- gate strict rules behind `APP_ENV=production`
- keep local defaults unchanged
- document the difference explicitly

### Problem: pressure to mark the issue complete without Postgres support

Response:

- do not declare full closure unless production persistence is actually Postgres-backed
- if needed, land all other hardening now and leave a clearly named persistence blocker slice

## Security posture requirements for every implementation step

- no secret file dependency in production images
- no public exposure of worker/Redis/Postgres surfaces
- no readiness signal when critical config is missing
- no production downgrade to insecure cookies or localhost URLs
- no silent fallback from production-intended queue/persistence choices to local-dev behavior
- no logging of secret material

## Validation plan

### Automated checks to add or update

- production-settings validation tests
- readiness endpoint tests
- Redis queue tests
- webhook/worker queue-backend selection tests
- Docker/runtime service-shape tests where practical
- preflight-script tests

### Manual/operator validation to document

- Railway service creation order
- env-var entry by service
- public/private exposure review
- first deploy bootstrap steps
- smoke tests for app, webhook, worker, queue, and auth wiring

## Exit criteria for this branch

This branch should leave the repo in one of two acceptable states:

1. full Track A implemented, fully tested, with Track B still explicitly blocking true launch, or
2. full Track A plus Track B implemented, allowing honest closure of issue `#36`

What is not acceptable:

- claiming Railway production readiness while the app can still silently launch in production on unsafe local assumptions
- leaving service topology, health checks, and secret handling ambiguous