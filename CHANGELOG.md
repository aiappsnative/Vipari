# Changelog

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
