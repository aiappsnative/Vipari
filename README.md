# Vipari

Vipari is a GitHub App backend that audits pull requests for changes that can alter AI system behavior, especially prompts, guardrails, model-routing logic, tool access, and related policy artifacts.

The product direction is now explicitly GitHub-native and static-first: Vipari treats risk and drift as properties of prompts, policies, model settings, tool definitions, and agent wiring visible in code review, rather than as a runtime observability problem.

Its job is not just to say that “an AI file changed”, but to help reviewers answer the customer-critical question:

**Did this PR materially change how the AI behaves, what it is allowed to do, or what it may reveal?**

Near-term, the product is being shaped around one especially important follow-up question:

**Does this AI-related PR need escalation before merge?**

## Customer value

Vipari provides value by reducing the gap between ordinary code review and safe AI change review.

For customers, that means:

- catching risky prompt and guardrail changes before they reach production
- explaining *why* a change is risky, not just that a file changed
- preserving audit history so teams can reason about drift over time
- making AI review operationally practical inside the existing GitHub PR workflow
- providing a defensible review trail for security, compliance, and platform teams

In product terms, Vipari is moving toward becoming a **change intelligence layer for AI behavior**.

## Why The Name?

Vipari is named from *viparinamadhamma* — the idea that systems we treat as fixed are in fact subject to change.

That maps directly to the product thesis: prompts, tools, policies, and model routing all change, and Vipari exists to make those changes visible, reviewable, and governable.

The product wedge is the PR review workflow. Dashboard and history views remain important, but they should reinforce PR-level decisions rather than replace them.

For the enduring product thesis behind that direction, see [SOUL.md](SOUL.md).

## Current status

The current `main` branch now includes the merged static-first drift engine milestone plus the follow-on escalation, approved-baseline, repo-provenance, repo-evidence, dashboard audit/performance, live default-branch tracking, baseline approval workflow, customer control-plane, and dashboard control-tower slices.

In practical terms, Vipari currently provides:

