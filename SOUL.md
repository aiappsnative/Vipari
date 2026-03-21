# PromptDrift Soul

## What this document is

This is the soul of PromptDrift.

If `Plan.MD` is the execution tracker, this document is the durable source of truth for why the project exists, what problem it solves, what it is not trying to be, and what principles should guide product and engineering decisions.

When roadmap details change, this document should remain stable.

---

## Our core belief

AI systems drift long before anyone notices it in production.

That drift often starts in places that are already visible in GitHub:
- prompts,
- policies,
- tool definitions,
- model settings,
- agent wiring,
- approval flows,
- and the pull requests that change them.

PromptDrift exists to make that design-level drift visible, understandable, and governable.

---

## Our value offering

PromptDrift helps teams govern AI systems in GitHub by detecting how prompts, policies, tool access, and agent wiring drift from an approved baseline.

It surfaces:
- weakened guardrails,
- expanded capability and blast radius,
- changes in autonomy,
- model/config shifts,
- and governance-relevant changes in ownership and review quality,

before those changes ship.

We do this without requiring runtime traffic, production logs, or access to customer data.

Our first practical job is narrower than “full AI governance.”

PromptDrift should help teams decide when an AI-related pull request must be escalated before merge because it changed what the AI is allowed to do, how tightly it is constrained, or how broadly it can act.

---

## What we are building

PromptDrift is a GitHub-native design drift engine for AI systems.

It analyzes AI systems as code and configuration.

It extracts meaningful static attributes from:
- prompts,
- configs,
- tool wiring,
- model parameters,
- repo metadata,
- PR history,
- reviewers,
- labels,
- and ownership rules.

It compares those attributes to a known-good baseline and explains what changed, why it matters, and where risk increased or decreased.

---

## What customers care about

Even without runtime data, teams still need clear answers to these questions:

- What did we originally approve as the baseline for this agent or prompt?
- What structural or policy-relevant edits have been made since then?
- Where did we relax or tighten guardrails?
- Where did we increase blast radius through tools, scopes, or permissions?
- Which agents have drifted furthest from their intended design?
- Who changed them, how often, and under what review conditions?

PromptDrift is built to answer those questions directly from GitHub-visible artifacts.

---

## What we can know without runtime access

Everything PromptDrift produces must be grounded in information extractable from code, prompts, config, and GitHub metadata.

That includes:

### 1. Prompt structure and complexity
- token and character count,
- section count,
- examples and few-shot coverage,
- instruction density,
- ambiguity or internal conflict signals.

### 2. Guardrail and policy strength
- explicit safety, privacy, compliance, and escalation rules,
- specificity of restrictions,
- policy clarity,
- presence of bounded actions and approval conditions.

### 3. Capability and blast radius
- tool inventory,
- read vs write capability,
- sandbox vs production access,
- sensitive system access,
- breadth of connected systems,
- scope-limiting constraints.

### 4. Autonomy and execution posture
- loop depth,
- step count,
- parallelism,
- human-in-the-loop markers,
- approval checkpoints.

### 5. Model and parameter choices
- model/provider selection,
- temperature and sampling settings,
- token limits,
- safety and moderation hooks if declared.

### 6. Governance and change control
- code owners,
- reviewers,
- labels,
- change frequency,
- review patterns,
- baseline provenance.

---

## Our abstraction: the agent attribute profile

Each agent or prompt should be representable as a stable static profile.

Examples of attributes include:
- `guardrail_robustness`
- `capability_risk`
- `autonomy_level`
- `stability_vs_creativity`
- `change_frequency`
- `governance_strength`

These are not runtime metrics.

They are design and governance attributes inferred from the AI system's visible definition in GitHub.

This is the foundation of the product.

---

## Our definition of drift

Drift is the change in an agent's attribute profile relative to a chosen baseline.

That includes:
- score deltas,
- semantic distance from baseline prompt text,
- added or removed guardrails,
- added or removed tools,
- privilege changes,
- autonomy changes,
- governance changes.

