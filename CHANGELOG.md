# Changelog

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
- PromptDrift now presents a more credible reviewer workflow on real repositories by pairing drift posture with concrete provenance and baseline authority
- the next product gap is clearer: stronger PR-linked evidence and reviewer-target quality are now more valuable than additional dashboard chrome

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
- PromptDrift now has a structurally coherent portfolio surface for triage and repo-level investigation
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
- PromptDrift now behaves more like a durable AI change-audit system than a one-shot comment bot
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
