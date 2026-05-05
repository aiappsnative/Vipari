# Issue #86 Manual QA Checklist

This checklist is the execution companion to the issue #86 plan.

Record each check with evidence, even when it passes.

## Phase 1: Setup And Evidence Preparation

- Confirm commit SHA under review.
- Confirm local branch and working tree are clean.
- Confirm required env vars and provider credentials for the intended QA slice.
- Prepare evidence folders for screenshots, payload notes, and runtime logs.
- Prepare workspace fixtures covering every required setup-aware state.
- Prepare one active workspace and one `dummyAI`-backed repo for end-to-end audit rehearsal.
- Confirm whether this pass uses local-only, mocked-provider, or live-provider execution for each slice.

## Phase 2: Public Entry And Auth Flow

- Verify `/` loads and matches current product branding.
- Verify `/pricing` plan names and compatibility notes are correct.
- Verify `/login` renders correct GitHub handoff language.
- Verify login error states render intelligible messages.
- Verify `/auth/github/start` preserves source and plan context when applicable.
- Verify `/auth/github/callback` routes users into the correct setup-aware path.
- Verify `/logout` clears session cleanly.

## Phase 3: Setup-Aware Control-Plane Flow

- Verify `/app` redirects correctly for unauthenticated users.
- Verify `/app` sends no-workspace users to workspace bootstrap.
- Verify `/app` sends no-subscription or pending-billing users to billing.
- Verify `/app` sends no-install users to install flow.
- Verify `/app` sends no-onboarded-repo users to repo setup.
- Verify `/app` handles comments-only workspaces exactly as documented.
- Verify `/app` sends fully active users to a dashboard-eligible path.

## Phase 4: Workspace Pages And Shell Quality

- Verify `/app/workspaces/new` copy, form defaults, and submit path.
- Verify `/app/profile` display-name and theme behavior.
- Verify `/app/settings` role-specific controls, forms, and summary cards.
- Verify `/app/policies` placeholder page is still polished and non-broken.
- Verify `/app/help` context-sensitive guidance is credible and useful.
- Verify `/app/admin` is only available to the intended identity.
- Verify sidebar, nav highlighting, profile link, and theme shell consistency across all authenticated pages.

## Phase 5: Billing, Install, And Repo Setup

- Verify `/app/billing` current plan, subscription state, and CTA correctness.
- Verify `/app/billing/claim` state handling and claim activation flow.
- Verify `/app/billing/portal` permission enforcement and redirect behavior.
- Verify `/app/setup/install` install CTA, callback wording, and manual link fallback.
- Verify `/app/repos` inventory cards, onboarding status, sort controls, and summary cards.
- Verify allocate and onboarding flows only appear for allowed roles.
- Verify connected-versus-onboarded distinctions are visible to operators.

## Phase 6: MCP And Customer Package Flow

- Verify `/app/integrations/mcp` renders correctly for owner, edit, and read roles.
- Verify API-key tab routing from `/app/settings/api-keys`.
- Verify machine-principal creation flow and one-time secret handoff behavior.
- Verify revoke flow behavior and audit visibility.
- Verify package download works from `/app/integrations/mcp/download`.
- Inspect zip contents for expected files only.
- Confirm README, env example, config example, and manifest agree on variable names and tool names.
- Run connector configuration in a controlled environment and verify token exchange.
- Verify all documented `vipari.*` tools can be listed and invoked with valid credentials.
- Verify invalid, revoked, or mis-scoped credentials fail safely and intelligibly.
- Verify legacy PromptDrift compatibility paths still behave as documented.

## Phase 7: Dashboard Overview And Repo Case File

- Verify `/dashboard` loads for an active, dashboard-eligible workspace.
- Verify first-load queue and selection behavior.
- Verify repo atlas, escalation queue, and posture strips render without layout breakage.
- Verify blocked-shell behavior on non-eligible workspaces.
- Verify `/dashboard/{repo_full}` loads for an onboarded repo.
- Verify each repo tab: drift, version-control, baseline, compliance, reports.
- Verify detail panel readability and recommended action quality.
- Verify deep links using `artifact`, `pr`, and `head_sha` parameters.
- Verify storyline, evidence, and baseline messaging remain coherent.
- Verify no broken skeletons, duplicate sections, or dead-end nav items.

## Phase 8: Compliance And Exports

- Verify `/app/compliance` readiness verdict and KPI row.
- Verify `/app/compliance/frameworks` framework cards and explanatory content.
- Verify `/app/compliance/exports` export setup form, status messages, and history.
- Verify `/app/compliance/evidence` filters, evidence freshness labels, and empty states.
- Trigger a compliance export and verify job creation.
- Verify export download path and access restrictions.
- Confirm governance and freshness language is consistent across views.

## Phase 9: DummyAI PR-Audit Rehearsal

- Confirm `dummyAI` can be connected to the active test workspace.
- Confirm installation, repo connection, and allocation state.
- Run or confirm onboarding and history backfill.
- Verify baseline-approved or pending-baseline state is visible.
- Use PR #43 or an equivalent controlled PR path to validate audit behavior.
- Confirm audit result storage and surfacing.
- Verify resulting review context appears in overview dashboard.
- Verify resulting review context appears in repo case-file view.
- Verify provenance, severity, and recommended action quality.
- Verify landed-drift versus PR-lifecycle behavior remains intelligible.

## Phase 10: Runtime And Deployment Readiness

- Verify `/health` on the running app.
- Verify `/health/ready` reports the expected status in healthy conditions.
- Verify readiness output changes appropriately under misconfiguration where safe to test.
- Verify `scripts/railway_preflight.py` still matches the blessed deployment model.
- Verify `scripts/db_migrate.py` flow and docs alignment for production-like paths.
- Verify helper scripts still read as non-production-only.
- Verify webhook and worker role assumptions are still aligned with docs.

## Phase 11: UI Polish Pass

- Check desktop-width layouts for all primary pages.
- Check mobile-width or narrow viewport layouts for all primary pages.
- Check dark and light modes where supported.
- Check visible focus states on buttons, tabs, links, and form controls.
- Check empty states and loading states for clarity and polish.
- Check copy for stale product names, placeholders, or misleading capability claims.
- Check consistency of icon usage, spacing, and typography across dashboard and control-plane shells.

## Phase 12: Automation Coverage Audit

- Map route families to existing tests.
- Identify which flows are adequately covered by automated tests.
- Identify which flows are only partially covered.
- Identify which flows still rely entirely on manual validation.
- Flag gaps that should become follow-up issues before or immediately after release.

## Phase 13: Release Decision Summary

- Summarize all failures by severity.
- Separate expected gating from actual defects.
- Identify unresolved `sev0` or `sev1` issues, if any.
- Identify unresolved UI-quality issues that make the release feel unfinished.
- Produce go / no-go recommendation.
- Produce follow-up list split into must-fix-before-release and safe-post-release.
