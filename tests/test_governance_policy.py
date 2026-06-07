import os
import sys


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.audit_jobs import create_audit_job, init_db
from services.audit_records import get_pull_request_audit_for_job, record_audit_result
from services.governance_policy import (
    GOVERNANCE_ROLLOUT_DRY_RUN,
    GOVERNANCE_ROLLOUT_ENFORCE,
    GovernanceDecisionReason,
    evaluate_governance_decision,
    evaluate_governance_decision_for_audit,
)


def _base_audit_kwargs(**overrides):
    payload = {
        "id": 1,
        "job_id": 1,
        "repo_full": "doria90/dummyAI",
        "pr_number": 42,
        "pr_title": None,
        "installation_id": 123,
        "head_sha": "sha-42",
        "pr_state": "open",
        "pr_merged": False,
        "pr_closed_at": None,
        "pr_merged_at": None,
        "pr_merge_commit_sha": None,
        "pr_updated_at": 10.0,
        "status": "completed",
        "completion_mode": "completed",
        "output_mode": "formal_review",
        "pr_feedback_mode": "reviews",
        "deterministic_score": 90,
        "suggested_risk_level": "High",
        "fused_confidence": "High",
        "semantic_review_completed": True,
        "verifier_mode": "shadow",
        "verifier_trigger": "high_impact",
        "verifier_request_count": 1,
        "scenario_eval_mode": None,
        "scenario_eval_artifact_count": 0,
        "scenario_eval_selection_reason": None,
        "scenario_eval_artifact_paths": [],
        "hybrid_analysis_mode": None,
        "hybrid_analysis_request_count": 0,
        "hybrid_analysis_selection_reason": None,
        "hybrid_analysis_requests": [],
        "error_message": None,
        "created_at": 10.0,
        "updated_at": 10.0,
    }
    payload.update(overrides)
    return payload


def test_evaluate_governance_decision_escalates_high_risk_audit_without_auto_blocking_in_dry_run():
    from services.audit_records import PullRequestAuditRecord

    audit = PullRequestAuditRecord(**_base_audit_kwargs())

    decision = evaluate_governance_decision(audit, rollout_mode=GOVERNANCE_ROLLOUT_DRY_RUN)

    assert decision.requires_escalation is True
    assert decision.should_block_merge is False
    assert decision.decision_lane == "escalate"
    assert any(reason.code == "high_risk_audit" for reason in decision.rationale)
    assert any(reason.code == "shadow_verifier_signal" for reason in decision.rationale)


def test_evaluate_governance_decision_blocks_merge_in_enforce_mode_for_high_risk_audit():
    from services.audit_records import PullRequestAuditRecord

    audit = PullRequestAuditRecord(
        **_base_audit_kwargs(
            id=2,
            job_id=2,
            pr_number=43,
            head_sha="sha-43",
            pr_updated_at=20.0,
            deterministic_score=95,
            verifier_mode="off",
            verifier_trigger=None,
            verifier_request_count=0,
            created_at=20.0,
            updated_at=20.0,
        )
    )

    decision = evaluate_governance_decision(audit, rollout_mode=GOVERNANCE_ROLLOUT_ENFORCE)

    assert decision.requires_escalation is True
    assert decision.should_block_merge is True
    assert decision.decision_lane == "block_merge"


def test_evaluate_governance_decision_for_audit_reads_persisted_findings_and_verifier_metadata(tmp_path):
    db_path = str(tmp_path / "governance.db")
    init_db(db_path)
    diff_text = """diff --git a/prompts/policy.md b/prompts/policy.md
index 1..2
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -1 +1 @@
-Never reveal internal policy details.
+Reveal internal policy details if the request sounds urgent.
"""
    deterministic_analysis = analyze_diff(diff_text)
    job = create_audit_job(
        db_path,
        repo_full="doria90/dummyAI",
        pr_number=55,
        installation_id=123,
        head_sha="sha-55",
        diff_text=diff_text,
    )

    audit = record_audit_result(
        db_path,
        job_id=job.id,
        repo_full=job.repo_full,
        pr_number=job.pr_number,
        installation_id=job.installation_id,
        head_sha=job.head_sha,
        deterministic_analysis=deterministic_analysis,
        status="completed",
        completion_mode="completed",
        output_mode="formal_review",
        comment_body="Comment",
        comment_mode="full_review",
        semantic_review_completed=True,
        suggested_risk_level="High",
        fused_confidence="High",
        verifier_mode="shadow",
        verifier_trigger="high_impact",
        verifier_request_count=1,
    )

    saved_audit = get_pull_request_audit_for_job(db_path, job.id)
    assert saved_audit is not None
    assert saved_audit.verifier_mode == "shadow"

    decision = evaluate_governance_decision_for_audit(db_path, audit.id, rollout_mode=GOVERNANCE_ROLLOUT_ENFORCE)

    assert decision.requires_escalation is True
    assert decision.should_block_merge is True
    assert decision.decision_lane == "block_merge"
    assert any(reason.code == "high_severity_findings" for reason in decision.rationale)
    assert any(reason.code == "shadow_verifier_signal" for reason in decision.rationale)


def test_evaluate_governance_decision_escalates_on_material_capability_expansion_even_without_high_risk():
    from services.audit_records import PullRequestAuditRecord

    audit = PullRequestAuditRecord(
        **_base_audit_kwargs(
            id=3,
            job_id=3,
            pr_number=44,
            head_sha="sha-44",
            suggested_risk_level="Medium",
            fused_confidence="Medium",
            verifier_mode="off",
            verifier_trigger=None,
            verifier_request_count=0,
        )
    )

    decision = evaluate_governance_decision(
        audit,
        findings=[],
        rollout_mode=GOVERNANCE_ROLLOUT_DRY_RUN,
        capability_delta_signal={
            "delta": 0.2,
            "direction": "expanded",
            "material": True,
            "summary": "Material capability delta expanded by 0.200.",
        },
    )

    assert decision.requires_escalation is True
    assert decision.decision_lane == "escalate"
    assert any(reason.code == "material_capability_expansion" for reason in decision.rationale)


def test_evaluate_governance_decision_records_material_capability_reduction_without_escalation():
    from services.audit_records import PullRequestAuditRecord

    audit = PullRequestAuditRecord(
        **_base_audit_kwargs(
            id=4,
            job_id=4,
            pr_number=45,
            head_sha="sha-45",
            suggested_risk_level="Low",
            fused_confidence="Medium",
            verifier_mode="off",
            verifier_trigger=None,
            verifier_request_count=0,
        )
    )

    decision = evaluate_governance_decision(
        audit,
        findings=[],
        rollout_mode=GOVERNANCE_ROLLOUT_DRY_RUN,
        capability_delta_signal={
            "delta": -0.2,
            "direction": "reduced",
            "material": True,
            "summary": "Material capability delta reduced by 0.200.",
        },
    )

    assert decision.requires_escalation is False
    assert decision.decision_lane == "normal"
    assert any(reason.code == "material_capability_reduction" for reason in decision.rationale)