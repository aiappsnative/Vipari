# DriftGuard Development Plan

This document outlines the phased roadmap for building the DriftGuard backend. It is intended as a living guide and will evolve as the project grows.

This is the canonical roadmap document. Product/usage guidance belongs in [README.md](README.md), while deeper architecture belongs in [docs/detection-engine-plan.md](docs/detection-engine-plan.md).

For the enduring product thesis, guardrails, and design principles that should outlast roadmap changes, see `SOUL.md`.

---

## Product value north star

DriftGuard exists to help customers safely ship AI changes.

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

### Current strategic decisions

- **Primary buyer:** Head of AI Platform or Head of Engineering at a mid-size SaaS company shipping AI features
- **Primary user:** senior backend / AI platform engineers, with a smaller set of security or platform reviewers for escalations
- **Product wedge:** GitHub PR review workflow
- **Primary decision improved:** whether an AI-related PR must be escalated before merge
- **Default baseline concept:** latest explicitly approved version of a control surface
- **Near-term commercial proof:** find concrete, non-obvious high-risk AI changes in real repos with tolerable noise

---

## Current repository map (April 2026)

### `main`
- Purpose: active post-merge baseline for the static-first drift engine plus the merged customer control plane and dashboard control tower
- Current status:
	- includes the merged `feature/drift-engine-v1` milestone
	- includes the merged `feature/pr-escalation-v1` slice
	- includes the merged `feature/approved-baseline-v1` slice
	- includes the merged `feature/repo-provenance-v1` slice
	- includes the merged `feature/dashboard-ux-v1` slice
	- includes the merged `feature/dashboard-redesign-v2` slice
	- includes the merged `feature/drift-storyline-v1` slice
	- includes the merged `feature/pr-comment-v2-tuning` slice
	- includes the merged `feature/repo-evidence-v1` slice
	- includes the merged `feature/signal-fusion-v1` slice
	- includes the merged `feature/discovery-precision-v1` slice
	- now includes the merged `feature/driftguard-base44-stripe-handoff-v1` slice
	- now includes the merged `feature/control-plane-foundation-v1-issue-57` slice (PR #63)
	- now contains the canonical implementation of the dashboard, onboarding, static drift-profile work, episode-aware PR comment lifecycle, risk-emoji reviewer comments, current-risk escalation label sync, approved-baseline provenance, provenance-linked repo case files, lightweight baseline promotion, landed-history-only dashboard evidence semantics, persisted PR lifecycle state, and the first split-service cloud deployment scaffolding
	- now includes the triage-first operator shell, repo case-file split-pane dashboard flow, artifact storyline APIs and rendering, repo journey snapshots, fused dashboard priority ordering, and grouped low-signal onboarding discovery for larger OSS repositories
	- now includes separate webhook/worker/api entrypoints, queue abstractions, Redis-backed token-cache support, admin-token-protected dashboard/operator routes in the split API service, and metrics exposure disabled by default
	- now includes the GitHub OAuth customer control plane, Base44/Wix billing handoff flow, free-tier and Stripe fallback billing paths, setup-aware app shells, entitlement-gated dashboard/webhook behavior, production-safe dashboard auth gating, and the final Stripe billing-ownership and webhook/worker hardening pass
	- now includes the merged `feature/production-persistence-v1` slice, including production fail-closed migration/runtime guardrails, Redis-first production queue contracts, split-service smoke tooling, and broader PostgreSQL-locator persistence regression coverage for restart, idempotency, and retry paths
	- now includes machine-principal identity management for workspace-bound service accounts: `machine_principals` table, CRUD records, HS256 JWT issuance and validation, `/cp/*` REST API routes, scope enforcement, runtime guardrails for `INTERNAL_JWT_SECRET` presence and minimum key length (RFC 7518 §3.2), and 33-test coverage in `test_control_plane_auth.py`
	- now includes the merged `feature/control-plane-low-risk-actions-v1` slice (issue #60): three new control-plane endpoints — `POST /cp/audits/{audit_id}/feedback` (append-only structured feedback events), `POST /cp/audits/{audit_id}/triage` (append-only triage state transitions, never mutates `pull_request_audits`), and `GET /cp/workspaces/{workspace_id}/exports/{export_id}` (export status without `result_blob`); all three enforce workspace isolation via 404-masking, require `drift.write.low` or `drift.read` scope respectively, and persist to two new migration-managed tables with validated input bounds
	- now includes the merged `feature/control-plane-read-models-v1` slice (issue #58): authenticated, workspace-scoped internal `/cp/*` JSON read endpoints for workspace listing, repo inventory, repo posture, repo case file, artifact inventory, artifact detail, artifact storyline, repo audits, audit detail, and the escalation queue; all endpoints are read-only and JSON-only; this layer is the canonical structured data source for the dashboard and future MCP integrations
	- now includes the merged `feature/control-plane-high-risk-proposals-v1` slice (issue #61): human-gated proposal-and-approval flows for the control plane; covers baseline-promote proposals (artifact-scoped) and repo-onboarding proposals (workspace-scoped); adds `services/proposals_records.py` as the full CRUD persistence layer, migration `0007_add_high_risk_proposal_tables`, 8 new API routes, `principal_kind` vocabulary and `validate_scope_kind_compatibility` in `internal_auth.py`, and `require_cp_principal_kind` in `cp_auth.py`; enforces four-eyes rule, TOCTOU-safe conditional UPDATE, flood limits, 30-day expiry, artifact_id/URL consistency guard (audit log integrity), and service-account-blocked-from-high-scope at both creation and route layer; 36 tests in `test_cp_high_risk_proposals.py`
	- now includes the merged `feature/dashboard-control-tower-v1` slice (issue #62, PR #67): workspace posture and escalation queue surfaces on `/dashboard`, repo decision panels and pending proposal summaries on `/dashboard/{owner/repo}`, the contextual `/app/help` experience, and post-review hardening so pending repo proposals remain workspace-scoped, repo-visible, and provenance-correct
	- now includes the merged `feature/security-hardening-issue-68` slice (PR #69): signed OAuth and install-flow cookies, session-bound install callback validation, sanitized public auth-session payloads, trusted workspace-install enforcement for onboarding/backfill, localhost-only local-owner fallback, and explicit separation between read-only standalone dashboard inspection and authenticated mutation/control-plane access
	- latest broader security regression sweep for the merged issue `#68` slice passed locally across `tests/test_control_plane_ui.py`, `tests/test_control_plane_auth.py`, `tests/test_operator_api.py`, `tests/test_runtime_guardrails.py`, `tests/test_dashboard_api.py`, and `tests/test_dashboard_control_tower.py` (`156 passed, 1 skipped`)

### `feature/mcp-map-server-v1` (issue #59)
- Purpose: active branch for the first customer agent-integration slice, pairing a downloadable customer MCP connector with a hosted PromptDrift broker
- Current status on this branch:
	- authenticated Agent Integrations page at `/app/integrations/mcp` with setup guidance, trust-boundary explanation, available-tool inventory, and visible workspace machine principals
	- authenticated download route at `/app/integrations/mcp/download` that packages the customer connector as a ZIP distributed through the product website only, not PyPI
	- new `services/mcp_broker.py` hosted broker layer with machine-principal Basic-auth authentication and four initial read-first tools: `promptdrift.list_repos`, `promptdrift.get_repo_posture`, `promptdrift.get_repo_casefile`, and `promptdrift.list_escalations`
	- new broker endpoints in `main.py`: `GET /api/agent-integrations/mcp/tools` and `POST /api/agent-integrations/mcp/invoke`
	- new `customer_mcp_server/` connector package with its own `requirements.txt`, `promptdrift_mcp_server.py`, bundled environment template, and example Claude Desktop host config
	- Help Center and API Keys surfaces now route customers toward the dedicated MCP integrations page instead of leaving agent setup implicit
	- focused regression coverage added in `tests/test_control_plane_ui.py` and new `tests/test_mcp_broker.py`; current local validation on this branch: `73 passed` across both touched test modules

### `feature/drift-storyline-v1`
- Purpose: completed branch for turning artifact history into a first-class case-file storyline surface
- Final status at merge:
	- `DriftEpisode` read models, featured artifact storyline rendering, artifact-level storyline API responses, and repo-level history cues are now on `main`
	- sparse-history fallback, provenance links, baseline/current posture markers, and storyline regression coverage are now part of the canonical repo dashboard surface
	- storyline work is now a dependency for future repo-evidence improvements rather than a pending roadmap slice

### `feature/compliance-export-package`
- Purpose: completed branch for turning the compliance export from a structural archive into a persisted-evidence package with explicit scope boundaries
- Final status on this branch:
	- compliance export rows now use persisted baseline, audit, artifact-version, static-profile, and posture data instead of placeholder blanks where source data already exists
	- risk events and version-history rows now read the stored posture risk level consistently
	- drift export files now emit real artifact drift history and leaderboard rows from persisted PR-side static profiles
	- optional raw artifact content export is intentionally limited to approved baseline content plus in-range PR scan versions, excluding historical backfill noise
	- exact-value regression coverage now validates export contents rather than only ZIP shape and file presence
- Exit note:
	- this slice is directionally complete for the current feature goal; remaining work should be separate product decisions such as broader control mapping, auditor-facing copy polish, or future export formats rather than more placeholder cleanup on this branch

### `feature/driftguard-base44-stripe-handoff-v1`
- Purpose: completed integration branch for issue `#25`, now merged to `main`
- Final status at merge:
	- customer control-plane persistence, auth, billing, install-linking, repo allocation, and setup-aware access gating are now mainline functionality
	- the final merge included Stripe billing-ownership hardening, worker allocation/entitlement revalidation, and stale webhook-delivery reclaim
	- remote and local feature-branch cleanup is complete
- Historical note:
	- keep [docs/base44-stripe-handoff-v1-handoff.md](docs/base44-stripe-handoff-v1-handoff.md) as archived branch context rather than a live restart target

### `feature/control-plane-foundation-v1-issue-57`
- Purpose: completed branch for issue `#57`, merged to `main` via PR #63
- Final status at merge:
	- `machine_principals` table and full CRUD records layer (`services/control_plane_records.py`)
	- HS256 JWT issuance and validation via `services/internal_auth.py` and `services/cp_auth.py` FastAPI dependencies
	- `/cp/*` REST routes: create principal, list principals, revoke principal, issue token (`services/api_service.py`)
	- `ALL_SCOPES` registry with scope re-validation on every token-issuance call
	- `CreatePrincipalRequest` Pydantic model with `Literal["service_account"]` kind and 1–120 char `display_name` bounds
	- `INTERNAL_JWT_SECRET` runtime guardrails: ≥32-byte minimum (RFC 7518 §3.2) in all environments; production `api`/`monolith` roles require the secret to be present
	- `0004_add_machine_principals` schema migration with `workspace_id` index
	- 33 tests in `tests/test_control_plane_auth.py` (all passing)
	- Security hardening commit resolved 7 issues: empty `aud` IndexError, key-length enforcement, production presence check, scope re-validation, input validation, import placement, and missing DB index
	- Pre-existing failures from prior sprint (migration 0003 table-existence guard, stale JS assertion) also resolved in this PR

### `feature/dashboard-control-tower-v1` (issue #62)
- Purpose: completed dashboard control-tower refactor branch, merged to `main` via PR #67
- Final status at merge:
	- `GET /api/dashboard/escalation-queue` — workspace-wide ranked flat list of escalating insights; accepts `?include_watch=true`; respects repo visibility filtering; returns `workspace_posture` ("risk"/"watch"/"healthy"), `workspace_posture_reasons` (top-3 deduplicated risk reasons), `escalation_count`, `watch_count`, and `items`
	- `GET /api/repos/{repo_full}/proposals/pending` — pending baseline proposals for a repo; enforces repo visibility, scopes results to the caller workspace, preserves `is_agent_proposal`, and returns `artifact_path`, `rationale`, and `created_at`; empty list if no proposals are pending in that visible workspace slice
	- new `EscalationQueueItem` frozen dataclass and `build_workspace_escalation_queue()` in `services/dashboard_views.py`
	- new repo-scoped pending-proposal read helpers in `services/proposals_records.py`, including the workspace-bound query used by the main dashboard surface
	- workspace posture bar injected into `dashboard_index.html` above the main content; escalation queue section added to main dashboard with rank, title, repo/artifact context, attribute delta chips, and direct review links
	- repo decision section added to `dashboard_repo.html` with posture badge, top finding, recommended action, and pending-proposals panel
	- `/app/help` now renders a dedicated contextual Help Center with workspace state summaries, next-step guidance, and support placeholders instead of a generic placeholder page
	- `dashboard-index.js` — `fetchEscalationQueue`, `renderWorkspacePostureBar`, `renderEscalationQueueTable`, `loadEscalationQueue` added; called on page init
	- `dashboard-repo.js` — `renderDecisionSection`, `loadPendingProposals` added; both called from `applyDashboardPayload`
	- `dashboard.css` — ~250 lines of new control-tower styles: posture bar, escalation table, repo decision section, delta chips, responsive breakpoint at 900 px
	- 17 tests in `tests/test_dashboard_control_tower.py` (16 pass, 1 conditional skip); covers empty-db healthy posture, key structure, drift detection, watch/review-now filtering, sort order, repo-filter, posture-reasons type, and all API endpoints
	- follow-up hardening commit `db33753` fixed repo-scope authorization, cross-workspace proposal visibility, agent/human proposal provenance, and the `filter=mine` nav mismatch; focused regressions passed before merge
	- remote and local feature-branch cleanup accompanies this merge-completion step

### `feature/control-plane-high-risk-proposals-v1` (issue #61)
- Purpose: completed branch for issue `#61`, human-gated proposal-and-approval flows for the control plane, now merged to `main`
- Final status at merge:
	- `POST /cp/artifacts/{artifact_id}/baseline/proposals` (`drift.write.low`) — create a baseline-promote proposal for an artifact; flood-limited to 5 pending per artifact; 30-day expiry; rationale (≤2000 chars), metadata (≤20 keys), linked_audit_ids (≤50, positive ints) validated; workspace isolation via allocation check plus `artifact_id` binding
	- `GET /cp/artifacts/{artifact_id}/baseline/proposals` (`drift.read`) — list baseline proposals for an artifact with workspace isolation
	- `POST /cp/artifacts/{artifact_id}/baseline/proposals/{proposal_id}/approve` (`drift.write.high`, `human_operator` only) — four-eyes rule; expiry enforced; TOCTOU-safe conditional UPDATE; `artifact_id` verified against proposal record so URL mismatch → 404 (audit log integrity guard)
	- `POST /cp/artifacts/{artifact_id}/baseline/proposals/{proposal_id}/reject` (`drift.write.high`) — reject a pending proposal; expired proposals may still be rejected; same `artifact_id` consistency guard
	- `POST /cp/workspaces/{workspace_id}/repos/onboarding-proposals` (`drift.write.low`) — create a repo-onboarding proposal; flood-limited to 20 pending per workspace; duplicate-pending guard per `(workspace_id, repo_full)`; 30-day expiry
	- `GET /cp/workspaces/{workspace_id}/repos/onboarding-proposals` (`drift.read`) — list onboarding proposals for a workspace
	- `POST /cp/workspaces/{workspace_id}/repos/onboarding-proposals/{proposal_id}/approve` (`drift.write.high`, `human_operator` only)
	- `POST /cp/workspaces/{workspace_id}/repos/onboarding-proposals/{proposal_id}/reject` (`drift.write.high`) — service accounts permitted
	- `POST /cp/principals` hardened with `validate_scope_kind_compatibility` — service accounts blocked from `drift.write.high` and `admin.write` at creation time
	- new `services/proposals_records.py` with `BEGIN IMMEDIATE` flood-check + INSERT atomicity and conditional UPDATE approve/reject pattern
	- migration `0007_add_high_risk_proposal_tables` creates `cp_baseline_proposals` and `cp_repo_onboarding_proposals` with indexes
	- `PRINCIPAL_KIND_SERVICE_ACCOUNT`, `PRINCIPAL_KIND_HUMAN_OPERATOR`, `SCOPES_REQUIRING_HUMAN_OPERATOR`, `validate_scope_kind_compatibility` added to `internal_auth.py`
	- `require_cp_principal_kind` added to `cp_auth.py` as the route-layer human-only gate
	- 36 tests in `test_cp_high_risk_proposals.py`; post-review security audit found and fixed an IDOR-class audit-integrity gap (`artifact_id` URL not verified against proposal record) with two regression tests
	- full suite green at `438 passed` after merge
	- remote and local feature-branch cleanup is complete

### `feature/control-plane-low-risk-actions-v1` (issue #60)
- Purpose: completed branch for issue `#60`, control-plane low-risk write actions, now merged to `main`
- Final status at merge:
	- `POST /cp/audits/{audit_id}/feedback` (`drift.write.low`) — append-only structured feedback events on PR audits with six validated `kind` values, optional free-text `comment` (max 2 000 chars), bounded `metadata` dict (max 20 keys, key ≤ 80 chars, value ≤ 500 chars), workspace isolation via audit→repo_full→allocation lookup (404 on miss), and CP audit log entry on every write
	- `POST /cp/audits/{audit_id}/triage` (`drift.write.low`) — append-only triage state transitions with three validated `state` values, optional `reason` (max 2 000 chars); writes only to `audit_triage_events`, never mutates `pull_request_audits`
	- `GET /cp/workspaces/{workspace_id}/exports/{export_id}` (`drift.read`) — returns export job status fields; `result_blob` is never included in the response
	- new `services/audit_feedback_records.py` service module with dataclasses, schema bootstrap, and CRUD for both new tables
	- migration `0006_add_audit_feedback_and_triage_tables` wired into `schema_migrations.py`
	- vocabulary validation enforced at both API layer (Pydantic `kind`/`state` checks + 422 response) and service layer (`add_audit_feedback` / `add_audit_triage` raise `ValueError` for unknown values)
	- 16 tests across all three endpoints; post-review security hardening fixed unbounded inputs, asymmetric response bodies, and service-layer vocabulary guard
	- remote and local feature-branch cleanup is complete

### `feature/control-plane-read-models-v1` (issue #58)
- Purpose: completed branch for issue `#58`, control-plane read model layer, now merged to `main`
- Final status at merge:
	- `GET /cp/workspaces` — workspace listing scoped to the caller
	- `GET /cp/workspaces/{workspace_id}/repos` — repo inventory with lightweight posture metadata (posture, last_scan_at, open_audit_count, open_escalation_count)
	- `GET /cp/repos/{workspace_id}/{owner}/{repo}/posture` — structured posture verdict (posture, top_reasons, attribute dimensions, trend summary)
	- `GET /cp/repos/{workspace_id}/{owner}/{repo}/casefile` — expanded repo case file (featured_artifacts, open_audits, key_episodes, recent_prs, governance_flags, coverage_summary)
	- `GET /cp/repos/{workspace_id}/{owner}/{repo}/artifacts` and `GET /cp/artifacts/{artifact_id}` — artifact inventory and detail
	- `GET /cp/artifacts/{artifact_id}/storyline` — normalized, ordered artifact episode history for design-drift visibility over time
	- `GET /cp/repos/{workspace_id}/{owner}/{repo}/audits` (with state/severity/artifact_id/recency filters) and `GET /cp/audits/{audit_id}` — audit list and full audit detail
	- `GET /cp/workspaces/{workspace_id}/escalations` — flat escalation queue for "Needs review now" use cases
	- all endpoints read-only, JSON-only, workspace-scoped, require appropriate `drift.read` scope
	- this layer is the canonical structured data source for dashboard surfaces and future MCP integrations, decoupling product truth from page-assembly logic
	- remote and local feature-branch cleanup is complete

### `feature/pr-comment-v2-tuning`
- Purpose: completed review-surface slice retained as the merge lineage for PR comment content tuning and label lifecycle hardening
- Current status at merge time:
	- reviewer-facing PR comments tuned around the most decision-useful deltas (`guardrail_robustness`, `capability_risk`, `autonomy_level`)
	- governance drift separated into its own conditional section instead of crowding the main `Key deltas` summary
	- evidence selection tuned to prefer concrete delta-linked examples before generic artifact metadata
	- decision-first headers now include risk emojis for high, medium, and low recommendations
	- escalation labels now sync bidirectionally with the latest review outcome, including de-escalation cleanup on later low-risk passes
	- live validation completed against dummyAI PR `#46`, including stale-worker debugging and final GitHub-side confirmation of the tuned comment format

### `feature/drift-engine-v1`
- Purpose: completed milestone branch retained as the merge lineage for the first static-first drift engine release
- Current status at merge time:
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
	- repo-detail baseline-vs-current design posture cards, static-only messaging, and risk-tag/provenance-lite surfacing implemented
	- overview regression-pattern summaries and compatibility hardening implemented so stale or sparse payloads degrade safely in the browser
	- artifact lineage and baseline-aware suppression implemented
	- initial managed PR comment lifecycle implemented
	- compact TLDR + collapsible reviewer comment format implemented
	- GitHub App auth hardening, opened-PR diff retry, and exact-SHA synchronize diff reconstruction implemented
	- atomic job claiming, failed-job requeue on same-SHA redelivery, and honest persistence-failure handling implemented
	- deterministic rule suppression improved for restrictive negated safety wording such as `Do not reveal ...`
	- live validation completed for benign, risky, synchronize, fallback, dedupe, and bot-auth scenarios
	- merged to `main` after the retry-window regression was fixed and the full suite passed
- Remaining limitations now inherited by `main`:
	- queue is local in-process SQLite for this stage
	- signal-fusion follow-on work should now focus on downstream read-side presentation or evaluation depth rather than reopening the core confidence-aware fusion rules
	- deterministic and semantic outputs still need tighter convergence work beyond the latest obvious negation false-positive fix
	- docs and product framing need periodic refresh to match implementation
	- onboarding discovery remains too noisy on some real OSS repositories and needs stronger prioritization and grouping
	- dashboard still needs broader real-repo validation and tuning on history-heavy OSS repositories even after the merged proposal-vs-history routing improvements

### Completed slices on `feature/drift-engine-v1`
1. Diff parsing and AI relevance classification
2. Artifact-aware context mode selection
3. Deterministic rule engine and risk floor scoring
4. Queue-backed webhook execution path
5. Retry handling, provider retry hints, and deterministic preliminary fallback
6. Structured semantic review packages for LLM review framing
7. Durable audit history and artifact lineage
8. Managed PR comment lifecycle on PR updates
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
21. Overview regression-pattern summaries plus payload-compatibility hardening for stale or sparse dashboard responses
22. Retry-window correctness fix so retryable LLM failures receive at least one retry before deterministic fallback
23. Reviewer-facing PR comment tuning, risk-emoji headers, and escalation-label lifecycle sync

### Next prioritized slices after the merge
1. Dashboard redesign v2 for triage-first shell, split-pane detail, and reduced scroll depth
2. Signal fusion improvements between deterministic and semantic findings
3. Discovery precision improvements for large OSS onboarding and backfill
4. Expanded evaluation harness coverage for seeded and real-repo scenarios
5. Production-oriented persistence and deployment hardening beyond the current split-service scaffolding

Suggested next branch sequence:
- `feature/dashboard-redesign-v2`
- `feature/drift-storyline-v1`
- `feature/signal-fusion-v1`
- `feature/discovery-precision-v1`
- `feature/eval-harness-v1`
- `feature/production-persistence-v1`

### What not to build yet

- full runtime observability, logging, or eval-loop infrastructure as a primary product surface
- generic AI code review across all code instead of AI control surfaces
- a heavy policy-authoring UI or full GRC suite

### Dashboard product direction for next iteration

The newly implemented dashboard is now the right structural product surface, but it is not yet the final customer-facing product end state.

The next design priority should be to deepen the decision surface with stronger proposal-vs-landed evidence synthesis, stronger merged-commit linkage, and stronger real-world examples, not to add more generic metrics.

Today's architecture priority should be to preserve the extracted frontend split and keep hardening it against payload evolution before adding more product complexity.

The current split should remain the rule:
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

Now implemented as the current dashboard baseline:
- `/dashboard` is the portfolio overview and decision queue
- `/dashboard/{owner/repo}` remains the repo detail surface for artifact-level inspection
- overview cards and coverage bars should keep evolving toward richer graphs rather than reverting to a repo card wall
- the overview now opens with an explicit portfolio risk-state summary so users see overall exposure before repo-level detail
- the overview now also highlights highest-risk drift and risk by control surface so the landing page reflects portfolio-wide hotspots before repo drill-down
- the overview now includes regression-pattern summaries and should fail gracefully when older local payloads omit newer fields
- the overview now behaves as a triage-first inbox with explicit `Triage` and `Coverage` modes
- repo detail now behaves as a case file with a featured review target, ranked queue, posture/provenance explorer, lower-confidence disclosure, and collapsed deep history

Important product rule:
- do not optimize the dashboard for exhaustiveness alone
- optimize it for reviewer prioritization and customer decision-making

Important architecture rule:
- do not continue growing dashboard HTML, CSS, and JavaScript inline in `main.py`
- use the extracted frontend surface as the only place for future dashboard UI work

### End-of-day checkpoint (2026-03-24)

Completed today on `feature/dashboard-ux-v1`:

- triage-first overview and repo case-file dashboard redesign shipped
- dashboard read models now expose rationale, recommended action, baseline labels, review targets, confidence labels, and lower-confidence queue separation
- large-repo onboarding was hardened by narrowing candidate discovery and switching artifact-content reads to the GitHub contents API
- real OSS onboarding validation completed for `doria90/hermes-agent`
- live local dashboard verification succeeded on a clean non-reload server at `127.0.0.1:8002`

Highest-value follow-on work from that checkpoint was:

1. improve signal quality and proposal-vs-landed evidence synthesis on real OSS repos
2. tighten merged-commit / PR provenance so repo case files route to better review targets
3. reduce discovery noise further on large public repositories
4. turn the current OSS validation path into a more repeatable harness

### Dashboard provenance and baseline checkpoint (2026-03-25)

Completed on `feature/dashboard-ux-v1` after the triage-first redesign:

- repo and overview dashboard read models now expose direct provenance URLs, compact `what changed / why flagged / where` summaries, and clearer review targets
- repo case files now surface qualitative drift labels (`small`, `medium`, `large`) instead of raw top-line drift numbers
- full posture comparison now includes per-attribute findings with reason, remediation, and code-level evidence when stored baseline/current snapshots exist
- snapshot content is now persisted for onboarding baselines, historical artifact versions, and PR artifact versions so later dashboard explanations can cite exact changed lines
- repo case files now allow a lightweight operator action to promote the latest stored source version to the approved baseline
- baseline promotion is idempotent for unchanged versions so repeated actions do not create duplicate approved-baseline rows
- dashboard, audit history, operator API, and full repository test suites were brought back to green after these changes

Current validation status:

- full automated suite passes locally (`282 passed`)
- dashboard API/read-model regression coverage passed during final hardening
- operator/onboarding/static-profile regression coverage passed during final hardening

### Near-term execution plan

#### Recently completed and now merged to `main`

- `feature/pr-escalation-v1`
- `feature/approved-baseline-v1`
- `feature/repo-provenance-v1`
- `feature/dashboard-ux-v1`
- `feature/dashboard-redesign-v2`
- `feature/drift-storyline-v1`
- `feature/signal-fusion-v1`
- `feature/discovery-precision-v1`
proceed- `feature/eval-harness-v1`
- cloud deployment scaffolding split with secured webhook/worker/api service boundaries
- `feature/control-plane-foundation-v1-issue-57` (PR #63, April 2026): machine-principal identity layer, internal JWT auth, `/cp/*` API, scope enforcement, runtime guardrails

#### Open issue alignment refresh (April 2026)

The live GitHub issue queue no longer matches the old ordering in this section.

Verified against repo state and open issues:

- GitHub issue `#20` (`feature/dashboard-redesign-v2`) is effectively completed and merged to `main`.
- GitHub issue `#17` (`feature/drift-storyline-v1`) is effectively completed and merged to `main`.
- GitHub issue `#41` (`feature/repo-evidence-v1`) is now merged to `main`.
- GitHub issue `#44` (`feature/oss-eval-harness-v1`) is now merged to `main` via the broader `feature/eval-harness-v1` slice.
- GitHub issue `#42` (`feature/signal-fusion-v1`) is still open on GitHub, but the feature is already merged to `main`; treat that issue as stale until it is closed or repurposed.
- `feature/discovery-precision-v1` is merged to `main` and should no longer appear in the near-term execution queue.
- GitHub issue `#45` (`feature/production-persistence-v1`) is now merged to `main`.
- GitHub issue `#57` (`feature/control-plane-foundation-v1`) is now merged to `main` via PR #63.
- GitHub issue `#60` (`feature/control-plane-low-risk-actions-v1`) is now merged to `main`; feedback/triage write endpoints and export-status read endpoint are mainline functionality. Issue is closed.
- GitHub issue `#58` (`feature/control-plane-read-models-v1`) is now merged to `main`; all `/cp/*` JSON read endpoints for workspaces, repos, posture, case file, artifacts, storyline, audits, and escalations are mainline functionality. Issue is closed.
- GitHub issue `#61` (`feature/control-plane-high-risk-proposals-v1`) is now merged to `main`; human-gated proposal-and-approval flows for baseline promotions and repo onboarding are mainline functionality. Issue is closed.
- The remaining open issues that align with real unfinished work are:
  - `#46` (`feature/railway-launch-hardening`) — production Railway deployment readiness
  - `#14` (`feature/attribute-profile-ui-v1`) — attribute profile as a first-class dashboard and review surface
  - `#15` (`feature/governance-signals-v1`) — first-class governance anomaly detection
  - `#59` (`feature/mcp-map-server-v1`) — brokered MCP server exposing curated PromptDrift tools to customer agents
  - `#62` (`feature/dashboard-control-tower-v1`) — refactor dashboard and audit pages into a control-tower model centered on escalations, case files, and human oversight

#### Remaining work analysis

Current repository state suggests the product has moved past dashboard shell redesign, storyline basics, signal fusion, discovery precision, and the core control-plane read and write layers.

What still looks unfinished on `main`:

- reviewer-target quality now has merged proposal-only vs landed-history separation and PR-first review targets, but still needs broader real-repo validation and tuning on history-heavy repositories
- production persistence hardening is now merged, but live Railway/operator validation is still needed before broader launch claims
- attribute-profile and governance-specific product surfaces now have enough foundation to justify dedicated follow-on slices
- the control-plane read model (issue #58), high-risk proposal layer (issue #61), and dashboard control tower (issue #62) are now merged, making the MCP server (issue #59) and launch hardening the most natural next integration layers

What should come next:

1. `feature/railway-launch-hardening` (issue #46)
	- should stay close behind persistence because launch readiness depends on the production data-path actually being proven
2. `feature/mcp-map-server-v1` (issue #59)
	- now directly buildable on top of the merged CP read models and machine-principal auth from issues #58 and #57
3. `feature/attribute-profile-ui-v1` (issue #14)
	- now becomes more leverageable because the dashboard and storyline surfaces already expose rich profile payloads
4. `feature/governance-signals-v1` (issue #15)
	- should follow once reviewer-target quality and attribute-profile presentation are stable enough to support another first-class dimension

#### Completed: `feature/eval-harness-v1`

Purpose:
- turn ad hoc validation into a repeatable evaluation harness for representative real repositories and seeded scenarios

Final status at merge:
- generalized the OSS-only harness into a broader evaluation harness while preserving OSS compatibility aliases
- added seeded scenario registry support with explicit assertions and saved assertion summaries
- added checked-in known-good and known-failing reference packages plus scenario-based comparison support
- updated CLI and docs around the shared `artifacts/eval-runs/` output contract

Exit note:
- the evaluation-harness foundation is now merged; future work should expand scenario depth and real-repository coverage incrementally rather than reopen the core harness contract immediately

#### Completed: `feature/repo-evidence-v1`

Purpose:
- strengthen reviewer-target quality on real repositories where historical drift exists and proposal-only PR audits need clearer separation and synthesis

Final status at merge:
- dashboard read models now distinguish `proposal only`, `proposal + history`, and `history only` evidence labels
- repo and overview queues now prefer the PR as the primary review target when proposal evidence exists, while retaining merged history as supporting context when both are available
- rationale, recommended action, score, and title selection now use proposal evidence when it is the reviewer’s best next stop
- landed posture still remains history-backed, preserving the separation between review guidance and merged drift state
- the repo dashboard no longer shows a misleading `baseline only` chip on mixed-evidence queue rows
- production deployments now always gate `/dashboard` through login, while localhost operator mode still supports seeded no-auth inspection during local development

Exit note:
- this slice is merged and directionally complete for the current feature goal; remaining work should focus on broader evaluation coverage and further review-target tuning rather than reopening the core proposal-vs-history routing logic

#### Completed: `feature/drift-storyline-v1`

Purpose:
- turn the existing artifact history and lightweight timeline groundwork into a first-class case-file storyline for important control surfaces

Planned scope:
- introduce a normalized `DriftEpisode` concept that groups meaningful history points from PR audits, historical backfill, and baseline-promotion milestones
- add a repo case-file `Drift storyline` section for the featured artifact with baseline milestone, major episodes, latest posture, and sparse-history disclosure
- add artifact-level episode inspection APIs so the frontend can expand beyond the compact repo timeline without overloading the initial dashboard payload
- surface small repo-level prioritization cues for repeated drift, stale baselines, mixed-direction changes, and latest severe episodes
- preserve the dashboard split by implementing storyline logic in `services/dashboard_views.py`, JSON APIs, and `static/dashboard-repo.js`

Implementation notes:
- current groundwork already exists in persisted profile history, provenance fields, and the lightweight repo history timeline in `services/dashboard_views.py`
- the first slice should keep timeline displays compact on the main repo page and reserve full episode history for artifact-level expansion or a dedicated API
- performance should be treated as a read-model concern from the start because larger OSS repos can accumulate many profile-history rows quickly

#### Completed: `feature/signal-fusion-v1`

Purpose:
- tighten the relationship between deterministic findings and semantic review output

Planned scope:
- make deterministic and semantic evidence reinforce or dampen each other more explicitly
- improve final reviewer explanations when the two channels disagree
- add regression fixtures for common conflict cases

Final status on this branch:
- fusion now treats deterministic and semantic agreement at `Medium` as a `High`-risk escalation case, while bounding semantic-only elevation unless the semantic review explicitly recommends merge-blocking action
- low-confidence semantic findings are now advisory rather than decisive, and missing structured semantic risk output is treated as low-confidence input instead of accidental corroboration
- reviewer comments now carry fused confidence in the context line and explain notable fusion outcomes such as held-low-confidence disagreements, agreement-based escalation, and merge-blocking semantic escalation
- completed PR audits and artifact-history rows now persist fused confidence alongside fused risk so downstream read models and evaluation surfaces can inspect the final signal-fusion decision durably
- focused helper, worker, comment-rendering, and audit-history regressions now cover the core conflict cases for this feature slice

Exit note:
- this slice is directionally complete for the current feature goal; remaining work should build on the persisted confidence signal in dashboards or evaluation flows rather than reopening the core fusion contract immediately

#### Completed: `feature/discovery-precision-v1`

Purpose:
- reduce onboarding and backfill noise on larger OSS repositories

Planned scope:
- tighten candidate-path discovery and grouping heuristics further
- reduce low-value artifact discovery without missing true AI control surfaces
- improve repo-level summaries so noisy discovery does not pollute reviewer prioritization

#### Completed: `feature/production-persistence-v1`

Purpose:
- harden local-stage persistence and deployment assumptions toward a production-ready posture

Final status at merge:
- production runtime and migration guardrails now fail closed when SQLite is targeted in production
- Railway preflight now rejects invalid production queue configuration before touching the local SQLite queue path
- split-service startup and readiness smoke coverage now exists for API, webhook, and worker paths on the PostgreSQL deployment shape
- local runtime smoke tooling now exercises the real monolith, API, webhook, and worker entrypoints instead of only the monolith app
- adapter-routed persistence seam simulations now cover webhook dedupe, branch-scan job persistence, completed-audit idempotency, persisted audit comment reads, and retry-wait reclamation when the app is configured with a PostgreSQL locator
- separate adapter/unit coverage already exercises `PostgresConnection` translation behavior directly in the persistence test suite
- restart durability is now proven for several production-relevant persistence seams on the PostgreSQL path:
	- completed PR audit state now survives restart strongly enough to suppress a later redelivery through the worker's normal `has_completed_audit(...)` idempotency check
	- webhook delivery dedupe survives split-service restart
	- worker push processing persists a branch-scan job that can be claimed after restart

Exit note:
- this slice is now merged and directionally complete for the code-level persistence contract it targeted
- the remaining work belongs to Railway/live-environment validation and release confidence rather than another obvious persistence-contract gap on `main`

#### 1. `feature/railway-launch-hardening`

Purpose:
- turn the current cloud-oriented scaffolding into an operator-safe Railway deployment shape for launch preparation

Planned scope:
- align the public API service with the real customer control-plane app
- add production startup validation, service-role contracts, and readiness-aware health checks
- introduce Redis-backed queue support for worker/webhook production use
- document Railway service topology, env matrix, launch checklist, and rollback guidance
- explicitly fail closed on unsafe production persistence assumptions until the deeper Postgres migration lands

#### 3. `feature/attribute-profile-ui-v1`

Purpose:
- make the already-shipped attribute-profile data first-class in dashboard and review surfaces instead of leaving it mostly implicit inside detail payloads

Planned scope:
- surface attribute-profile dimensions and shifts more clearly in repo case files and reviewer-facing outputs
- tighten how attribute deltas drive explanations, labels, and prioritization cues
- add explicit regression coverage for the promoted profile surfaces

#### 4. `feature/governance-signals-v1`

Purpose:
- add first-class governance anomaly detection and governance posture surfaces now that baseline, storyline, and fused-priority foundations exist on `main`

Planned scope:
- model governance-specific drift as an explicit reviewer-facing dimension instead of a secondary side effect of other signals
- surface governance-related anomalies in dashboard prioritization and evidence summaries
- keep the first slice additive and explainable rather than expanding into a generic compliance suite

#### 5. `feature/mcp-map-server-v1` (issue #59)

Purpose:
- build a brokered MCP server that exposes curated PromptDrift tools to customer agents without leaking internal credentials or collapsing the product boundary

Planned scope:
- create a dedicated MCP service or module with tool discovery and invocation, workspace binding, internal CP access via machine principals, and agent-shaped output formatting
- bind every MCP session to a single workspace; no cross-workspace roaming
- authenticate to `/cp/*` using short-lived internal JWTs from issue #57 machine-principal infrastructure
- implement initial read-first tool set: `promptdrift.list_repos`, `get_repo_posture`, `get_repo_casefile`, `list_artifacts`, `get_artifact_storyline`, `list_escalations`, `get_audit`
- shape outputs for agent usability (compact structured results, normalized enums, no UI-centric nesting)
- no high-risk write tools, no polished customer onboarding UI in this slice

### Trial success criteria for the next version

The next version should be judged by whether it can:

- surface meaningful AI-control-surface changes in real PRs that reviewers would otherwise miss
- keep visible PR noise low enough that teams do not mute or uninstall it
- trigger real review interventions, edits, or explicit acknowledgements on a meaningful share of surfaced findings
- avoid becoming a generic dashboard-first or governance-suite-first product before the PR wedge is trusted

### Future backlog kept for reference

The sections below replace the older stage-based roadmap so this document stays aligned with the current codebase while still preserving future direction.

#### `audit-feedback-loop-v1`

Purpose:
- learn from reviewer reactions, managed-comment feedback, and eventual PR outcomes without treating those signals as automatic truth

Likely scope:
- passive PR outcome capture tied to audited head SHA
- lightweight feedback collection on managed DriftGuard comments
- derived learning signals for reporting and later evaluation work

Guardrails:
- feedback remains advisory first
- raw user events and derived learning signals stay logically separate
- early use is reporting and evaluation, not automatic retraining

#### `customer-onboarding-baseline-v1`

Purpose:
- extend already-landed onboarding and selective backfill into a more installation-aware customer baseline workflow

Likely scope:
- stronger installation-time artifact discovery summaries
- clearer onboarding confidence and artifact-purpose hints
- optional targeted historical backfill where customer value justifies it

Guardrails:
- do not require full-history replay on every install
- keep onboarding bounded in time and cost
- preserve the distinction between install-time baseline capture and reconstructed history

#### Long-horizon architecture direction

For future reference only:
- keep one relational store in the near term, with SQLite locally and PostgreSQL as the production target
- continue the logical separation between operational queue tables and durable audit/history tables
- scale worker/back-pressure strategy only after reviewer usefulness, repo evidence, discovery precision, and evaluation coverage are stronger