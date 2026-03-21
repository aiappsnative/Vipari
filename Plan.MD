# PromptDrift Development Plan

This document outlines the phased roadmap for building the PromptDrift backend. It is intended as a living guide and will evolve as the project grows.

For the enduring product thesis, guardrails, and design principles that should outlast roadmap changes, see `SOUL.md`.

---

## Product value north star

PromptDrift exists to help customers safely ship AI changes.

The core value is not generic PR summarization. The core value is:

- detecting behavior-changing AI edits before they reach production
- helping reviewers understand whether a PR changed safety, disclosure, authority, or policy behavior
- preserving a durable history of AI drift over time
- fitting into the customer's existing GitHub-based engineering workflow

This framing should shape roadmap decisions:

- prefer explainability over opaque scoring
- prefer durable history over one-off comments
- prefer reviewer usefulness over model cleverness for its own sake
- prefer change-level evidence over vague AI risk statements
- prefer operational reliability because customers only trust what shows up consistently in PR review

---

## Current branch map (March 2026)

### `main`
- Purpose: stable verified MVP baseline
- Current status: intentionally behind ongoing engine work
- Merge policy: receive only approved, validated engine milestones

### `feature/drift-engine-v1`
- Purpose: active integration branch for next-generation detection engine work
- Current status:
	- deterministic drift analysis implemented
	- queued audit worker with retry/fallback implemented
	- structured semantic review packages implemented
	- durable audit/history persistence implemented
	- first-pass static drift profiling implemented for GitHub-visible prompts and configs
	- static artifact profile persistence implemented with baseline-linked history records
	- reviewer-facing static drift summary block implemented in PR comments when artifact snapshots are available
	- repo-level static drift summary and top-drifting artifact queries implemented as first read-side trend primitives
	- repository onboarding inventory persistence and selective historical backfill-job planning implemented
	- historical backfill execution implemented for onboarded artifacts, with persisted historical versions and profile lineage
	- unified repo dashboard read models, JSON query APIs, dashboard HTML pages, and local operator CLI implemented
	- large-repo dashboard aggregation optimized so OSS-scale repo views remain interactive
	- extracted dashboard frontend into dedicated templates/static assets and introduced an overview-vs-detail dashboard split
	- first repo-detail history and drift timeline visualization implemented for onboarded artifacts
	- artifact lineage and baseline-aware suppression implemented
	- PR comment replace-on-update behavior implemented
	- compact TLDR + collapsible reviewer comment format implemented
	- GitHub App auth hardening, opened-PR diff retry, and exact-SHA synchronize diff reconstruction implemented
	- atomic job claiming, failed-job requeue on same-SHA redelivery, and honest persistence-failure handling implemented
	- deterministic rule suppression improved for restrictive negated safety wording such as `Do not reveal ...`
	- live validation completed for benign, risky, synchronize, fallback, dedupe, and bot-auth scenarios
- Current known limitations:
	- queue is local in-process SQLite for this stage
	- signal fusion still needs improvement
	- deterministic and semantic outputs still need tighter convergence work beyond the latest obvious negation false-positive fix
	- docs and product framing need periodic refresh to match implementation
	- current dashboard is still operator-oriented rather than customer decision-oriented
	- onboarding discovery remains too noisy on some real OSS repositories and needs stronger prioritization and grouping
	- dashboard currently exposes metrics and inventory, but not yet enough action-oriented insight for customers

