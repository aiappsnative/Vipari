# OSS Onboarding Evaluation Plan

## Purpose

This document defines the end-goal validation plan for PromptDrift's onboarding and historical drift capabilities.

The target scenario is simple:

- an open-source repository installs PromptDrift as a GitHub App,
- PromptDrift discovers the repository's AI-relevant control surface,
- PromptDrift establishes an initial baseline,
- PromptDrift selectively digests enough historical evolution to power drift analysis,
- and PromptDrift produces useful reviewer signals, history views, and dashboard-level summaries.

This document is the bridge between today's feature implementation and the final real-world proof that the product works on repositories we do not control.

---

## Why this is the right end-goal test

Yes — this is the right test.

It validates the full product story rather than only local unit correctness.

A successful open-source onboarding evaluation proves that PromptDrift can:
- onboard a fresh repository with no hand-curated fixtures,
- find AI-relevant prompts/configs/tooling from repository structure and content,
- establish meaningful baselines,
- digest real historical evolution,
- populate the drift engine with durable history,
- and surface useful results in the same GitHub-native workflow customers would actually use.

This is stronger than testing only synthetic repositories because it measures:
- ambiguity tolerance,
- generalization,
- onboarding robustness,
- scalability of historical digestion,
- and product usefulness on messy real codebases.

---

## Evaluation objective

PromptDrift should be able to onboard a selected open-source repository and answer these questions with useful accuracy:

- Where are the likely AI control surfaces in this repo?
- What should be considered the initial baseline for those artifacts?
- How have those artifacts evolved over time?
- Which prompts/configs/tools have drifted most from baseline?
- Where did guardrails weaken or strengthen?
- Where did capability or blast radius expand?
- Does the resulting history support useful PR review and dashboard summaries?

---

## Product capabilities this evaluation must validate

### 1. App installation and repository discovery
PromptDrift should:
- authenticate as a GitHub App,
- enumerate repository contents safely,
- detect likely AI-relevant artifact paths,
- classify them into meaningful categories.

### 2. Baseline-first onboarding
PromptDrift should:
- capture a present-day baseline artifact inventory,
- persist baseline versions and metadata,
- attach confidence notes where discovery is uncertain.

### 3. Selective historical digestion
PromptDrift should:
- inspect historical commits or PRs relevant to discovered artifacts,
- avoid replaying the entire commit graph unless explicitly needed,
- build a useful lineage graph for artifact evolution,
- produce durable static profile history.

### 4. Drift engine usefulness
PromptDrift should:
- compute baseline-relative profile deltas,
- identify top-drifting artifacts,
- support drift narratives over time,
- surface the results in reviewer- and dashboard-friendly form.

### 5. Dashboard and reporting usefulness
PromptDrift should eventually show:
- repo-level summary cards,
- most-changed artifacts,
- highest capability-risk shifts,
- biggest guardrail regressions,
- strongest governance regressions,
- timelines of major drift jumps.

---

## Recommended evaluation phases

### Phase 1 — Candidate repo selection
Build a small candidate set of open-source repositories.

Selection criteria:
- public GitHub repos,
- visible prompts/configs/agent workflows,
- meaningful commit history,
- multiple AI control surfaces,
- preferably a mix of prompt-heavy and code-wiring-heavy repos.

Good candidate categories:
- prompt engineering libraries,
- agent frameworks,
- LLM application templates,
- AI assistants with tool definitions,
- retrieval-augmented example apps.

Target set:
- 3 small repos,
- 2 medium repos,
- 1 more complex repo for stretch validation.

### Phase 2 — Baseline-first onboarding validation
For each selected repository, test whether PromptDrift can:
- identify candidate AI artifacts,
- classify artifact type,
- create an onboarding inventory,
- store baseline versions,
- explain confidence and coverage.

Success criteria:
- high recall on obvious AI control surfaces,
- low confusion between generic code and AI-control code,
- reasonable artifact categorization,
- baseline persistence works without manual cleanup.

### Phase 3 — Historical digestion validation
For each repository, run selective backfill on discovered artifacts.

PromptDrift should:
- ingest only relevant history,
- link artifact versions cleanly,
- create static profile history,
- preserve repo/path provenance,
- avoid excessive duplicate history.

Success criteria:
- history is complete enough to explain major drift,
- baseline-to-current comparisons are meaningful,
- top-drifting artifacts are plausible to a human reviewer,
- runtime is operationally bounded.

