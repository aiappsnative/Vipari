# Railway Launch Readiness Plan

Branch: `feature/railway-launch-hardening`

Issue: `#36 [Launch Blocker] Production-hardening and Railway deployment readiness for DriftGuard`

## Executive summary

This issue is not a pure deployment/configuration task. The current codebase is still **SQLite-native** at the persistence layer and **SQLite/SQS-native** at the queue layer. That means true production readiness on Railway requires two kinds of work:

1. immediate launch-hardening work that can and should land now
2. deeper persistence work required before the issue can honestly be called fully complete

The critical engineering principle for this branch is:

- do not fake production readiness with unsafe fallbacks
- do not hide architectural blockers behind env-var indirection
- do make the deployment shape, readiness behavior, queue choice, and secret handling production-grade and explicit
- do fail closed when production-critical assumptions are missing or unsafe

## Current-state audit findings

### 1. Production persistence is still SQLite-bound

Observed facts:

- `config.py` still defaults `DATABASE_URL` to `sqlite:///...`
- `services/persistence.py` exposes only `connect_sqlite(...)`
- operational and durable stores still use `sqlite3` directly across the codebase
- local/dev compose still mounts `/data` and points all services at the same SQLite file
- there is currently no Postgres adapter, migration framework, or dual-backend abstraction

Implication:

- true Postgres-backed Railway production is not available yet
- the issue cannot be considered fully done until persistence stops being SQLite-only

### 2. Queueing is not aligned with the target production architecture

Observed facts:

- webhook and worker queue selection is `sqlite` or `sqs`
- there is no Redis queue backend yet
- `REDIS_URL` exists in config/docs, but is not the queue implementation in practice

Implication:

- queue reliability on Railway is not yet aligned with the issue requirements

### 3. Split cloud service shape is only partially production-usable

Observed facts:

- `Dockerfile.api`, `run_api.py`, and `services/api_service.py` represent a split operator API/dashboard service
- the actual customer control plane, auth, and workspace app live in `main.py`
- current `Dockerfile.api` does not copy `main.py`

Implication:

- the current “api” service shape does not match the product surface the issue wants to launch
- the public API service must be aligned with the real app surface

### 4. Health endpoints are only liveness checks

Observed facts:

- `services/api_service.py` exposes `GET /health` returning `{status: ok}`
- `services/webhook_service.py` exposes `GET /health` returning `{status: ok}`
- there is no readiness concept checking config, persistence, or queue connectivity

Implication:

- Railway can currently mark a service healthy while it is unusable or dangerously misconfigured

### 5. Secret and production-safety rules are not explicit enough

Observed facts:

- production still permits local-file private key assumptions via `GITHUB_PRIVATE_KEY_PATH`
- secure-cookie, app-base-url, and production env constraints are not centrally validated
- there is no formal startup validation layer for production-critical config

Implication:

- deploys can come up in broken or insecure states without a clear startup failure

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

### Phase 6. Persistence blocker treatment

Changes:

- explicitly document that the current code remains SQLite-native
- if Track B is not completed in this branch, production mode must fail closed on SQLite rather than pretending support
- capture the persistence migration path as the remaining blocking work

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