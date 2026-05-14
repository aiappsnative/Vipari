# Issue 4: Audit Feedback Loop V1 Plan

Branch: `feature/audit-feedback-loop-v1`

## Goal

Persist structured feedback signals tied to each PR audit episode so Vipari can later analyze whether reviewers found the output useful and whether teams followed or overrode the recommendation.

This slice is capture and modeling only. It does not change risk thresholds, auto-retrain prompts, or expose customer-facing analytics.

## Current anchors in the codebase

- PR lifecycle fields already exist on `pull_request_audits` in [services/audit_records.py](../services/audit_records.py): `pr_state`, `pr_merged`, `pr_closed_at`, `pr_merged_at`, `pr_merge_commit_sha`, `pr_updated_at`.
- Managed GitHub outputs already persist through `audit_comments` in [services/audit_records.py](../services/audit_records.py), including `github_comment_id` and `github_review_id`.
- Webhook ingress in [main.py](../main.py) currently handles only `pull_request`, `push`, `installation`, and `installation_repositories` events.
- PR close and merge state updates already flow through `update_pull_request_audit_state(...)` in [main.py](../main.py).
- Existing operator access patterns already live in [scripts/repo_ops.py](../scripts/repo_ops.py), which is the lowest-friction place to add inspection/export commands.

## Proposed scope

### 1. Append-only feedback event storage

Add a new persistence surface in [services/audit_records.py](../services/audit_records.py):

- `audit_feedback_events`
  - `id`
  - `audit_id`
  - `repo_full`
  - `pr_number`
  - `head_sha`
  - `kind` (`reaction`, `explicit_feedback`, `pr_outcome`)
  - `source` (`github_reaction`, `feedback_link`, `lifecycle`)
  - `actor_github_id` nullable
  - `actor_github_login` nullable
  - `event_key` nullable unique-ish dedupe token
  - `payload_json`
  - `created_at`

Design constraints:

- Keep this table append-only.
- Do not store derived scores or verdicts here.
- Dedupe by stable `event_key` where possible so webhook replays and refresh passes are safe.

### 2. Direct feedback capture

Two channels should be supported in this version.

#### 2a. GitHub reactions on managed outputs

Capture reactions tied to Vipari-managed PR output.

Preferred path:

- If GitHub App webhook support for the needed reaction event is available in our installed app configuration, ingest those events directly.

Fallback path:

- Use the persisted `github_comment_id` and `github_review_id` to run a bounded refresh against the GitHub reactions API after initial posting and again in a delayed refresh window.

Planned v1 behavior:

- Record reaction content (`+1`, `-1`, `heart`, `eyes`, etc.).
- Record actor login/id and timestamp.
- Support initial capture and one delayed refresh window.
- Ignore reactions on non-managed comments.

#### 2b. Explicit feedback link

Add a simple feedback endpoint in [main.py](../main.py) and include its URL in managed output.

- Route shape:
  - `GET /feedback/pr/{owner}/{repo}/{number}` renders a minimal form.
  - `POST /feedback/pr/{owner}/{repo}/{number}` persists feedback.
- Query/body fields:
  - `audit_id`
  - `sentiment` (`helpful`, `noisy`, `strongly_disagree`)
  - optional `notes`

Persistence:

- Store each submission as an `explicit_feedback` event in `audit_feedback_events`.
- Keep the free text inside `payload_json` rather than creating a separate table in v1.

Guardrails:

- Endpoint should validate that the referenced audit exists.
- Endpoint should degrade safely if feedback is disabled or malformed.
- No auth-heavy operator workflow is required for the first slice, but the endpoint must not allow arbitrary audit spoofing without an audit lookup and bounded accepted fields.

### 3. Indirect feedback from PR outcomes

Build derived outcome events from the lifecycle information we already persist.

Approach:

- Extend `update_pull_request_audit_state(...)` usage with a helper that records a `pr_outcome` event when a previously open audit episode reaches a stable post-comment state.
- Tie outcome derivation to `repo_full`, `pr_number`, `head_sha`, and the audit's final recommendation lane.

Initial derived labels:

- `recommendation_followed`
- `recommendation_ignored`
- `aligned_merge`
- `aligned_reject`
- `unknown`

Rules should remain coarse in v1:

- If Vipari escalated before merge and the PR merged anyway, classify as `recommendation_ignored`.
- If Vipari escalated and the PR closed without merge, classify as `aligned_reject`.
- If Vipari stayed in normal review and the PR merged, classify as `aligned_merge`.
- If Vipari stayed in normal review and the PR closed without merge, classify as `recommendation_followed` only if we later add a stronger non-escalation recommendation type; until then prefer `unknown` over overclaiming.

### 4. Operator access

Add a narrow operator path in [scripts/repo_ops.py](../scripts/repo_ops.py):

- `feedback-events --repo owner/repo`
- optional filters:
  - `--audit-id`
  - `--kind`
  - `--limit`
  - `--output`

Output format:

- default JSON to stdout
- optional JSON file output

CSV can wait unless export consumers immediately need it.

## Recommended implementation slices

### Slice 1: Persistence foundation

- add `audit_feedback_events` table and record/list helpers
- add tests for migration, insert, dedupe, and list-by-repo/audit

### Slice 2: Explicit feedback link

- add feedback URL rendering to managed output footer
- add GET/POST endpoint in [main.py](../main.py)
- persist `explicit_feedback` events
- add tests for valid submit, invalid audit id, and disabled/malformed cases

### Slice 3: Outcome capture

- add a helper that derives and records `pr_outcome` events from close/merge transitions
- call it from the existing PR webhook lifecycle path
- add tests for merge, close-without-merge, reopen, and dedupe

### Slice 4: Reaction capture

- add GitHub integration helpers for listing reactions on managed comment/review artifacts
- choose direct webhook ingestion if available, otherwise implement bounded refresh capture
- add tests for managed comment filtering, dedupe, and delayed refresh

### Slice 5: Operator inspection

- add `repo_ops.py` feedback listing/export command
- add focused CLI tests

## Open questions to resolve before implementation starts

- Whether the installed GitHub App configuration can receive the exact reaction events we need, or whether polling/refresh is the correct v1 capture path.
- Whether feedback links should be publicly accessible with an opaque token, or limited to signed/internal links. For v1, a bounded audit lookup plus minimal accepted fields may be sufficient, but the route design should leave room for signed tokens.
- Whether reactions on formal PR reviews are available via the same path we use for issue comments, or whether v1 should only guarantee reaction capture for managed issue comments first.

## Out of scope

- changing risk thresholds based on feedback
- automatic retraining or prompt tuning
- customer-facing analytics dashboards
- generalized sentiment analysis over free-text feedback

## Exit criteria

- feedback events persist for explicit submissions and PR lifecycle outcomes
- reaction ingestion is implemented through either webhook or refresh capture with dedupe
- operator tooling can list/export feedback events for a repo or audit id
- regression tests cover the new persistence and lifecycle paths