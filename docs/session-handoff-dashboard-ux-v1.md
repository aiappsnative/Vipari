# Session Handoff: `dashboard-ux-v1` (Archived)

> Archive note: this slice has already been merged into `main`. This document is preserved as historical implementation context only. For the active roadmap, use [Plan.MD](../Plan.MD). For shipped outcomes, use [CHANGELOG.md](../CHANGELOG.md).

## Archived state

As of 2026-03-24:

- `feature/pr-escalation-v1` has been merged into `main`
- `feature/approved-baseline-v1` has been merged into `main`
- `feature/repo-provenance-v1` has been merged into `main`

`main` now includes:

- GitHub-native PR escalation guidance
- approved-baseline provenance for drift comparisons
- repo-detail provenance for history and PR context
- a working dashboard split across:
  - portfolio overview at `/dashboard`
  - repo detail at `/dashboard/{owner/repo}`

Current dashboard limitations:

- prioritization is still not obvious enough
- trust/explainability is still weaker than needed
- panels feel too equally weighted
- the dashboard is informative, but not yet optimized for “what should I open next in GitHub?”

## Historical planned slice

Name:

- `feature/dashboard-ux-v1`

Purpose:

- redesign the dashboard around portfolio and repo-level triage in support of PR review

Strategic source:

- [Plan.MD](../Plan.MD)
- [SOUL.md](../SOUL.md)
- [README.md](../README.md)
- [docs/detection-engine-plan.md](detection-engine-plan.md)

## Product rule for this slice

The dashboard is not a replacement for PR review.

Its job is to answer, quickly:

- which AI control surfaces and repos need attention right now
- why they are ranked highly
- which PR or change the reviewer should click into next

The dashboard should route users into GitHub, not create a parallel review universe.

## Primary user

Optimize first for:

- AI/platform owners
- staff-level engineers responsible for AI control surfaces

Secondary users:

- security/risk reviewers
- tech leads

Expected usage pattern:

- exception handling
- burst triage during active AI change
- periodic review 2–3 times per week

This is not yet a daily command-center product.

## Core design objective

Within 30–60 seconds, a user should be able to answer:

- Do we have any AI-impacting changes that require escalation or focused review today?
- If yes, which repo and which control surface or PR should I open first?

If the redesign does not make that flow obvious, it is not successful.

## What is wrong with the current layout

The current dashboard already has useful information, but the main failure is:

- weak prioritization
- weak trust/explainability

The next slice should not focus on adding more metrics.

It should focus on making the top 3 things obvious and making the reason for their rank trustworthy.

## Target UX shape

### Overview page

The overview page should emphasize:

1. cross-repo hotspot detection
2. review queue
3. repo coverage/health

It should answer:

- where do I go first?
- which repos have risky or drifted AI surfaces?
- are monitored repos covered by meaningful baselines?

Risk-pattern charts and broader analytics are secondary.

### Repo-detail page

The repo-detail page should be artifact-first.

Each artifact or control surface should be the anchor for the story.

Under an artifact, the user should be able to see:

- latest PRs touching it
- drift vs approved baseline
- compact provenance
- short history context
- recommended next action

Above the fold, the repo page should show:

1. `Needs review now` lane
   - 3–7 highest-priority items max
   - each with clear reason labels

2. baseline vs current posture
   - compact visual for:
     - guardrails
     - capability risk
     - autonomy
     - governance strength

3. top drifting artifacts
   - even if there is no current open PR

Everything else should be secondary or progressively disclosed.

## Prioritization rules

Rank high-priority items primarily by:

1. blast radius / control-surface type
2. capability expansion
3. guardrail weakening
4. governance weakening
5. autonomy increase
6. recency
7. confidence

Guidance:

- money movement, PII access, auth/identity, broad data export, and production-facing prompts should float up by default
- governance regressions should matter more than they currently do
- low-confidence findings should not pollute the main triage lane

### Required UI behavior

The dashboard should have two distinct lanes:

- primary `Needs review now`
- secondary `Interesting but lower-confidence findings`

The lower-confidence lane should be visible, but collapsed or de-emphasized by default.

### Explainable ranking

The dashboard does not need to expose ranking math.

It must expose ranking reasons.

Each top item should have a short rationale shaped like:

- ranked high because of critical surface + capability expansion + weakened controls + recency

## Provenance and trust requirements

Every high-priority item should show, without extra clicks:

- PR number or merged commit
- actor/author
- timestamp
- baseline identity

Second layer detail can include:

- commit SHA
- review status
- reviewer identity
- richer provenance tags

Baseline authority must remain explicit.

Preferred wording:

- `Baseline: Approved v1.3`
- `Baseline: Auto-baseline (current main – not yet approved)`

This distinction is part of product trust and should not be hidden.

## Narrative and action model

Each high-priority item should provide:

1. one-line summary
2. short rationale
3. recommended action
4. optional compact evidence bullets

Each item must answer:

- what changed
- why it is risky
- relative to which baseline
- what to inspect next
- whether escalation is implied

Recommended actions should be operational and directive.

Good direction:

- escalate to AI platform and security
- route to repo owner before merge
- review policy/guardrail diff first
- investigate merged drift hotspot

Weak direction:

- generic “keep an eye on this”

## Layout and interaction principles

For the next slice:

- keep card/table-based UI
- prefer fewer panels with clearer hierarchy
- use progressive disclosure for detail
- keep the main screen focused on triage
- use click-through into GitHub as the terminal action

The main column should prioritize:

- `Needs review now`
- `Top drifting artifacts`

Secondary space can hold:

- posture summary
- coverage/confidence
- lower-confidence findings
- pattern context

Do not overload first view with exhaustive history or analytics.

## Minimal filtering for this slice

Only add filters that directly support triage:

- by repo
- by surface type
- by state:
  - open PR
  - merged drift

Defer:

- full search
- faceted analytics
- broad exploratory filtering

## In-scope outcomes

This slice should aim to deliver:

1. clearer `Needs review now` ranking on repo detail
2. clearer cross-repo review queue on overview
3. stronger baseline visibility and provenance on top items
4. artifact-first repo-detail structure
5. explicit lower-confidence lane
6. more directive rationales and recommended actions
7. cleaner click-through path back into GitHub

## Out of scope

Do not let this slice sprawl into:

- runtime telemetry
- heavy analytics or trend dashboards
- global heatmaps as a primary surface
- GRC/policy authoring workflows
- complex search UI
- baseline approval workflow expansion
- PR comment redesign
- signal-fusion redesign beyond what is needed for clearer ranking output

## Recommended implementation order

### 1. Reframe dashboard outputs around triage

Update the read-side in [services/dashboard_views.py](../services/dashboard_views.py) so the main payloads explicitly support:

- high-priority queue items
- lower-confidence items
- baseline identity
- provenance summary
- reviewer-facing rationale
- recommended next action

### 2. Redesign overview hierarchy

Update:

- [templates/dashboard_index.html](../templates/dashboard_index.html)
- [static/dashboard-index.js](../static/dashboard-index.js)

Priority:

- top cross-repo queue first
- coverage/health second
- patterns/context later

### 3. Redesign repo-detail hierarchy

Update:

- [templates/dashboard_repo.html](../templates/dashboard_repo.html)
- [static/dashboard-repo.js](../static/dashboard-repo.js)

Priority:

- artifact-first layout
- `Needs review now`
- top drifting artifacts
- compact posture summary
- progressive disclosure for history and detail

### 4. Improve prioritization semantics

Refine current heuristics in [services/dashboard_views.py](../services/dashboard_views.py) so ranking better reflects:

- blast radius
- governance weakening
- recency
- confidence separation

### 5. Lock the contract with tests

Update:

- [tests/test_dashboard_views.py](../tests/test_dashboard_views.py)
- [tests/test_dashboard_api.py](../tests/test_dashboard_api.py)

Potentially add focused UI-facing payload assertions for:

- top-item ordering
- baseline labeling
- lower-confidence separation
- rationale/action fields

## Likely files to inspect first

Core candidates:

- [services/dashboard_views.py](../services/dashboard_views.py)
- [templates/dashboard_index.html](../templates/dashboard_index.html)
- [templates/dashboard_repo.html](../templates/dashboard_repo.html)
- [static/dashboard-index.js](../static/dashboard-index.js)
- [static/dashboard-repo.js](../static/dashboard-repo.js)
- [static/dashboard.css](../static/dashboard.css)
- [tests/test_dashboard_views.py](../tests/test_dashboard_views.py)
- [tests/test_dashboard_api.py](../tests/test_dashboard_api.py)

## Success criteria

A good first pass should allow an AI/platform owner to:

- identify the top 3 AI changes needing review in 30–60 seconds
- understand why they are ranked highly
- trust the baseline context and provenance
- click through to the right PR or commit next

Evidence of success:

- better internal triage speed
- better escalation decisions
- fewer “what is this showing me?” questions
- stronger trust that the top-ranked items deserve attention

## Definition of a good first pass

A successful first pass should:

- make the main triage lane obvious
- improve prioritization without adding metric sprawl
- preserve GitHub as the primary review surface
- keep baseline authority explicit
- strengthen provenance and next-action clarity
- reduce visual noise through hierarchy and disclosure
- leave advanced analytics, search, and workflow expansion for later

## Research-backed IA pivot

After multiple UI iterations, the main remaining problem appears to be structural rather than cosmetic.

The current dashboard still mixes three different jobs into one default surface:

1. triage / “what do I open next?”
2. coverage / “what are we monitoring?”
3. analysis / “what patterns exist across the portfolio?”

That mixture makes the page feel busy even when each individual component is improved.

### Working conclusion

DriftGuard should treat the dashboard primarily as an **AI change review inbox**.

The core mental model should be:

- an ordered queue of AI-related changes or drift hotspots
- each item explains why it is ranked highly
- each item routes the reviewer into GitHub or repo detail

Coverage and analytics are still valuable, but they should not compete with the queue on first view.

## Recommended product shape

### Default mode: `Triage`

The default portfolio page should behave like an inbox.

Above the fold, it should answer only three things:

1. Do we have anything urgent?
2. What are the top 3 items to inspect next?
3. Are there any blind spots that reduce trust in the queue?

Recommended sections, in order:

1. **Urgency banner**
  - one sentence portfolio state
  - count of `review now` repos/items
  - strongest current hotspot

2. **Open next**
  - top 3 ranked queue items only
  - each item shows:
    - repo
    - artifact/control surface
    - why it ranked high
    - baseline label
    - source PR/commit
    - single CTA

3. **Continue reviewing**
  - remaining queue items
  - lower visual weight than top 3

4. **Coverage trust strip**
  - compact summary only:
    - approved baselines present?
    - low-confidence queue items?
    - coverage gaps?
  - should feel like context, not a second dashboard

5. **Everything else behind disclosure or secondary mode**
  - pattern summaries
  - control-surface distribution
  - broader inventory

### Secondary mode: `Coverage`

Coverage should become a secondary view, not a peer competing with triage on the landing surface.

Its job is to answer:

- what repositories and control-surface types are covered?
- where are approved baselines missing?
- where is discovery confidence weak?

This mode can hold:

- control-surface coverage
- baseline authority status
- lower-confidence discovery inventory
- pattern counts
- repo inventory tables

### Repo detail should become a case file

The repo page should feel less like a dashboard and more like a case file for the current repo.

Above the fold:

1. **Open this first**
  - one featured item
  - strongest recommendation
  - direct source target

2. **Review queue for this repo**
  - 3–7 items max
  - explicit ranking reasons

3. **Why DriftGuard flagged this repo**
  - compact baseline/current posture summary
  - provenance + baseline authority

Below the fold / progressive disclosure:

- historical drift storyline
- artifact inventory
- lower-confidence findings
- grouped control-surface coverage

## Wireframe direction

### Portfolio `Triage`

- top banner: `2 repos need review now · strongest hotspot: refunds / production authority expansion`
- first card stack: `Open next`
- second card stack: `Continue reviewing`
- narrow right rail or compact strip: `Coverage trust`
- hidden by default: `Pattern context`, `Inventory`

### Portfolio `Coverage`

- summary of monitored repos and artifacts
- approved baseline coverage by repo / surface type
- low-confidence discovery inventory
- recurring change patterns

### Repo detail

- featured case card: `Open this first`
- repo queue: ranked evidence cards
- posture + provenance block
- collapsed history/inventory below

## Why this direction is stronger

This structure matches the user goal more directly:

- triage is list-first, not panel-first
- the top 3 items become unmistakable
- coverage remains available without diluting urgency
- analytics remain useful without pretending to be the primary workflow
- the terminal action stays in GitHub

## Recommended next implementation slice

If continuing on `feature/dashboard-ux-v1`, the next implementation should pivot from “better dashboard panels” to “clearer workflow modes”.

### Phase A — make the overview explicitly triage-first

- relabel the portfolio page as `Triage`
- reduce the first view to:
  - urgency banner
  - top 3 open-next items
  - remaining queue
  - compact coverage trust strip
