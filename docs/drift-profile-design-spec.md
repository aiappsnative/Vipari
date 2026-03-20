# Drift Profile Design Spec

## Purpose

This document describes the first implemented slice of the GitHub-native drift engine: the static drift-profile layer.

It explains what was built, why it exists, how it fits the PromptDrift product thesis, and how it should evolve into persistence, PR-facing summaries, and longitudinal trend analysis.

Read this alongside [SOUL.md](../SOUL.md), [Plan.MD](../Plan.MD), and [docs/detection-engine-plan.md](detection-engine-plan.md).

---

## Product fit

PromptDrift is now explicitly centered on a GitHub-native static analysis thesis:

- we do not need runtime traffic to deliver useful design governance value
- the control surface is visible in prompts, policies, tool wiring, model config, and governance metadata
- drift is the change in a static attribute profile relative to a baseline

The drift-profile layer is the first code implementation of that thesis.

Before this slice, PromptDrift could already determine whether a PR contained AI-relevant changes and whether those changes looked risky.

After this slice, PromptDrift can start representing an agent or prompt as a stable design profile that can be compared over time.

That is a major conceptual step toward:
- baseline-aware review
- trend analysis
- per-agent history views
- governance-oriented reporting
- reviewer-visible design-drift summaries in pull requests

---

## Scope of the first implementation

The first implementation is intentionally narrow and heuristic-first.

It does **not** yet render profile deltas in GitHub comments.

It **does** introduce:
- static signal extraction from prompt/config text
- a normalized attribute profile
- baseline-to-current comparison
- semantic similarity and distance
- explainable drift narrative output
- durable storage of artifact-level static profiles and baseline-linked deltas inside audit history
- regression tests validating the expected score direction of representative prompt changes

The implementation lives in `engine/drift_profile.py`.

---

## Current data model

### `GovernanceContext`

Represents GitHub-visible governance inputs that do not come from prompt text directly.

Current fields:
- `codeowners_required`
- `approved_reviewers`
- `security_review_present`
- `recent_changes_30d`

This is intentionally small and acts as the bridge between code/text analysis and repository governance analysis.

### `StaticSignals`

Represents raw extracted signals from prompt/config text.

Examples include:
- token count
- section count
- example count
- instruction density
- explicit limit count
- ambiguity count
- guardrail-category counts
- read/write indicators
- production/sandbox indicators
- sensitive tool mentions
- systems touched count
- human review count
- parallelism signals
- maximum step count
- `temperature`
- `top_p`

This structure exists so higher-level scores remain explainable.

### `AgentAttributeProfile`

Represents the static design profile of an agent or prompt.

Current attributes:
- `guardrail_robustness`
- `capability_risk`
- `autonomy_level`
- `stability_vs_creativity`
- `governance_strength`
- `change_frequency`
- `semantic_density`
- `signals`

This is the core design abstraction for future baselines and trend analysis.

### `AgentDriftDelta`

Represents the difference between two profiles.

Current outputs:
- `baseline`
- `current`
- `semantic_similarity`
- `semantic_distance`
- `attribute_deltas`
- `narrative`

This object is designed to become the payload for future PR summaries, stored audit records, and trend queries.

---

## Current scoring model

The current scoring model is heuristic and intentionally interpretable.

### 1. Guardrail robustness

This score increases when PromptDrift finds:
- explicit constraint language
- bounded authority phrases
- policy/safety/escalation/audit rule language
- examples that clarify expected behavior

It decreases when PromptDrift finds:
- ambiguity markers
- looser prose without explicit constraints

Interpretation:
- higher score means the artifact appears more explicitly constrained and governed
- lower score means more vague or permissive design language

### 2. Capability risk

This score increases when PromptDrift finds:
- write-capable action language
- production environment wording
- sensitive tool or system mentions
- broader system reach

It decreases when PromptDrift finds:
- sandbox/test-only wording
- explicit authority limits
- human review gates
- stronger guardrail signal density

Interpretation:
- higher score means the artifact appears to grant broader or more sensitive authority
- lower score means capability seems more bounded or safer in context

### 3. Autonomy level

This score increases when PromptDrift finds:
- more step depth
- planner/parallel/concurrent wording
- more self-directed execution hints

It decreases when PromptDrift finds:
- human approval or escalation markers

Interpretation:
- higher score means the design suggests more independent execution
- lower score means more embedded human control

