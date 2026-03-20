# PromptDrift

PromptDrift is a GitHub App backend that audits pull requests for changes that can alter AI system behavior, especially prompts, guardrails, model-routing logic, tool access, and related policy artifacts.

The product direction is now explicitly GitHub-native and static-first: PromptDrift treats risk and drift as properties of prompts, policies, model settings, tool definitions, and agent wiring visible in code review, rather than as a runtime observability problem.

Its job is not just to say that “an AI file changed”, but to help reviewers answer the customer-critical question:

**Did this PR materially change how the AI behaves, what it is allowed to do, or what it may reveal?**

## Customer value

PromptDrift provides value by reducing the gap between ordinary code review and safe AI change review.

For customers, that means:

- catching risky prompt and guardrail changes before they reach production
- explaining *why* a change is risky, not just that a file changed
- preserving audit history so teams can reason about drift over time
- making AI review operationally practical inside the existing GitHub PR workflow
- providing a defensible review trail for security, compliance, and platform teams

In product terms, PromptDrift is moving toward becoming a **change intelligence layer for AI behavior**.

For the enduring product thesis behind that direction, see [SOUL.md](SOUL.md).

## Current status

The active branch has moved beyond the original MVP. The current system has been validated end to end against a private GitHub repository and now includes:

- working GitHub App authentication and bot-authored PR comments
- webhook ingestion with fast acknowledgement and queued audit execution
- deterministic drift analysis and structured semantic review packages
- retry and fallback behavior for transient model/provider failures
- opened-event PR diff fetching with transient `404` retry and synchronize-only exact commit-pair reconstruction
- atomic SQLite job claiming, failed-job requeue on webhook redelivery, and honest terminal failure states when persistence fails
- durable audit/history persistence in SQLite for local development
- artifact lineage and baseline-aware suppression for better risk judgment
- negation-aware suppression for restrictive prompt additions such as `Do not reveal ...` so obvious safety wording is not scored as risky drift
- managed PR comments that are replaced on PR updates so the timeline reflects the latest audit moment
- compact reviewer-facing comments with TLDR risk summaries and collapsible detail without duplicating the summary inside the expanded section
- first-pass static drift profiling for prompts/configs, including guardrail, capability, autonomy, creativity/stability, governance, and change-frequency attributes
- durable local persistence of static artifact profiles and baseline-linked drift deltas for changed AI artifacts
- reviewer-facing PR comments enriched with a compact static drift summary block when artifact snapshots are available
- repo-level static drift summaries and top-drifting artifact queries as first dashboard/read-side primitives

## What PromptDrift does today

- receives GitHub `pull_request` webhooks at `/webhook`
- verifies webhook signatures
- fetches private PR diffs using GitHub App installation auth
- retries transient opened-PR diff `404`s and reconstructs synchronize-event diffs from exact base/head commit trees to avoid stale PR snapshot races
- runs a fast AI relevance gate on the webhook path
- queues relevant audits for background execution
- claims queued jobs atomically so concurrent workers cannot double-process the same audit
- performs deterministic analysis of AI-relevant changes
- extracts a static agent attribute profile from prompt/config text so future audits can compare design-level drift against baselines
- stores static artifact profiles in audit history so later versions can compare against the previous known baseline
- injects a compact static drift summary into PR comments so reviewers can see guardrail/capability/autonomy movement against prior baselines
- exposes read-side trend helpers for repo summaries and artifact drift leaderboards
- prepares structured semantic review context for the LLM
- falls back to a deterministic preliminary audit when the model call is permanently unavailable
- posts a managed PR comment and replaces the previous managed comment on later PR updates
- persists audit, finding, artifact, and comment history for later analysis
- marks jobs failed instead of pretending success when comment posting or durable persistence breaks

## High-level architecture

- **Webhook path:** verify signature, fetch diff, run relevance gate, enqueue audit job
- **Worker path:** deterministic analysis, semantic review, retry/fallback handling, replace-on-update comment publishing, durable persistence
- **Static drift layer:** derive design attributes from prompts/configs and compare them to a baseline to measure design drift without runtime data
- **Persistence:** operational queue tables plus durable audit/history tables, artifact versions, and static profile records in one relational store for now

## Static drift profile model

The first implemented drift-engine slice introduces a static attribute model for GitHub-visible AI artifacts.

The current profile dimensions are:

- `guardrail_robustness`
- `capability_risk`
- `autonomy_level`
- `stability_vs_creativity`
- `governance_strength`
- `change_frequency`
- `semantic_density`

These are computed from static signals such as:

- instruction and constraint density (`must`, `never`, `do not`, `always`)
- explicit limits (`up to`, `above`, `max`, bounded authority wording)
- tool and privilege wording (read vs write, production vs sandbox, sensitive systems)
- autonomy markers (steps, loops, parallelism, human approval hints)
- model settings such as `temperature` and `top_p`
- governance inputs such as CODEOWNERS requirements, review strength, and recent churn

This gives PromptDrift a concrete foundation for future baseline comparison, trend analysis, and PR-facing drift summaries without relying on runtime telemetry.

## Requirements

- Python 3.11+
- A GitHub App installed on the repository you want to audit
- An Azure OpenAI or compatible Foundry endpoint
- ngrok for local webhook testing

## Environment setup

Copy [.env.example](.env.example) to `.env` and fill in your real values.

Required variables:

- `GITHUB_APP_ID`
- `GITHUB_PRIVATE_KEY_PATH`
- `GITHUB_WEBHOOK_SECRET`
- `OPENAI_API_KEY` or `FOUNDRY_API_KEY`
- `AZURE_OPENAI_ENDPOINT`

Optional variables:

- `AI_MODEL` (defaults to `gpt-4o`)
- `FOUNDRY_PROJECT_ENDPOINT`
- `GITHUB_PAT`
- `NGROK_AUTHTOKEN`

## Installation

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the service locally:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Local end-to-end testing

1. Start the FastAPI app.
2. Start ngrok and expose port `8000`.
3. Point the GitHub App webhook URL to `https://<your-ngrok-host>/webhook`.
4. Open or update a pull request containing AI-relevant changes.
5. Confirm PromptDrift posts a PR comment.

The helper script [scripts/verify_credentials.py](scripts/verify_credentials.py) can be used to validate the local credential setup before testing.

Recent live validation on the active branch covered:

- risky opened PR flow with durable audit persistence and bot comment posting
- synchronize re-audit flow with exact-SHA diff reconstruction and managed comment replacement
- non-AI PR flow returning `no relevant changes` without queueing an audit
- invalid-model fallback flow posting a deterministic preliminary comment and recording `fallback_posted`

## Known limitations

- signal fusion between deterministic and semantic evidence is still early-stage
- the queue and durable store are still local SQLite in the current dev shape
- no customer dashboard or operator UI yet
- no production deployment packaging or multi-tenant control plane yet
- AI relevance and policy coverage should continue expanding beyond the current rule set
- nuanced fusion between deterministic and semantic outputs still needs refinement beyond the current negation-aware guardrail suppression

## Safe repo practices

- Do not commit `.env`
- Do not commit private key files
- Use [.env.example](.env.example) as the only committed environment template

## Next planned focus

The next major workstreams are:

- improve signal fusion between deterministic findings and semantic review
- expand read-side history and trend analysis capabilities
- refresh product and architecture docs to match the real implemented system
- continue the path from local/dev architecture toward production-grade persistence and dashboarding
- plan for a future `audit-feedback-loop-v1` workflow to capture customer feedback and PR outcomes for evaluation and engine improvement
- plan for a future `customer-onboarding-baseline-v1` workflow to establish repository baselines and AI artifact inventories at install time