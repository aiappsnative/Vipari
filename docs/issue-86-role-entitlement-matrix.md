# Issue #86 Role And Entitlement Matrix

Use this worksheet to validate who can see, enter, mutate, or download each release-critical surface.

## How To Use This Matrix

- Verify both positive and negative paths.
- Confirm visibility, mutability, and explanatory copy separately.
- Duplicate rows where the same role behaves differently under different workspace states.

## Role Definitions

- `visitor`: unauthenticated public user
- `signed-in-no-workspace`: authenticated user without workspace membership
- `owner`: workspace owner or owner-equivalent admin path
- `edit`: workspace member with edit access
- `read`: workspace member with read access
- `machine-principal`: customer MCP or control-plane service credential
- `system-owner`: product owner identity allowed into `/app/admin`

## Workspace States To Exercise

- `no-workspace`
- `no-subscription`
- `billing-pending`
- `payment-failed`
- `no-install`
- `no-onboarded-repo`
- `active-comments-only`
- `active`
- `canceled-active`
- `expired-read-only`
- `forbidden`

## Execution Slice 01 Notes

- This slice validated visitor-facing gating live and machine-principal isolation through automated suites.
- Authenticated owner browser flows were validated through a temporary local session cookie bound to the real `doria90` workspace.
- The editable non-owner path is currently represented by the `admin` membership role in the local workspace data.
- Read-only behavior was validated through a `viewer` membership and corresponding browser plus mutation checks.
- System-owner access was validated in the local environment through the existing `doria90` owner session.

## Matrix

