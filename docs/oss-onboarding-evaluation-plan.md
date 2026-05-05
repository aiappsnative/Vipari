# Evaluation Harness Plan

## Purpose

This document now defines the evaluation foundation for repeatable real-repository validation and the next hardening phase of `feature/eval-harness-v1`.

It should be read as a bridge between:

- the OSS onboarding and dashboard work already merged on `main`
- the next product-first iterations (`feature/repo-evidence-v1`, `feature/signal-fusion-v1`, and `feature/discovery-precision-v1`)
- the later need for a repeatable evaluation harness that keeps those improvements honest

The goal is no longer to prove that Vipari can touch a public repository at all.
That proof now exists.

The current goal is to turn ad hoc real-repository validation into a repeatable product-evaluation loop, building on the harness groundwork that already exists on `main`.

This harness should be treated as internal developer/operator infrastructure. It exists so product and engine changes can be evaluated repeatably by humans and later by CI, not as a customer-facing workflow that repository owners are expected to run themselves.

Shipped groundwork on `main` already includes:

- CLI-driven eval candidate listing and eval runs
- seeded scenario listing for machine-checkable comparison targets
- repeatable saved run packages under `artifacts/eval-runs/`
- saved repo and overview dashboard payload snapshots
- branch-to-branch comparison summaries for eval packages

---

## Current validated baseline

Vipari has already validated meaningful onboarding behavior on real repositories.

Confirmed on `main` today:

- baseline-first onboarding works on public repositories
- selective historical backfill can persist artifact versions and static profile lineage
- the dashboard can render real repo history and case-file views
- persisted snapshot content can support code-level posture evidence later
- large-repo onboarding needed bounded discovery and direct GitHub contents reads to stay operationally reasonable

Known completed live validations:

- `doria90/openfang`
- `doria90/hermes-agent`

Current takeaway:

- Vipari is already past the "toy demo" stage for real-repository onboarding
- the biggest remaining trust gaps are discovery precision, proposal-vs-landed evidence synthesis, and repeatability of evaluation outputs
- the harness should therefore optimize for product usefulness, not only ingest success

---

## Evaluation objective

For each selected evaluation target, Vipari should be able to answer these questions with useful, reviewable evidence:

- where are the likely AI control surfaces?
- which discovered artifacts look authoritative enough to baseline?
- how has each important artifact drifted over time?
- which artifacts deserve reviewer attention now?
- where is urgency coming from real PR or merged-change evidence versus only historical hotspots?
- does the resulting dashboard output help a human decide what to inspect next?

---

## Harness contract

The harness should continue producing a stable package of evaluation artifacts for each candidate target, with the next work focused on expanding coverage quality rather than inventing the mechanism from scratch.

### Required inputs

- repository identity (`owner/repo`)
- GitHub App installation context
- onboarding mode (`baseline_only` or `baseline_plus_backfill`)
- optional backfill limits or filters
- optional notes about expected AI control surfaces for manual comparison
- optional seeded scenario key for explicit assertions

### Required workflow steps

1. run repository onboarding
2. persist the discovered artifact inventory and baseline versions
3. optionally execute selective historical backfill
4. collect overview and repo-detail read-model payloads
5. record a short reviewer judgment against the outputs

### Required outputs

Each run should save or emit:

- onboarding summary
- discovered artifact inventory
- baseline coverage summary
- backfill execution summary
- repo dashboard payload snapshot
- overview dashboard payload snapshot
- top artifacts requiring review
- manual evaluation notes against a fixed rubric
- explicit assertions and pass/fail summary when a seeded scenario is attached

The purpose of the harness is not only replayability. It is comparability across branches.

That is why the current implementation relies on a scriptable CLI and isolated local persistence for many runs. The CLI is the cheapest deterministic control surface for internal evaluation, and isolated databases keep one run's onboarding/history artifacts from contaminating another run or a developer's normal local state.

### Seeded scenario registry

The harness should support a small built-in registry of seeded scenarios in addition to live repository candidates.

Each scenario should:

- point at a real or seeded target repo
- carry a stable scenario key
- define explicit assertions for expected behavior
- optionally point at a checked-in reference package for stable branch-to-reference comparison

Examples of useful assertions:

- minimum high-confidence baseline coverage
- top review target must exist
- top review target should match a known artifact path
- lower-confidence queue must stay below a defined ceiling

This turns scenario runs into something stronger than saved snapshots alone: they become lightweight regression checks that can fail explicitly when ranking or discovery quality drifts.

When a scenario also carries a checked-in reference package, the CLI can compare a new run directly against that baseline without requiring a manually supplied package path. That keeps seeded regressions reproducible across branches and local environments.

---

## Product capabilities this evaluation must validate

### 1. Discovery quality
Vipari should:
- detect likely prompts, policies, tool definitions, model-routing config, and agent wiring
- keep obviously generic code out of the primary artifact set
- expose confidence or lower-confidence handling when discovery is uncertain

