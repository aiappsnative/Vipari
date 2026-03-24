# PromptDrift Detection Engine Plan

## Purpose

This document defines the target architecture for the next-generation PromptDrift detection engine. It now serves as the post-merge architecture reference for the implementation living on `main`, and should be used alongside the Mermaid diagram in [docs/detection-engine-diagram.mmd](docs/detection-engine-diagram.mmd).

This document is intentionally architecture-focused. Roadmap sequencing lives in [Plan.MD](../Plan.MD), while product and local-usage guidance lives in [README.md](../README.md).

It should be read together with [SOUL.md](SOUL.md), which captures the stable product thesis: PromptDrift is a GitHub-native design drift engine for AI systems, not a runtime observability product.

The core design principle is a **hybrid engine**:

- deterministic rules provide stability, auditability, and policy alignment
- static design profiling provides durable attribute baselines for GitHub-visible prompts, configs, and agent wiring
- early LLM reasoning provides semantic interpretation and nuance
- context selection determines the right review unit before semantic analysis runs
- relevant audits are executed asynchronously so webhook ingestion stays fast and resilient
- persistence enables history, baselines, and long-term value
- final output is generated from fused evidence, not from a single raw model judgment

---

## Customer value frame

PromptDrift should be understood as an AI change-review system, not merely a webhook bot.

The value to customers is:

- identifying changes that materially alter AI behavior, disclosure rules, or authority
- helping human reviewers understand why a change is risky
- creating durable memory about how prompts and guardrails evolve over time
- reducing the odds that risky AI behavior changes slip through ordinary code review unnoticed

This matters because AI risk often enters through small textual edits that look harmless in a normal diff but have outsized behavioral consequences.

Every engine decision should therefore favor:

- reviewer trust
- evidence-backed explanations
- operational consistency
- durable history and trendability
- precise detection of meaningful drift over noisy file changes

The design should also now favor:

- GitHub-native inputs over runtime dependencies
- baseline-relative drift over absolute claims of universal model safety
- explainable attribute movement over opaque scoring
- escalation-quality reviewer output over generic broad-spectrum AI commentary

---

## Design goals

1. Detect AI-relevant changes more accurately than simple filename keyword matching.
2. Separate raw diff ingestion from risk analysis and explanation generation.
3. Use deterministic rules as the backbone of the engine.
4. Introduce an early LLM semantic review stage for behavior-changing prompt drift.
5. Produce structured findings, explicit evidence, risk level, and confidence.
6. Keep the system testable with fixture-based evaluation.
7. Select semantic review context based on artifact type rather than using raw diff-only or whole-commit review by default.
8. Preserve enough structured history to support trend analysis, baseline comparison, and artifact lineage over time.
9. Keep webhook acknowledgement fast by moving expensive LLM work into a background audit path.
10. Produce comments and records that are useful to both day-to-day reviewers and longer-horizon governance workflows.

---

## High-level architecture

### Current implementation snapshot (March 2026)

The current `main` branch already implements a meaningful subset of this target architecture.

Implemented today:
- webhook-path signature verification, diff fetch, AI relevance gating, and audit job creation
- background worker execution with deterministic analysis, semantic review, retry handling, and fallback behavior
- durable persistence for PR audits, changed artifacts, findings, audit comments, and artifact versions
- a first-pass static drift-profile engine that converts prompt/config text plus governance metadata into a stable attribute profile and drift delta
- durable local persistence of static artifact profiles with baseline links to prior profile history for the same artifact
- first-pass PR comment integration for static drift summaries when artifact snapshots are available
- first-pass read-side trend aggregation for repo summaries and top-drifting artifacts
- unified repo dashboard read models, JSON query APIs, dashboard HTML pages, and local CLI operator workflows
- a triage-first dashboard frontend with portfolio Triage/Coverage modes and repo case-file layouts built on those read models
- dashboard aggregation optimized for larger OSS repositories so per-repo views stay interactive
- bounded OSS onboarding improvements through narrower discovery candidate selection and direct GitHub contents API reads for artifact content
- artifact lineage and baseline-aware suppression for rewritten-but-not-new sensitive terms
- negation-aware suppression for clearly restrictive added safety lines so `Do not reveal ...` is not treated as authority expansion
- managed PR comment replacement behavior so synchronize audits appear at the correct place in the PR timeline
- reviewer-facing comment formatting with TLDR risk summary and collapsible details, without repeating the summary inside the expanded section
- GitHub App auth hardening, transient opened-PR diff retry handling, and exact-SHA synchronize diff reconstruction
- atomic SQLite job claiming, failed same-SHA job revival, and truthful failure states when persistence breaks after comment posting

