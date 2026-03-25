# Feature Templates

This file provides working scaffolds for future `feature/*` branches.

Use it to keep each slice product-first, reviewable, and easy to clean up after merge.

## Default feature branch template

- Branch name: `feature/<short-name>-v1`
- Customer outcome: one sentence describing what gets materially easier, safer, or more trustworthy
- Product hypothesis: what should improve for the reviewer or operator if this branch succeeds
- Non-goals: 2–5 bullets describing adjacent work that should stay out of scope
- Scope: 3–7 concrete deliverables
- Evidence / evaluation plan:
  - what seeded or real scenarios must improve
  - what output or payload should be compared before vs after
  - what would count as a regression
- Tests:
  - critical unit coverage
  - critical integration or read-model coverage
  - any manual validation steps that still matter
- Docs to update:
  - `Plan.MD`
  - `README.md` if public behavior changes
  - deeper design docs affected by the slice
  - `CHANGELOG.md` after merge-ready behavior is confirmed
- Migration / backfill impact: yes or no, with one short note
- Cleanup obligations:
  - temporary diagnostics to remove before merge
  - stale roadmap or handoff docs to archive or refresh
  - branch deletion expectations after merge
- Exit criteria: 3–5 bullets describing what must be true before the slice is considered complete

## Short planning prompt

Before implementation starts, answer these questions:

1. What reviewer decision becomes better after this branch?
2. What evidence will prove the improvement on a real or seeded repo?
3. Which surfaces change: PR comment, dashboard, onboarding, persistence, or docs?
4. What should explicitly remain unchanged?
5. What follow-on branch should become easier after this one lands?

## Example: `feature/repo-evidence-v1`

- Customer outcome: repo case files point reviewers to more trustworthy PR or merged-change targets instead of relying mainly on historical hotspots
- Product hypothesis: denser provenance and source attribution will make dashboard urgency feel actionable rather than merely interesting
- Non-goals:
  - no major dashboard chrome redesign
  - no production persistence work
  - no full signal-fusion redesign
- Scope:
  - improve merged-commit and PR linkage in repo detail read models
  - enrich reviewer-facing provenance context for top-ranked artifacts
  - keep lower-confidence findings from diluting the primary review queue
  - add regression coverage for source-link and reviewer-target payloads
- Evidence / evaluation plan:
  - compare before vs after repo-detail output on the current OSS validation repos
  - confirm top review targets have clearer source attribution
  - treat broken or weaker review-target routing as a regression
- Tests:
  - dashboard/read-model regression coverage
  - provenance payload coverage
  - manual inspection of overview and repo case-file ranking on seeded or OSS data
- Docs to update:
  - `Plan.MD`
  - `docs/detection-engine-plan.md`
  - `CHANGELOG.md`
- Migration / backfill impact: no schema migration expected in the smallest pass; history reuse should remain compatible with existing data
- Cleanup obligations:
  - archive stale handoff notes once the slice merges
  - remove any temporary inspection-only debug output
- Exit criteria:
  - repo case files show stronger review targets
  - provenance links and labels remain stable on old data
  - OSS validation shows better actionability, not just more fields