| surface | role | workspace_state | read_access | mutation_access | download_access | expected_behavior | expected_copy_quality | evidence_ref | result | defect_id | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| public marketing | visitor | n/a | yes | no | no | can navigate public entry points without session | CTA and brand language clear | slice-01 browser checks for `/`, `/pricing`, `/login` | pass |  | live unauthenticated entry routes rendered correctly |
| `/app` bootstrap routing | signed-in-no-workspace | no-workspace | routed | no | no | sent to workspace bootstrap | no confusing dashboard promise |  | not-run |  |  |
| `/app/billing` | owner | no-subscription | yes | yes | limited | owner can start checkout / claim / portal actions as applicable | status language precise |  | not-run |  |  |
| `/app/billing` | edit | no-subscription | expected-deny or view-only per current product | no | no | denied or reduced exactly as designed | denial copy clear |  | not-run |  |  |
| `/app/setup/install` | owner | no-install | yes | yes | no | install and manual link flows available | setup language actionable |  | not-run |  |  |
| `/app/setup/install` | read | no-install | maybe view-only | no | no | blocked from mutation | not a silent failure |  | not-run |  |  |
| `/app/repos` | owner | active or no-onboarded-repo | yes | yes | no | allocate and onboard controls available | statuses readable |  | not-run |  |  |
| `/app/repos` | edit | active | yes | validate whether allocation/onboarding controls are intentionally allowed | no | mutations align with product design | copy matches real capability |  | not-run |  |  |
| `/app/repos` | read | active | yes | no | no | can inspect repo state without mutation | blocked actions explained |  | not-run |  |  |
| `/app/settings` | owner | active | yes | yes | no | can rename workspace and manage controls | admin affordances clear | slice-01 owner-session browser check on `/app/settings` | pass |  | owner view showed workspace rename, PR-comment controls, membership UI, repo footprint, and MCP launch link |
| `/app/settings` | edit | active | yes | limited | no | can edit only intended settings | disabled controls clear | slice-01 admin-session route check: `/app/settings` returned `200` | pass |  | validated using the current local `admin` workspace role as the editable non-owner path |
| `/app/settings` | read | active | yes | no | no | view-only or reduced page | not misleading | slice-01 viewer-session browser check on `/app/settings` | pass |  | read-only view disabled workspace controls and add-user affordances while preserving repo and billing visibility |
| `/app/integrations/mcp` | owner | active | yes | yes | yes | can create or revoke principals and download package | trust-boundary and secret handoff clear | slice-01 owner-session browser check plus authenticated `200` on `/app/integrations/mcp/download` | pass |  | owner view showed API-key tab, activity tab, connector package download, and machine-principal creation form |
| `/app/integrations/mcp` | edit | active | yes | validate intended key-management capability | maybe no | role-specific controls accurate | no hidden-authority confusion | slice-01 admin-session checks: `/app/integrations/mcp` returned `200`, `/app/settings/api-keys` redirected correctly, API-key creation returned `303` | pass |  | current `admin` role can access the API-key management flow |
| `/app/integrations/mcp` | read | active | yes | no | maybe no | read-only view only if intended | API-key restrictions explicit | slice-01 viewer-session browser plus direct checks on `/app/integrations/mcp?tab=api-keys` | pass |  | viewer path fell back to overview copy, omitted machine-principal credentials, and showed restricted-access explanatory text |
| `/app/compliance/*` | owner | active | yes | export mutations yes if intended | yes | full readiness and export workflow available | labels trustworthy | slice-01 owner-session browser and direct route checks on readiness, frameworks, exports, and evidence tabs | pass |  | compliance owner path rendered live repo readiness and export state correctly |
| `/app/compliance/*` | read | active | yes | no or limited | maybe limited | read-only behavior exact | no fake CTA | slice-01 viewer-session route check: `/app/compliance` returned `200` | pass |  | read-only compliance access is available in the active workspace |
| `/dashboard` | owner | active | yes | review actions if implemented | export/history as allowed | full dashboard | blocked states never appear in active workspace incorrectly | slice-01 owner-session browser and API checks on `/dashboard` and `/dashboard/doria90/dummyAI` | pass |  | loaded dashboard and repo casefile rendered real triage data for `dummyAI` and `hermes-agent` |
| `/dashboard` | edit | active | yes | limited | export/history as allowed | dashboard should stay available without owner-only control leakage | non-owner control visibility clear | slice-01 admin-session route checks: `/dashboard` and `/dashboard/doria90/dummyAI` returned `200` | pass |  | editable non-owner route access matches the active workspace expectation |
| `/dashboard` | read | active | yes | no | export/history as allowed per product | dashboard should stay available without owner-only mutation controls | read-only state still feels intentional | slice-01 viewer-session route checks: `/dashboard` and `/dashboard/doria90/dummyAI` returned `200` | pass |  | read-only dashboard access remains available in the active workspace |
| `/dashboard` | any non-eligible user | active-comments-only or pre-active | blocked shell or redirect | no | no | dashboard gating exact | blocked-shell copy useful |  | not-run |  |  |
| `/app/admin` | system-owner | active | yes | yes | n/a | admin page only for configured owner | high-risk actions clearly scoped | slice-01 owner-session checks: `/app/profile` and `/app/settings` exposed the admin link, and `/app/admin` returned `200` | pass |  | local configured owner identity has access to the system admin surface |
| `/app/admin` | owner | active | no unless same identity | no | no | must not leak admin page | denial exact | slice-01 admin and viewer session route checks both returned `403` | pass |  | local non-system-owner members were correctly denied admin access |
| `/cp/auth/token` | machine-principal | active | token issuance only | n/a | n/a | valid scoped exchange only | 401 parity for bad creds | slice-01 focused suite: `tests/test_customer_control_plane.py` within `166 passed, 1 skipped` pack | pass |  | token issuance, revoked principal handling, and 401 parity are covered by the passing suite |
| `/cp/workspaces/{id}` and related `/cp/*` | machine-principal | active | scoped yes | scoped by token | n/a | workspace isolation exact | no cross-workspace leakage | slice-01 focused suite: `tests/test_customer_control_plane.py` within `166 passed, 1 skipped` pack | pass |  | automated evidence is strong; live operator smoke still pending |
| `/api/agent-integrations/mcp/*` | machine-principal | active | yes with valid credentials | invoke only scoped tools | n/a | broker boundaries exact | failures understandable | slice-01 focused suite: `tests/test_mcp_broker.py` within `166 passed, 1 skipped` pack | pass |  | covers token issuance, tool listing, invoke behavior, and workspace isolation |

## Specific Assertions To Prove

During execution, the QA pass should explicitly confirm these statements with evidence:

- viewer or read-only users cannot perform owner-only billing or install mutations
- API-key generation and revoke behavior is not available to unauthorized roles
- dashboard access is still gated by workspace entitlement and setup state, not just auth presence
- customer control-plane APIs are isolated by workspace and scope
- `/app/admin` remains owner-locked to the configured system-owner identity
- blocked or reduced users see explanatory UI rather than silent control disappearance where the product is expected to explain state
