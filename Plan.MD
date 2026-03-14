# PromptDrift Development Plan

This document outlines the phased roadmap for building the PromptDrift backend.  It is intended as a living guide and will evolve as the project grows.

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
	- live validation completed for benign, risky, fallback, and larger PR scenarios
- Current known limitations:
	- bot comment dedupe/update is not implemented yet
	- queue is local in-process SQLite for this stage
	- durable audit/history schema is not implemented yet

### Completed slices on `feature/drift-engine-v1`
1. Diff parsing and AI relevance classification
2. Artifact-aware context mode selection
3. Deterministic rule engine and risk floor scoring
4. Queue-backed webhook execution path
5. Retry handling, provider retry hints, and deterministic preliminary fallback
6. Structured semantic review packages for LLM review framing

### Next prioritized slices on `feature/drift-engine-v1`
1. Durable audit record persistence (`PullRequestAudit`, `ChangedArtifact`, `Finding`, `AuditComment`)
2. Signal fusion improvements between deterministic and semantic findings
3. Bot comment dedupe/update behavior
4. Artifact lineage and baseline support (`ArtifactVersion`)
5. Better reviewer-facing explanation formatting

### Persistence architecture direction
- one relational database for the near term
- SQLite for local/dev and PostgreSQL as the production target
- logical separation between operational queue tables and durable audit/history tables
- hot structured records in primary storage, cold/raw payloads minimized or archived later
- do not split into multiple primary databases yet; design for future decomposition instead

---

## Stage 1 – MVP (Core Logic & Webhook Processing)

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
No database, no queueing, no multi‑tenant support.  Focus on correctness of the analysis logic.  This stage will serve as a prototype and be sufficient for initial customer trials or internal use.

---

## Stage 2 – Persistence, Multi‑Tenancy & Dashboard

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
This stage lays the foundation for scalable, multi‑user deployments; the MVP logic is reused and verified.  Focus on security and tenancy boundaries.  Add tests for database interactions and dashboard endpoints.  Avoid premature multi-database splits; start with one well-structured relational store and introduce decomposition only when workloads justify it.

---

## Stage 3 – Enterprise Scaling & Advanced Features

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
By this stage, the product should be ready for enterprise customers with SLAs.  The architectural decisions from earlier phases must prove functional; we will iterate based on customer feedback and usage patterns.

---

## Epilogue
Each stage builds on the previous one.  The early emphasis on modularity and separation of concerns ensures that the core audit logic developed in Stage 1 is easily reused as we add persistence, multi‑tenancy, and scaling features.  This plan will be referenced and updated as the project progresses.