Still intentionally incomplete:
- a crisp PR escalation workflow with opinionated taxonomy and explicit escalation signals
- one authoritative approved-baseline model shared by PR review, dashboards, and history views
- richer signal fusion between deterministic and semantic channels
- richer PR comment integration for attribute-delta summaries beyond the current compact summary block
- a value-first dashboard insights layer that translates artifact history into customer actions and reviewer priorities
- production-grade persistence/deployment posture beyond the current local-stage setup

### Dashboard evolution note

The current dashboard layer should be understood as an early customer-facing decision surface built on read-model APIs, not yet the final customer product.

It already proves that PromptDrift can:
- onboard repositories,
- expose discovered AI control surfaces,
- persist baseline and history information,
- render a portfolio overview page plus repo-level detail views for inspection.

But that alone is not enough for customer value.

The main frontend cleanup has already been completed.

The current branch now proves a more opinionated product shape:
- overview is a triage-first inbox rather than a flat metrics dashboard,
- coverage has been separated into a secondary mode,
- repo detail behaves like a case file with one featured item, a ranked follow-on queue, lower-confidence progressive disclosure, and collapsed deep history.

Future dashboard work should keep following this split:
- FastAPI route handlers remain thin and focused on routing plus JSON APIs
- page markup moves into `templates/`
- dashboard CSS and JavaScript move into `static/`
- repo-level dashboard rendering consumes the read-model API rather than embedding product logic in `main.py`

The next dashboard iteration should emphasize:
- prioritized review targets,
- guardrail regressions,
- capability expansion,
- control-surface grouping,
- and historical storylines for top artifacts.

The first concrete move in that direction is now implemented: repo detail pages include a lightweight artifact history timeline built from historical profile records plus PR drift samples.

The overview page should now also be treated as the landing risk surface, with an explicit portfolio risk-state summary ahead of the deeper repo queue and coverage views.

It should also surface cross-repo hotspots directly, which now includes a first pass of highest-risk drift and control-surface risk panels.

Repo detail pages should now be understood as the place where PromptDrift explains static design movement explicitly: baseline-vs-current attribute posture, readable risk tags, and provenance-lite context derived from Git history and PR records.

Recent OSS validation against `doria90/hermes-agent` also showed the current architectural boundary clearly: the dashboard is now useful on real backfilled history, but urgency is still strongest when PromptDrift has PR-linked evidence in addition to historical hotspots.

In other words, the dashboard is now a real first decision surface and should keep evolving in that direction rather than reverting to a raw metrics surface.

The next architectural improvements for this layer are:
- stronger provenance on repo detail pages
- denser cross-repo examples and evaluation coverage
- better signal fusion so the dashboard reflects more trustworthy reviewer priorities
- richer merged-commit and PR linkage so real OSS repos produce more than history-only urgency

### Execution model

### Product decision model

The primary product decision PromptDrift should improve is not raw allow/deny.

It is:
- whether an AI-related PR can remain in the normal review lane,
- or whether it must be escalated before merge to AI platform, security, or product owners.

The architecture should therefore optimize for:
- high-precision identification of meaningful AI control-surface changes
- low visible noise in PRs
- clearer provenance and baseline context for why a change deserves escalation

PromptDrift should separate **event ingestion** from **audit execution**.

The webhook endpoint should do only the minimum amount of work required to decide whether a PR deserves audit processing:
- verify signature and event shape
- fetch the PR diff
- retry briefly when GitHub returns a transient opened-PR `404` immediately after PR creation
- reconstruct synchronize-event diffs from the exact `base.sha` and `head.sha` commit pair so freshly changed PRs do not rely on stale PR snapshots
- run a fast AI relevance gate
- persist an audit job for relevant changes
- return success quickly to GitHub

The expensive path should run in a background worker:
- deterministic analysis
- semantic context selection
- LLM review
- retry and backoff handling
- deterministic fallback generation if the LLM remains unavailable
- create a fresh managed PR comment and delete the previous managed one after successful posting
- durable audit persistence
- mark the job failed if durable persistence cannot be completed after comment publication

This is the right fit for PromptDrift because the model call is variable-latency, subject to rate limits, and not required for webhook acknowledgement.

### Why queue relevant audits by default

The system should not keep two separate execution models where healthy requests are synchronous but failures become asynchronous.

That split would make the system harder to reason about and operate.

The cleaner rule is:
- irrelevant changes exit inline
- relevant changes become audit jobs
- workers perform the full audit lifecycle

This keeps the online path thin while allowing controlled concurrency, retries, back-pressure, and future durability.