- queue-backed GitHub App PR auditing with deterministic analysis, semantic review, retry/fallback behavior, episode-aware managed PR comments, and exact dashboard deep links for the current PR review episode when a public app URL is configured
- escalation-aware PR review with decision-first comments, risk-emoji headers, concrete evidence panels, and GitHub label sync for before-merge escalation cases
- persisted pull-request lifecycle state across audit jobs and durable audit records, including close/reopen and merge metadata
- approved-baseline-aware static drift profiling for prompts, configs, and related AI control surfaces
- onboarding and selective historical backfill for repository-level artifact inventories and profile history
- a triage-first dashboard surface with portfolio Triage/Coverage modes and repo case-file drill-down pages, including baseline provenance in repo/history views
- a shared audit-focused dashboard shell across portfolio, repo audit, and repo setup surfaces, with repo-level journey context, direct audit navigation from repository setup, and obscured entitlement-aware deep-link shells that preserve the requested review context when full dashboard access is unavailable
- explicit repo and artifact baseline approval review, including pending candidate state, approval history, and snapshot-driven rebaseline proposals
- live default-branch posture tracking driven by push-triggered branch scans so landed drift stays current between PRs
- landed drift views driven by approved baselines plus merged-history evidence, while proposal-only PR audit evidence remains separate from landed-history posture
- reviewer queues now distinguish `proposal only`, `proposal + history`, and `history only` evidence so the repo case file can route reviewers to the right PR or merged commit without contaminating landed posture
- repo-detail provenance links that route directly to the backing PR or commit when stored source context exists
- repo-scoped pre-audit relevance decisions are now readable from the dashboard surface and can be surfaced inside PR/head-scoped repo case-file views
- concise `What changed`, `Why flagged`, and `Where` explanations in both overview and repo dashboard surfaces
- baseline-vs-current posture detail with qualitative drift labels, per-attribute findings, and code-level evidence when stored snapshots are available
- a lightweight baseline-promotion action that lets operators promote the latest stored source version as the approved baseline for an artifact
- a control-tower portfolio layer with workspace posture, ranked escalation queueing, and repo-level decision panels that keep review attention on the next human action
- workspace overview and escalation links now preserve `artifact`, `pr`, and `head_sha` so reviewers can move from portfolio triage into the exact repo review episode
- workspace-scoped pending proposal views that preserve human-vs-agent origin and no longer leak cross-workspace proposal metadata
- a contextual Help Center at `/app/help` that reflects current workspace onboarding/baseline/export state instead of a static placeholder
- batched overview aggregation, repo-preview caching, fingerprinted static asset caching, and `Server-Timing` response instrumentation for materially faster dashboard loads
- real OSS onboarding validation against `doria90/openfang` and `doria90/hermes-agent`, including larger-repo historical backfill and dashboard rendering
- bounded large-repo onboarding through narrower candidate-path discovery and direct GitHub contents API fetches for artifact snapshots
- GitHub OAuth login and encrypted session-backed identity state for the customer control plane
- Base44 handoff source/plan passthrough across login, workspace bootstrap, claim continuation, and billing entry
- workspace bootstrap, membership-aware access resolution, and setup-aware app surfaces
- a first-class `free` plan with one-repository entitlement, PR comments enabled, and dashboard access disabled
- provider-neutral entitlement flags separating PR-comment access from dashboard access
- signed billing handoff claims for external providers, with Base44/Wix-style payment-first activation and workspace claim flow
- local free-tier activation plus optional Stripe fallback for paid checkout and billing portal support
- GitHub App install linkage, setup-URL callback handling, synced repository connection inventory, and repo allocation into the existing onboarding engine
- additive SQLite repair migrations for legacy control-plane databases, including rebuilt `repo_connections` and `repo_allocations` foreign keys that now correctly target `github_installations.installation_id`
- setup-state persistence that now recomputes `workspaces.setup_state` from entitlement, install, and onboarding facts
- dashboard gating that blocks incomplete setup states from falling through to broken dashboard routes, including JSON API routes when the control plane is active
- production deployments always gate `/dashboard` through login, while true localhost operator mode can still expose the dashboard directly for local seeded inspection
- webhook gating that suppresses PR audits/comments for managed repos that are installed but not allocated or not entitled for comments, while leaving unmanaged legacy installs compatible with queued audits
- owner/admin-only protection for billing and provisioning mutations so viewer roles can inspect state but not mutate it
- actionable setup-state, free-tier, and active-state app shells so `/app` always exposes a real continuation path
- Starter-and-above profile pages with editable display name, read-only GitHub identity, and provider-sourced next payment date display
- strictly allowlisted admin pages covering registered users, dashboard-entitled workspaces, billing handoff claims, and unclaimed/public GitHub App installations
- public GitHub App install callback capture so direct marketplace/setup flows are visible before workspace claiming completes
- a dedicated `scripts/control_plane_preflight.py` helper for tomorrow's provider-backed setup checks
- Stripe webhook ownership hardening so paid-plan activation now resolves through stored Stripe customer/subscription bindings instead of trusting workspace metadata alone
- worker-side allocation and entitlement revalidation before queued PR audits run, plus stale webhook-delivery reclaim for crash-safe redelivery
- customer self-service API key management at `/app/settings/api-keys`: scope-gated machine principal creation with one-time secret flash delivery (atomic creation + flash in a single transaction, consumed on first GET), revocation, and per-workspace principal limits
- client-credentials token exchange at `/cp/auth/token` with sliding-window rate limiting (20 req/60 s), constant-time secret comparison, and full audit logging per exchange
- a customer-facing Agent Integrations surface at `/app/integrations/mcp` with a downloadable MCP connector package, hosted broker token exchange, tool discovery, and workspace-bound read-first invocation routes

Latest merged validation on 2026-04-30:

- focused post-merge dashboard/control-plane validation passed locally after the issue `#62` merge and proposal-visibility hardening:
	- `pytest tests/test_dashboard_api.py -k "test_dashboard_overview_api_filter_mine_limits_repos_to_current_allocator or test_pending_proposals_api_requires_repo_visibility or test_pending_proposals_api_scopes_to_workspace_and_preserves_agent_origin or test_pending_proposals_api_response_shape"`
	- `pytest tests/test_dashboard_control_tower.py -k "pending_proposals"`
- the latest broader full-suite local verification recorded before this merge remains `438 passed`
- tunnel-backed live validation previously confirmed GitHub OAuth handoff, workspace bootstrap, GitHub App install linkage, repo connection sync, repo allocation for `doria90/dummyAI`, and dashboard unlock after simulated Team billing

For detailed roadmap status, see [Plan.MD](Plan.MD). For architecture details, see [docs/detection-engine-plan.md](docs/detection-engine-plan.md).

The dashboard should now be read as two linked product surfaces:

- `/dashboard` is the portfolio decision surface for triage, hotspots, and coverage trust, with a secondary coverage mode for inventory and pattern scans
- `/dashboard/{owner/repo}` is the repo case file for baseline-relative posture, prioritized review targets, lower-confidence findings, artifact-level evidence, and approved-baseline promotion

The active repo-evidence slice also sharpens the ranked queue inside those surfaces:

- proposal-only PR evidence can drive the primary review target, rationale, recommended action, and queue priority when no merged-history snapshot is the right first stop
- mixed evidence now points reviewers to the PR first while preserving the latest merged commit as supporting context
- landed posture remains history-backed, so proposal context improves reviewer actionability without being mistaken for merged drift

