# Database Migration Runbook

## Purpose

This runbook defines the supported bootstrap and migration path for Vipari databases.

For production, this runbook is part of the single blessed Docker-based deployment path. Use it alongside `scripts/railway_preflight.py` and the split-service Docker deploy flow; do not substitute local smoke helpers for this migration step.

Current migration contract:

- application schema bootstrap and repair are applied through `services.schema_migrations`
- the canonical operator entrypoint is `python scripts/db_migrate.py`
- runtime startup paths call the same migration entrypoint indirectly through `init_db(...)`
- applied migrations are recorded in the `schema_migrations` table

## Current migration set

- `0001_bootstrap_relational_schema`
  - creates the relational application schema for the active backend
  - applies legacy SQLite repair steps when running against SQLite
  - skips SQLite-only rebuild logic when running against PostgreSQL

## When to run migrations

### Local development

- runtime startup will bootstrap automatically
- you can also run `python scripts/db_migrate.py --db <path>` manually when inspecting a database file

### Production and pre-production

- run `python scripts/db_migrate.py` before cutting traffic to a new deploy
- use the final production `DATABASE_URL` and environment variables when running the command
- when `APP_ENV=production`, `scripts/db_migrate.py` now rejects SQLite targets and must point at the production PostgreSQL locator
- do not rely on ad hoc first-request bootstrapping as the operational migration step
- do not treat local monolith, local Docker helper, or internal smoke workflows as substitutes for this production migration sequence

## First deploy procedure

1. Provision PostgreSQL.
2. Set the production `DATABASE_URL`.
3. Run `python scripts/db_migrate.py` with the production environment loaded.
4. Confirm the command exits successfully and records `0001_bootstrap_relational_schema`.
5. Deploy or start the Docker-based `api`, `webhook`, and `worker` services.
6. Confirm `/health/ready` passes on the public services.

## Repeat deploy procedure

1. Run `python scripts/db_migrate.py` against the target database.
2. Confirm the command reports no pending migrations.
3. Deploy the new Docker images for the application version.
4. Verify health checks and core smoke flows.

## Failure handling

### Migration command fails before services are updated

- stop the rollout
- fix the migration error before deploying the new app version
- if the failure is a SQLite-target rejection in production, correct `DATABASE_URL` or the `--db` override to point at PostgreSQL rather than bypassing the guardrail
- do not force services live against a partially migrated database

### Services fail readiness after a migration

- inspect the migration output and application logs
- if schema creation succeeded but app startup fails, roll back the application image first
- do not revert the database manually unless there is a documented reverse migration for the specific change

## Notes

- SQLite remains supported for local development only
- PostgreSQL is the intended production backend
- the migration command now enforces that same production backend contract before applying schema changes
- production assumes split-service Docker deployment, not direct monolith startup
- future schema changes should add new versioned migrations rather than expanding `0001_bootstrap_relational_schema`