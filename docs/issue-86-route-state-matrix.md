# Issue #86 Route And State Matrix

Use this worksheet during the pre-launch QA overpass to record route behavior by auth state, workspace state, role, expected outcome, and observed result.

## How To Use This Matrix

- One row represents one route or one tightly related route family.
- Duplicate rows when the same route behaves materially differently across states.
- Record expected gating before the run, then record actual behavior during the run.
- A route is not complete until both behavior and UI quality are assessed.

## Status Values

- `not-run`
- `pass`
- `fail`
- `waived`

## Severity Values For Failures

- `sev0`
- `sev1`
- `sev2`
- `sev3`

## Matrix Fields

- `route`
- `surface_family`
- `auth_state`
- `workspace_state`
- `role`
- `expected_route_result`
- `expected_primary_cta`
- `expected_ui_notes`
- `api_dependencies`
- `evidence_ref`
- `result`
- `defect_id`
- `severity`
- `notes`

## Execution Slice 01

- Commit under review: `1d32b47`
- Environment: local monolith on `http://127.0.0.1:8011`
- Working tree note: issue-86 QA docs are new and currently uncommitted; no application-code edits were made in this slice
- Evidence sources:
	- focused regression pack: `166 passed, 1 skipped in 191.93s`
	- PR lifecycle coverage pack: `44 passed in 67.34s`
	- dashboard proposal-only slice: `2 passed in 7.19s`
	- live browser checks: `/`, `/pricing`, `/login`, `/app`, `/health`, `/health/ready`
	- live local-operator checks: `/dashboard`, `/dashboard/doria90/dummyAI`, `/app/profile`, `/app/settings`, `/app/integrations/mcp`, `/app/compliance`
	- live owner-session checks: `/app/profile`, `/app/settings`, `/app/integrations/mcp`, `/app/integrations/mcp?tab=api-keys`, `/app/compliance`, `/dashboard`, `/dashboard/doria90/dummyAI`
	- live role checks: temporary `admin` and `viewer` sessions on `/app/settings`, `/app/integrations/mcp`, `/app/compliance`, `/dashboard`, `/dashboard/doria90/dummyAI`, plus mutation denials for viewer
	- live MCP broker check: downloaded connector artifact matched expected contents, but `/api/agent-integrations/mcp/token` returned `503` with `Internal JWT auth is not configured.`
	- restart log: successful uvicorn startup and repeated `200 OK` traffic on dashboard and control-plane routes
	- design-confirmation tests: `tests/test_control_plane_ui.py` and `tests/test_operator_api.py` show local operator shell pages may render without a session while backing APIs still return `401`
	- local QA auth bypass: temporary owner session seeded directly in the local SQLite control-plane DB and injected as a browser cookie; no application code changes were required

## Public And Auth Entry Routes