## What Vipari does today

- receives GitHub `pull_request` webhooks at `/webhook`
- verifies webhook signatures
- fetches private PR diffs using GitHub App installation auth
- retries transient opened-PR diff `404`s and reconstructs synchronize-event diffs from exact base/head commit trees to avoid stale PR snapshot races
- runs a fast AI relevance gate on the webhook path
- queues relevant audits for background execution
- claims queued jobs atomically so concurrent workers cannot double-process the same audit
- performs deterministic analysis of AI-relevant changes
- extracts a static agent attribute profile from prompt/config text so future audits can compare design-level drift against baselines
- stores static artifact profiles in audit history with explicit baseline provenance so later versions can compare against an approved baseline when available
- keeps PR comments reviewer-focused on risk, escalation, and recommendation rather than internal drift metrics
- applies or removes the GitHub escalation label so the PR reflects the latest high-confidence before-merge recommendation
- exposes read-side trend helpers for repo summaries and artifact drift leaderboards
- supports baseline-first repository onboarding that persists discovered AI artifacts and baseline versions
- supports selective historical backfill planning and execution for onboarded artifacts
- persists historical artifact versions and static profile lineage for backfilled snapshots
- exposes baseline provenance in dashboard and history read models so fallback vs approved authority is visible outside the PR comment
- stores baseline, historical, and PR snapshot content so dashboard explanations can attach code-level evidence to posture drift
- builds compliance export packages from persisted baseline, audit, posture, and drift records, with per-file manifest hashes and optional raw artifact content limited to approved baselines plus in-range PR scan versions
- exposes JSON query APIs for repository listings and unified dashboard payloads
- exposes an overview dashboard API at `GET /api/dashboard/overview`
- exposes a repo-scoped pre-audit relevance decisions API at `GET /api/repos/{owner/repo}/relevance-decisions`
- exposes local dashboard pages at `/dashboard` and `/dashboard/{owner/repo}`
- includes `scripts/repo_ops.py` for local operator workflows and read-side inspection
- prepares structured semantic review context for the LLM
- falls back to a deterministic preliminary audit when the model call is permanently unavailable
- upserts the managed PR comment for the current PR head SHA while preserving prior-episode comments for earlier commits
- includes a dashboard deep link in managed PR comments when the configured app base URL is publicly reachable, and carries the exact `head_sha` episode through to the repo dashboard selector
- persists audit, finding, artifact, and comment history for later analysis
- updates stored PR lifecycle state on `opened`, `synchronize`, `closed`, and `reopened` webhook flows without leaving stale close/merge timestamps behind
- queues a repo branch scan when a tracked PR closes as merged so current artifact state, history, and Version Journey advance from the merge commit even if a separate push delivery is absent or delayed
- marks jobs failed instead of pretending success when comment posting or durable persistence breaks
- provides a customer-facing self-service API key management UI at `/app/settings/api-keys` where workspace owners and admins can create scope-gated machine principals, receive the one-time `client_secret` on creation, and revoke keys
- exchanges client credentials for short-lived JWTs at `/cp/auth/token` with sliding-window rate limiting, constant-time secret verification, production entitlement gating, and per-exchange audit log entries
- provides a customer MCP integration flow: `/app/integrations/mcp` for setup and download, `POST /api/agent-integrations/mcp/token` for short-lived broker tokens, `GET /api/agent-integrations/mcp/tools` for workspace-scoped tool discovery, and `POST /api/agent-integrations/mcp/invoke` for brokered read-first tool execution
- accepts structured feedback on PR audits at `POST /cp/audits/{audit_id}/feedback` (`drift.write.low`): append-only events with validated `kind` (six values), optional bounded `comment`, and bounded `metadata`; workspace isolation enforced via 404-masking
- captures PR-review feedback signals for audit episodes through three v1 paths: explicit reviewer submissions on managed feedback links, coarse PR close/merge lifecycle outcomes, and GitHub reactions refreshed against Vipari-managed comments/reviews
- records triage state transitions on PR audits at `POST /cp/audits/{audit_id}/triage` (`drift.write.low`): append-only events with validated `state` (three values) and optional bounded `reason`; never mutates `pull_request_audits`
- returns export job status (without `result_blob`) at `GET /cp/workspaces/{workspace_id}/exports/{export_id}` (`drift.read`)

Local operator workflows for that feedback loop now include:

- `python scripts/repo_ops.py feedback-events owner/repo --db path/to/db.sqlite` to list persisted feedback events
- `python scripts/repo_ops.py feedback-events owner/repo --db path/to/db.sqlite --kind pr_outcome --output feedback-events.json` to filter by event kind and write the same JSON payload to a file
- `python scripts/repo_ops.py refresh-feedback-reactions owner/repo INSTALLATION_ID --audit-id 123 --db path/to/db.sqlite` to force-refresh GitHub reactions for one persisted audit episode
- `python scripts/repo_ops.py refresh-feedback-reactions owner/repo INSTALLATION_ID --pr-number 88 --head-sha abc123 --db path/to/db.sqlite` to refresh all matching audits for a specific PR/head

