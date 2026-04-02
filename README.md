# PromptDrift

PromptDrift is a GitHub App backend that audits pull requests for changes that can alter AI system behavior, especially prompts, guardrails, model-routing logic, tool access, and related policy artifacts.

The product direction is now explicitly GitHub-native and static-first: PromptDrift treats risk and drift as properties of prompts, policies, model settings, tool definitions, and agent wiring visible in code review, rather than as a runtime observability problem.

Its job is not just to say that “an AI file changed”, but to help reviewers answer the customer-critical question:

**Did this PR materially change how the AI behaves, what it is allowed to do, or what it may reveal?**

Near-term, the product is being shaped around one especially important follow-up question:

**Does this AI-related PR need escalation before merge?**

## Customer value

PromptDrift provides value by reducing the gap between ordinary code review and safe AI change review.

For customers, that means:

- catching risky prompt and guardrail changes before they reach production
- explaining *why* a change is risky, not just that a file changed
- preserving audit history so teams can reason about drift over time
- making AI review operationally practical inside the existing GitHub PR workflow
- providing a defensible review trail for security, compliance, and platform teams

In product terms, PromptDrift is moving toward becoming a **change intelligence layer for AI behavior**.

The product wedge is the PR review workflow. Dashboard and history views remain important, but they should reinforce PR-level decisions rather than replace them.

For the enduring product thesis behind that direction, see [SOUL.md](SOUL.md).

## Current status

The current `main` branch now includes the merged static-first drift engine milestone plus the follow-on escalation, approved-baseline, repo-provenance, and dashboard UX hardening slices.

In practical terms, PromptDrift currently provides:

- queue-backed GitHub App PR auditing with deterministic analysis, semantic review, retry/fallback behavior, and managed PR comments
- escalation-aware PR review with managed comments plus GitHub labels for high-confidence before-merge escalation cases
- persisted pull-request lifecycle state across audit jobs and durable audit records, including close/reopen and merge metadata
- approved-baseline-aware static drift profiling for prompts, configs, and related AI control surfaces
- onboarding and selective historical backfill for repository-level artifact inventories and profile history
- a triage-first dashboard surface with portfolio Triage/Coverage modes and repo case-file drill-down pages, including baseline provenance in repo/history views
- landed drift views driven by approved baselines plus merged-history evidence, while proposal-only PR audit evidence remains separate from landed-history posture
- repo-detail provenance links that route directly to the backing PR or commit when stored source context exists
- concise `What changed`, `Why flagged`, and `Where` explanations in both overview and repo dashboard surfaces
- baseline-vs-current posture detail with qualitative drift labels, per-attribute findings, and code-level evidence when stored snapshots are available
- a lightweight baseline-promotion action that lets operators promote the latest stored source version as the approved baseline for an artifact
- real OSS onboarding validation against `doria90/openfang` and `doria90/hermes-agent`, including larger-repo historical backfill and dashboard rendering
- bounded large-repo onboarding through narrower candidate-path discovery and direct GitHub contents API fetches for artifact snapshots
- a local operator CLI and JSON APIs for onboarding, backfill, and dashboard inspection

For detailed roadmap status, see [Plan.MD](Plan.MD). For architecture details, see [docs/detection-engine-plan.md](docs/detection-engine-plan.md).

The dashboard should now be read as two linked product surfaces:

- `/dashboard` is the portfolio decision surface for triage, hotspots, and coverage trust, with a secondary coverage mode for inventory and pattern scans
- `/dashboard/{owner/repo}` is the repo case file for baseline-relative posture, prioritized review targets, lower-confidence findings, artifact-level evidence, and approved-baseline promotion

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
- stores static artifact profiles in audit history with explicit baseline provenance so later versions can compare against an approved baseline when available
- keeps PR comments reviewer-focused on risk, escalation, and recommendation rather than internal drift metrics
- applies a GitHub escalation label for high-confidence before-merge escalation cases
- exposes read-side trend helpers for repo summaries and artifact drift leaderboards
- supports baseline-first repository onboarding that persists discovered AI artifacts and baseline versions
- supports selective historical backfill planning and execution for onboarded artifacts
- persists historical artifact versions and static profile lineage for backfilled snapshots
- exposes baseline provenance in dashboard and history read models so fallback vs approved authority is visible outside the PR comment
- stores baseline, historical, and PR snapshot content so dashboard explanations can attach code-level evidence to posture drift
- exposes JSON query APIs for repository listings and unified dashboard payloads
- exposes an overview dashboard API at `GET /api/dashboard/overview`
- exposes local dashboard pages at `/dashboard` and `/dashboard/{owner/repo}`
- includes `scripts/repo_ops.py` for local operator workflows and read-side inspection
- prepares structured semantic review context for the LLM
- falls back to a deterministic preliminary audit when the model call is permanently unavailable
- posts a managed PR comment and replaces the previous managed comment on later PR updates
- persists audit, finding, artifact, and comment history for later analysis
- updates stored PR lifecycle state on `opened`, `synchronize`, `closed`, and `reopened` webhook flows without leaving stale close/merge timestamps behind
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
- PR close and reopen lifecycle validation with durable state updates and stale timestamp clearing
- non-AI PR flow returning `no relevant changes` without queueing an audit
- invalid-model fallback flow posting a deterministic preliminary comment and recording `fallback_posted`

