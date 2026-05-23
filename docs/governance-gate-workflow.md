# Governance Gate Workflow

Vipari now exposes a stable governance-decision contract for persisted PR audits on three read surfaces:

- session-auth dashboard API: `/api/repos/{owner/repo}/governance-decision`
- admin-token API: `/api/repos/{owner/repo}/governance-decision`
- control-plane machine-auth API: `/cp/workspaces/{workspace_id}/repos/{owner/repo}/governance-decision`

Each response includes:

- `conclusion`: `success`, `neutral`, or `failure`
- `recommended_gate`: `pass`, `warn`, or `block`
- `recommended_exit_code`: integer suitable for shell or workflow exit behavior
- `governance_decision`: the underlying rollout-mode, lane, and rationale details

The checked-in runner [scripts/governance_gate.py](../scripts/governance_gate.py) calls one of those endpoints and exits with the recommended code. When a GitHub token is available, it can also post a commit status or a completed check run back onto the evaluated head SHA.

Vipari's worker can also post the same governance signal automatically during normal PR audit processing when `GOVERNANCE_STATUS_ROLLOUT_MODE` or `GOVERNANCE_CHECK_RUN_ROLLOUT_MODE` is enabled. Both default to `off`, so worker-side GitHub actuation remains opt-in. When pre-audit relevance review decides a PR does not need full audit, the worker can emit a neutral skipped governance check run. When a transient model failure forces a retry, the worker can emit an in-progress governance check run and later update that same check run to its completed outcome instead of creating duplicates. When semantic review falls back to deterministic-only output, the worker can emit a neutral fallback governance check run instead of leaving the PR silent.

## Example: GitHub Actions

This example assumes:

- Vipari is reachable at `VIPARI_BASE_URL`
- the workflow can authenticate with an admin token
- a persisted PR audit already exists for the current PR head SHA

```yaml
name: vipari-governance-gate

on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  governance-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Run Vipari governance gate
        env:
          VIPARI_BASE_URL: ${{ secrets.VIPARI_BASE_URL }}
          VIPARI_ADMIN_TOKEN: ${{ secrets.VIPARI_ADMIN_TOKEN }}
          GITHUB_STATUS_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_CHECK_RUN_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        run: |
          python scripts/governance_gate.py \
            "${VIPARI_BASE_URL}/api/repos/${{ github.repository }}/governance-decision" \
            --pr-number "${PR_NUMBER}" \
            --head-sha "${HEAD_SHA}" \
            --rollout-mode enforce \
            --admin-token "${VIPARI_ADMIN_TOKEN}" \
            --github-status-token "${GITHUB_STATUS_TOKEN}" \
            --github-status-context "vipari/governance-gate" \
            --github-check-run-token "${GITHUB_CHECK_RUN_TOKEN}" \
            --github-check-run-name "Vipari Governance"
```

Behavior:

- `recommended_gate=pass` returns exit code `0`
- `recommended_gate=warn` returns exit code `0`
- `recommended_gate=block` returns exit code `1`
- transport or contract errors return exit code `2`

That means `enforce` mode can fail the workflow directly, while `dry_run` still leaves room for visibility without merge blocking.

When `--github-status-token` is supplied, the runner also posts a standard GitHub commit status to the evaluated `head_sha`:

- `conclusion=success` or `neutral` maps to commit status `success`
- `conclusion=failure` maps to commit status `failure`
- any malformed or incomplete response maps to script exit code `2` instead of posting a partial status

When `--github-check-run-token` is supplied, the runner also posts a completed GitHub check run to the evaluated `head_sha`:

- `conclusion=success`, `neutral`, and `failure` map directly to the check-run conclusion
- the check-run title follows the governance gate: `pass`, `warn`, or `block`
- `governance_decision.rationale` and top evidence items are rendered into the check-run body when present

## Control-plane Variant

When a caller already has a control-plane JWT, use the CP route instead:

```bash
python scripts/governance_gate.py \
  "https://vipari.example.com/cp/workspaces/42/repos/org/repo/governance-decision" \
  --pr-number 84 \
  --head-sha sha-governance-84 \
  --rollout-mode enforce \
  --bearer-token "$VIPARI_CP_TOKEN"
```

## Guidance

- Use `dry_run` when you want an advisory governance signal without failing CI.
- Use `enforce` when the workflow should fail on `block_merge` decisions.
- Set `GOVERNANCE_STATUS_ROLLOUT_MODE=dry_run|warn|enforce` when the worker should publish governance commit statuses automatically during PR audits.
- Use `GOVERNANCE_STATUS_CONTEXT` to override the default worker-side commit-status context `vipari/governance`.
- Set `GOVERNANCE_CHECK_RUN_ROLLOUT_MODE=dry_run|warn|enforce` when the worker should publish completed GitHub check runs for governance outcomes.
- Use `GOVERNANCE_CHECK_RUN_NAME` to override the default worker-side check-run name `Vipari Governance`.
- Worker-side pre-audit skips use the same check-run rollout and can surface a neutral governance check when Vipari decides a PR does not need full audit.
- Worker-side retried audits use the same check-run rollout and can surface an in-progress governance check while Vipari is waiting to retry semantic review.
- Worker-side retry and completion updates reuse the same in-progress check run for a given name and head SHA so GitHub checks do not accumulate duplicate entries.
- Worker-side fallback audits use the same check-run rollout and surface a neutral fallback governance check when full semantic review is unavailable.
- Use `--github-status-token` when you want the same governance decision to show up directly in the PR checks/status area.
- Use `--github-check-run-token` when the runner should publish a richer GitHub check run with neutral `warn` outcomes plus rationale evidence.
- Prefer the top-level `conclusion`, `recommended_gate`, and `recommended_exit_code` fields for automation.
- Use `governance_decision.rationale` for human-readable logs or workflow summaries.