## High-level architecture

- **Webhook path:** verify signature, fetch diff, run relevance gate, enqueue audit job
- **Worker path:** deterministic analysis, semantic review, retry/fallback handling, current-head comment upsert, escalation-label sync, durable persistence
- **Static drift layer:** derive design attributes from prompts/configs and compare them to a baseline to measure design drift without runtime data
- **Persistence:** operational queue tables plus durable audit/history tables, artifact versions, and static profile records in one relational store for now
- **Customer agent integration path:** machine-principal credentials mint short-lived MCP broker tokens, the hosted broker exposes a curated workspace-bound read surface, and the downloadable connector stays thin so internal control-plane tokens never leave Vipari

## Compliance export package

Vipari now supports a compliance export package built from persisted repository evidence rather than ad hoc rendering-time placeholders.

Current export behavior:

- core compliance files are generated from approved baseline records, baseline audit history, persisted PR audits, findings, and repo posture snapshots within the requested time window
- drift-mode files are generated from persisted static artifact profiles and artifact versions recorded during PR audits in that same window
- PR scan history labels whether reviewer-facing output came from a deterministic fallback path or an AI-assisted review narrative
- `manifest.json` includes a SHA-256 hash per exported file so package integrity can be checked downstream
- optional raw content export is intentionally narrow: when enabled, `09-artifact-content.json` includes approved baseline content and in-window PR scan artifact content, labels each row by control-surface provenance, and does not include historical backfill content

This scope is deliberate. The export is meant to answer what baseline was approved, what changed in the requested period, and what evidence supported that conclusion, without becoming a noisy historical archive dump.

For EU AI Act readiness positioning, treat these labels as transparency metadata, not legal conclusions. Vipari distinguishes deterministic records, AI-assisted review output, and human-reviewed baseline decisions so operators can explain where a package element came from.

## Static drift profile model

The first implemented drift-engine slice introduces a static attribute model for GitHub-visible AI artifacts.

The current profile dimensions are:

- `guardrail_robustness`
- `capability_risk`
- `autonomy_level`
- `stability_vs_creativity`
- `governance_strength`
- `change_frequency`
- `semantic_density`

These are computed from static signals such as:

- instruction and constraint density (`must`, `never`, `do not`, `always`)
- explicit limits (`up to`, `above`, `max`, bounded authority wording)
- tool and privilege wording (read vs write, production vs sandbox, sensitive systems)
- autonomy markers (steps, loops, parallelism, human approval hints)
- model settings such as `temperature` and `top_p`
- governance inputs such as CODEOWNERS requirements, review strength, and recent churn

This gives Vipari a concrete foundation for future baseline comparison, trend analysis, and PR-facing drift summaries without relying on runtime telemetry.

## Requirements

- Python 3.11+
- A GitHub App installed on the repository you want to audit
- An Azure OpenAI or compatible Foundry endpoint
- ngrok for local webhook testing

## Environment setup

Copy [.env.example](.env.example) to `.env` and fill in your real values.

Required variables:

- `GITHUB_APP_ID`
- `GITHUB_PRIVATE_KEY_PATH` or `GITHUB_APP_PRIVATE_KEY`
- `GITHUB_WEBHOOK_SECRET`
- `OPENAI_API_KEY` or `FOUNDRY_API_KEY`
- `AZURE_OPENAI_ENDPOINT`

Optional variables:

- `AI_MODEL` (defaults to `gpt-4o`)
- `FOUNDRY_PROJECT_ENDPOINT`
- `GITHUB_PAT`
- `NGROK_AUTHTOKEN`
- `AUDIT_DB_PATH`
- `APP_BASE_URL`
- `APP_ENCRYPTION_KEY`
- `SESSION_COOKIE_NAME`, `SESSION_COOKIE_SECURE`, and `SESSION_TTL_SECONDS`
- `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, and `GITHUB_OAUTH_CALLBACK_URL`
- `BILLING_HANDOFF_SECRET`, `BILLING_HANDOFF_TTL_SECONDS`, and `BASE44_CHECKOUT_URL`
- `OWNER_GITHUB_LOGIN`, `OWNER_GITHUB_USER_ID`, and `OWNER_EMAIL` for the single owner-locked admin surface
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PORTAL_CONFIGURATION_ID`
- `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_TEAM`, `STRIPE_PRICE_ENTERPRISE`, and `STRIPE_PRICE_BUSINESS`
- `REDIS_URL`
- `QUEUE_BACKEND`, `SQS_QUEUE_URL`, and `SQS_DLQ_URL`
- `API_ADMIN_TOKEN` for the split API service
- `API_PORT` and `WEBHOOK_PORT` for local split-service overrides; deployed services still honor platform-provided `PORT`
- `ENABLE_METRICS` (defaults to `false`)