### Lean-first persistence principle

PromptDrift should remain lean in implementation, but storage must be accounted for in the design now.

This means:
- do **not** overbuild a large analytics platform yet
- do design a stable audit record and artifact history model early
- do leave clear extension points for history, reporting, and longitudinal analysis

Storage is therefore a **planned architectural capability**, even if its first implementation is intentionally minimal.

### Persistence architecture principle

PromptDrift should avoid premature database sprawl.

The recommended architecture is:
- one relational database for the near-to-mid term
- SQLite for local and early development
- PostgreSQL as the intended production-grade durable store
- logical separation between operational queue data and durable audit/history data
- future decomposition only when workload or tenant isolation actually justifies it

This means PromptDrift should **design for separation without deploying multiple databases yet**.

---

### 1. Diff ingestion

The engine starts by turning a GitHub pull request diff into structured internal objects.

This stage executes inside the background audit worker after a relevant job has already been accepted.

Expected responsibilities:
- split diff by file
- identify added, removed, and modified hunks
- preserve exact evidence spans
- normalize changed text for later analysis

Expected outputs:
- `ChangedFile`
- `DiffHunk`
- `EvidenceSpan`

---

### 2. AI relevance filter

This stage determines whether a changed file is relevant to AI behavior.

Signals may include:
- file path patterns (`prompt`, `system`, `policy`, `assistant`, `llm`, `rag`, `model`)
- content patterns (`system prompt`, `tools`, `temperature`, `policy`, `refuse`)
- known config formats
- code touchpoints that affect model selection or tool access

Expected outputs:
- `ai_relevant: bool`
- `relevance_reason`
- `candidate_change_type`

This stage should remain conservative: false positives are acceptable early, false negatives are more dangerous.

### Execution note
This is the main analysis stage that should stay on the webhook path because it is cheap and useful for queue suppression.

---

### 3. Structured change extraction

This stage converts relevant hunks into normalized change descriptors.

Examples:
- system instruction added
- refusal text removed
- model changed from one identifier to another
- tool access broadened
- sensitive data mention introduced

Expected outputs:
- `StructuredChange`
- `ChangeCategory`
- `evidence_spans`

This stage is shared by both deterministic and LLM-driven analysis.

---

### 4. Static design profile extraction

This stage turns GitHub-visible prompt/config content into a stable attribute profile that can be compared over time.

It exists because PromptDrift's product direction is explicitly static-first and GitHub-native: customers want to understand how agent design changes, even when PromptDrift never sees runtime traffic.

### Responsibilities
- extract durable static signals from prompt/config text and related metadata
- summarize those signals into a small attribute vector
- make the vector explainable enough for PR review and longitudinal reporting
- support future baseline comparison and trend analysis

### Example profile dimensions
- `guardrail_robustness`
- `capability_risk`
- `autonomy_level`
- `stability_vs_creativity`
- `governance_strength`
- `change_frequency`
- `semantic_density`

### Example signals
- count of explicit constraints such as `must`, `never`, `do not`, `always`
- bounded authority phrases such as `up to`, `above`, `max`, `limit`
- examples/few-shot density
- write vs read capability language
- production vs sandbox indicators
- sensitive system and privileged tool mentions
- human approval markers and execution-step depth
- model parameters such as `temperature` and `top_p`
- governance metadata such as CODEOWNERS requirements, security review presence, and recent churn

### First implemented slice
The first implementation of this stage now exists in `engine/drift_profile.py`.

That module currently:
- extracts static signals from text
- builds an `AgentAttributeProfile`
- compares baseline and current versions
- returns attribute deltas, lexical similarity, semantic distance, and short narrative summaries

The first persistence integration for this stage now exists in `services/audit_records.py`.

That integration currently:
- stores one static profile record per changed artifact snapshot when artifact text is available
- links the current profile to the prior profile for the same normalized artifact id
- stores attribute deltas and narrative output for later history and trend queries

### Design note
This stage is intentionally heuristic-first.

That is acceptable because:
- its scores are baseline-relative rather than universal truth claims
- its logic is explainable and testable
- it provides a concrete bridge between raw artifact changes and future trend/history features

---

### 5. Static baseline and drift comparison

This stage compares a current static profile against a chosen baseline profile.

It answers questions such as:
- did guardrails weaken relative to the approved baseline?
- did capability risk increase because a new production write path was introduced?
- did autonomy increase because approval checks were removed?
- did governance weaken because review controls dropped while churn increased?

### Responsibilities
- choose the relevant baseline (previous version, approved baseline, or onboarding baseline)
- compute attribute deltas
- attach an explainable narrative to the deltas
- persist enough data for future trend and history views

