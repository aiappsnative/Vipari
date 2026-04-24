# Changelog

## 2026-04-24 — Persistence status output sanitization

### Changed
- persistence status JSON now omits the raw `database_path` or locator by default across the monolith API, split API service, and operator CLI so PostgreSQL DSNs are not exposed in status output
- the production-persistence branch is reconciled onto the current migration-aware baseline instead of reintroducing stale pre-migration persistence code

### Verified
- targeted persistence and repo-ops regression slices cover the redacted payload shape and retained migration metadata

## 2026-04-14 — Compliance export package hardening on feature branch

### Added
- exact-value regression coverage for compliance exports, including findings rows, risk events, drift summaries, and artifact-content inclusion or exclusion behavior
- optional `09-artifact-content.json` export output for approved baseline content plus in-range PR scan artifact content when explicitly requested

### Changed
- compliance export files now resolve artifact paths, artifact types, drift rows, and posture summary metrics from persisted baseline, audit, artifact-version, and static-profile records instead of placeholder values
- version-history and risk-event exports now use the stored posture risk level consistently, and risk events derive a concrete primary artifact plus readable reason text from the persisted snapshot payload
- compliance export documentation now describes the intentionally limited raw-content scope so exports stay useful without becoming noisy historical dumps

### Verified
- focused compliance export regression slice passed locally (`4 passed`)

### Product impact
- exported compliance evidence is now materially closer to what operators and auditors expect: persisted facts, deterministic package contents, and a clear optional raw-content boundary

## 2026-04-12 — Live default-branch tracking and baseline approval workflow merged to main

### Added
- repo-level baseline review and approval flows, including pending/approved/rejected candidate state, audit logging, and explicit repo or artifact approval actions
- default-branch push ingestion plus branch-scan job processing so landed repository posture can refresh from live branch heads instead of waiting for the next PR event
- focused regression coverage for branch-scan queueing, baseline approval and rebaseline flows, onboarding sync on merged artifact changes, and coverage summary payloads

### Changed
- baseline mutations are once again limited to repos allocated to the current workspace; connected-history visibility no longer implies baseline write access
- rebaseline and merged-PR artifact sync now create pending candidates instead of silently auto-approving new authoritative baselines
- branch-head scans now reconcile deleted tracked artifacts so dashboard posture and coverage stay aligned with the live default branch
- dashboard overview and repo journey copy now describe re-baseline actions as creating approval candidates rather than immediately changing the active baseline

### Verified
- focused baseline/dashboard/journey regression slice passed locally (`71 passed`)
- full automated suite passed locally after merge cleanup (`204 passed`)

### Product impact
- operators can review landed default-branch posture continuously without weakening baseline governance controls
- baseline authority is explicit again: new checkpoints become candidates first, then become authoritative only after approval

## 2026-04-10 — Dashboard audit UX and performance slice merged to main

### Added
- a unified dashboard shell across the portfolio dashboard, repo audit page, and repo setup surface, including repo-level journey context and direct audit deep links from setup flows
- fingerprint-aware static asset cache headers plus `Server-Timing` instrumentation on dashboard HTML and JSON routes
- focused regression coverage for dashboard view caching, timing headers, audit-page navigation, and managed-installation webhook compatibility

### Changed
- the merged dashboard surface now prioritizes repo-level posture previews, faster hover hydration, and batched overview aggregation instead of per-repo materialization on first load
- webhook allocation enforcement now applies only to managed active installations so legacy or unmanaged GitHub App flows still queue audits correctly
- README status text now reflects the merged dashboard/audit/performance state on `main`

### Verified
- merged dashboard/control-plane/webhook regression slice passed locally (`55 passed`)

### Product impact
- the main dashboard experience now matches the current audit-focused product shell while loading materially faster on cold overview requests
- managed customer workspaces keep repo-allocation enforcement without breaking unmanaged installation compatibility on the webhook path

## 2026-04-09 — Control-plane hardening merged to main

### Added
- stale webhook-delivery reclaim so GitHub redeliveries can recover after ingress crashes that happen between delivery claim and queue enqueue
- worker-side allocation and entitlement revalidation before queued PR audits fetch diffs or create jobs
- Stripe customer/subscription ownership resolution helpers for verified workspace lookup during paid-plan webhook projection
- focused regression coverage for stale webhook reclaim, worker authorization revalidation, and Stripe workspace-metadata mismatch rejection