### Phase 4 — Reviewer output validation
Replay selected historical PRs or commit deltas through PromptDrift.

PromptDrift should produce:
- deterministic findings,
- semantic review notes,
- static drift summaries,
- profile deltas against prior baselines.

Success criteria:
- reviewer output is understandable,
- summaries highlight the right dimensions,
- obvious guardrail/capability changes are surfaced,
- output is not dominated by noise.

### Phase 5 — Dashboard/read-side validation
Once dashboard and trend surfaces exist, validate that they answer:
- what drifted most,
- what changed most often,
- what got riskier,
- what governance weakened,
- what patterns matter at repo level.

Success criteria:
- dashboard summaries are directionally correct,
- results match repository history reasonably well,
- a human can use them to prioritize inspection.

---

## Evaluation harness requirements

To support this test well, PromptDrift will need:
- onboarding inventory persistence,
- selective historical ingest jobs,
- static profile read models,
- top-drifting artifact queries,
- repo-level aggregation queries,
- exportable evaluation snapshots,
- an operator-friendly way to re-run onboarding/backfill for a selected repo.

Recommended future harness components:
- `onboard_repository()` workflow,
- `backfill_repository_history()` workflow,
- repo-level evaluation fixture exporter,
- benchmark script for candidate repos,
- comparison report between expected vs discovered AI control surfaces.

Current implementation status:
- `onboard_repository()` now exists as a baseline-first onboarding workflow
- selective historical backfill-job planning now exists for discovered artifacts
- full historical backfill execution is still pending

---

## What to measure

### Discovery quality
- count of discovered AI artifacts,
- precision/recall against manual spot-checks,
- artifact-type classification quality,
- confidence distribution.

### Baseline quality
- percent of discovered artifacts with persisted baseline,
- onboarding completeness,
- false-positive artifact baselines.

### Historical digestion quality
- artifact lineage coverage,
- number of linked historical versions,
- duplicate/fragmented history rate,
- ingest runtime and storage cost.

### Drift usefulness
- plausibility of top-drifting artifacts,
- correctness of obvious guardrail/capability/autonomy changes,
- dashboard usefulness in human spot review,
- reviewer usefulness of PR summary blocks.

### Operational metrics
- onboarding duration per repo,
- historical backfill duration,
- storage growth per repo,
- number of failed/retried ingest tasks.

---

## Risks and guardrails

### Risk: over-consuming full history
Guardrail:
- start baseline-first,
- use selective historical backfill only on discovered artifacts,
- keep time/cost bounded.

### Risk: AI artifact discovery is too noisy
Guardrail:
- store discovery confidence,
- make artifact purpose probabilistic,
- allow later manual overrides if necessary.

### Risk: dashboard becomes misleading before baselines are trustworthy
Guardrail:
- clearly label onboarding confidence,
- distinguish baseline-first data from backfilled history,
- avoid overclaiming on low-confidence repos.

### Risk: open-source repos vary wildly in structure
Guardrail:
- test across repo categories,
- compare by cohort rather than treating one repo as decisive,
- treat candidate set expansion as an ongoing evaluation program.

---

## Definition of success

The end-goal test is successful when PromptDrift can onboard selected open-source repos and produce outputs that a human reviewer would judge as useful and directionally correct.

At minimum, that means:
- onboarding finds the major AI control surfaces,
- historical digestion builds usable artifact/profile history,
- top drift candidates are plausible,
- PR summaries highlight meaningful design movement,
- dashboard-level summaries help prioritize review.

---

## Immediate preparation plan

### What to build next
1. Read-side trend queries over persisted static profiles.
2. Repo-level aggregation helpers for top-drifting artifacts.
3. Onboarding inventory data model.
4. Selective historical ingest job model.
5. Simple operator CLI/API to onboard and backfill a repository.
6. Dashboard primitives backed by those read models.

### What to prepare in parallel
- shortlist candidate OSS repos,
- define manual review rubrics for discovery quality,
- define a benchmark report format,
- save sample outputs for regression comparison.

---

## Summary

The final proof of PromptDrift should not be a synthetic demo.

It should be this:

**a real public repository installs PromptDrift, PromptDrift discovers its AI control surface, digests enough history to build drift intelligence, and produces PR and dashboard outputs that are actually useful.**

That is the correct end-goal test for this feature.