### 2. Baseline-first onboarding quality
Vipari should:
- persist a usable initial artifact inventory
- store baseline versions for discovered control surfaces
- distinguish stronger findings from weaker discovery guesses

### 3. Historical/backfill usefulness
Vipari should:
- build enough artifact history to explain meaningful drift
- avoid replaying irrelevant repository history
- preserve clear lineage and provenance for stored versions

### 4. Reviewer usefulness
Vipari should:
- surface plausible high-priority artifacts
- explain why an artifact is risky or important
- avoid letting urgency come only from noisy history accumulation

### 5. Dashboard usefulness
Vipari should:
- make `/dashboard` useful as a triage surface
- make `/dashboard/{owner/repo}` useful as a case file
- help a reviewer decide what to inspect next in GitHub

---

## Pass / fail criteria

The harness should grade each run across five dimensions.

### Discovery pass criteria

Pass when:
- major obvious AI control surfaces are discovered
- false positives do not dominate the primary queue
- artifact categorization is directionally correct

Fail when:
- the main artifact list is mostly generic code
- obviously important prompts or policies are missed
- lower-confidence findings overwhelm the useful queue

### Baseline pass criteria

Pass when:
- most high-confidence discovered artifacts receive baseline versions
- baseline provenance is visible enough to interpret drift later

Fail when:
- important artifacts lack a baseline without clear reason
- baseline state is too ambiguous to trust later comparisons

### Historical/backfill pass criteria

Pass when:
- history explains major drift on important artifacts
- duplicate or fragmented lineage is limited
- runtime remains operationally bounded for the repo size

Fail when:
- lineage is sparse or incoherent
- backfill cost is disproportionate to the value of the resulting history

### Reviewer-output pass criteria

Pass when:
- top-ranked items feel plausible to a human reviewer
- explanations point to meaningful guardrail, capability, autonomy, or governance movement
- repo detail suggests a credible next review target

Fail when:
- urgency is driven mainly by accumulated historical noise
- ranking feels arbitrary or disconnected from concrete evidence

### Dashboard pass criteria

Pass when:
- overview and repo-detail surfaces agree with the observed repository history
- a human can use them to choose what to inspect next
- the product story stays reviewer-first rather than metrics-first

Fail when:
- outputs are interesting but not actionable
- dashboard ranking contradicts available provenance and evidence

---

## Candidate target strategy

The harness should keep a small but diverse candidate set.

### Current confirmed candidates

- `doria90/openfang`
- `doria90/hermes-agent`

These are the initial built-in OSS candidates, not the full product boundary. The harness itself should stay generic enough to evaluate any repository or seeded comparison target we choose to route through the same onboarding, dashboard, and comparison contract.

### Target expansion shape

- 2–3 smaller prompt-heavy repos
- 2 medium repos with agent wiring or tool definitions
- 1 larger stretch repo with noisier structure and meaningful history

Preferred repo traits:

- public GitHub availability
- visible prompts, policies, or agent workflow config
- meaningful commit history
- at least a few distinct AI control surfaces
- a mix of text-heavy and code-wiring-heavy layouts

---

## What to measure

### Quantitative checks

- discovered artifact count
- percent of high-confidence artifacts with persisted baselines
- historical versions linked per artifact
- onboarding duration per repo
- backfill duration per repo
- failed or retried operations

### Qualitative checks

- precision of the primary artifact queue
- plausibility of top-drifting artifacts
- clarity of provenance and review-target explanations
- usefulness of repo case-file output for a human reviewer

The harness should keep both kinds of measurements. Product usefulness cannot be reduced to counts alone.

---

## Immediate gaps this harness must expose

This document should shape the next iterations by making the current weak spots explicit.

The harness needs to tell us:

1. whether `feature/repo-evidence-v1` actually improves reviewer-target quality
2. whether `feature/signal-fusion-v1` makes ranking and explanations more trustworthy
3. whether `feature/discovery-precision-v1` reduces low-value artifacts without missing real control surfaces
4. whether later changes improve repo usefulness across more than one hand-picked repository

That means the harness should preserve enough output to compare branch-to-branch behavior, not only branch-to-main pass/fail status.

---

## Implementation notes for `feature/eval-harness-v1`

The future harness slice should stay lightweight.

Recommended components:

- a small candidate-repo registry
- one reproducible runner for onboarding and optional backfill
- saved payload snapshots for overview and repo detail
- a fixed evaluator rubric stored alongside run results
- one branch-comparison summary that calls out product regressions or improvements

It should not become a heavyweight benchmarking platform before Vipari has stronger repo evidence and discovery quality.

---

## Summary

Vipari has already shown it can onboard and render real OSS repositories.

The next step is to make that proof repeatable.

This evaluation plan exists to ensure that future product work is judged by whether it improves discovery trust, reviewer-target quality, and real decision usefulness on repositories we do not control.

**a real public repository installs Vipari, Vipari discovers its AI control surface, digests enough history to build drift intelligence, and produces PR and dashboard outputs that are actually useful.**

That is the correct end-goal test for this feature.