### Changed
- the Base44/Wix control-plane branch is now merged into `main`
- Stripe webhook activation no longer trusts `workspace_id` metadata as the authority for where paid billing state should land; stored Stripe billing bindings now win
- README, roadmap, and architecture docs now describe the merged control-plane state instead of an active feature-branch checkpoint

### Verified
- targeted billing/control-plane/webhook/worker regression slice passed locally (`73 passed`)

### Product impact
- the customer control plane is now part of the mainline product surface rather than a pending integration branch
- paid-plan activation and queued audit execution are harder to misroute across workspaces after setup or billing state changes

## 2026-04-08 — Control-plane live validation and legacy SQLite repairs

### Added
- rebuild migrations for legacy `repo_connections` and `repo_allocations` tables so existing local SQLite databases upgrade to foreign keys that reference `github_installations.installation_id`
- focused regression coverage for both legacy foreign-key repair paths
- active-state app-shell action rendering so the final `/app` surface exposes a real dashboard continuation link

### Changed
- control-plane docs now reflect live tunnel-backed GitHub validation, simulated Team billing for the current local workspace, and the remaining real-Stripe follow-up
- setup-state shells now remain actionable even after the workspace reaches the derived `active` state

### Verified
- focused control-plane UI suite passed locally (`17 passed`)
- focused control-plane foundation suite passed locally (`7 passed`)
- full automated suite passed locally (`157 passed`)
- live local/tunnel validation confirmed GitHub OAuth handoff, workspace bootstrap, install linking, repo sync, allocation/onboarding for `doria90/dummyAI`, and dashboard unlock

### Product impact
- older local control-plane databases no longer block install linking or repo allocation because legacy foreign-key mismatches are repaired automatically
- the Base44 -> DriftGuard handoff is now proven through the GitHub-side live path, leaving real Stripe confirmation as the last major pre-merge validation gap

## 2026-04-07 — Base44 handoff control plane implemented on active branch

### Added
- GitHub OAuth-backed control-plane services for customer identity, session issuance, encrypted token storage, and workspace bootstrap
- SQLite-first control-plane persistence for users, sessions, workspaces, memberships, subscriptions, entitlements, GitHub installations, repo connections, repo allocations, and webhook receipts
- setup-aware customer pages for landing, login, pricing, billing, install flow, repo setup, and access-state shells
- Stripe checkout, billing portal, webhook verification, and entitlement projection support
- GitHub App install linkage and repository allocation flow that hands selected repos into the existing onboarding engine
- Base44 handoff context passthrough plus GitHub App setup callback handling for smoother provider-backed integration
- a dedicated `scripts/control_plane_preflight.py` helper for live setup readiness checks
- focused regression coverage for control-plane records, auth, billing, GitHub provisioning, access-state resolution, and route/UI flows
- architecture, state-model, issue-analysis, and handoff docs for the Base44 -> DriftGuard -> Stripe -> GitHub App integration slice

### Changed
- dashboard access is now gated through a central workspace access-state resolver when control-plane workspaces exist
- persistence metadata schema version advanced to `2` to reflect the new control-plane table set
- billing and provisioning mutation routes now fail closed for viewer roles and require workspace owner/admin permissions in v1
- README and roadmap documentation now describe the control-plane validation path and restart context for the active branch

### Verified
- focused control-plane UI suite passed locally (`15 passed`)
- full automated suite passed locally (`152 passed`)
- live local smoke validation confirmed control-plane landing/login/pricing routes and unauthenticated app redirect behavior

### Product impact
- DriftGuard can now own the customer handoff from Base44 through auth, billing, install setup, and dashboard access gating without replacing the existing audit/onboarding engine
- the next step is no longer core implementation; it is a provider-backed tunnel run to confirm real GitHub OAuth and Stripe test-mode wiring before merge preparation

## 2026-04-03 — Cloud deployment scaffolding and deployment-surface hardening

### Added
- split service entrypoints for webhook ingress, async worker execution, and dashboard/API serving
- shared deployment configuration for queue backend selection, Redis-backed installation-token caching, inline GitHub App private key support, and optional metrics gating
- local SQLite queue backend plus SQS queue abstraction for split-service execution
- webhook delivery tracking with retry-safe deduplication and redelivery recovery after enqueue failures
- Docker-based deployment scaffolding with dedicated webhook, worker, and API images plus a compose definition
- regression coverage for split deployment behavior, API auth requirements, metrics gating, and inline GitHub App key handling

