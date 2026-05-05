# Issue #80 Plan: Blessed Production Topology

Issue: `#80 Suggestion: De-Emphasize Non-Core Run Paths and Bless a Single Production Topology`

## Executive Summary

Issue `#80` should be treated as release-critical production-readiness work, not as a cosmetic documentation pass.

The repo already contains meaningful production-hardening work:

- split `api` / `webhook` / `worker` service roles
- production fail-closed runtime guardrails
- Railway preflight and deployment guidance
- PostgreSQL-capable persistence path
- Redis production queue requirement for `webhook` and `worker`

But the operator-facing story is still too diffuse.

The current repo still presents multiple runnable modes with comparable visual weight:

- monolith `uvicorn main:app`
- SQLite local runs
- Docker-based local rehearsal variants
- local full-stack rehearsals
- eval harness and smoke DB flows

That is acceptable for engineering, but unsafe for launch unless the product clearly blesses one production topology and pushes all other paths into explicitly non-production categories.

This issue should therefore converge the repo onto one clear operator message:

**Production means Docker-deployed split services + Postgres + Redis, with explicit service-role contracts and fail-closed startup validation. Everything else is local dev, staging rehearsal, or internal evaluation.**

## Planning Stance

This plan is intentionally cautious.

The main risk is not that Vipari lacks a production topology. The main risk is that operators can still misread internal or local workflows as peer deployment options.

The plan below treats that ambiguity as a release blocker.

## Current-State Audit

### What is already in place

The following foundations already exist on `main` and should be treated as current truth rather than future aspirations:

- `config.py` already models `APP_ENV` and `SERVICE_ROLE`
- `run_api.py`, `run_webhook.py`, and `run_worker.py` already provide the split service entrypoints
- `services/runtime_guardrails.py` already fails closed in production on unsafe contracts such as:
  - SQLite production persistence
  - non-HTTPS `APP_BASE_URL`
  - insecure session cookies on public API/control-plane roles
  - file-path-only GitHub private key usage in production
  - non-Redis queue backend for production `webhook` / `worker`
- `scripts/railway_preflight.py` already validates role-aware readiness against runtime config, persistence, migrations, and queue reachability
- `docs/railway-launch-readiness-plan.md` and `docs/railway-deployment-guide.md` already describe a split production topology

### What is still risky

The remaining production-readiness problem is convergence and emphasis.

Observed repo-level risks:

- `README.md` still gives substantial space to local and internal run paths after introducing the production path
- direct monolith examples still appear in the main README and can be mistaken for generally acceptable deployment guidance
- the Docker wrapper script is convenient, but its current presentation is still too close to launch guidance rather than clearly dev-only guidance
- the control-plane preflight helper is still focused on provider/config presence, not on topology classification or launch-mode explanation
- local/eval/smoke workflows still sit near deployment instructions without one strong operator-facing hierarchy

### Working hypothesis for issue #80

The repo no longer needs a new production architecture.

It needs:

1. one canonical Docker-based production topology elevated above all other paths
2. explicit classification of all other paths as `local dev`, `staging rehearsal`, or `internal evaluation`
3. small contract hardening where current code or scripts still imply too much equivalence between these paths

## Goal

Make it operationally difficult to misunderstand how Vipari should be deployed in production.

Success means a new operator or future team member should infer, within minutes, that:

- production uses Docker images built from this repo's service Dockerfiles
- production uses split services
- production uses Postgres
- production uses Redis for `webhook` and `worker`
- cloud deployment should follow the GitHub -> Docker build/deploy path
- monolith and SQLite paths are for local development only

## Non-Goals

- no new orchestration platform is required for this issue
- no Helm chart is required to close the issue
- no removal of the local monolith workflow if it remains useful for developers
- no removal of eval harness infrastructure
- no forced reorganization of every internal script if labeling and placement solve the risk more safely
- no runtime refactor that reopens already-landed production-hardening work without evidence of a real gap

## Canonical Production Topology

The blessed production shape should be stated consistently across code comments, README, deployment docs, and preflight guidance.

It should also be stated plainly that the only blessed production run path is Docker-based deployment from this repo's committed service Dockerfiles. The platform may vary, but the operator story should not: cloud providers deploy Vipari by building and running the Docker images defined in this repository.