### Current implementation note
The first durable baseline linkage now compares the current artifact snapshot against the latest persisted profile for the same repo/path pair.

That is not yet the final baseline-selection strategy, but it is the first working durable form of baseline-aware profile history.

The first reviewer-facing presentation of this data now appears as a compact static drift block inserted into PR comments ahead of the detailed semantic review section.

The first read-side trend layer now exists as repo summary and artifact leaderboard queries over persisted static profile history.

### Expected outputs
- `AgentDriftDelta`
- `semantic_similarity`
- `semantic_distance`
- attribute-specific deltas
- reviewer-facing explanations

---

## History and baseline retrieval

This stage retrieves prior audit and artifact context when available.

It exists because long-term product value depends on being able to answer questions such as:
- how has this prompt changed over time?
- is risk increasing or decreasing?
- is this the first risky change or part of a pattern?
- what was the previous known baseline for this artifact?

### Responsibilities
- find previous versions of the same artifact
- retrieve recent related audit results
- provide baseline or lineage context to downstream stages
- support future trend and regression analysis

### Design note
This stage should be optional in early implementation.

If no history exists, the engine should still function normally.
If history exists, it should enrich context selection, semantic analysis, and risk interpretation.

It should also enrich static drift comparison by providing the right baseline profile for the current artifact or agent.

### Expected outputs
- `previous_artifact_version`
- `artifact_lineage`
- `recent_audit_context`
- `baseline_reference`
- `baseline_attribute_profile`

---

## Audit job orchestration

This stage governs how relevant audits move from webhook ingestion into durable background execution.

### Responsibilities
- create one audit job per relevant PR event
- deduplicate or coalesce repeated events when possible
- atomically claim one queued job at a time so concurrent workers cannot double-process the same row
- track job state transitions
- limit worker concurrency to protect the LLM quota
- persist attempt counts and last error state
- persist job creation time so retry age and maximum wait windows can be enforced
- revive a previously failed same-SHA job on webhook redelivery instead of permanently suppressing that audit opportunity

### Recommended initial states
- `queued`
- `processing`
- `retry_wait`
- `completed`
- `fallback_posted`
- `failed`

### Why this matters
The issue seen in live testing was a `429 RateLimitReached` failure, which indicates quota pressure or request bursts rather than a fundamentally oversized diff.

That means PromptDrift should solve the operational problem with queueing and retry discipline, not only by shrinking prompts.

### Retry policy guidance
Retryable failures should be treated differently from permanent failures.

#### Retryable failures
- `429` rate limit responses
- request timeouts
- temporary upstream connectivity failures
- transient `5xx` class provider failures

These should remain in the queue and be retried over a longer wall-clock window.

The worker should prefer provider retry hints such as `retry-after` or `retry-after-ms` when available.

If no provider hint is present, PromptDrift should apply a bounded escalating retry schedule.

#### Non-retryable failures
- invalid model identifiers
- malformed requests
- permanent authentication or configuration problems

These should move to deterministic fallback quickly instead of waiting in the queue unnecessarily.

### Lean implementation guidance
The first version can use a lightweight local store such as SQLite plus a simple worker loop.

That is sufficient to prove the architecture before introducing heavier infrastructure.

### Operational storage boundary
`AuditJob` is an operational table.

Its purpose is to support:
- queueing
- retry scheduling
- failure recovery
- final execution state

It should not become the long-term customer history model.

### Current hardening notes

The active branch has already validated several reliability behaviors that materially affect customer trust:

- opened PR events stay on the ordinary PR diff endpoint, while synchronize events alone use exact commit-pair reconstruction
- transient GitHub diff `404`s are retried for both raw HTTP failures and GitHub client exception shapes
- SQLite job claims use one atomic `UPDATE ... RETURNING` path instead of a race-prone select-then-update sequence
- persistence failures are treated as terminal execution failures, not silent success
- deterministic fallback has been live-validated with an intentionally invalid model configuration and correctly records `fallback_posted`

---

## Semantic context selection

This stage decides what the LLM should actually review.

This is a first-class architectural decision because semantic review quality depends heavily on context size and context type.

### Core rule
The LLM should review the **smallest complete semantic unit** required to understand the behavior change.

The engine should not default to:
- raw diff only
- entire commit only

Instead, it should choose context by artifact type.

### Supported context modes

#### 1. `diff_only`
Use when:
- the meaning is highly localized
- the change is simple and low ambiguity
- the changed hunk itself carries the full semantic signal

Typical examples:
- minor config toggles
- clearly scoped instruction edits