PromptDrift should be able to say:

- what changed,
- how far it moved from baseline,
- in which direction,
- and why that movement matters.

---

## What we are not

PromptDrift is not a runtime observability platform.

We are not primarily building:
- latency monitoring,
- cost dashboards,
- session tracing,
- live quality scoring,
- output analytics,
- behavioral guarantees from production traffic,
- or end-user monitoring.

Our center is design drift and governance in GitHub.

---

## Product surfaces

### GitHub PR experience
On every PR that touches AI-relevant files, PromptDrift should explain:
- what changed,
- which attributes moved,
- whether risk increased or decreased,
- and what reviewers should pay attention to.

This is the product wedge.

PromptDrift should first win as a high-signal PR reviewer for AI control-surface changes, not as a generic governance dashboard.

The primary workflow outcome is not automatic allow/deny.
It is an escalation decision:
- can this stay in the normal review lane,
- or does it require AI platform, security, or product review before merge?

### Agent history view
For each agent, PromptDrift should show:
- baseline vs current profile,
- a timeline of major attribute changes,
- drift history,
- and governance context.

The product should feel native to GitHub and useful during review, not only after deployment.

The dashboard and history views should reinforce the PR workflow by showing what those PR-level decisions add up to over time.

---

## Why this matters

AI systems are often governed loosely, even when the surrounding software is governed strictly.

Prompts, tools, policies, and model settings can change quickly, and those changes can materially alter risk.

Organizations need a way to:
- preserve intended design,
- detect meaningful divergence,
- review changes with context,
- and maintain traceability,

without giving vendors access to sensitive production data.

PromptDrift exists to meet that need.

---

## Who this is for

PromptDrift is for teams building and governing AI systems in software organizations.

The primary first buyer is:
- a Head of AI Platform or Head of Engineering at a mid-size SaaS company shipping AI features

The primary day-to-day users are:
- senior backend and AI platform engineers responsible for prompts, model configs, tools, and agent wiring
- a smaller set of platform or security reviewers subscribed to high-risk AI changes

Secondary stakeholders may include:
- security teams,
- compliance and GRC stakeholders,
- and product owners responsible for AI behavior and change control.

The buyer values:
- low-friction adoption,
- GitHub-native workflows,
- privacy-preserving analysis,
- explainable risk signals,
- and governance visibility.

---

## Our design principles

### 1. GitHub-native first
If it cannot be grounded in GitHub-visible artifacts, it is not core to PromptDrift.

### 2. Static analysis with real business value
We do not apologize for being static-first.
Static design analysis is the product, not a temporary limitation.

### 3. Baseline over absolutes
We care more about movement from approved intent than about claiming perfect universal scoring.

### 4. Explainability over magic
Every score or summary should be traceable to visible changes.

### 5. Governance is a feature
Ownership, review, approval, and change history are first-class product inputs.

### 6. Privacy is part of the value proposition
We should not require customer runtime data to deliver meaningful insight.

### 7. Risk should be actionable
Signals should help reviewers decide what deserves attention right now.

---

## The standard we should hold ourselves to

A good PromptDrift result should help a reviewer answer:

- Is this AI system materially different from what we approved?
- Did guardrails get weaker or stronger?
- Did capability expand?
- Did autonomy increase?
- Is the governance around this change strong enough?
- Should this PR be escalated before merge?

And a good product trial should be able to prove:

- PromptDrift found real, non-obvious, high-impact AI changes in live repos
- those findings were difficult to catch in ordinary code review alone
- the visible PR noise remained low enough that engineers kept trusting the product

If we answer those well, we are building the right thing.

---

## The one-line version

PromptDrift makes AI system design drift visible in GitHub before it becomes a production problem.

---

## Relationship to the plan

This document defines the enduring purpose of the project.

`Plan.MD` should define:
- current execution priorities,
- milestones,
- phases,
- tasks,
- and delivery tracking.

If there is ever tension between a short-term task and this document, we should revisit the task.

The plan serves the soul.
The soul does not serve the plan.