### Services

- `api`: customer control plane, dashboards, HTML pages, authenticated JSON APIs
- `webhook`: GitHub webhook ingress only
- `worker`: background audit processing, exports, background jobs

### Packaging and deploy path

- `Dockerfile.api` is the canonical production image definition for `api`
- `Dockerfile.webhook` is the canonical production image definition for `webhook`
- `Dockerfile.worker` is the canonical production image definition for `worker`
- production docs should describe GitHub-connected cloud deployment in terms of these Dockerfiles, not direct Python entrypoints
- direct `python` / `uvicorn` launch commands remain documented only for development, testing, or controlled rehearsal

### Data plane

- `DATABASE_URL` -> PostgreSQL
- `QUEUE_BACKEND=redis` for `webhook` and `worker`
- `REDIS_URL` -> Redis

### Runtime contract

- `APP_ENV=production`
- role-specific startup checks must fail closed
- public exposure only where intended:
  - `api`: public
  - `webhook`: public only for webhook ingress
  - `worker`: not public
  - Postgres/Redis: not public

## Required Deliverables

### 1. Canonical production guidance in the README

The README should be restructured so the first deployment story is the only production story.

Required changes:

- move the canonical split production topology to the top of deployment guidance
- lead with Docker as the only blessed production execution path
- state plainly that this is the recommended production path
- collapse production commands to the minimum operator-safe set
- move monolith, SQLite, and eval flows into clearly named lower sections

Target messaging:

- `Recommended production deployment`
- `Local development only`
- `Internal evaluation and smoke workflows`

### 2. Reclassification of non-core run paths

Every runnable path in docs should be assigned one explicit class.

#### `Production`

- Docker-based deploys from repository Dockerfiles
- split `api` / `webhook` / `worker`
- PostgreSQL
- Redis
- Railway or another cloud platform that builds and runs those Docker images from GitHub

#### `Local development`

- direct monolith `uvicorn main:app`
- SQLite-based local runs
- local Docker wrapper flows for developer convenience only

#### `Staging / production-like rehearsal`

- local Postgres + Redis split-service compose rehearsal
- preflight plus migration plus readiness verification

#### `Internal evaluation`

- eval harness
- smoke DBs
- seeded scenario flows
- OSS onboarding validation scripts

### 3. Role-and-environment contract review

This issue should re-audit role contracts, but only land code changes where a real ambiguity still exists.

Required review items:

- `api` role contract
- `webhook` role contract
- `worker` role contract
- `monolith` role contract as explicitly non-production

Questions to answer in code and docs:

- does each role state what it is allowed to expose?
- does each role state what secrets and dependencies are mandatory?
- do startup failures clearly explain why a non-canonical production combo is rejected?
- do helper scripts avoid silently implying that local flows are launch-grade?

### 4. Preflight and operator checklist convergence

The current preflight story should be tightened so it reinforces the blessed topology rather than only validating raw config.

Required outcomes:

- one environment matrix visible in the README and deployment docs
- one launch-day checklist that matches the canonical production topology exactly
- one preflight workflow that operators can run role-by-role before deploy
- one migration sequence with explicit stop conditions

### 5. Developer/internal script labeling

Developer convenience is allowed to remain, but not as ambiguous launch guidance.

Required changes:

- add explicit `dev-only` comments or notes to helper scripts where appropriate
- describe `scripts/docker-stack.ps1` as a local wrapper, not a production deployment method
- distinguish production Dockerfiles from local Docker helper flows so "Docker" does not become an overloaded term in docs
- ensure local convenience defaults are framed as intentional developer shortcuts rather than deploy defaults

## Proposed Implementation Phases

### Phase 1: Production message convergence

Primary goal:

- make the canonical production path impossible to miss

Likely files:

- `README.md`
- `docs/railway-deployment-guide.md`
- `docs/railway-launch-readiness-plan.md`

Work:

- elevate one Docker-first production section
- demote local and eval paths into clearly bounded sections
- add a simple environment matrix
- add a concise `do not use for production` warning on monolith/SQLite/direct-entrypoint paths

Acceptance criteria:

- a new reader sees one Docker-based production recommendation within the first deployment section
- no local path is described with production-neutral language

### Phase 2: Runtime and helper-script audit

Primary goal:

- confirm current guardrails actually match the blessed topology and identify only the remaining real gaps

Likely files:

- `services/runtime_guardrails.py`
- `config.py`
- `run_api.py`
- `run_webhook.py`
- `run_worker.py`
- `scripts/docker-stack.ps1`

Work:

- confirm `monolith` is documented as non-production
- confirm helper scripts do not hide unsafe assumptions
- add `dev-only` header comments where needed
- harden any remaining ambiguous bind/default behavior only if a real production misread is still possible

Acceptance criteria:

- no helper script looks like a first-class production entrypoint without explicitly saying so
- current startup contract stays fail-closed in staging/production

### Phase 3: Preflight and launch checklist alignment

Primary goal:

- make preflight, migration, and deploy steps reflect one single operating story

Likely files:

- `scripts/railway_preflight.py`
- `scripts/control_plane_preflight.py`
- `docs/database-migration-runbook.md`
- `docs/railway-deployment-guide.md`

Work:

- verify role naming and operator sequence are consistent
- make clear which preflight script is for production topology versus control-plane/provider checks
- document stop-the-line failures clearly

Acceptance criteria:

- operators can follow one deploy sequence without guessing which helper applies when
- local-only helpers are not confused with launch-time production checks

### Phase 4: Internal and evaluation path demotion

Primary goal:

- preserve internal tooling without letting it pollute release-facing guidance

Likely files:

- `README.md`
- selected docs under `docs/internal/`
- eval harness references in README and plan docs

Work:

- move or relabel internal-only guidance into clearly bounded sections
- keep evaluation infrastructure documented, but not adjacent to production launch guidance unless explicitly marked internal

Acceptance criteria:

- internal evaluation flows remain usable for engineering
- launch-facing docs no longer read like a menu of equal deployment options

## Specific Open Questions To Resolve During Implementation

These should be answered explicitly while doing the issue, not left implicit.

1. Should `monolith` startup in `APP_ENV=production` be allowed at all, or merely undocumented?
2. Is `scripts/control_plane_preflight.py` still named appropriately if issue `#80` wants one canonical pre-release validation story?
3. Should `scripts/docker-stack.ps1` remain in its current location, or is labeling sufficient?
4. Does the README need a sharper distinction between `staging rehearsal` and `production` so Postgres+Redis local compose does not read as a deployment recommendation?
5. Is there any remaining route surface or service mode that still weakens auth or exposure decisions based on convenience rather than explicit environment contracts?

## Validation Plan

Issue `#80` should not be closed on documentation edits alone.

Validation should cover both operator clarity and runtime contract safety.

### Automated validation

- targeted tests for runtime guardrails if any contract changes land
- preflight tests if preflight behavior or classification changes land
- split-service smoke coverage where modified
- focused README/doc consistency review in the PR itself

### Manual validation

- follow the production deployment section as if you were a first-time operator
- confirm there is only one recommended production path
- confirm local monolith and SQLite flows are unmistakably labeled as non-production
- confirm preflight guidance matches the deployment guide and migration runbook

## Release Gate For This Issue

Issue `#80` should be considered complete only when all of the following are true:

- the repo presents exactly one blessed production topology
- non-core run paths are clearly marked as local dev, staging rehearsal, or internal evaluation
- role-aware startup and readiness contracts still enforce the canonical production shape
- no developer helper script is easy to mistake for a production deployment recipe
- operator documentation, preflight flow, and migration guidance are aligned

## Explicit Failure Modes To Avoid

- treating this as a README wording tweak while leaving runtime ambiguity intact
- over-rotating into a large runtime refactor even though most contract hardening has already landed
- removing useful developer workflows instead of simply classifying them correctly
- documenting a production path that diverges from current startup guardrails
- presenting production and local Docker paths as equally recommended

## Recommended First Implementation Slice

The safest first slice is documentation convergence plus a narrow helper-script audit.

That means:

1. rewrite the README deployment hierarchy around one blessed production path
2. align Railway docs and the environment matrix
3. relabel local/eval/helper flows
4. land only the smallest code/script changes needed to remove remaining ambiguity

This ordering is deliberate.

If the docs are clarified first, the repo can stop teaching the wrong operating model immediately, while any remaining contract hardening can then be landed against a sharper, already-agreed topology.