#### 2. `section_context`
Use when:
- the enclosing block, object, function, or section matters
- the change must be interpreted in local surrounding context

Typical examples:
- JSON/YAML config changes inside a larger object
- code changes inside an AI-related function
- modified prompt subsection within a longer template

#### 3. `full_artifact_compare`
Use when:
- the artifact is prompt-like or policy-like
- instruction ordering and interaction matter
- unchanged text is required to understand the changed behavior

Typical examples:
- system prompt files
- policy documents
- long instruction templates
- assistant behavior definitions

### Recommended default policy
- prompt / system / policy artifacts → `full_artifact_compare`
- config changes → `section_context`
- code behavior changes → `section_context`
- simple isolated changes → `diff_only`

### Expected outputs
- `SemanticContextMode`
- `context_selection_reason`
- `review_package`

---

## Deterministic analysis layer

This is the control backbone of the engine.

### Responsibilities
- classify changed files
- trigger explicit risk rules
- assign weighted findings
- preserve evidence for every finding

### Initial rule families

#### A. Guardrail drift
- refusal language removed
- safety restrictions weakened
- instruction hierarchy changed in favor of compliance over safety

#### B. Sensitive data drift
- access to customer, financial, health, or internal data introduced
- broader permission wording added
- privacy boundary language weakened

#### C. Capability drift
- tool usage added or expanded
- autonomy or execution capability increased
- retrieval scope or external access broadened

#### D. Model drift
- model identifier changed
- provider changed
- generation settings changed in a way that may affect safety or consistency

#### E. Prompt scope drift
- assistant role changed
- target domain expanded
- output expectations shifted in a materially risky way

### Expected outputs
- `RuleFinding`
- `severity_weight`
- `rule_id`
- `evidence_spans`
- `baseline_score`

---

## Early LLM semantic review layer

This stage exists to identify meaningful semantic drift that deterministic rules may miss.

It operates on a review package assembled by the semantic context selection stage.

### Why it exists
Deterministic rules struggle with:
- paraphrased guardrail weakening
- subtle expansion of authority
- intent changes without obvious keywords
- instruction hierarchy changes that are semantic rather than lexical

### Constraints
The LLM is **not** the final authority at this stage.
It should return structured semantic observations, not an unconstrained final risk verdict.

It should also avoid reviewing oversized irrelevant context. The quality of this stage depends on context discipline.

### Target questions for the LLM
- Did the prompt meaning materially change?
- Did the assistant’s authority or scope expand?
- Were safety boundaries weakened?
- Was access to sensitive data or internal logic broadened?
- Is the change likely behavior-changing or merely editorial?

### Expected outputs
- `SemanticFinding`
- `semantic_change_detected`
- `semantic_category`
- `confidence`
- `reasoning`
- `evidence_summary`

### Guardrails on this stage
- operate on selected semantic review packages, not the entire PR blindly
- use tightly scoped prompts
- prefer categorical output schemas
- treat low-confidence LLM findings as advisory, not decisive

### Reliability requirements
- set an explicit request timeout
- retry on transient failures and `429` with bounded exponential backoff
- prefer provider retry hints over static local delays when the model provider supplies them
- keep retryable jobs alive for a longer wall-clock retry window before falling back
- record token or quota-related failures for later tuning
- hand control to deterministic fallback output if LLM attempts are exhausted

### Important architectural note
For prompt-like artifacts, semantic review should usually compare the **full old artifact** and the **full new artifact**, with the diff included as navigation aid.

For config or code changes, semantic review should usually operate on the smallest meaningful enclosing section rather than the entire file or entire commit.

---

## Signal fusion layer

This stage combines deterministic and semantic findings into one decision set.

### Responsibilities
- merge evidence from both channels
- reconcile conflicts
- raise or lower risk based on confidence and consistency
- attach confidence to the fused decision

### Example fusion rules
- a high-severity deterministic finding can force at least `Medium`
- strong deterministic and semantic agreement can elevate to `High`
- low-confidence LLM findings cannot override strong deterministic safety evidence
- semantic findings can elevate risk when deterministic evidence is weak but behavior change appears material
- repeated similar findings across recent history can increase confidence
- recent improvements or repeated reversions can be surfaced as trend context without dominating core risk scoring

### Expected outputs
- `FusedFindingSet`
- `final_score`
- `risk_level`
- `confidence`

---

## Explanation synthesis layer

This stage prepares the reviewer-facing output.

### Responsibilities
- summarize what changed
- explain why it matters
- cite the main triggered risks
- recommend reviewer action