- remove duplicated secondary analytics from the default view

### Phase B — separate coverage from triage

- add a simple mode switch:
  - `Triage`
  - `Coverage`
- keep `Triage` as default
- move existing coverage and pattern sections into `Coverage`

### Phase C — make repo detail read like a review case file

- feature one strongest item at the top
- compress the rest of the queue into a ranked list
- move lower-confidence findings below the main evidence block
- keep history and full artifact inventory collapsed

### Phase D — tune wording before adding more UI

- strengthen urgency banner language
- make every CTA explicit about the next review target
- make every primary card answer:
  - what changed
  - why it matters
  - what baseline it is compared against
  - where to click next

## Decision rule for future iterations

When deciding whether to add a section to the default page, ask:

> Does this help a reviewer decide what to open next in under 60 seconds?

If not, it belongs in a secondary mode, disclosure block, or later slice.

## End-of-session wrap-up

### What shipped on this branch

By the end of this session, `feature/dashboard-ux-v1` is no longer just a design direction.

It now includes:

- a triage-first overview page built as an AI change-review inbox
- an explicit `Triage` / `Coverage` split on the overview surface
- a repo-detail case-file layout with one featured insight, a ranked queue, and progressive disclosure for lower-confidence findings and deep history
- richer dashboard read-model fields for rationale, recommended action, baseline label, review target, confidence labeling, and queue-lane separation
- stronger prioritization semantics that weight blast radius, governance weakening, recency, and lower-confidence separation more explicitly
- onboarding optimizations that keep larger OSS repos operationally bounded during discovery and backfill

### Real OSS validation completed

This branch was validated against a fresh public-repo onboarding flow, not only local fixtures.

Two useful reference repos were exercised:

- `doria90/openfang`
- `doria90/hermes-agent`

Most importantly, `doria90/hermes-agent` now has a successful local onboarding record with:

- 22 discovered artifacts
- 22 baseline versions
- completed selective historical backfill jobs
- working dashboard payloads from the live local SQLite store

This matters because it confirms the dashboard and read-side are now grounded in real OSS history rather than only seeded tests.

### Signal assessment from `doria90/hermes-agent`

The current signals are directionally credible.

What looked good:

- discovered artifacts clustered around plausible AI control surfaces such as parser logic, ACP tooling, prompt-related assets, and model/config wiring
- control-surface grouping looked sane (`tools`, `models`, `prompts`, `agents`)
- the top historical hotspot and follow-on queue items were recognizable enough to support a real case-file workflow
- lower-confidence items were separated from the main lane instead of polluting the primary queue

What remains true:

- the strongest `hermes-agent` urgency currently comes from historical drift hotspots, not active PR audits
- `pull_request_audit_count` is still `0` for that repo, so current-case evidence is thinner than the seeded reviewer path
- `drift_summary` remains sparse without live PR-linked profile data

Conclusion:

- the dashboard is ready for broader testing as a triage surface
- the next quality step is not more frontend reshaping
- it is denser PR-linked evidence and continued discovery/provenance refinement on public repos

### Local operator note

During validation, a reload-based local server instance on port `8001` entered a stale state and caused the dashboard to appear stuck in a loading loop.

The reliable recovery path was:

- stop the stale reload server
- restart Uvicorn without `--reload`
- use the new clean port

At the end of the session, the known-good local preview was running on `127.0.0.1:8002`.

### Recommended next slice after this branch

If work continues after this branch is merged, the next highest-value slice should focus on signal quality rather than layout novelty:

1. increase PR-linked evidence density on real repos
2. improve merged-commit provenance and reviewer target linking
3. keep refining discovery precision for large OSS repos
4. evaluate whether the current top-ranked items still hold up across a broader candidate set

## Historical restart checklist

When work resumes, the fastest useful restart path is:

1. start a clean local server without `--reload`
2. verify [README.md](../README.md) operator steps still match local reality
3. open `/dashboard` and the `doria90/hermes-agent` repo page first
4. inspect whether the top-ranked repo and artifact still feel like the right next review target
5. choose one of the following follow-up slices before touching UI again:
  - provenance enrichment
  - PR-linked evidence density
  - discovery precision on OSS repos
  - lightweight OSS evaluation harness

Recommended first implementation target at the time:

- improve reviewer target quality on real repos by linking more repo insights to concrete PR or merged-commit provenance
