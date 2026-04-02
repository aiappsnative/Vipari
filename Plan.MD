# PromptDrift Development Plan

This document outlines the phased roadmap for building the PromptDrift backend. It is intended as a living guide and will evolve as the project grows.

This is the canonical roadmap document. Product/usage guidance belongs in [README.md](README.md), while deeper architecture belongs in [docs/detection-engine-plan.md](docs/detection-engine-plan.md).

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
- Purpose: active post-merge baseline for the static-first drift engine
- Current status:
	- includes the merged `feature/drift-engine-v1` milestone
	- includes the merged `feature/pr-escalation-v1` slice
	- includes the merged `feature/approved-baseline-v1` slice
	- includes the merged `feature/repo-provenance-v1` slice
	- includes the merged `feature/dashboard-ux-v1` slice
	- is green at the full-suite level (`98 passed` on the latest local verification)
	- now contains the canonical implementation of the dashboard, onboarding, static drift-profile work, escalation labeling, approved-baseline provenance, provenance-linked repo case files, lightweight baseline promotion, landed-history-only dashboard evidence semantics, and persisted PR lifecycle state
- Merge policy: keep future work in smaller milestone branches that extend the merged engine rather than reviving long-lived integration drift on `main`

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
	- PR comment replace-on-update behavior implemented
	- compact TLDR + collapsible reviewer comment format implemented
	- GitHub App auth hardening, opened-PR diff retry, and exact-SHA synchronize diff reconstruction implemented
	- atomic job claiming, failed-job requeue on same-SHA redelivery, and honest persistence-failure handling implemented
	- deterministic rule suppression improved for restrictive negated safety wording such as `Do not reveal ...`
	- live validation completed for benign, risky, synchronize, fallback, dedupe, and bot-auth scenarios
	- merged to `main` after the retry-window regression was fixed and the full suite passed
- Remaining limitations now inherited by `main`:
	- queue is local in-process SQLite for this stage
	- signal fusion still needs improvement
	- deterministic and semantic outputs still need tighter convergence work beyond the latest obvious negation false-positive fix
	- docs and product framing need periodic refresh to match implementation
	- current dashboard is now directionally customer-oriented, but reviewer queues still need tighter synthesis between proposal-only PR audits and landed-history posture
	- onboarding discovery remains too noisy on some real OSS repositories and needs stronger prioritization and grouping
	- dashboard still needs stronger merged-commit linkage and reviewer-target quality on history-heavy OSS repositories

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
21. Overview regression-pattern summaries plus payload-compatibility hardening for stale or sparse dashboard responses
22. Retry-window correctness fix so retryable LLM failures receive at least one retry before deterministic fallback

### Next prioritized slices after the merge
1. Reviewer-queue synthesis between proposal-only PR audits and landed-history evidence on real repos
2. Signal fusion improvements between deterministic and semantic findings
3. Discovery precision improvements for large OSS onboarding and backfill
4. Expanded OSS evaluation harness coverage for seeded and real-repo scenarios
5. Production-oriented persistence and deployment hardening

Suggested next branch sequence:
- `feature/repo-evidence-v1`
- `feature/signal-fusion-v1`
- `feature/discovery-precision-v1`
- `feature/oss-eval-harness-v1`
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

- full automated suite passes locally (`98 passed`)
- dashboard API/read-model regression coverage passed during final hardening
- operator/onboarding/static-profile regression coverage passed during final hardening

### Near-term execution plan

#### Recently completed and now merged to `main`

- `feature/pr-escalation-v1`
- `feature/approved-baseline-v1`
- `feature/repo-provenance-v1`
- `feature/dashboard-ux-v1`

#### 1. `feature/repo-evidence-v1`

Purpose:
- strengthen reviewer-target quality on real repositories where historical drift exists and proposal-only PR audits need clearer separation and synthesis

Planned scope:
- preserve the landed-only rule for dashboard posture while making proposal-only PR audit context easier to interpret in reviewer queues
- improve merged-commit and PR linkage so repo case files route to better review targets
- enrich history-backed insights with stronger source attribution and reviewer-facing context
- keep the read-model focused on actionable evidence rather than new top-level dashboard chrome

#### 2. `feature/signal-fusion-v1`

Purpose:
- tighten the relationship between deterministic findings and semantic review output

Planned scope:
- make deterministic and semantic evidence reinforce or dampen each other more explicitly
- improve final reviewer explanations when the two channels disagree
- add regression fixtures for common conflict cases

#### 3. `feature/discovery-precision-v1`

Purpose:
- reduce onboarding and backfill noise on larger OSS repositories

Planned scope:
- tighten candidate-path discovery and grouping heuristics further
- reduce low-value artifact discovery without missing true AI control surfaces
- improve repo-level summaries so noisy discovery does not pollute reviewer prioritization

#### 4. `feature/oss-eval-harness-v1`

Purpose:
- make future changes safer by evaluating the engine against representative OSS repos and curated scenarios

Planned scope:
- add repeatable seeded-repo runs
- record expected overview and repo-detail outputs for key scenarios
- turn recent manual OSS validation into lightweight automated coverage

#### 5. `feature/production-persistence-v1`

Purpose:
- harden local-stage persistence and deployment assumptions toward a production-ready posture

Planned scope:
- define the next durable-store step beyond the current local SQLite shape
- keep operational queue concerns and durable audit/history concerns logically separated as deployment hardening grows
- document a production-oriented path without overbuilding the local developer workflow

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
- lightweight feedback collection on managed PromptDrift comments
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