### Output principles
- concise
- evidence-based
- suitable for PR comments
- not overly verbose

### Expected outputs
- Markdown comment body
- short summary
- risk rationale
- recommended follow-up

---

## Deterministic fallback output

PromptDrift should still post useful reviewer output when the LLM path fails after bounded retries.

### Purpose
- prevent silent audit drops
- preserve reviewer trust
- make deterministic findings operationally useful on their own

### Trigger conditions
- repeated `429` or quota exhaustion
- timeout exhaustion
- temporary upstream availability failures
- retry window expiration for transient failures
- non-retryable semantic review failures

### Output expectations
- present the result as a preliminary audit rather than an internal failure notice
- summarize deterministic findings and evidence
- preserve risk floor from deterministic analysis
- avoid exposing raw provider error details in the customer-facing comment

### Internal versus external reporting
The PR comment should stay reviewer-friendly.

That means:
- raw provider errors belong in persisted job metadata and logs
- customer-facing comments should describe the result, not the infrastructure failure
- the fallback comment can mention that further semantic review may refine the assessment later

---

## Persistence layer

Persistence should be treated as a separate architectural concern, not embedded directly in the webhook endpoint.

### Current implementation status

At the current branch stage, PromptDrift now persists both operational queue state and a durable audit/history layer.

That means the database currently stores enough to support:
- async execution
- retries and retry windows
- durable PR audit records
- changed artifact records
- explainable findings
- audit comment tracking including GitHub comment id
- artifact version lineage
- internal error tracking and completion mode inspection

This is no longer only a queue store. It is now the beginning of a customer-memory layer for AI change review.

### Storage strategy going forward

PromptDrift should distinguish between two kinds of persisted data:

#### 1. Operational storage
Used for:
- queue execution
- retries
- transient failures
- in-flight work state

Current example:
- `AuditJob`

#### 2. Durable product storage
Used for:
- customer-visible audit history
- explainable findings
- artifact evolution
- trend and governance reporting

Current examples:
- `PullRequestAudit`
- `ChangedArtifact`
- `Finding`
- `AuditComment`
- `ArtifactVersion`

This separation should exist even if both logical groups live in the same physical database at first.

### Why it matters
PromptDrift becomes significantly more valuable when it can show:
- artifact history
- risk trend over time
- recurring risk patterns
- whether teams are improving after policy changes

### Customer value model

The next phase of persistence should be reverse-engineered from the customer value PromptDrift is expected to provide.

#### 1. PR-level review value
Customers should be able to answer:
- what changed in this PR?
- why was it risky?
- what evidence drove the decision?
- was the result deterministic-only or semantically reviewed?

This means the database must store:
- one durable audit record per evaluated PR head
- final score, risk level, confidence, and comment text
- deterministic findings and semantic findings in structured form
- whether the result came from a full semantic pass or a preliminary fallback path

#### 2. Artifact history value
Customers should be able to answer:
- how has this prompt or policy evolved over time?
- when did risk first begin increasing?
- which exact artifact versions triggered concern?

This means the database must store:
- normalized artifact identity
- artifact version records
- linkage from each audit to the artifact versions it evaluated
- enough structure to compare current artifact versions against previous known baselines

#### 3. Trend and governance value
Customers should be able to answer:
- which repos or teams generate repeated high-risk changes?
- are policy changes reducing risk over time?
- what rule families fire most often?

This means the database must store:
- auditable findings over time
- stable rule identifiers and semantic categories
- repository and installation scoping metadata
- timestamps and indexes that support time-based reporting without scanning raw payload blobs

#### 4. Operational trust value
Customers and operators should be able to answer:
- was the audit fully completed or did it fall back?
- did the system retry due to provider pressure?
- was a comment already posted for this PR head?

This means the database must store:
- job lifecycle state
- retry history or at minimum retry counters and last error class
- comment posting records or a durable audit output record
- enough information to implement and preserve comment dedupe or comment update behavior

### What should be persisted

At minimum:

#### Audit run metadata
- installation or tenant identifier
- repository identifier
- pull request number
- commit SHA or head SHA
- timestamp
- job status
- attempt count
- last error category
- completion mode (`completed`, `fallback_posted`, `failed`)

#### Changed artifact metadata
- artifact path
- artifact type
- context mode used
- artifact hash or version identifier
- changed hunk count
- added and removed line counts

#### Findings
- deterministic findings
- semantic findings
- fused score
- risk level
- confidence
- finding source (`deterministic`, `semantic`, `fused`)
- stable category / rule identifiers

#### Reviewer output
- final comment body
- summary
- rationale
- output mode (`full_review`, `preliminary_fallback`)

