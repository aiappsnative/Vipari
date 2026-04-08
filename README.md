# DriftGuard

DriftGuard is a GitHub App backend that audits pull requests for changes that can alter AI system behavior, especially prompts, guardrails, model-routing logic, tool access, and related policy artifacts.

The product direction is now explicitly GitHub-native and static-first: DriftGuard treats risk and drift as properties of prompts, policies, model settings, tool definitions, and agent wiring visible in code review, rather than as a runtime observability problem.

Its job is not just to say that “an AI file changed”, but to help reviewers answer the customer-critical question:

**Did this PR materially change how the AI behaves, what it is allowed to do, or what it may reveal?**

Near-term, the product is being shaped around one especially important follow-up question:

**Does this AI-related PR need escalation before merge?**

## Customer value

DriftGuard provides value by reducing the gap between ordinary code review and safe AI change review.

For customers, that means:

- catching risky prompt and guardrail changes before they reach production
- explaining *why* a change is risky, not just that a file changed
- preserving audit history so teams can reason about drift over time
- making AI review operationally practical inside the existing GitHub PR workflow
- providing a defensible review trail for security, compliance, and platform teams

In product terms, DriftGuard is moving toward becoming a **change intelligence layer for AI behavior**.

The product wedge is the PR review workflow. Dashboard and history views remain important, but they should reinforce PR-level decisions rather than replace them.

For the enduring product thesis behind that direction, see [SOUL.md](SOUL.md).

## Current status

The current `main` branch now includes the merged static-first drift engine milestone plus the follow-on escalation, approved-baseline, repo-provenance, and dashboard UX hardening slices.

The active integration branch `feature/driftguard-base44-stripe-handoff-v1` extends that baseline with a first customer-facing control plane for the Base44/Wix -> DriftGuard billing handoff plus GitHub App onboarding flow.

In practical terms, DriftGuard currently provides:

- queue-backed GitHub App PR auditing with deterministic analysis, semantic review, retry/fallback behavior, and episode-aware managed PR comments
- escalation-aware PR review with decision-first comments, risk-emoji headers, concrete evidence panels, and GitHub label sync for before-merge escalation cases
- persisted pull-request lifecycle state across audit jobs and durable audit records, including close/reopen and merge metadata
- approved-baseline-aware static drift profiling for prompts, configs, and related AI control surfaces
- onboarding and selective historical backfill for repository-level artifact inventories and profile history
- a triage-first dashboard surface with portfolio Triage/Coverage modes and repo case-file drill-down pages, including baseline provenance in repo/history views
- landed drift views driven by approved baselines plus merged-history evidence, while proposal-only PR audit evidence remains separate from landed-history posture
- repo-detail provenance links that route directly to the backing PR or commit when stored source context exists
- concise `What changed`, `Why flagged`, and `Where` explanations in both overview and repo dashboard surfaces
- baseline-vs-current posture detail with qualitative drift labels, per-attribute findings, and code-level evidence when stored snapshots are available
- a lightweight baseline-promotion action that lets operators promote the latest stored source version as the approved baseline for an artifact
- real OSS onboarding validation against `doria90/openfang` and `doria90/hermes-agent`, including larger-repo historical backfill and dashboard rendering
- bounded large-repo onboarding through narrower candidate-path discovery and direct GitHub contents API fetches for artifact snapshots
- a local operator CLI and JSON APIs for onboarding, backfill, and dashboard inspection

On the active integration branch, DriftGuard additionally provides:

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
- webhook gating that suppresses PR audits/comments for repos that are installed but not allocated or not entitled for comments
- owner/admin-only protection for billing and provisioning mutations so viewer roles can inspect state but not mutate it
- actionable setup-state, free-tier, and active-state app shells so `/app` always exposes a real continuation path
- Starter-and-above profile pages with editable display name, read-only GitHub identity, and provider-sourced next payment date display
- strictly allowlisted admin pages covering registered users, dashboard-entitled workspaces, billing handoff claims, and unclaimed/public GitHub App installations
- public GitHub App install callback capture so direct marketplace/setup flows are visible before workspace claiming completes
- a dedicated `scripts/control_plane_preflight.py` helper for tomorrow's provider-backed setup checks

Latest branch validation on 2026-04-08:

- full automated suite passed locally: `167 passed`
- targeted control-plane/access-state regression coverage passed locally for free-tier activation, signed billing handoff claims, dashboard gating, and webhook allocation enforcement
- tunnel-backed live validation previously confirmed GitHub OAuth handoff, workspace bootstrap, GitHub App install linkage, repo connection sync, repo allocation for `doria90/dummyAI`, and dashboard unlock after simulated Team billing

For detailed roadmap status, see [Plan.MD](Plan.MD). For architecture details, see [docs/detection-engine-plan.md](docs/detection-engine-plan.md).

The dashboard should now be read as two linked product surfaces:

- `/dashboard` is the portfolio decision surface for triage, hotspots, and coverage trust, with a secondary coverage mode for inventory and pattern scans
- `/dashboard/{owner/repo}` is the repo case file for baseline-relative posture, prioritized review targets, lower-confidence findings, artifact-level evidence, and approved-baseline promotion

