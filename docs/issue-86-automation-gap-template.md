# Issue #86 Automation Gap Template

Use this table after mapping manual QA scope against the current automated suites.

## Coverage Categories

- `adequate`: current automated coverage is strong enough that manual QA is confirmation, not discovery
- `partial`: automation exists but leaves meaningful release risk uncovered
- `missing`: no meaningful automation for this release-critical surface

## Gap Table

| surface_family | route_or_workflow | current_tests | coverage_category | current_strength | remaining_manual_need | release_risk | recommended_follow_up |
| --- | --- | --- | --- | --- | --- | --- | --- |
| public entry | `/`, `/pricing`, `/login` | `tests/test_control_plane_ui.py`, `tests/test_main.py` | partial | server-render coverage exists | visual polish and broken-link pass still manual | medium | add browser or snapshot-level route checks |
| setup-aware routing | `/app`, bootstrap, billing, install, repos` | `tests/test_control_plane_ui.py`, `tests/test_control_plane_auth.py`, `tests/test_access_state.py` | partial | routing and state logic covered | real UX continuity still manual | high | add higher-level route-state workflow tests |
| workspace pages | profile, settings, help, policies | `tests/test_control_plane_ui.py` | partial | presence and some behavior covered | role-specific polish and layout still manual | medium | expand page-specific assertions and role coverage |
| billing and handoff | billing, claim, portal, webhook | `tests/test_billing_service.py`, `tests/test_control_plane_auth.py` | partial | service logic covered | operator-facing state wording and live-provider behavior manual | high | add route-level billing integration tests |
| repo setup | install, repos, onboarding | `tests/test_github_provisioning.py`, `tests/test_onboarding.py`, `tests/test_pr_merge_onboarding_sync.py` | partial | backend flow covered | shell UX and operator comprehension manual | high | add UI-level repo setup workflow checks |
| dashboard overview | `/dashboard` | `tests/test_dashboard_control_tower.py`, `tests/test_dashboard_api.py` | partial | read model and API covered | browser rendering and interaction polish manual | high | add browser-driven dashboard smoke pack |
| repo case file | `/dashboard/{repo_full}` | `tests/test_dashboard_api.py`, `tests/test_repo_journey.py` | partial | payload logic covered | tab behavior, deep links, and layout manual | high | add repo page browser regression checks |
| compliance | `/app/compliance*` and APIs | `tests/test_compliance_api.py`, `tests/test_compliance_readiness.py`, `tests/test_compliance_export_service.py` | partial | API and model logic covered | visual clarity and end-to-end export UX manual | medium | add route and download workflow tests |
| MCP package | package generation and manifest | `tests/test_mcp_package.py`, `tests/test_mcp_broker.py` | partial | manifest and broker contract covered; live package download also matched expected artifact contents in slice-01 | real connector-host setup remains manual and currently depends on environment JWT config | high | add end-to-end connector invocation harness and a startup guard that surfaces missing internal JWT broker config before connector rollout |
| customer control plane | `/cp/*` | `tests/test_customer_control_plane.py` | adequate | strong credential and isolation coverage | only final smoke needed | medium | maintain current suite and add live smoke if needed |
| runtime guardrails | health, readiness, env validation, preflight | `tests/test_runtime_guardrails.py`, `tests/test_railway_preflight.py`, `tests/test_cloud_deployment.py` | adequate | config contract is strongly covered | production-like live rehearsal still manual | medium | keep focused ops rehearsal in release checklist |
| dummyAI audit rehearsal | end-to-end PR audit path | mixed targeted suites only | missing | no single release-level automated proof of full operator story | full flow must be manual today | high | add canonical fixture-backed end-to-end audit workflow test |

## Notes

- Execution slice 01 exact automated coverage:
	- focused high-risk pack: `tests/test_control_plane_ui.py`, `tests/test_dashboard_api.py`, `tests/test_dashboard_control_tower.py`, `tests/test_compliance_api.py`, `tests/test_mcp_package.py`, `tests/test_mcp_broker.py`, `tests/test_customer_control_plane.py`, `tests/test_operator_api.py`, `tests/test_runtime_guardrails.py` -> `166 passed, 1 skipped`
	- PR lifecycle and landed-drift pack: `tests/test_repo_journey.py`, `tests/test_pr_merge_onboarding_sync.py`, `tests/test_audit_worker.py` -> `44 passed`
	- proposal-only dashboard slice: `tests/test_dashboard_views.py -k proposal` -> `2 passed`
- Slice-01 conclusion: backend and contract coverage is strong across customer control plane, MCP broker/package, runtime guardrails, dashboard APIs, and PR lifecycle state transitions. Authenticated browser UX risk has been reduced materially by live owner/admin/viewer/system-owner checks; the largest remaining release risk is the full manual `dummyAI` operator story, plus rerunning live MCP connector invocation in an environment where internal JWT broker config is enabled.
- The purpose of this table is to separate true product risk from already-covered backend behavior.