#### Optional raw payload retention
- raw diff snapshot only where operationally justified
- large prompt or policy snapshots only when needed for lineage or baseline comparison
- evidence excerpts preferred over full raw content in hot tables

---

## Reverse-engineered next schema

The current durable schema should stay compact while evolving from stored history into usable reporting and governance views.

### Physical storage recommendation

#### Near-term
- one database
- SQLite for local/dev
- compact normalized tables

#### Production target
- PostgreSQL as the primary durable store
- strong indexing on repository, PR, artifact, and time dimensions
- future ability to add partitions, replicas, or archives without changing the core logical model

#### What not to do yet
- do not introduce multiple primary databases now
- do not introduce a separate analytics warehouse now
- do not choose a document database as the primary source of truth for audit history

### Keep the existing operational table
- `AuditJob`

This remains the execution and retry mechanism.

### Current durable audit record layer

#### `PullRequestAudit`
Purpose:
- one durable record for one evaluated PR head SHA

Currently stores or is designed to store:
- installation identifier
- repository identifier
- PR number
- head SHA
- audit status
- completion mode (`completed`, `fallback_posted`, `failed`)
- fused score
- risk level
- confidence
- summary
- rationale
- posted comment body
- timestamps

#### `ChangedArtifact`
Purpose:
- identify which AI-relevant artifacts were part of the audit

Should store:
- foreign key to `PullRequestAudit`
- artifact path
- artifact type
- context mode
- changed hunk counts
- added and removed counts

#### `ArtifactVersion`
Purpose:
- track artifact lineage over time

Should store:
- normalized artifact identifier
- repository identifier
- artifact path
- version hash
- prior version reference
- optional normalized content snapshot or retrievable pointer
- timestamps

### Hot versus cold data policy

#### Hot queryable data
Keep in primary durable tables:
- audit metadata
- findings
- scores, risk levels, confidence
- artifact identity and version metadata
- comment metadata and body

#### Cold or archival data
Plan to move or minimize over time:
- large raw diffs
- large prompt or policy snapshots
- verbose retry traces
- oversized semantic payloads

The goal is to keep customer-facing and reporting queries fast without losing forensic flexibility.

#### `Finding`
Purpose:
- preserve explainable findings as first-class records

Should store:
- foreign key to `PullRequestAudit`
- optional foreign key to `ChangedArtifact`
- source (`deterministic`, `semantic`, `fused`)
- rule id or semantic category
- severity
- title
- rationale
- confidence if available
- evidence summary

#### `AuditComment`
Purpose:
- track what was posted back to GitHub and later enable dedupe/update behavior

Should store:
- foreign key to `PullRequestAudit`
- GitHub comment id
- comment body
- comment mode
- created / updated timestamps

---

## Performance and scale planning

PromptDrift should plan for growth before the database becomes sluggish.

### Likely causes of future sluggishness
- storing large raw text blobs in primary query tables
- missing indexes on repository, PR, artifact, or time dimensions
- mixing queue polling with heavy historical reporting workloads without separation
- retaining all raw payloads in hot storage forever

### Design responses
- keep operational queue tables separate from durable audit tables
- keep hot tables structured and compact
- use evidence excerpts and hashes where full raw content is not required
- introduce archival or cold-storage policies later for large payloads

### Suggested future decomposition triggers
Consider stronger separation only when one or more of these become true:
- queue throughput materially impacts historical query latency
- reporting queries become too expensive for the primary store
- tenant isolation requirements become stricter
- raw snapshot volume grows disproportionately relative to structured audit data

Before those triggers, one well-structured relational database is the right tradeoff.

---

## Recommended next persistence phase

The foundational persistence phases have now been completed in first form.

### Phase 5A — Durable audit record
Status: implemented

Delivered:
- `PullRequestAudit`
- `ChangedArtifact`
- `Finding`
- `AuditComment`

Delivered customer value:
- reviewable audit history per PR
- explainable stored findings beyond posted comment text
- durable tracking of posted GitHub comments

### Phase 5B — Artifact lineage
Status: implemented

Delivered:
- `ArtifactVersion`
- normalized artifact identity by repo and path
- previous-version linkage

Delivered customer value:
- prompt and policy history
- baseline comparison support
- trend-ready lineage foundation

### Phase 5C — Read-side history services
Status: partially implemented

Delivered so far:
- repository audit history queries
- artifact history queries
- finding history queries for repo/artifact drill-down

Still needed:
- stronger trend aggregation views
- customer-facing reporting surfaces
- clearer governance-oriented summaries across repos and teams

