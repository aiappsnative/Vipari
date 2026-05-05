# AI Act Capability Map

This document is for internal product and delivery use. It maps current Vipari capabilities to recurring AI Act readiness themes without claiming legal compliance.

## Positioning

Vipari supports evidence preparation, oversight, monitoring, and traceability for repository-level AI control surfaces.

Vipari does not perform legal classification, determine whether a system is high-risk, or guarantee regulatory compliance.

## Capability Matrix

| Theme | Current product support | Current evidence surface | Gaps / notes |
| --- | --- | --- | --- |
| Logging and traceability | Durable PR audits, baseline audit log, repo posture snapshots, artifact versions | `04-pr-scan-history.csv`, `02-baseline-audit-log.csv`, `03-version-history.csv`, dashboard repo case file | Expand Docker + SQLite validation against real onboarded repos before merge |
| Human oversight | Baseline approval, rejection, and rebaseline flows with actor, rationale, and decision type | Repo dashboard baseline review panel, `02-governance-summary.json`, `02-baseline-audit-log.csv` | No dedicated escalation workflow yet beyond current review actions |
| Monitoring | Repository drift history, findings, risk events, artifact-level storyline | Dashboard insights, `05-findings.csv`, `06-risk-events.csv`, `07-drift/*` | Still repository-centric rather than runtime-centric |
| Transparency of review output | Provenance labels for deterministic fallback vs AI-assisted review output; provenance labels for exported raw artifact content | `04-pr-scan-history.csv`, `09-artifact-content.json`, dashboard export copy | PR comment surfaces can still become more explicit in future work |
| Governance evidence packaging | Auditor-facing export with integrity manifest and control mapping | `manifest.json`, `08-control-mapping.md`, export package UI | Helpful for oversight conversations, not legal certification |

## Implementation Notes

- Treat provenance labels as factual metadata about how an output was produced.
- Keep human-reviewed baseline decisions separate from AI-assisted review narratives.
- Avoid language that implies Vipari classifies systems under the law.
- Prefer embedded evidence surfaces over a standalone compliance subsystem.

## Current acceptance bar

- Dashboard renders provenance labels for onboarded artifacts.
- Compliance export includes governance decisions and review-output provenance labels.
- Public-facing copy states readiness support, not legal guarantees.
- SQLite + Docker validation against existing onboarded repositories remains a required pre-merge step.