Control-plane preflight helper:

```bash
python scripts/control_plane_preflight.py
```

Use it before the live-provider run to confirm the GitHub OAuth, GitHub App, billing-provider, and app-base settings are populated and consistent.

## Installation

Install dependencies for local development and test workflows:

```bash
pip install -r requirements.txt
```

## Recommended production deployment

Vipari has one blessed production run path: cloud deployment from this repository's committed service Dockerfiles.

Production means:

- Docker images built from `Dockerfile.api`, `Dockerfile.webhook`, and `Dockerfile.worker`
- split `api`, `webhook`, and `worker` services
- PostgreSQL via `DATABASE_URL`
- Redis via `REDIS_URL` with `QUEUE_BACKEND=redis` for `webhook` and `worker`
- `APP_ENV=production` with fail-closed runtime guardrails and readiness checks

Recommended operator sequence:

```bash
python scripts/railway_preflight.py --service-role api --app-env production
python scripts/railway_preflight.py --service-role webhook --app-env production
python scripts/railway_preflight.py --service-role worker --app-env production
python scripts/db_migrate.py
```

Then deploy the three production services from GitHub using the repository Dockerfiles.

`main` includes the split production shape required for that path:

- `run_webhook.py` for webhook ingress
- `run_worker.py` for the async audit worker
- `run_api.py` for dashboard and operator APIs
- `docker-compose.yml` plus dedicated Dockerfiles for the three services

Important deployment rules for this split surface:

- the webhook ingress should be the only internet-facing unauthenticated route surface
- the public Railway `api` service should now run the real customer control-plane app from `main.py`, with `SERVICE_ROLE=api` so webhook ingress is not active there
- the legacy split operator API in `services/api_service.py` still exists for local/operator scenarios, but it is not the preferred public launch surface
- split-service entrypoints now resolve bind ports through centralized settings: use `API_PORT` and `WEBHOOK_PORT` for local overrides, while deployed services continue to honor the platform-provided `PORT`
- Prometheus metrics are disabled by default and only exposed when `ENABLE_METRICS=true`
- SQLite remains the default local/shared-volume store for local development only, while production can now use a PostgreSQL `DATABASE_URL`
- production hardening now fails closed on unsafe settings such as SQLite persistence, insecure cookies, non-HTTPS base URLs, file-path private keys, or non-Redis queue settings for webhook/worker services

### Railway launch note

The repository now includes production guardrails, a PostgreSQL-capable persistence adapter, Redis-backed queue support, and Railway-focused documentation.

Current production stance:

- use Railway Postgres via `DATABASE_URL=postgresql://...`
- use Redis for webhook and worker queueing in production
- keep SQLite for local development and local shared-volume workflows only
- keep the preflight and readiness checks enabled so production rejects unsafe startup contracts instead of silently degrading

Use the detailed plan in [docs/railway-launch-readiness-plan.md](docs/railway-launch-readiness-plan.md) and the operator guide in [docs/railway-deployment-guide.md](docs/railway-deployment-guide.md) before attempting a real launch

The preflight helper now validates the runtime contract plus live readiness for the selected role, including database connectivity and queue connectivity for `webhook` and `worker`.

The migration workflow and failure handling are documented in [docs/database-migration-runbook.md](docs/database-migration-runbook.md).

## Environment matrix

| Environment | Runtime path | DB | Queue | Services | Notes |
| --- | --- | --- | --- | --- | --- |
| local-dev | direct Python or local Docker helper | SQLite by default | in-proc or SQLite | monolith or limited split | development only |
| staging | Docker deployment from service Dockerfiles | Postgres | Redis | split | production-like rehearsal |
| production | Docker deployment from service Dockerfiles | Postgres | Redis | split | only blessed production path |

## Local development only

The workflows in this section are for local development, debugging, and rehearsal. They are not production deployment recipes.

For the fastest local app loop, run the repo-owned launcher from the repo root:

```powershell
./scripts/run-local-app.ps1
```

That script always resolves the repo root and the checked-in `.venv` automatically, so it works even when your current shell started outside the repo. Use `-Restart` when you want it to reclaim `8011` before booting again:

```powershell
./scripts/run-local-app.ps1 -Restart
```

If you want the raw monolith command instead, run:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Do not use the monolith path for production, internet-exposed, or multi-tenant deployment.

You can start the split services locally with Docker Compose after providing the required environment variables in `.env`.

