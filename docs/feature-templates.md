# Feature Templates

This file provides lightweight scaffolds for new feature branches and their expected deliverables.

Use these templates when creating new `feature/*` branches to keep work consistent and review-friendly.

## Minimal feature branch scaffold

- Branch name: `feature/<short-name>-v1`
- Purpose: one-line summary of customer value
- Scope: bullet list of 3–6 concrete deliverables
- Tests: list of critical unit/integration tests to add
- Docs: `Plan.MD` entry + README change if public behavior changes
- Migration/backfill: yes/no

## Example: `feature/drift-engine-v1`

- Purpose: collect historic and ongoing audit data to analyze drift over time (trend, baseline, evolution)
- Scope:
  - ingest artifact baselines at install time
  - optionally backfill historical commits for selected repos
  - store per-artifact version history and signal-term index
  - read-side queries for trend analysis (per-repo, per-artifact)
  - tests for baseline capture and artifact-version links
- Tests: fixture-based artifact history tests; end-to-end ingest smoke test
- Docs: `Plan.MD` feature entry and `docs/detection-engine-plan.md` update