## What DriftGuard does today

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
- exposes JSON query APIs for repository listings and unified dashboard payloads
- exposes an overview dashboard API at `GET /api/dashboard/overview`
- exposes local dashboard pages at `/dashboard` and `/dashboard/{owner/repo}`
- includes `scripts/repo_ops.py` for local operator workflows and read-side inspection
- prepares structured semantic review context for the LLM
- falls back to a deterministic preliminary audit when the model call is permanently unavailable
- upserts the managed PR comment for the current PR head SHA while preserving prior-episode comments for earlier commits
- persists audit, finding, artifact, and comment history for later analysis
- updates stored PR lifecycle state on `opened`, `synchronize`, `closed`, and `reopened` webhook flows without leaving stale close/merge timestamps behind
- marks jobs failed instead of pretending success when comment posting or durable persistence breaks

## High-level architecture

- **Webhook path:** verify signature, fetch diff, run relevance gate, enqueue audit job
- **Worker path:** deterministic analysis, semantic review, retry/fallback handling, current-head comment upsert, escalation-label sync, durable persistence
- **Static drift layer:** derive design attributes from prompts/configs and compare them to a baseline to measure design drift without runtime data
- **Persistence:** operational queue tables plus durable audit/history tables, artifact versions, and static profile records in one relational store for now

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

This gives DriftGuard a concrete foundation for future baseline comparison, trend analysis, and PR-facing drift summaries without relying on runtime telemetry.

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
- `ADMIN_GITHUB_LOGINS`, `ADMIN_GITHUB_USER_IDS`, and `ADMIN_EMAILS` for explicit control-plane admin allowlisting
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PORTAL_CONFIGURATION_ID`
- `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_TEAM`, `STRIPE_PRICE_ENTERPRISE`, and `STRIPE_PRICE_BUSINESS`
- `REDIS_URL`
- `QUEUE_BACKEND`, `SQS_QUEUE_URL`, and `SQS_DLQ_URL`
- `API_ADMIN_TOKEN` for the split API service
- `ENABLE_METRICS` (defaults to `false`)

Control-plane preflight helper:

```bash
python scripts/control_plane_preflight.py
```

Use it before the live-provider run to confirm the GitHub OAuth, GitHub App, billing-provider, and app-base settings are populated and consistent.

## Installation

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the service locally:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Cloud deployment scaffolding

`main` now includes a first-pass split deployment shape for cloud-oriented hosting:

- `run_webhook.py` for webhook ingress
- `run_worker.py` for the async audit worker
- `run_api.py` for dashboard and operator APIs
- `docker-compose.yml` plus dedicated Dockerfiles for the three services

Important deployment rules for this split surface:

- the webhook ingress should be the only internet-facing unauthenticated route surface
- the split API service requires `API_ADMIN_TOKEN`; dashboard pages and JSON APIs are protected by that token
- Prometheus metrics are disabled by default and only exposed when `ENABLE_METRICS=true`
- SQLite remains the default local/shared-volume store in the scaffolding; PostgreSQL is still the longer-term production target

You can start the split services locally with Docker Compose after providing the required environment variables in `.env`.

## Local end-to-end testing

1. Start the FastAPI app.
2. Start ngrok and expose port `8000`.
3. Point the GitHub App webhook URL to `https://<your-ngrok-host>/webhook`.
4. Open or update a pull request containing AI-relevant changes.
5. Confirm DriftGuard posts a PR comment.

The helper script [scripts/verify_credentials.py](scripts/verify_credentials.py) can be used to validate the local credential setup before testing.

Recent live validation on the active branch covered:

- risky opened PR flow with durable audit persistence and bot comment posting
- synchronize re-audit flow with exact-SHA diff reconstruction, same-head comment updates, and prior-episode comment preservation across new commits
- PR close and reopen lifecycle validation with durable state updates and stale timestamp clearing
- non-AI PR flow returning `no relevant changes` without queueing an audit
- invalid-model fallback flow posting a deterministic preliminary comment and recording `fallback_posted`
- escalation-label lifecycle validation covering add, remove, and re-add behavior as PR risk changes across review passes

## Control-plane end-to-end testing

The Base44 -> DriftGuard handoff work adds a second end-to-end path on top of the existing PR-audit flow.

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

### Local runtime smoke test

Run the app locally from the repo root:

```bash
uvicorn main:app --host 127.0.0.1 --port 8010 --app-dir PromptDrift
```

Then confirm:

1. `/` renders the DriftGuard control-plane landing page
2. `/login` renders the GitHub auth entry
3. `/pricing` renders Free, Starter, Team, and Enterprise plans
4. `/app` redirects to `/login` when no session exists

### Real provider-backed E2E

For a true end-to-end pass with GitHub OAuth, GitHub App install, and Base44/Wix payment-first handoff, the local server must be reachable from GitHub and the external billing provider.

Recommended flow:

