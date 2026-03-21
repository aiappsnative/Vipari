# Session Handoff: `feature/pr-escalation-v1`

## Status

This branch was created from `main` on 2026-03-21 to begin the next planned slice:

- branch: `feature/pr-escalation-v1`
- strategic docs already updated on `main`
- local working tree was clean before this handoff note was added

## Goal for the next session

Make the pull-request review workflow the clear product wedge by adding a high-signal escalation path:

- PromptDrift should still post a managed PR comment
- PromptDrift should also apply a GitHub label when escalation is warranted
- the first version should prefer precision over coverage
- avoid hard merge blocking for now

## Product decisions already locked in

- escalation mechanism: comment + label
- critical-surface model: hybrid defaults plus light overrides later
- dashboard role: equal-priority companion surface, but PR review remains the wedge
- baseline direction: latest explicitly approved version is the future authoritative baseline concept

## Recommended implementation order

### 1. Define the first escalation contract

Add a compact, explicit output model for:

- escalation decision (`normal_review` vs `escalate_before_merge`)
- escalation reason taxonomy
- optional reviewer target (`ai-platform`, `security`, `product`, or combined later)

Keep v1 opinionated and static-first. Start with a small taxonomy aligned to the roadmap:

- guardrail or policy weakening
- capability or blast-radius expansion
- autonomy increase
- governance bypass
- critical-surface modification

### 2. Wire label operations into the GitHub layer

Likely home:

- `services/github_integration.py`

Add focused helpers for issue/PR labels, ideally preserving the current managed-comment behavior.

Expected need:

- create/apply an escalation label to the PR issue
- keep behavior idempotent
- avoid touching unrelated labels

### 3. Attach escalation behavior to audit execution

Likely home:

- `services/audit_worker.py`

Suggested shape:

- compute escalation recommendation from deterministic analysis first
- include that recommendation clearly in the PR comment summary/body
- after posting the comment, upsert the escalation label when needed
- keep fallback behavior safe and explicit if label application fails

### 4. Add tests before widening scope

Likely tests to extend first:

- `tests/test_github_integration.py`
- `tests/test_audit_worker.py`

Target cases:

- applies label when escalation is required
- does not apply label for normal-review changes
- re-running an audit is idempotent for labels
- managed comment replacement still works
- fallback path remains correct if label posting fails after comment creation

## Likely files to touch first

Primary:

- `services/github_integration.py`
- `services/audit_worker.py`
- `tests/test_github_integration.py`
- `tests/test_audit_worker.py`

Possibly soon after:

- `engine/analysis.py`
- `engine/rules.py`
- `services/audit_records.py`
- `main.py` only if an API surface becomes necessary later

## Known current implementation anchors

- managed PR comment behavior already exists in `upsert_pr_comment()`
- job execution and fallback behavior live in `process_job()` / `_handle_fallback()`
- static drift summary injection already exists and should remain intact
- no dedicated PR label helper exists yet

## Acceptance criteria for v1

A strong first pass should:

- publish a clearer escalation recommendation in the managed PR comment
- apply an escalation label only for high-confidence escalation cases
- preserve current retry/fallback behavior
- preserve existing managed-comment replacement behavior
- add regression tests for comment + label orchestration

## Baseline verification completed tonight

- repo branch created successfully: `feature/pr-escalation-v1`
- smoke test from repo root passed:
  - `tests/test_main.py` → 9 passed

Note:

- a full-suite run was not captured cleanly tonight because an earlier terminal test invocation ended with an interruption while collecting tests; re-run the full suite tomorrow from repo root before or after the first implementation pass

## Suggested first move tomorrow

Start in this order:

1. implement label helper(s) and tests in `services/github_integration.py`
2. add escalation decision plumbing in `services/audit_worker.py`
3. update comment wording to surface escalation clearly
4. run targeted tests for worker + GitHub integration
5. then run the full suite from repo root
