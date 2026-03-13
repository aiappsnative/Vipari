# PromptDrift Detection Engine Plan

## Purpose

This document defines the target architecture for the next-generation PromptDrift detection engine. It is intended to guide implementation on the `feature/drift-engine-v1` branch and to be used alongside the Mermaid diagram in [docs/detection-engine-diagram.mmd](docs/detection-engine-diagram.mmd).

The core design principle is a **hybrid engine**:

- deterministic rules provide stability, auditability, and policy alignment
- early LLM reasoning provides semantic interpretation and nuance
- context selection determines the right review unit before semantic analysis runs
- relevant audits are executed asynchronously so webhook ingestion stays fast and resilient
- persistence enables history, baselines, and long-term value
- final output is generated from fused evidence, not from a single raw model judgment

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

---

## High-level architecture

### Execution model

PromptDrift should separate **event ingestion** from **audit execution**.

The webhook endpoint should do only the minimum amount of work required to decide whether a PR deserves audit processing:
- verify signature and event shape
- fetch the PR diff
- run a fast AI relevance gate
- persist an audit job for relevant changes
- return success quickly to GitHub

The expensive path should run in a background worker:
- deterministic analysis
- semantic context selection
- LLM review
- retry and backoff handling
- deterministic fallback generation if the LLM remains unavailable
- PR comment posting

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

### Expected outputs
- `previous_artifact_version`
- `artifact_lineage`
- `recent_audit_context`
- `baseline_reference`

---

## Audit job orchestration

This stage governs how relevant audits move from webhook ingestion into durable background execution.

### Responsibilities
- create one audit job per relevant PR event
- deduplicate or coalesce repeated events when possible
- track job state transitions
- limit worker concurrency to protect the LLM quota
- persist attempt counts and last error state

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

### Lean implementation guidance
The first version can use a lightweight local store such as SQLite plus a simple worker loop.

That is sufficient to prove the architecture before introducing heavier infrastructure.

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

### Output expectations
- clearly state that semantic review was unavailable
- summarize deterministic findings and evidence
- preserve risk floor from deterministic analysis
- optionally mark the comment as a fallback audit result

---

## Persistence layer

Persistence should be treated as a separate architectural concern, not embedded directly in the webhook endpoint.

### Why it matters
PromptDrift becomes significantly more valuable when it can show:
- artifact history
- risk trend over time
- recurring risk patterns
- whether teams are improving after policy changes

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

#### Changed artifact metadata
- artifact path
- artifact type
- context mode used
- artifact hash or version identifier

#### Findings
- deterministic findings
- semantic findings
- fused score
- risk level
- confidence

#### Reviewer output
- final comment body
- summary
- rationale

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
