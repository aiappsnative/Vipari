from __future__ import annotations

from dataclasses import dataclass, field

from .audit_records import PullRequestAuditRecord, get_pull_request_audit_by_id, list_findings_for_audit


GOVERNANCE_ROLLOUT_OFF = "off"
GOVERNANCE_ROLLOUT_DRY_RUN = "dry_run"
GOVERNANCE_ROLLOUT_WARN = "warn"
GOVERNANCE_ROLLOUT_ENFORCE = "enforce"


@dataclass(frozen=True)
class GovernanceDecisionReason:
    code: str
    summary: str
    severity: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class GovernanceDecision:
    rollout_mode: str
    requires_escalation: bool
    should_block_merge: bool
    decision_lane: str
    rationale: tuple[GovernanceDecisionReason, ...] = field(default_factory=tuple)


def build_governance_ci_outcome(decision: GovernanceDecision) -> dict[str, object]:
    decision_lane = str(decision.decision_lane or "inactive").strip().lower()

    if decision.should_block_merge or decision_lane == "block_merge":
        return {
            "conclusion": "failure",
            "recommended_exit_code": 1,
            "recommended_gate": "block",
        }
    if decision.requires_escalation or decision_lane == "escalate":
        return {
            "conclusion": "neutral",
            "recommended_exit_code": 0,
            "recommended_gate": "warn",
        }
    return {
        "conclusion": "success",
        "recommended_exit_code": 0,
        "recommended_gate": "pass",
    }


def normalize_governance_rollout_mode(value: str | None) -> str:
    candidate = str(value or GOVERNANCE_ROLLOUT_OFF).strip().lower()
    if candidate == GOVERNANCE_ROLLOUT_ENFORCE:
        return GOVERNANCE_ROLLOUT_ENFORCE
    if candidate == GOVERNANCE_ROLLOUT_WARN:
        return GOVERNANCE_ROLLOUT_WARN
    if candidate == GOVERNANCE_ROLLOUT_DRY_RUN:
        return GOVERNANCE_ROLLOUT_DRY_RUN
    return GOVERNANCE_ROLLOUT_OFF


def evaluate_governance_decision(
    audit: PullRequestAuditRecord,
    *,
    findings: list[object] | None = None,
    rollout_mode: str = GOVERNANCE_ROLLOUT_DRY_RUN,
) -> GovernanceDecision:
    normalized_rollout_mode = normalize_governance_rollout_mode(rollout_mode)
    reasons: list[GovernanceDecisionReason] = []
    findings = list(findings or [])

    if audit.status != "completed":
        reasons.append(
            GovernanceDecisionReason(
                code="audit_incomplete",
                summary="Governance decision stayed inactive because the underlying PR audit is not complete yet.",
                severity="info",
                evidence=(f"audit_status={audit.status}",),
            )
        )
        return GovernanceDecision(
            rollout_mode=normalized_rollout_mode,
            requires_escalation=False,
            should_block_merge=False,
            decision_lane="inactive",
            rationale=tuple(reasons),
        )

    if audit.suggested_risk_level == "High":
        risk_evidence = [f"fused_risk={audit.suggested_risk_level}"]
        if audit.fused_confidence:
            risk_evidence.append(f"fused_confidence={audit.fused_confidence}")
        reasons.append(
            GovernanceDecisionReason(
                code="high_risk_audit",
                summary="The completed PR audit reached a high-risk outcome and should enter the escalation lane.",
                severity="high",
                evidence=tuple(risk_evidence),
            )
        )

    high_severity_findings = [finding for finding in findings if str(getattr(finding, "severity", "")).lower() == "high"]
    if high_severity_findings:
        reasons.append(
            GovernanceDecisionReason(
                code="high_severity_findings",
                summary="Deterministic high-severity findings were recorded for this audit and warrant governance escalation.",
                severity="high",
                evidence=tuple(
                    str(getattr(finding, "rule_id", "unknown_rule"))
                    for finding in high_severity_findings[:3]
                ),
            )
        )

    if audit.verifier_mode == "shadow" and audit.verifier_request_count > 0:
        verifier_evidence = [f"verifier_mode={audit.verifier_mode}", f"verifier_request_count={audit.verifier_request_count}"]
        if audit.verifier_trigger:
            verifier_evidence.append(f"verifier_trigger={audit.verifier_trigger}")
        reasons.append(
            GovernanceDecisionReason(
                code="shadow_verifier_signal",
                summary="Shadow-mode verifier planning was triggered, which should be recorded as calibration evidence but not used as a standalone governance decision.",
                severity="info",
                evidence=tuple(verifier_evidence),
            )
        )

    requires_escalation = any(reason.code in {"high_risk_audit", "high_severity_findings"} for reason in reasons)
    should_block_merge = normalized_rollout_mode == GOVERNANCE_ROLLOUT_ENFORCE and requires_escalation
    if should_block_merge:
        decision_lane = "block_merge"
    elif requires_escalation:
        decision_lane = "escalate"
    elif normalized_rollout_mode == GOVERNANCE_ROLLOUT_OFF:
        decision_lane = "inactive"
    else:
        decision_lane = "normal"

    if not reasons:
        reasons.append(
            GovernanceDecisionReason(
                code="normal_review_lane",
                summary="No governance-level escalation signal was found beyond the completed audit record.",
                severity="info",
                evidence=(f"fused_risk={audit.suggested_risk_level}",),
            )
        )

    return GovernanceDecision(
        rollout_mode=normalized_rollout_mode,
        requires_escalation=requires_escalation,
        should_block_merge=should_block_merge,
        decision_lane=decision_lane,
        rationale=tuple(reasons),
    )


def evaluate_governance_decision_for_audit(
    db_path: str,
    audit_id: int,
    *,
    rollout_mode: str = GOVERNANCE_ROLLOUT_DRY_RUN,
) -> GovernanceDecision:
    audit = get_pull_request_audit_by_id(db_path, audit_id)
    if audit is None:
        raise ValueError(f"Audit {audit_id} does not exist.")
    findings = list_findings_for_audit(db_path, audit_id)
    return evaluate_governance_decision(
        audit,
        findings=findings,
        rollout_mode=rollout_mode,
    )