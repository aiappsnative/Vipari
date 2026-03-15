# PromptDrift

PromptDrift is a GitHub App backend that audits pull requests for changes that can alter AI system behavior, especially prompts, guardrails, model-routing logic, tool access, and related policy artifacts.

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

## Current status

The active branch has moved beyond the original MVP. The current system has been validated end to end against a private GitHub repository and now includes:

- working GitHub App authentication and bot-authored PR comments
- webhook ingestion with fast acknowledgement and queued audit execution
- deterministic drift analysis and structured semantic review packages
- retry and fallback behavior for transient model/provider failures
- durable audit/history persistence in SQLite for local development
- artifact lineage and baseline-aware suppression for better risk judgment
- managed PR comments that are replaced on PR updates so the timeline reflects the latest audit moment
- compact reviewer-facing comments with TLDR risk summaries and collapsible detail without duplicating the summary inside the expanded section

## What PromptDrift does today

- receives GitHub `pull_request` webhooks at `/webhook`
- verifies webhook signatures
- fetches private PR diffs using GitHub App installation auth
- reconstructs synchronize-event diffs from exact base/head commit trees to avoid stale PR snapshot races
- runs a fast AI relevance gate on the webhook path
- queues relevant audits for background execution
- performs deterministic analysis of AI-relevant changes
- prepares structured semantic review context for the LLM
- posts a managed PR comment and replaces the previous managed comment on later PR updates
- persists audit, finding, artifact, and comment history for later analysis

## High-level architecture

- **Webhook path:** verify signature, fetch diff, run relevance gate, enqueue audit job
- **Worker path:** deterministic analysis, semantic review, retry/fallback handling, replace-on-update comment publishing, durable persistence
- **Persistence:** operational queue tables plus durable audit/history tables in one relational store for now

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

## Known limitations

- signal fusion between deterministic and semantic evidence is still early-stage
- the queue and durable store are still local SQLite in the current dev shape
- no customer dashboard or operator UI yet
- no production deployment packaging or multi-tenant control plane yet
- AI relevance and policy coverage should continue expanding beyond the current rule set

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