| route | surface_family | auth_state | workspace_state | role | expected_route_result | expected_primary_cta | expected_ui_notes | api_dependencies | evidence_ref | result | defect_id | severity | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/` | public-marketing | unauthenticated | n/a | visitor | render marketing page | sign in / pricing | copy, branding, CTA hierarchy, no stale PromptDrift branding | none | slice-01 browser check: page title `Vipari Control Plane`, correct Vipari marketing shell | pass |  |  | branding and primary CTA path looked correct in the live browser pass |
| `/pricing` | public-pricing | unauthenticated | n/a | visitor | render pricing page | plan selection / sign in | plan naming, compatibility copy, visual quality | none | slice-01 browser check: title `Pricing | Vipari`, pricing cards and compatibility note rendered | pass |  |  | visual pass was limited to desktop-width live rendering |
| `/login` | auth-entry | unauthenticated | n/a | visitor | render login entry | sign in with GitHub | handoff-state messaging correct, error messaging clear | none | slice-01 browser check: title `Login | Vipari`, GitHub entry shell rendered cleanly | pass |  |  | error-state copy still needs explicit negative-path exercise |
| `/auth/github/start` | auth-entry | unauthenticated | n/a | visitor | redirect to GitHub authorize URL or fail clearly if misconfigured | GitHub OAuth redirect | no broken state propagation | GitHub OAuth config |  | not-run |  |  |  |
| `/auth/github/callback` | auth-entry | oauth-return | varies | visitor | create session and route into setup-aware flow | redirect based on flow state | preserves source / plan / install context | auth service, control-plane records |  | not-run |  |  |  |
| `/logout` | auth-entry | authenticated | varies | any signed-in user | clear session and redirect safely | login or public entry | no stale session state | session store |  | not-run |  |  |  |

## Setup-Aware Control-Plane Routes

| route | surface_family | auth_state | workspace_state | role | expected_route_result | expected_primary_cta | expected_ui_notes | api_dependencies | evidence_ref | result | defect_id | severity | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/app` | setup-router | unauthenticated | n/a | visitor | redirect to `/login` | continue with GitHub | redirect exactness, no dead end | auth session | slice-01 browser check: unauthenticated visit resolved to login flow | pass |  |  | validates the visitor gate only; other access-state branches remain open |
| `/app` | setup-router | authenticated | no-workspace | signed-in user | redirect to workspace bootstrap | create workspace | no confusing intermediate shell | access-state resolver |  | not-run |  |  |  |
| `/app` | setup-router | authenticated | no-subscription | owner | redirect to billing | open billing | state copy clear | access-state resolver, billing records |  | not-run |  |  |  |
| `/app` | setup-router | authenticated | billing-pending | owner | redirect to billing | review billing state | does not imply access is active too early | access-state resolver |  | not-run |  |  |  |
| `/app` | setup-router | authenticated | no-install | owner | redirect to install | install GitHub app | no broken setup sequence | access-state resolver, GitHub installation records |  | not-run |  |  |  |
| `/app` | setup-router | authenticated | no-onboarded-repo | owner | redirect to repos | open repositories | repo setup state readable | access-state resolver, repo connections |  | not-run |  |  |  |
| `/app` | setup-router | authenticated | active-comments-only | owner | redirect to repos | onboard or upgrade | comments-only state is explicit | access-state resolver |  | not-run |  |  |  |
| `/app` | setup-router | authenticated | active | owner | redirect to dashboard-eligible surface | open dashboard / continue work | state feels complete | access-state resolver |  | not-run |  |  |  |
| `/app/workspaces/new` | workspace-bootstrap | authenticated | no-workspace | signed-in user | render bootstrap form | create workspace | form clarity, no odd defaults, theme polish | control-plane records |  | not-run |  |  |  |
| `/app/billing` | billing | authenticated | no-subscription or billing-pending | owner | render billing page | checkout / claim / portal | plan copy, status wording, CTA clarity | billing service, entitlement view |  | not-run |  |  |  |
| `/app/billing/claim` | billing | authenticated | claim-flow | owner | render claim state or redirect | activate claim | no ambiguous ownership wording | billing handoff records |  | not-run |  |  |  |
| `/app/billing/portal` | billing | authenticated | active | owner | redirect to billing portal | manage billing | permission enforcement exact | billing service |  | not-run |  |  |  |
| `/app/setup/install` | install | authenticated | no-install | owner | render install guidance | install GitHub app | callback and manual fallback copy clear | GitHub provisioning |  | not-run |  |  |  |
| `/app/repos` | repo-setup | authenticated | install-linked | owner | render repo inventory and onboarding shell | allocate / onboard | inventory, status chips, sort UI, empty states | repo connections, onboarding summaries |  | not-run |  |  |  |

## Authenticated Workspace Routes