### Completed slices on `feature/drift-engine-v1`
1. Diff parsing and AI relevance classification
2. Artifact-aware context mode selection
3. Deterministic rule engine and risk floor scoring
4. Queue-backed webhook execution path
5. Retry handling, provider retry hints, and deterministic preliminary fallback
6. Structured semantic review packages for LLM review framing
7. Durable audit history and artifact lineage
8. Managed comment replacement behavior on PR updates
9. Reviewer-facing comment formatting polish
10. GitHub App auth hardening, transient PR diff retry, and synchronize diff freshness hardening
11. Queue correctness hardening for atomic claim, failed-job revival, and truthful terminal states
12. Deterministic negation-aware suppression for restrictive safety wording
13. Static design drift-profile layer with attribute extraction and baseline comparison primitives
14. Static artifact profile persistence and baseline-linked profile history
15. Reviewer-facing static drift summary block in PR comment output
16. Read-side static drift aggregation queries for repo summaries and artifact leaderboards
17. Baseline-first onboarding inventory persistence and selective historical backfill-job planning
18. Historical backfill execution with persisted artifact/profile lineage
19. Operator dashboard, JSON query APIs, and local CLI for onboarding/backfill inspection
20. Large-repo dashboard aggregation optimization for real OSS repo responsiveness

### Next prioritized slices on `feature/drift-engine-v1`
1. Signal fusion improvements between deterministic and semantic findings
2. Frontend extraction for dashboard pages into dedicated templates/static assets so product work no longer lands in `main.py`
3. Value-first dashboard insights layer (`review now`, guardrail regressions, capability expansions, recommended actions)
4. Artifact grouping and control-surface map for prompts, agents, tool wiring, model config, and guardrails
5. Historical storyline and timeline views for top drifting artifacts
6. OSS evaluation harness and candidate-repo runner
7. Production-oriented persistence and deployment hardening

### Dashboard product direction for next iteration

The newly implemented dashboard is a strong operator and validation surface, but it is not yet the customer-facing product end state.

Tomorrow's design priority should be to shift the dashboard from a metrics inventory into a decision surface.

Today's architecture priority should be to extract the current dashboard frontend into dedicated files before adding more product complexity.

That extraction should establish a cleaner split:
- FastAPI routes stay thin and primarily serve JSON plus page shells
- dashboard HTML lives in `templates/`
- dashboard styling and interactivity live in `static/`
- future overview graphs and storyline views land in versioned frontend assets rather than inline strings in `main.py`

That means the next dashboard iteration should answer:
- what needs review now,
- what got riskier,
- where guardrails weakened,
- which control surfaces matter most,
- and what the reviewer should inspect next.

Recommended dashboard v2 sections:
1. `Needs attention now` ranked artifact list with short narratives and recommended actions
2. `Highest-risk drift` panel highlighting capability expansion, autonomy jumps, and safety regressions
3. `Control surface map` grouping artifacts by prompts, agents, tool wiring, model config, retrieval, and guardrails
4. `History and drift timeline` for top artifacts so customers can understand drift as a storyline rather than raw counts
5. `Coverage and confidence` panel distinguishing high-confidence artifacts from lower-confidence discovery output

Now implemented as the first structural step:
- `/dashboard` is the portfolio overview and decision queue
- `/dashboard/{owner/repo}` remains the repo detail surface for artifact-level inspection
- overview cards and coverage bars should keep evolving toward richer graphs rather than reverting to a repo card wall
- the overview now opens with an explicit portfolio risk-state summary so users see overall exposure before repo-level detail

Important product rule:
- do not optimize the dashboard for exhaustiveness alone
- optimize it for reviewer prioritization and customer decision-making

Important architecture rule:
- do not continue growing dashboard HTML, CSS, and JavaScript inline in `main.py`
- use the extracted frontend surface as the only place for future dashboard UI work

### Planned future workflow

Name:
- `audit-feedback-loop-v1`

Suggested branch name:
- `feature/audit-feedback-loop-v1`

Purpose:
- create a feedback and outcome loop so PromptDrift can learn from reviewer reactions, explicit customer feedback, and eventual PR outcomes
- improve the risk engine using real-world disagreement and acceptance patterns rather than only internal intuition

Why it matters:
- a strong AI review product should not only publish audits, it should also learn where its guidance was useful, noisy, ignored, accepted, or overridden
- customer feedback and PR outcomes are a future source of product-quality signals, evaluation fixtures, and rule-tuning priorities

Important interpretation rule:
- a `High` risk PR being merged is not automatic proof that the engine was wrong
- it should be treated as a disagreement or review-outcome signal that requires context
- learning inputs should remain advisory until validated through broader analysis