### 4. Stability vs creativity

This score is derived from generation settings such as `temperature` and `top_p`.

Interpretation:
- higher score means more stable/deterministic posture
- lower score means more creative/open-ended generation posture

### 5. Governance strength

This score increases with:
- CODEOWNERS requirement
- multiple approvals
- explicit security review

It decreases with:
- higher recent churn

Interpretation:
- higher score means changes appear to be happening under stronger review controls
- lower score means weaker governance around the artifact

### 6. Change frequency

This is a simple normalized view of how often the artifact changes.

Interpretation:
- higher score means greater churn and potentially lower baseline stability

### 7. Semantic density

This is a lightweight proxy for how densely the artifact is packed with system/policy/tool/model-control concepts.

Interpretation:
- higher score means a larger proportion of the text appears to carry AI-control semantics

---

## Why heuristic-first is the right design choice

The first version does not need perfect universal scoring.

It needs:
- stable dimensions
- explainable logic
- deterministic behavior
- testability
- baseline-relative usefulness

A heuristic-first layer is the right bridge because it lets PromptDrift move from:
- file-level and line-level change detection

to:
- agent-level and artifact-level design profile comparison

without waiting for a much larger modeling platform.

This gives immediate architectural leverage while keeping the logic reviewable.

---

## How this fits the broader architecture

The static drift-profile layer sits between raw artifact analysis and future read-side history/reporting.

### Before this slice
PromptDrift had:
- webhook ingestion
- AI relevance classification
- deterministic rule analysis
- semantic review packaging
- worker execution
- durable audit persistence

### After this slice
PromptDrift also has:
- a static attribute vocabulary for design drift
- a concrete baseline-comparison payload
- a scoring layer suitable for trend graphs and PR summaries
- persisted artifact-level profile history for later read-side queries

### Architectural role
This layer should become the shared scoring substrate for:
- PR-facing drift summaries
- artifact baseline comparison
- onboarding baseline capture
- trend aggregation
- governance reporting

---

## Current limitations

The current implementation deliberately does **not** yet do the following:

- attach profile deltas to `pull_request_audits`
- select a real stored baseline automatically
- read GitHub review metadata directly from persisted records or live API lookups
- use embeddings for semantic similarity
- support artifact-type-specific scoring models
- generate customer-facing radar/timeline visualizations

Current semantic similarity is lexical and lightweight.
That is acceptable for the first slice because the main goal is architectural progress, not final scoring sophistication.

---

## Expected next evolutions

### 1. Enrich persisted profile snapshots
Extend durable storage with:
- stronger agent-level identifiers in addition to artifact path lineage
- explicit baseline provenance types (previous version vs approved baseline vs onboarding baseline)
- easier read models for timeline and repo-level aggregations

### 2. Feed PR review output
Add a compact PR summary block such as:
- `Guardrail robustness: 0.82 -> 0.74 (-0.08)`
- `Capability risk: 0.40 -> 0.58 (+0.18)`
- `Autonomy: 0.30 -> 0.45 (+0.15)`

### 3. Introduce stored baselines
Support baseline selection from:
- previous artifact version
- onboarding baseline
- latest approved profile

### 4. Add trend read models
Support:
- per-agent timelines
- repo-level aggregate drift
- top-drifting artifacts
- governance regression views

### 5. Improve semantic similarity
Move from lexical overlap to embedding-based similarity when the additional complexity is justified.

### 6. Tune by artifact type
Different artifact classes may eventually want slightly different attribute weighting:
- prompts
- policies
- tool configs
- model routing files
- workflow wiring

---

## Test strategy for this slice

The first regression tests validate directional correctness rather than exact absolute score philosophy.

They prove that representative prompt changes produce the expected movement:
- constrained sandboxed prompt < lower capability risk
- production write-capable prompt < higher capability risk
- stronger review controls < higher governance strength
- weaker guardrails < lower guardrail robustness
- semantically changed prompt < non-zero semantic distance

This is the correct test strategy for an early heuristic engine because it protects behavior without freezing the scoring formulas too rigidly.

---

## Summary

This slice is important because it is not just another rule.

It introduces the central product abstraction PromptDrift needs in order to become a true GitHub-native design drift engine:

**an agent or prompt can now be represented as a static attribute profile and compared against a baseline over time.**

That is the bridge from point-in-time audit comments to durable design intelligence.