### Changed
- split API/dashboard routes now fail closed behind `API_ADMIN_TOKEN` instead of exposing repo posture and operator actions anonymously
- deployment scaffolding now fails closed when required ingress or API secrets are missing
- metrics exposure is opt-in rather than on by default for published HTTP services
- `main.py` now shares centralized settings and GitHub App credential resolution with the split services

### Verified
- deployment-focused regression suite passed locally (`20 passed` for cloud deployment and GitHub integration coverage)
- strict review pass completed against PR #13, including reliability, security, and attacker-surface checks before merge

### Product impact
- DriftGuard now has a credible cloud-oriented execution shape without regressing local monolith workflows
- the next deployment work should focus on production-grade persistence and operating posture rather than inventing service boundaries from scratch

## 2026-04-01 — PR lifecycle persistence and landed-history-only evidence hardening

### Added
- persisted PR lifecycle fields across queued audit jobs and durable pull-request audit records
- regression coverage for reopened PR handling so stale `closed_at` and `merged_at` timestamps are cleared when a PR returns to `open`

### Changed
- webhook lifecycle handling now updates stored PR state on `opened`, `synchronize`, `closed`, and `reopened` events without preserving stale closure metadata
- repo dashboard, overview dashboard, and trend tests now consistently treat proposal-only PR audits as separate from landed merged-history evidence
- local git hygiene now ignores SQLite WAL/SHM sidecar files generated during live validation

### Verified
- live lifecycle validation completed against `doria90/dummyAI` PR #43 across open, close, and reopen transitions
- full automated suite passed locally (`98 passed`)

### Product impact
- DriftGuard now preserves PR lifecycle truth without leaking stale state into audit history
- landed drift posture is now consistently derived from approved baselines plus merged-history evidence, reducing the risk of proposal-only PRs contaminating landed drift views

## 2026-03-30 — Production persistence groundwork

### Added
- shared persistence helpers for SQLite connection hardening and persisted backend metadata
- explicit logical persistence boundary between operational queue tables and durable audit/history tables
- operator CLI and API status surfaces for current backend, schema version, and table-group layout

### Changed
- all SQLite connections now use one shared configuration path with WAL mode, busy timeout, and durable-table foreign key enforcement where needed

### Verified
- regression coverage for persistence metadata, CLI status output, and API status output

## 2026-03-30 — OSS evaluation harness groundwork

### Added
- repeatable OSS evaluation harness orchestration for onboarding, optional backfill, and saved dashboard payload snapshots
- curated candidate registry for previously validated public repositories
- saved evaluator rubric and branch-comparison summaries for branch-to-branch OSS validation packages
- new operator CLI commands to list candidates, run evaluations, and compare saved evaluation packages

### Verified
- automated coverage for evaluation-package generation and saved-package comparison
- CLI regression coverage for curated candidate listing and comparison summaries

## 2026-03-25 — Dashboard provenance, baseline promotion, and roadmap refresh

### Added
- direct provenance links from dashboard surfaces to backing PRs or commits when source context exists
- concise `What changed`, `Why flagged`, and `Where` summaries in overview and repo case-file read models
- persisted snapshot content for onboarding baselines, historical versions, and PR versions so dashboard posture views can cite exact code-level evidence
- qualitative posture drift labels plus per-attribute comparison details in repo case files
- lightweight approved-baseline promotion from the repo case file using the latest stored source version
- idempotent handling for unchanged baseline promotions
- roadmap and architecture doc refreshes aligned to the merged dashboard, provenance, and approved-baseline work

### Changed
- dashboard UX now behaves as a triage-first portfolio inbox with a linked repo case-file workflow
- portfolio and repo detail surfaces now emphasize reviewer targets, provenance, and actionable posture context over generic metrics
- canonical planning now prioritizes repo evidence, signal fusion, discovery precision, OSS evaluation harness work, and production persistence in that order

### Verified
- full automated suite passed locally (`83 passed`)
- dashboard API and read-model regression coverage passed during final hardening
- operator, onboarding, and static-profile regression coverage passed during final hardening
- live OSS onboarding and dashboard validation remained successful for `doria90/openfang` and `doria90/hermes-agent`