Planned scope:
- collect lightweight feedback on managed PromptDrift comments
- support future explicit textual feedback from reviewers
- capture PR outcomes such as merged, closed, changed-after-audit, and merged-after-follow-up
- derive learning signals that can be used for reporting, evaluation, and future engine tuning

Expected future data concepts:
- `AuditFeedback`
- `PullRequestOutcome`
- `AuditLearningSignal`

Recommended future rollout:
1. Passive PR outcome capture
2. GitHub emoji reaction capture on managed comments
3. Structured textual feedback on managed comments or a dedicated future input surface
4. Derived learning-signal reporting
5. Engine-tuning and evaluation workflows built on top of those signals

Guardrails for future implementation:
- only feedback tied to managed PromptDrift comments should count as product feedback
- raw user events and derived learning signals should be stored separately
- outcome collection should be tied to the audited head SHA, not only the PR number
- the first consumer of this data should be reporting and evaluation, not automatic model retraining

### Planned future workflow

Name:
- `customer-onboarding-baseline-v1`

Suggested branch name:
- `feature/customer-onboarding-baseline-v1`

Purpose:
- help PromptDrift understand a newly installed repository before future PRs arrive
- establish an initial baseline for AI-relevant artifacts, prompt locations, and likely behavioral control surfaces
- make later drift detection more accurate by grounding audits in the repo's starting state

Why it matters:
- many meaningful AI changes only make sense relative to the pre-existing baseline
- onboarding intelligence can reduce false positives caused by treating every future edit as isolated
- it can help PromptDrift identify where prompts, policies, model-routing logic, and tool-authority definitions actually live inside a repo
- it creates the foundation for future trend, governance, and repo-specific tuning workflows

Core customer value:
- faster time-to-value after installation
- more accurate first audits because the engine already knows the likely AI control surface
- better artifact lineage from day one
- future ability to explain not only what changed, but what changed relative to the repo's original AI baseline

Key product question:
- should PromptDrift ingest the full historical commit graph on install, or establish a strong present-day baseline and optionally backfill later?

Recommended starting answer:
- start with a **baseline-first onboarding model**, not full historical ingestion by default
- capture the repository's current AI-relevant artifact map and baseline versions at installation time
- add selective or optional historical backfill later only where it produces clear customer value

Why baseline-first is the right first move:
- it is much cheaper and faster than replaying full repo history
- it gives immediate product value without long installation delays
- it avoids over-collecting data before there is a strong use case for it
- it keeps onboarding operationally predictable for larger repositories

When historical backfill may later be justified:
- regulated customers want governance history from before install time
- a repo already has a long and important prompt/policy evolution history
- customers want trend views that start before PromptDrift was installed
- baseline-only mode proves insufficient for high-value onboarding use cases

Planned onboarding outputs:
- discovered AI-relevant artifact inventory
- artifact classification and likely purpose hints
- baseline artifact versions and hashes
- repo-level onboarding summary
- confidence notes about what PromptDrift believes are the main AI control surfaces in that repo

Expected future data concepts:
- `RepositoryOnboarding`
- `OnboardedArtifact`
- `OnboardingBaselineVersion`
- optional future `HistoricalBackfillJob`

Recommended future rollout:
1. Installation-time repository scan and AI artifact discovery
2. Baseline version capture for relevant artifacts
3. Repo-level onboarding summary and persisted inventory
4. Optional targeted historical backfill for selected repos or artifacts
5. Future onboarding-aware detection and reporting improvements

Guardrails for future implementation:
- do not require full historical replay for every customer install
- keep onboarding bounded in time and cost
- preserve a distinction between baseline captured at install time and history reconstructed later
- treat inferred artifact purpose as probabilistic metadata, not hard truth
- prefer selective backfill over all-history ingestion unless customers explicitly need it

### End-goal validation plan

The ultimate validation for the `drift-engine` and onboarding work should be real open-source repository onboarding.

