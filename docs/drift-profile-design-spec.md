# Drift Profile Design Spec

## Purpose

This document describes the first implemented slice of the GitHub-native drift engine: the static drift-profile layer.

It explains what was built, why it exists, how it fits the PromptDrift product thesis, and how it should evolve into stronger repo evidence, better signal fusion, and longitudinal trend analysis.

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

The current product direction is now explicit:
the preferred baseline is the **latest explicitly approved version** of a control surface, with onboarding or historical fallbacks used only when no approved baseline exists yet.

---

## Scope of the first implementation

The first implementation is intentionally narrow and heuristic-first.

It now renders a compact first-pass static drift summary in GitHub comments when artifact snapshots are available.

It **does** introduce:
- static signal extraction from prompt/config text
- a normalized attribute profile
- baseline-to-current comparison
- semantic similarity and distance
- explainable drift narrative output
- durable storage of artifact-level static profiles and baseline-linked deltas inside audit history
- regression tests validating the expected score direction of representative prompt changes

The profile computation core lives in `engine/drift_profile.py`, while the persisted history, read-side aggregation, and dashboard consumption now extend into the audit, onboarding, and dashboard service layers.

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

This object now informs PR summaries, stored audit records, and trend queries.

Baseline provenance is now part of the broader product contract around these comparisons:
- approved baseline when one exists
- onboarding fallback when no approved baseline exists yet
- historical fallback when that is the best available reference

The next product improvement is not to invent provenance from scratch, but to make repo evidence and reviewer-target context denser and easier to trust.

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
- dashboard surfaces that render baseline-vs-current design posture and drift history for prioritized artifacts

### Architectural role
This layer is now the shared scoring substrate for:
- PR-facing drift summaries
- artifact baseline comparison
- onboarding baseline capture
- trend aggregation
- governance reporting

The first onboarding integration is now in place through persisted onboarding baseline versions and planned selective historical backfill jobs.

---

## Current limitations

The current implementation deliberately still has important limits:

- semantic similarity remains lexical and lightweight rather than embedding-based
- scoring is still mostly artifact-agnostic and heuristic-first
- landed posture intentionally depends on approved baselines plus merged-history evidence, while proposal-only PR audits still need clearer reviewer-facing synthesis
- profile outputs are explainable, but they are not yet fused as tightly as they should be with deterministic and semantic review channels
- customer-facing visualization remains intentionally lightweight and reviewer-first rather than exhaustive

Current semantic similarity is lexical and lightweight.
That is acceptable for the first slice because the main goal is architectural progress, not final scoring sophistication.

---

## Expected next evolutions

### 1. Enrich persisted profile snapshots
Extend durable storage with:
- stronger agent-level identifiers in addition to artifact path lineage
- cleaner baseline provenance read models across approved, onboarding, and historical references
- easier read models for timeline and repo-level aggregations

### 2. Feed PR review output
Expand the current compact PR summary block such as:
- `Guardrail robustness: 0.82 -> 0.74 (-0.08)`
- `Capability risk: 0.40 -> 0.58 (+0.18)`
- `Autonomy: 0.30 -> 0.45 (+0.15)`

### 3. Strengthen repo evidence and reviewer targeting
Use drift-profile outputs more directly to support:
- better repo-detail review targets
- clearer linkage between historical posture movement and concrete PR or merged-change evidence
- less dependence on raw history accumulation alone

### 4. Add trend read models
The first read-side slice now supports:
- repo-level static drift summaries
- top-drifting artifact leaderboards

It should continue expanding to support:
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