### Recommended implementation order from here
1. Keep `AuditJob` as the operational queue table
2. Improve read-side reporting over `PullRequestAudit`, `Finding`, `AuditComment`, and `ArtifactVersion`
3. Strengthen deterministic/semantic signal fusion and scoring calibration
4. Add dashboard/operator views once the read model stabilizes
5. Continue tightening the path from local SQLite architecture toward production-grade persistence

### Lean implementation guidance
The first implementation should store only what is necessary to support:
- future baseline retrieval
- audit history listing
- simple trend reporting

Do not start with a full analytics warehouse or overly complex dashboard schema.

---

## Artifact lineage and version tracking

Prompt-like artifacts should be tracked over time as evolving objects, not only as isolated diffs.

### Why this matters
Prompt drift is often meaningful only when viewed across versions.

Examples:
- multiple small guardrail weakenings across several PRs
- repeated model swaps
- gradual expansion of sensitive-data access language

### Recommended initial strategy
- store a normalized artifact identifier
- store a version hash per audit
- link each new version to the most recent prior known version

This is enough to support future baseline comparisons without overengineering the first storage model.

---

## Trend and reporting support

Long-term product value comes from more than single-PR comments.

The architecture should be able to support future views such as:
- risk over time by repository
- drift history for a prompt or policy artifact
- repeated rule triggers across time
- distribution of high-risk findings by team or repo

This should be designed as a read layer over persisted audit records rather than as part of the online webhook path.

---

## Recommended initial data model direction

The first persistent schema should stay compact.

Suggested core entities:

- `Installation`
- `Repository`
- `AuditJob`
- `PullRequestAudit`
- `ChangedArtifact`
- `ArtifactVersion`
- `Finding`
- `AuditComment`

### Recommended indexing direction
- `PullRequestAudit(repository_id, pr_number, head_sha)`
- `PullRequestAudit(created_at)`
- `ChangedArtifact(repository_id, artifact_path)`
- `ArtifactVersion(normalized_artifact_id, created_at)`
- `Finding(rule_id)`
- `Finding(source, severity)`

This is sufficient to support history, baseline retrieval, and future trend views without prematurely locking the system into a large platform design.

---

## Evaluation and maturity path

### Initial evaluation strategy
Build a fixture set of representative diffs:
- benign prompt wording updates
- model changes with no safety impact
- removed guardrails
- new sensitive data access
- broadened tool use
- false-positive traps

### What to measure
- precision
- recall
- false-positive rate
- false-negative rate
- consistency across similar diffs

### Mature engine characteristics
A mature PromptDrift engine should provide:
- low false-positive rate
- explainable risk findings
- stable scoring
- tenant-tunable policies
- historical drift comparisons over time

---

## Proposed implementation phases

### Phase 1 — Engine extraction
- move drift logic out of `main.py`
- create core engine models and interfaces
- keep existing webhook flow intact

### Phase 2 — Deterministic foundation
- implement structured diff parsing
- implement initial rule families
- return structured findings and score

### Phase 3 — Audit job orchestration
- add a minimal durable audit job model
- enqueue relevant audits from the webhook path
- run a worker loop with bounded concurrency
- add retry, backoff, and deterministic fallback behavior

### Phase 4 — Early semantic review
- add narrowly scoped LLM semantic pass
- define response schema
- integrate semantic findings into fusion layer

### Phase 5 — Minimal persistence and history
- define canonical audit record shape
- persist audit runs and artifact versions
- support basic history and baseline retrieval

### Phase 6 — Reviewer output
- generate improved Markdown comments from fused findings
- add confidence and recommended action

### Phase 7 — Evaluation harness
- add fixture-based tests
- create baseline cases for benign, medium, and high-risk drift

---

## Recommended module shape

Potential near-term module structure:

- `engine/models.py`
- `engine/diff_parser.py`
- `engine/relevance.py`
- `engine/context_selector.py`
- `engine/rules.py`
- `engine/semantic_review.py`
- `engine/fusion.py`
- `engine/reporting.py`
- `services/audit_jobs.py`
- `services/audit_worker.py`
- `storage/models.py`
- `storage/repository.py`
- `services/history.py`

This keeps the webhook layer thin and makes the engine easier to test independently.

---

## Summary

PromptDrift should evolve into a **rule-guided semantic drift engine**.

The deterministic layer should provide control and policy grounding.
The early LLM layer should contribute semantic interpretation.
The final comment should be based on fused evidence, not on raw model intuition alone.
Relevant audit work should run asynchronously so model latency and rate limiting do not break webhook reliability.
The persistence layer should make those decisions useful over time through history, baselines, and trend analysis.