For the simplest local Docker workflow, use the checked-in wrapper script. This is a developer convenience wrapper, not a production deployment method:

```powershell
./scripts/docker-stack.ps1 up sqlite
./scripts/docker-stack.ps1 down sqlite
./scripts/docker-stack.ps1 up postgres
./scripts/docker-stack.ps1 down postgres
```

Those commands start the local API-first inspection flow and keep the app reachable at `http://127.0.0.1:8011` in either database mode. The wrapper exists because the base compose file hard-requires runtime values such as `API_ADMIN_TOKEN`, and the SQLite and Postgres variants each need different compose overrides. Those details are now wrapped so you do not need to pass ad hoc environment variables or use the old `.tmp` overlay path directly.

`up` runs detached by default so the containers stay alive while you inspect the UI. Use `./scripts/docker-stack.ps1 logs sqlite` or `./scripts/docker-stack.ps1 logs postgres` when you want to tail service output.

If you want the full split stack for webhook and worker testing, opt in explicitly:

```powershell
./scripts/docker-stack.ps1 up postgres -FullStack
./scripts/docker-stack.ps1 down postgres -FullStack
```

The full-stack path expects valid GitHub App and AI provider credentials. The simple default path intentionally clears GitHub App credentials so the local control-plane API can start even when your `.env` contains placeholder or host-only key paths.

If you want the raw compose commands, SQLite mode is:

```powershell
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml up --build
```

For a production-like local Docker rehearsal using the bundled Postgres and Redis containers, copy `docker-compose.local.env.example` to a local env file and run:

```bash
docker compose --env-file docker-compose.local.env.example up --build
```

This keeps `APP_ENV=local` for a safe local run while exercising the split `api`/`webhook`/`worker` services against Postgres and Redis instead of the default SQLite path.

## Internal evaluation and smoke workflows

The remaining runtime helpers below are for internal validation and engineering confidence. They are not part of the recommended production path.

## Local end-to-end testing

1. Start the FastAPI app.
2. Start ngrok and expose port `8000`.
3. Point the GitHub App webhook URL to `https://<your-ngrok-host>/webhook`.
4. Open or update a pull request containing AI-relevant changes.
5. Confirm Vipari posts a PR comment.

The helper script [scripts/verify_credentials.py](scripts/verify_credentials.py) can be used to validate the local credential setup before testing.

Recent live validation covered:

- risky opened PR flow with durable audit persistence and bot comment posting
- synchronize re-audit flow with exact-SHA diff reconstruction, same-head comment updates, and prior-episode comment preservation across new commits
- PR close and reopen lifecycle validation with durable state updates and stale timestamp clearing
- non-AI PR flow returning `no relevant changes` without queueing an audit
- invalid-model fallback flow posting a deterministic preliminary comment and recording `fallback_posted`
- escalation-label lifecycle validation covering add, remove, and re-add behavior as PR risk changes across review passes

## SBOM generation

Vipari now includes a repeatable CycloneDX SBOM path for dependency tracking.

Install the SBOM tooling alongside the application dependencies:

```bash
pip install -r requirements.txt -r requirements-sbom.txt
```

Generate an SBOM from the current Python environment:

```bash
python scripts/generate_sbom.py --output artifacts/sbom/driftguard.cyclonedx.json
```

The repository also includes [.github/workflows/sbom.yml](.github/workflows/sbom.yml), which generates the SBOM on pushes, pull requests, and manual workflow dispatch, then uploads it as a workflow artifact.

## Control-plane end-to-end testing

The Base44 -> Vipari handoff work adds a second end-to-end path on top of the existing PR-audit flow.

### Automated local E2E

The fastest proof is the focused route-flow suite in [tests/test_control_plane_ui.py](tests/test_control_plane_ui.py). It covers:

- GitHub OAuth start and callback
- session creation
- workspace bootstrap
- free-tier activation
- signed billing handoff claim creation and claim consumption
- GitHub installation linkage
- repo allocation
- dashboard unlock and free-tier dashboard denial
- webhook suppression for unallocated repos

Run it with:

```bash
python -m pytest tests/test_control_plane_ui.py -q
```

### Internal-only local runtime smoke test

Run the smoke test helper from the repo root when you want a local or internal confidence check. It is not a production preflight replacement:

```bash
python scripts/local_runtime_smoke.py --db sqlite:///./promptdrift-smoke.db
```

Run the app locally from the repo root:

```bash
uvicorn main:app --host 127.0.0.1 --port 8010 --app-dir PromptDrift
```

Then confirm:

1. `/` renders the Vipari control-plane landing page
2. `/login` renders the GitHub auth entry
3. `/pricing` renders Free, Starter, Team, and Enterprise plans
4. `/app` redirects to `/login` when no session exists