1. Run DriftGuard locally.
2. Expose it with `ngrok http 8010` or an equivalent tunnel.
3. Set `APP_BASE_URL=https://<tunnel-host>`.
4. Set `GITHUB_OAUTH_CALLBACK_URL=https://<tunnel-host>/auth/github/callback`.
5. Register the same callback URL in the GitHub OAuth app settings.
6. Point the GitHub App webhook URL to `https://<tunnel-host>/webhook`.
7. Configure Base44/Wix to POST signed purchase activations to `https://<tunnel-host>/api/billing/handoff/base44` using `BILLING_HANDOFF_SECRET`.
8. Start from the claim URL returned by that handoff or from `/claim/<token>` and walk through login, workspace creation, claim activation, install, repo allocation, and dashboard access.
9. For the free tier, start from `/app/billing` or `POST /app/billing/checkout` with `plan=free`, then continue directly to install and repo allocation.
10. If the GitHub App supports a setup URL, point it to `https://<tunnel-host>/app/setup/install/callback` so installation completion returns directly into DriftGuard.

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

## Local operator and dashboard testing

Once the app is running locally, you can inspect the current drift dashboard in the browser:

- `/dashboard` — triage-first portfolio inbox with a primary review target, ranked queue, coverage trust, and a secondary coverage scan mode
- `/dashboard/<owner>/<repo>` — repo case file with one featured review target, ranked follow-on queue, posture/provenance context, and collapsed history inventory

Recommended 5-minute local inspection flow:

1. Open `/dashboard` first and confirm the portfolio risk-state hero, featured review target, ranked queue, and coverage-trust panels render.
2. Switch to Coverage mode and confirm the coverage atlas, control-surface coverage, and repo inventory capsules render.
3. Open `/dashboard/<owner>/<repo>` for a seeded repository and confirm the featured insight, repo command deck, posture explorer, and collapsed history inventory render.
4. In the repo case file, confirm the provenance links open the backing PR or commit, the posture explorer shows per-attribute findings, and the baseline action is available when a stored source version exists.
5. If the local data store is sparse or an older API payload is still being served, the frontend should degrade gracefully instead of throwing browser errors.

You can also inspect or drive the workflow locally with the CLI:

```bash
python scripts/repo_ops.py list-repos
python scripts/repo_ops.py persistence-status
python scripts/repo_ops.py dashboard owner/repo
python scripts/repo_ops.py onboard owner/repo <installation_id> --plan-backfill --execute-backfill
python scripts/repo_ops.py backfill owner/repo <installation_id>
python scripts/repo_ops.py list-eval-candidates
python scripts/repo_ops.py eval-run openfang <installation_id> --run-label main-openfang --compare-to artifacts/oss-evals/main/doria90-openfang/main-openfang/run-package.json
python scripts/repo_ops.py eval-compare path/to/current-run-package.json path/to/baseline-run-package.json
```

The OSS evaluation harness writes repeatable run packages under `artifacts/oss-evals/` by default. Each package includes onboarding and baseline summaries, optional backfill results, saved repo and overview dashboard payloads, ranked review targets, and a fixed evaluator rubric so branch-to-branch comparison stays lightweight but reproducible.

Checked-in reference artifacts for past live validation can also appear under `live/oss-evals/` when a snapshot is intentionally preserved for handoff or comparison.

Useful JSON endpoints:

- `GET /api/persistence`
- `GET /api/repos`
- `GET /api/dashboard/overview`
- `GET /api/repos/{owner/repo}/dashboard`
- `POST /api/repos/{owner/repo}/onboard`
- `POST /api/repos/{owner/repo}/backfill`
- `POST /api/repos/{owner/repo}/artifacts/{artifact_path}/baseline`

When using the split API service, these dashboard and JSON routes require the configured `API_ADMIN_TOKEN` via `Authorization: Bearer ...` or `X-Admin-Token`.

Operational note:

- local SQLite may create `promptdrift.db-wal` and `promptdrift.db-shm` sidecar files while the server is running; these are ignored and can be removed once local uvicorn processes are stopped

## Known limitations

- the current backend is still SQLite, but persistence metadata now makes the logical boundary explicit: operational queue tables vs durable audit/history tables, with PostgreSQL remaining the production target
- the dashboard is now structurally ready for OSS validation, but landed posture intentionally depends on approved baselines plus merged-history evidence rather than proposal-only PR audits
- larger public repos now onboard successfully, but discovery precision and reviewer-target quality from merged-history evidence still need continued refinement
- cloud deployment scaffolding is now landed, but the deployed shape is still SQLite-first, effectively single-tenant, and not yet a full production control plane
- AI relevance coverage and deterministic/semantic signal fusion still need refinement
- PR review, dashboard prioritization, and landed-history narratives still need tighter synthesis so proposal-only evidence is visible without contaminating merged-history drift

## Safe repo practices

- Do not commit `.env`
- Do not commit private key files
- Use [.env.example](.env.example) as the only committed environment template

## Roadmap and deeper design docs

- [Plan.MD](Plan.MD) tracks milestone status, near-term feature order, and future workflows
- [docs/detection-engine-plan.md](docs/detection-engine-plan.md) captures the detection-engine architecture and implementation snapshot
- [docs/drift-profile-design-spec.md](docs/drift-profile-design-spec.md) describes the static drift-profile layer in more depth