| route | surface_family | auth_state | workspace_state | role | expected_route_result | expected_primary_cta | expected_ui_notes | api_dependencies | evidence_ref | result | defect_id | severity | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/app/profile` | control-plane | authenticated | active | owner/edit/read | render profile page | save profile | theme controls, field states, nav consistency | profile preferences |  | not-run |  |  |  |
| `/app/profile` | control-plane | authenticated | active | owner | render profile page | save profile | theme controls, field states, nav consistency | profile preferences | slice-01 owner-session browser check: `Profile | Vipari` rendered with workspace `Wow Team`, owner role, editable display name, theme controls, and admin link | pass |  |  | protected page loaded correctly under the temporary owner session |
| `/app/profile` | control-plane | authenticated | active | edit/read | render profile page | save profile if allowed | role badge and nav continuity clear | profile preferences | slice-01 direct route checks: admin and viewer sessions both returned `200` | pass |  |  | non-owner roles reached profile successfully; deeper profile mutation behavior still not exercised |
| `/app/settings` | control-plane | authenticated | active | owner/edit/read | render settings page with role-appropriate controls | save settings | role visibility, membership UI, repo footprint, shell quality | workspace, subscription, installations |  | not-run |  |  |  |
| `/app/settings` | control-plane | authenticated | active | owner | render settings page with role-appropriate controls | save settings | role visibility, membership UI, repo footprint, shell quality | workspace, subscription, installations | slice-01 owner-session browser check: settings page showed active team plan, owner controls, membership invite UI, and repo footprint for `dummyAI`, `hermes-agent`, and `openfang` | pass |  |  | owner-path workspace settings shell and data rendered cleanly |
| `/app/settings` | control-plane | authenticated | active | edit/read | render settings page with role-appropriate controls | save settings only when allowed | permissions should be explicit, not implied | workspace, subscription, installations | slice-01 admin and viewer checks: both returned `200`; viewer browser view showed disabled controls and owner/admin-only copy | pass |  |  | live non-owner settings behavior matched the intended permission messaging |
| `/app/settings/api-keys` | control-plane | authenticated | active | owner/admin | redirect into MCP page tab or deny | open API keys tab | redirect exactness | MCP page routing |  | not-run |  |  |  |
| `/app/settings/api-keys` | control-plane | authenticated | active | owner/admin | redirect into MCP page tab or deny | open API keys tab | redirect exactness | MCP page routing | slice-01 owner-session browser check: `/app/settings/api-keys` resolved to `/app/integrations/mcp?tab=api-keys` and rendered machine-principal credentials UI | pass |  |  | owner-path redirect and API-key tab rendering matched the expected flow |
| `/app/settings/api-keys` | control-plane | authenticated | active | edit/read | redirect into MCP page tab or deny based on role | open restricted overview or API-key UI | read-only users must not see hidden-authority controls | MCP page routing | slice-01 admin and viewer checks: both redirected to `/app/integrations/mcp?tab=api-keys`; admin API-key create returned `303`, viewer API-key create returned `403` | pass |  |  | current `admin` role can manage keys, while viewer falls back to restricted copy |
| `/app/integrations/mcp` | mcp | authenticated | active | owner/edit/read | render integrations page with role-sensitive panels | download connector / manage keys | download flow, trust-boundary copy, tab quality | MCP broker tools, machine principals |  | not-run |  |  |  |
| `/app/integrations/mcp` | mcp | authenticated | active | owner | render integrations page with role-sensitive panels | download connector / manage keys | download flow, trust-boundary copy, tab quality | MCP broker tools, machine principals | slice-01 owner-session browser check plus direct route check: overview and API-keys tab rendered; `/app/integrations/mcp/download` returned `200` with `application/zip` | pass |  |  | page showed Vipari connector quickstart, `vipari.*` tools, API-key tab, and download affordance |
| `/app/integrations/mcp` | mcp | authenticated | active | edit/read | render integrations page with role-sensitive panels | download connector and manage keys if entitled | permission differences explicit | MCP broker tools, machine principals | slice-01 admin and viewer checks: both returned `200`; viewer API-keys tab fell back to overview with restricted-copy messaging | pass |  |  | live role-sensitive MCP presentation matched the intended owner/admin vs viewer split |
| `/app/integrations/mcp/download` | mcp | authenticated | active | owner/admin or documented allowed roles | download zip package | download connector | correct file, no auth leak | package builder |  | not-run |  |  |  |
| `/app/integrations/mcp/download` | mcp | authenticated | active | owner/admin or documented allowed roles | download zip package | download connector | correct file, no auth leak | package builder | slice-01 owner-session download and unpack check: package contained `README.md`, `requirements.txt`, `vipari.env.example`, `claude-desktop-config.json.example`, `tool-manifest.json`, `vipari_mcp_server.py` | pass |  |  | downloaded artifact shape matched the intended customer package |
| `/app/policies` | control-plane | authenticated | active | owner/edit/read | render placeholder shell | no invalid CTA | placeholder state still polished | none |  | not-run |  |  |  |
| `/app/help` | control-plane | authenticated | active | owner/edit/read | render contextual help | next relevant page CTA | context-aware guidance quality | workspace summaries, repo status |  | not-run |  |  |  |
| `/app/compliance` | compliance | authenticated | active | owner/edit/read | render readiness page | compliance next action | verdict clarity, metrics, table quality | compliance view, export jobs |  | not-run |  |  |  |
| `/app/compliance` | compliance | authenticated | active | owner | render readiness page | compliance next action | verdict clarity, metrics, table quality | compliance view, export jobs | slice-01 owner-session browser check: `Compliance | Vipari` rendered with readiness verdict, repo table, gap links, and export summary | pass |  |  | protected readiness page rendered with real workspace data |
| `/app/compliance/frameworks` | compliance | authenticated | active | owner/edit/read | render frameworks page | open evidence / exports | framework cards and copy quality | compliance view |  | not-run |  |  |  |
| `/app/compliance/frameworks` | compliance | authenticated | active | owner | render frameworks page | open evidence / exports | framework cards and copy quality | compliance view | slice-01 direct route check: `200`, page contains `Framework mapping` | pass |  |  | subpage responded correctly under owner session |
| `/app/compliance/exports` | compliance | authenticated | active | owner/edit/read | render exports page | generate export | form clarity, download history presentation | export jobs |  | not-run |  |  |  |
| `/app/compliance/exports` | compliance | authenticated | active | owner | render exports page | generate export | form clarity, download history presentation | export jobs | slice-01 direct route check: `200`, page contains `Run compliance exports` | pass |  |  | export subpage reachable and populated |
| `/app/compliance/evidence` | compliance | authenticated | active | owner/edit/read | render evidence page | filter / open audit page | filters, evidence freshness, empty states | compliance view |  | not-run |  |  |  |
| `/app/compliance/evidence` | compliance | authenticated | active | owner | render evidence page | filter / open audit page | filters, evidence freshness, empty states | compliance view | slice-01 direct route check: `200`, page contains `Repository evidence posture` | pass |  |  | evidence subpage reachable and populated |
| `/app/admin` | admin | authenticated | active | system-owner | render admin page only to correct actor | admin mutations | owner-lock correctness, high-risk UI clarity | admin records | slice-01 owner-session checks: admin link present on profile/settings and `/app/admin` returned `200` | pass |  |  | configured local owner identity can reach the admin surface |
| `/app/admin` | admin | authenticated | active | admin/read | deny | none | denial exact and non-leaky | admin records | slice-01 admin and viewer route checks: `/app/admin` returned `403` for both | pass |  |  | workspace roles do not leak into system-owner access |

## Dashboard Routes

| route | surface_family | auth_state | workspace_state | role | expected_route_result | expected_primary_cta | expected_ui_notes | api_dependencies | evidence_ref | result | defect_id | severity | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/dashboard` | dashboard-overview | authenticated | active | dashboard-eligible user | render overview dashboard | review urgent changes | shell, queue, default selection, nav continuity | `/api/repos`, `/api/dashboard/overview`, `/api/dashboard/escalation-queue` |  | not-run |  |  |  |
| `/dashboard` | dashboard-overview | authenticated | active | owner | render overview dashboard | review urgent changes | shell, queue, default selection, nav continuity | `/api/repos`, `/api/dashboard/overview`, `/api/dashboard/escalation-queue` | slice-01 owner-session browser and API checks: dashboard rendered loaded overview counts, escalation queue, and repo map; supporting APIs returned `200` | pass |  |  | owner-path dashboard fully hydrated under the temporary session |
| `/dashboard` | dashboard-overview | unauthenticated | local-operator | visitor | render static local operator shell while protected APIs remain unauthorized | none | shell may render, but overview data should fail closed with `401` until authenticated | `/api/repos`, `/api/dashboard/overview`, `/api/dashboard/escalation-queue` | slice-01 browser check plus `tests/test_control_plane_ui.py` and `tests/test_operator_api.py` | pass |  |  | live page rendered a shell and showed `401` failure states; nearby tests confirm this is intentional local behavior |
| `/dashboard` | dashboard-overview | authenticated | blocked-setup-state | non-eligible user | redirect or setup-aware shell per state | state CTA | blocked shell copy and CTA accuracy | access resolver |  | not-run |  |  |  |
| `/dashboard/{repo_full}` | dashboard-repo | authenticated | active | dashboard-eligible user | render repo case file | resolve artifact / review item | tabs, detail panel, evidence readability, baseline messaging | repo dashboard APIs |  | not-run |  |  |  |
| `/dashboard/{repo_full}` | dashboard-repo | authenticated | active | owner | render repo case file | resolve artifact / review item | tabs, detail panel, evidence readability, baseline messaging | repo dashboard APIs | slice-01 owner-session browser and API checks for `doria90/dummyAI`: case file rendered decision summary, drift storyline, attribute profile, and PR-backed recommendation; repo API returned `200` | pass |  |  | `dummyAI` case file hydrated successfully with real review context |
| `/dashboard/{repo_full}` | dashboard-repo | unauthenticated | local-operator | visitor | render static repo shell while protected repo APIs remain unauthorized | none | repo shell may render loading or unavailable states without leaking data | repo dashboard APIs | slice-01 browser check for `/dashboard/doria90/dummyAI` plus `tests/test_operator_api.py` | pass |  |  | live repo page showed the expected shell with `401`-backed unavailable states; this matches the pinned operator-mode behavior |
| `/dashboard/{repo_full}?artifact=...&pr=...` | dashboard-repo | authenticated | active | dashboard-eligible user | deep-link into intended context | review linked context | deep-link survives load and tab changes | repo dashboard APIs | slice-01 deep-link check on `doria90/dummyAI?artifact=system_prompt.md&pr=48&head_sha=31db2a3...` | pass |  |  | selected artifact, PR context, and storyline all hydrated correctly after network idle |
| `/dashboard/{repo_full}?tab=version-control` | dashboard-repo | authenticated | active | dashboard-eligible user | render version-control posture and journey | compare baseline to current | radar, timeline, and baseline comparison must hydrate | repo dashboard APIs | slice-01 browser wait check on `doria90/dummyAI?tab=version-control` | pass |  |  | rendered posture radar, snapshot journey, and `+0.000` baseline-vs-current comparison |
| `/dashboard/{repo_full}?tab=baseline` | dashboard-repo | authenticated | active | dashboard-eligible user | render baseline review and artifact registry | open flagged change / review baseline | baseline review state and artifact registry must hydrate | repo dashboard APIs | slice-01 browser wait check on `doria90/dummyAI?tab=baseline` | pass |  |  | rendered review target, baseline review summary, rebaseline history, and artifact registry after network idle |
| `/dashboard/{repo_full}?tab=compliance` | dashboard-repo | authenticated | active | dashboard-eligible user | render repo compliance cues | inspect governance context | repo-level oversight copy must stay clear and non-legalistic | repo dashboard APIs | slice-01 browser check on `doria90/dummyAI?tab=compliance` | pass |  |  | rendered AI-surface counts, audit cues, and lower-confidence summary cleanly |
| `/dashboard/{repo_full}?tab=reports` | dashboard-repo | authenticated | active | dashboard-eligible user | render repo export flow | generate export or review audits | export form should be intact and related-audits handoff should work | repo dashboard APIs | slice-01 browser check on `doria90/dummyAI?tab=reports` | pass |  |  | export form rendered correctly; export history remained empty/loading without evidence of a broken route |