That means PromptDrift should eventually be able to behave as if a public repository installed it as a GitHub App and then:
- discover likely AI control surfaces,
- establish a strong present-day baseline,
- selectively digest relevant historical evolution,
- populate artifact and static-profile history,
- and surface useful PR and dashboard-level drift outputs.

The `doria90/openfang` onboarding run has now validated that the current system can at least discover and render a large public-agent-style repository through the new dashboard surface.

That run also exposed the next product truth clearly:
- discovery alone is not sufficient,
- customers need the dashboard to translate noisy artifact inventories into prioritized, explainable review guidance.

Why this is the right end-goal test:
- it validates onboarding robustness on repositories we did not design
- it tests whether baseline-first onboarding and selective history digestion produce useful drift intelligence
- it tests whether the eventual dashboard and read-side surfaces are helpful on real repository history
- it keeps product development honest by requiring value on messy, real-world GitHub repositories

Recommended validation sequence:
1. Build onboarding inventory persistence and baseline capture
2. Add selective historical backfill for discovered artifacts
3. Add read-side trend and repo-level aggregation queries
4. Add operator/API surfaces for onboarding and backfill
5. Validate against a curated set of public OSS repositories with visible prompts/configs/tooling

See `docs/oss-onboarding-evaluation-plan.md` for the detailed evaluation plan and success criteria.

### Persistence architecture direction
- one relational database for the near term
- SQLite for local/dev and PostgreSQL as the production target
- logical separation between operational queue tables and durable audit/history tables
- hot structured records in primary storage, cold/raw payloads minimized or archived later
- do not split into multiple primary databases yet; design for future decomposition instead

---

## Stage 1 – MVP (Core Logic & Webhook Processing)

### Goals
1. Deliver a working FastAPI service that receives GitHub `pull_request` webhooks and analyzes diffs for AI‑related changes.
2. Implement the core audit logic using OpenAI to produce a summary and risk level.
3. Post comments back to the PR.
4. Provide a simple `.env.example` and `requirements.txt` so the service can be run locally.
5. Structure the code for easy extension: separate modules for GitHub interactions, auditing, and HTTP handling.

### Features
- FastAPI app with `/webhook` endpoint and signature verification.
- JWT generation for GitHub App authentication and installation token retrieval.
- Diff fetching and simple filename filter for `prompt|ai|llm`.
- OpenAI integration (gpt-4o-mini) with prescribed system prompt.
- Commenting on PRs via PyGithub.
- Minimal error handling and logging.
- Basic unit tests covering the analyzer logic and webhook verification.

### Deliverables
- `main.py` containing endpoint and helpers.
- `requirements.txt` (specified packages).
- `.env.example` with necessary variables.
- A handful of tests.

### Notes
This stage is effectively complete and now surpassed by the active branch. The product has already moved beyond pure synchronous MVP behavior.

---

## Stage 2 – Persistence, Multi-Tenancy & Dashboard

### Goals
1. Introduce data storage to record audit results, installations, and users.
2. Add basic multi‑tenant architecture (installations as tenants) with row‑level scoping.
3. Implement a simple admin dashboard (API endpoints and minimal UI) for customers.
4. Add background processing so webhooks enqueue work and workers perform analysis asynchronously.
5. Improve environment variable handling and configuration.

### Features
- PostgreSQL (or SQLite for development) with SQLAlchemy or Tortoise models.
- `Installation`, `AuditRecord`, and `User` models; migrations included.
- separate operational queue data from durable audit/history data at the schema and service layer even if they share one physical database initially.
- Token caching in Redis (installation tokens expire every 10 minutes).
- Background task queue using FastAPI’s `BackgroundTasks` or Celery/Redis for the audit jobs.
- API routes for dashboard data: total PRs audited, risk distribution, recent summaries.
- Simple web UI (FastAPI’s Jinja templates or a React/HTML static bundle) that shows metrics per installation.
- GitHub OAuth login for users; role-based access control.
- Enhanced logging and error handling; proper HTTP responses for failures.
- Configuration module using Pydantic’s `BaseSettings`.