### Internal provider-backed E2E rehearsal

For a true end-to-end pass with GitHub OAuth, GitHub App install, and Base44/Wix payment-first handoff, the local server must be reachable from GitHub and the external billing provider.

This is internal rehearsal infrastructure, not the recommended production deployment path.

Recommended flow:

1. Run Vipari locally.
2. Expose it with `ngrok http 8010` or an equivalent tunnel.
3. Set `APP_BASE_URL=https://<tunnel-host>`.
4. Set `GITHUB_OAUTH_CALLBACK_URL=https://<tunnel-host>/auth/github/callback`.
5. Register the same callback URL in the GitHub OAuth app settings.
6. Point the GitHub App webhook URL to `https://<tunnel-host>/webhook`.
7. Configure Base44/Wix to POST signed purchase activations to `https://<tunnel-host>/api/billing/handoff/base44` using `BILLING_HANDOFF_SECRET`.
8. Start from the claim URL returned by that handoff or from `/claim/<token>` and walk through login, workspace creation, claim activation, install, repo allocation, and dashboard access.
9. For the free tier, start from `/app/billing` or `POST /app/billing/checkout` with `plan=free`, then continue directly to install and repo allocation.
10. If the GitHub App supports a setup URL, point it to `https://<tunnel-host>/app/setup/install/callback` so installation completion returns directly into Vipari.

Stripe remains available as a compatibility fallback for paid checkout on this branch, but it is no longer the primary launch path.

Recommended sequence before the live run:

```bash
python scripts/control_plane_preflight.py
python -m pytest tests/test_control_plane_ui.py -q
```

### What is authoritative in E2E

- GitHub OAuth success creates identity and session state
- free checkout is authoritative only for the local free entitlement path
- external paid checkout redirects are not authoritative for access on their own
- `POST /api/billing/handoff/base44` plus the resulting claim activation is authoritative for external paid-plan activation
- `POST /webhooks/stripe` remains authoritative for Stripe-backed paid-plan activation when Stripe fallback is used
- dashboard access is granted only after plan activation plus install and repo onboarding, and free workspaces remain comments-only

### End-of-day branch note

The control-plane branch is now implemented, locally validated, and tunnel-validated for the GitHub handoff/install/allocation path on `feature/driftguard-base44-stripe-handoff-v1`.

The main unfinished validation item is a real Base44/Wix signed handoff pass without local simulation. Stripe fallback remains available, and persisted `workspaces.setup_state` is now synchronized from entitlement/install/onboarding facts. See [docs/base44-stripe-handoff-v1-handoff.md](docs/base44-stripe-handoff-v1-handoff.md) for the updated branch handoff summary and next-step checklist.

## Internal operator and dashboard testing

Once the app is running locally, you can inspect the current drift dashboard in the browser for engineering validation:

- `/dashboard` — triage-first portfolio inbox with a primary review target, ranked queue, coverage trust, and a secondary coverage scan mode
- `/dashboard/<owner>/<repo>` — repo case file with one featured review target, ranked follow-on queue, posture/provenance context, and collapsed history inventory

Recommended 5-minute local inspection flow:

1. Open `/dashboard` first and confirm the portfolio risk-state hero, featured review target, ranked queue, and coverage-trust panels render.
2. Switch to Coverage mode and confirm the coverage atlas, control-surface coverage, and repo inventory capsules render.
3. Open `/dashboard/<owner>/<repo>` for a seeded repository and confirm the featured insight, repo command deck, posture explorer, and collapsed history inventory render.
4. In the repo case file, confirm the provenance links open the backing PR or commit, the posture explorer shows per-attribute findings, and the baseline action is available when a stored source version exists.
5. If the local data store is sparse or an older API payload is still being served, the frontend should degrade gracefully instead of throwing browser errors.

You can also inspect or drive the workflow locally with the CLI. These commands are for development, debugging, and internal evaluation, not for production operations:

```bash
python scripts/local_runtime_smoke.py --service-role monolith
python scripts/local_runtime_smoke.py --service-role api
python scripts/local_runtime_smoke.py --service-role webhook
python scripts/local_runtime_smoke.py --service-role worker
python scripts/repo_ops.py list-repos
python scripts/repo_ops.py persistence-status
python scripts/repo_ops.py dashboard owner/repo
python scripts/repo_ops.py onboard owner/repo <installation_id> --plan-backfill --execute-backfill
python scripts/repo_ops.py backfill owner/repo <installation_id>
python scripts/repo_ops.py list-eval-candidates
python scripts/repo_ops.py list-eval-scenarios
python scripts/repo_ops.py eval-run openfang <installation_id> --run-label main-openfang --compare-to artifacts/eval-runs/main/doria90-openfang/main-openfang/run-package.json
python scripts/repo_ops.py eval-run doria90/dummyAI <installation_id> --scenario dummyai-review-target --run-label seeded-dummyai
python scripts/repo_ops.py eval-run doria90/dummyAI <installation_id> --scenario dummyai-review-target --compare-to-scenario dummyai-review-target --run-label compare-seeded-dummyai
python scripts/repo_ops.py eval-compare path/to/current-run-package.json path/to/baseline-run-package.json
```