## Runtime And Webhook Routes

| route | surface_family | auth_state | workspace_state | role | expected_route_result | expected_primary_cta | expected_ui_notes | api_dependencies | evidence_ref | result | defect_id | severity | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/health` | runtime | n/a | n/a | operator | liveness OK on healthy service | none | payload minimal and non-sensitive | runtime guardrails | slice-01 browser check: `{"status":"ok","service_role":"monolith"}` | pass |  |  | live payload matched expected local monolith liveness |
| `/health/ready` | runtime | n/a | n/a | operator | readiness reports exact dependency state | none | config / persistence / queue status readable and correct | runtime guardrails, persistence, queue | slice-01 browser check: healthy local readiness payload for config, persistence, and migrations | pass |  |  | degraded or misconfigured readiness paths still need deliberate negative-path rehearsal |
| `/webhook` | webhook | n/a | n/a | GitHub app | accept only valid webhook traffic and gate correctly | none | failures diagnosable, no silent acceptance | GitHub auth, queue |  | not-run |  |  |  |
| `/webhooks/stripe` | webhook | n/a | n/a | Stripe | accept only valid billing events | none | no unsafe activation path | billing service |  | not-run |  |  |  |

## API Families Requiring Focused Validation

Record detailed API checks separately if needed, but ensure these route families are explicitly marked complete in QA evidence:

- `/api/auth/session`
- `/api/workspaces/current/access-state`
- `/api/dashboard/*`
- `/api/repos/*`
- `/api/compliance/*`
- `/api/export/*`
- `/api/agent-integrations/mcp/*`
- `/cp/*`

### Slice-01 API note for MCP broker

- `/api/agent-integrations/mcp/token`: live call returned `503` with `Internal JWT auth is not configured.` while static page rendering and package download both succeeded
- implication: connector-host end-to-end validation remains blocked by local environment configuration, not by route reachability or package assembly