### Deliverables
- `app/` package with submodules (`models.py`, `audit.py`, `routers/` etc.).
- Dashboard API and minimal front‑end assets.
- Updated `requirements.txt` with new dependencies (sqlalchemy, psycopg2-binary, redis, celery, jinja2, etc.).
- Deployment notes for using PostgreSQL and Redis.
- Migration scripts (Alembic or similar).

### Notes
This stage is now partially underway. Durable persistence, queue-backed execution, and history structures have already started. The remaining work is to turn those foundations into customer-visible reporting and operational control surfaces.

### Planned future extension: audit feedback loop

Once the durable audit/history layer is stable enough, PromptDrift should add a feedback-and-outcome workflow under `audit-feedback-loop-v1`.

This future slice should aim to answer:
- did reviewers find the audit useful?
- did reviewers disagree with the assigned risk level?
- was a high-risk PR merged anyway?
- was the PR materially changed after the audit before it was accepted?
- which findings or rule families most often correlate with overrides or disagreement?

This work should initially be framed as:
- customer feedback intake
- PR outcome telemetry
- evaluation signal generation

It should not initially be framed as:
- automatic retraining
- direct auto-lowering or auto-raising of risk levels without review

### Planned future extension: customer onboarding baseline

Once PromptDrift has a stable durable history layer, it should add onboarding intelligence under `customer-onboarding-baseline-v1`.

This future slice should aim to answer:
- where do the repo's likely prompts, guardrails, and AI control artifacts live?
- which files appear to define behavior, policy, model routing, tool access, or disclosure boundaries?
- what baseline artifact versions should future audits compare against?
- what can PromptDrift learn immediately at install time without replaying full history?

Recommended product stance:
- onboarding should default to present-day baseline capture
- historical commit ingestion should be optional, selective, and justified by customer value

This work should initially be framed as:
- installation-time artifact discovery
- baseline capture
- future drift-context enrichment

It should not initially be framed as:
- mandatory full historical mining for every repository
- expensive repo reconstruction before the product can provide any value

---

## Stage 3 – Enterprise Scaling & Advanced Features

### Goals
1. Harden the system for high traffic and multi‑region deployments.
2. Add advanced auditing capabilities and reports.
3. Provide optional per‑tenant configuration (e.g. custom OpenAI key, drift thresholds).
4. Build administrative tooling for operators and a richer customer UI.
5. Implement billing hooks, usage tracking, and exportable reports.

### Features
- Add Celery/RabbitMQ or managed queue for processing; autoscale workers.
- Use PostgreSQL with read replicas, apply RLS policies, and optionally support separate schemas or databases per tenant.
- Implement a dedicated `PromptHistory` table and drift metrics (token counts, model versioning, baseline comparisons).
- Offer email or Slack notifications for high‑risk PRs.
- Add Sentry or another APM for error tracing and performance monitoring.
- Support multiple AI models and providers; allow customers to plug in their own evaluation logic or rules engine.
- Rich dashboard with charts (using Chart.js or similar), export to PDF/CSV, per‑repo drill‑downs.
- Admin panel for organization owners to manage installations, view usage, and override settings.
- Multi‑region deployment guidance and infrastructure as code (Terraform) examples.
- Add robust rate‑limiting, retry logic, and back‑pressure to handle bursts.

### Deliverables
- Scalable deployment documentation (Kubernetes manifests, cloud templates).
- Instrumentation for metrics (Prometheus, Grafana dashboards).
- Full operator API for tenants, users, and billing.
- Comprehensive test suite including load tests and multi‑tenant isolation tests.

### Notes
By this stage, the product should be ready for enterprise customers with SLAs. The architectural decisions from earlier phases must prove functional; we will iterate based on customer feedback and usage patterns.

---

## Epilogue
Each stage builds on the previous one. The early emphasis on modularity and separation of concerns ensures that the core audit logic developed in Stage 1 is easily reused as we add persistence, multi-tenancy, and scaling features. This plan should continue to be updated whenever the implementation materially outpaces the roadmap text.