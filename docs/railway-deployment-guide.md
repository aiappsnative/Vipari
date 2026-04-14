# Railway Deployment Guide

## Target topology

Railway project services:

- `api`:
  public
  Docker source: `Dockerfile.api`
  runtime entrypoint: `run_api.py`
  role: customer control plane, dashboard, auth, setup, billing, workspace app
- `webhook`:
  public
  Docker source: `Dockerfile.webhook`
  runtime entrypoint: `run_webhook.py`
  role: GitHub App webhook ingress only
- `worker`:
  private
  Docker source: `Dockerfile.worker`
  runtime entrypoint: `run_worker.py`
  role: async queue consumption, audits, background processing
- `postgres`:
  private
  Railway managed Postgres
  role: intended durable production store
- `redis`:
  private
  Railway managed Redis
  role: production queue backend

## Public/private exposure rules

- `api` must be public
- `webhook` must be public
- `worker` must remain private
- `postgres` must remain private
- `redis` must remain private

## Service env matrix

### Shared production settings

- `APP_ENV=production`
- `APP_BASE_URL=https://<your-api-domain>`
- `GITHUB_APP_ID`
- `GITHUB_APP_PRIVATE_KEY`
- `APP_ENCRYPTION_KEY`

### API service

- `SERVICE_ROLE=api`
- `SESSION_COOKIE_SECURE=true`
- `OWNER_GITHUB_LOGIN` and/or `OWNER_GITHUB_USER_ID` and/or `OWNER_EMAIL`
- `GITHUB_OAUTH_CLIENT_ID`
- `GITHUB_OAUTH_CLIENT_SECRET`
- `GITHUB_OAUTH_CALLBACK_URL=https://<your-api-domain>/auth/github/callback`
- `DATABASE_URL=<postgres url>`

### Webhook service

- `SERVICE_ROLE=webhook`
- `GITHUB_WEBHOOK_SECRET`
- `QUEUE_BACKEND=redis`
- `REDIS_URL=<railway redis url>`
- `DATABASE_URL=<postgres url>`

### Worker service

- `SERVICE_ROLE=worker`
- `QUEUE_BACKEND=redis`
- `REDIS_URL=<railway redis url>`
- `DATABASE_URL=<postgres url>`
- `OPENAI_API_KEY` or `FOUNDRY_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AI_MODEL`

## Health checks

Configure Railway health checks:

- API liveness: `/health`
- API readiness: `/health/ready`
- webhook liveness: `/health`
- webhook readiness: `/health/ready`

Use readiness for final deploy health if Railway supports it for your service type.

## Launch-day checklist

- create Railway Postgres
- create Railway Redis
- create `api`, `webhook`, and `worker` services
- attach the correct Dockerfile to each service
- set `APP_ENV=production` on all three services
- set `SERVICE_ROLE` correctly on each service
- set all required production secrets as env vars
- confirm `worker`, `postgres`, and `redis` are not public
- run `python scripts/railway_preflight.py --service-role <role> --app-env production` locally against the production env set before deploy
- confirm GitHub OAuth callback URL matches the API domain exactly
- confirm GitHub App webhook URL matches the webhook domain exactly

## Rollback notes

### Bad deploy

- redeploy the last known-good image/commit
- do not keep a partially healthy deploy live if `/health/ready` fails

### Broken login

- check `APP_BASE_URL`
- check `GITHUB_OAUTH_CALLBACK_URL`
- check `SESSION_COOKIE_SECURE`
- confirm the API domain is HTTPS and matches the GitHub OAuth app settings

### Broken webhooks

- check `SERVICE_ROLE=webhook`
- check `GITHUB_WEBHOOK_SECRET`
- check GitHub App webhook URL
- check `QUEUE_BACKEND=redis` and `REDIS_URL`

### Persistence blocker

- if production startup fails due to SQLite detection, do not bypass the guardrail casually
- the correct response is to finish the Postgres persistence migration rather than force a SQLite production launch