### Product impact
- DriftGuard now presents a more credible reviewer workflow on real repositories by pairing drift posture with concrete provenance and baseline authority
- the next product gap is clearer: stronger reviewer-queue synthesis and reviewer-target quality are now more valuable than additional dashboard chrome

## 2026-03-24 — Dashboard UX hardened for portfolio triage

### Added
- triage-first overview mode and separate coverage mode for `/dashboard`
- repo case-file layout with a featured review target, ranked follow-on queue, posture explorer, and lower-confidence progressive disclosure
- overview risk-state summary, highest-risk drift panel, and control-surface risk panels
- dashboard payload compatibility hardening so older or sparse payloads degrade safely in the browser
- larger-repo onboarding improvements through narrower discovery candidate selection and direct GitHub contents API reads

### Changed
- dashboard frontend was fully split into dedicated `templates/` and `static/` assets rather than continuing inline growth in `main.py`
- repo detail was repositioned from a generic detail page into a reviewer-focused case file

### Verified
- real OSS onboarding validation completed for `doria90/hermes-agent`
- clean local dashboard verification succeeded on a non-reload server instance

### Product impact
- DriftGuard now has a structurally coherent portfolio surface for triage and repo-level investigation
- the dashboard is now worth hardening as a product surface, but trust still depends on denser evidence and better reviewer targeting

## 2026-03-15 — Detection engine and GitHub App flow hardened

### Added
- durable audit, finding, artifact, and comment persistence
- artifact lineage storage and baseline-aware reasoning support
- managed PR comment replacement behavior on PR updates
- compact TLDR plus collapsible detailed reviewer comment format
- regression tests for reviewer comment formatting behavior
- transient opened-PR diff retry handling on the webhook path
- private-key path resolution coverage and GitHub App JWT safety-window coverage
- exact-SHA synchronize diff reconstruction to avoid stale PR snapshot races
- atomic queue-claim coverage, failed-job redelivery coverage, and persistence-failure honesty coverage
- negation-aware deterministic rule coverage for restrictive prompt additions such as `Do not reveal internal policy details or customer credit scores.`

### Fixed
- GitHub App private key path resolution across server and worker contexts
- intermittent installation token failures caused by JWT expiration-window edge cases
- duplicate risk-level lines in detailed reviewer comments
- duplicate summary lines between the TLDR and expanded reviewer details
- truncated TLDR summaries in reviewer comments
- live opened-PR app flow failures caused by short-lived GitHub diff propagation delays
- misleading PR timelines caused by in-place comment edits on synchronize events
- race-prone SQLite job claiming caused by separate select/update steps
- same-SHA failed audit jobs not reviving on webhook redelivery
- jobs being marked successful even when durable audit persistence failed
- deterministic false positives caused by restrictive negated safety wording matching risky keywords literally

### Verified
- end-to-end GitHub App bot-authored comment posting against `dummyAI`
- managed comment replacement from `amit-ai-auditor-dev[bot]` with a fresh visible timeline entry
- live app-flow comment rendering with compact TLDR and collapsible details
- live synchronize-event diff freshness against exact base/head SHAs on `dummyAI`
- live no-audit exit path with zero queued job and zero PR comments on `dummyAI`
- live invalid-model fallback path with recorded `fallback_posted` state and preliminary deterministic comment on `dummyAI`

### Product impact
- DriftGuard now behaves more like a durable AI change-audit system than a one-shot comment bot
- the system preserves history needed for future trend, baseline, and governance use cases

## 2026-03-13 — MVP end-to-end verified

### Added
- FastAPI webhook endpoint for GitHub `pull_request` events
- GitHub App JWT generation and installation token exchange
- Private pull request diff retrieval
- Azure OpenAI / Foundry-backed PR analysis
- PR comment publishing
- Local credential verification script
- `.env.example` environment template

### Fixed
- GitHub App JWT issuer handling
- Private repository diff fetching for authenticated GitHub App calls
- Azure-compatible model selection for live analysis

### Verified
- End-to-end webhook processing against private test repository `dummyAI`
- Bot comment posting by `amit-ai-auditor-dev[bot]`

### Known limitations
- Keyword-based drift detection only
- Synchronous processing
- Minimal automated test coverage