`local_runtime_smoke.py` now honors the requested service role instead of always loading the monolith app. Use it to smoke the split API and webhook services directly, or to run a worker readiness smoke against the currently configured database and queue settings.

Keep this helper in the engineering-validation lane. Production deployment validation should continue to flow through `scripts/railway_preflight.py`, `scripts/db_migrate.py`, and the Docker-based split-service deploy path.

The evaluation harness writes repeatable run packages under `artifacts/eval-runs/` by default. Each package includes onboarding and baseline summaries, optional backfill results, saved repo and overview dashboard payloads, ranked review targets, a fixed evaluator rubric, and an assertion summary so branch-to-branch comparison stays lightweight but reproducible. The built-in candidate registry currently starts with OSS repositories, but the harness itself also supports ad hoc owner/repo targets and seeded scenarios through the same contract.

This harness is internal infrastructure for developers, operators, and later CI or release-check automation. It is not a customer-facing product workflow, and the CLI is intended to provide a deterministic non-UI control surface for evaluation runs rather than a public user experience.

Seeded scenarios let you pin explicit expectations such as minimum baseline coverage, expected top review target presence/path, and maximum lower-confidence queue size. When a run uses `--scenario`, those assertions are saved into the package and surfaced again in `eval-compare` output.

Built-in scenarios can also point at checked-in reference packages under `fixtures/eval-harness/`. When you pass `--compare-to-scenario`, the harness resolves that scenario's reference package automatically and writes a comparison summary without requiring you to hand-type a `run-package.json` path.

For the same reason, isolated local databases are useful during eval runs: they keep seeded or branch-comparison state separate from whatever normal local dashboard or onboarding data already exists, so evaluation results stay reviewable and disposable.

Checked-in reference artifacts for past live validation can also appear under `live/oss-evals/` when a snapshot is intentionally preserved for handoff or comparison.

Useful JSON endpoints:

- `GET /api/persistence`
- `GET /api/repos`
- `GET /api/dashboard/overview`
- `GET /api/repos/{owner/repo}/dashboard`
- `POST /api/repos/{owner/repo}/onboard`
- `POST /api/repos/{owner/repo}/backfill`
- `POST /api/repos/{owner/repo}/artifacts/{artifact_path}/baseline`

Repo onboarding now plans and executes history backfill by default for the onboarded repo, using a bounded window of 5 commits per tracked artifact unless the caller explicitly overrides or disables that behavior.

Merged PR close events now also queue an incremental branch scan against the merge commit for onboarded repositories, so repo posture and Version Journey can advance from normal merge traffic without requiring a full history rerun.

When using the split API service, these dashboard and JSON routes require the configured `API_ADMIN_TOKEN` via `Authorization: Bearer ...` or `X-Admin-Token`.

Operational note:

- local SQLite may create `promptdrift.db-wal` and `promptdrift.db-shm` sidecar files while the server is running; these are ignored and can be removed once local uvicorn processes are stopped

## Known limitations

- the current backend is still SQLite, but persistence metadata now makes the logical boundary explicit: operational queue tables vs durable audit/history tables, with PostgreSQL remaining the production target
- the dashboard is now structurally ready for OSS validation, but landed posture intentionally depends on approved baselines plus merged-history evidence rather than proposal-only PR audits
- larger public repos now onboard successfully, but discovery precision and reviewer-target quality from merged-history evidence still need continued refinement
- cloud deployment scaffolding and production-persistence hardening are now landed, but live PostgreSQL-backed Railway/operator validation is still needed before treating the deployment path as fully production-proven
- AI relevance coverage and deterministic/semantic signal fusion still need refinement
- reviewer-target quality and queue tuning still need broader real-repo validation on history-heavy repositories, even though proposal-only and mixed evidence are now separated in the dashboard read models

## Safe repo practices

- Do not commit `.env`
- Do not commit private key files
- Use [.env.example](.env.example) as the only committed environment template

## Roadmap and deeper design docs

- [Plan.MD](Plan.MD) tracks milestone status, near-term feature order, and future workflows
- [docs/detection-engine-plan.md](docs/detection-engine-plan.md) captures the detection-engine architecture and implementation snapshot
- [docs/drift-profile-design-spec.md](docs/drift-profile-design-spec.md) describes the static drift-profile layer in more depth