## Local operator and dashboard testing

Once the app is running locally, you can inspect the current drift dashboard in the browser:

- `/dashboard` — triage-first portfolio inbox with a primary review target, ranked queue, coverage trust, and a secondary coverage scan mode
- `/dashboard/<owner>/<repo>` — repo case file with one featured review target, ranked follow-on queue, posture/provenance context, and collapsed history inventory

Recommended 5-minute local inspection flow:

1. Open `/dashboard` first and confirm the portfolio risk-state hero, featured review target, ranked queue, and coverage-trust panels render.
2. Switch to Coverage mode and confirm the coverage atlas, control-surface coverage, and repo inventory capsules render.
3. Open `/dashboard/<owner>/<repo>` for a seeded repository and confirm the featured insight, repo command deck, posture explorer, and collapsed history inventory render.
4. In the repo case file, confirm the provenance links open the backing PR or commit, the posture explorer shows per-attribute findings, and the baseline action is available when a stored source version exists.
5. If the local data store is sparse or an older API payload is still being served, the frontend should degrade gracefully instead of throwing browser errors.

You can also inspect or drive the workflow locally with the CLI:

```bash
python scripts/repo_ops.py list-repos
python scripts/repo_ops.py persistence-status
python scripts/repo_ops.py dashboard owner/repo
python scripts/repo_ops.py onboard owner/repo <installation_id> --plan-backfill --execute-backfill
python scripts/repo_ops.py backfill owner/repo <installation_id>
python scripts/repo_ops.py list-eval-candidates
python scripts/repo_ops.py eval-run openfang <installation_id> --run-label main-openfang --compare-to artifacts/oss-evals/main/doria90-openfang/main-openfang/run-package.json
python scripts/repo_ops.py eval-compare path/to/current-run-package.json path/to/baseline-run-package.json
```

The OSS evaluation harness writes repeatable run packages under `artifacts/oss-evals/` by default. Each package includes onboarding and baseline summaries, optional backfill results, saved repo and overview dashboard payloads, ranked review targets, and a fixed evaluator rubric so branch-to-branch comparison stays lightweight but reproducible.

Checked-in reference artifacts for past live validation can also appear under `live/oss-evals/` when a snapshot is intentionally preserved for handoff or comparison.

Useful JSON endpoints:

- `GET /api/persistence`
- `GET /api/repos`
- `GET /api/dashboard/overview`
- `GET /api/repos/{owner/repo}/dashboard`
- `POST /api/repos/{owner/repo}/onboard`
- `POST /api/repos/{owner/repo}/backfill`
- `POST /api/repos/{owner/repo}/artifacts/{artifact_path}/baseline`

Operational note:

- local SQLite may create `promptdrift.db-wal` and `promptdrift.db-shm` sidecar files while the server is running; these are ignored and can be removed once local uvicorn processes are stopped

## Known limitations

- the current backend is still SQLite, but persistence metadata now makes the logical boundary explicit: operational queue tables vs durable audit/history tables, with PostgreSQL remaining the production target
- the dashboard is now structurally ready for OSS validation, but landed posture intentionally depends on approved baselines plus merged-history evidence rather than proposal-only PR audits
- larger public repos now onboard successfully, but discovery precision and reviewer-target quality from merged-history evidence still need continued refinement
- no production deployment packaging or multi-tenant control plane yet
- AI relevance coverage and deterministic/semantic signal fusion still need refinement
- PR review, dashboard prioritization, and landed-history narratives still need tighter synthesis so proposal-only evidence is visible without contaminating merged-history drift

## Safe repo practices

- Do not commit `.env`
- Do not commit private key files
- Use [.env.example](.env.example) as the only committed environment template

## Roadmap and deeper design docs

- [Plan.MD](Plan.MD) tracks milestone status, near-term feature order, and future workflows
- [docs/detection-engine-plan.md](docs/detection-engine-plan.md) captures the detection-engine architecture and implementation snapshot
- [docs/drift-profile-design-spec.md](docs/drift-profile-design-spec.md) describes the static drift-profile layer in more depth