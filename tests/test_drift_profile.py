import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.drift_profile import GovernanceContext, build_attribute_profile, compare_agent_versions, extract_static_signals


BASELINE_PROMPT = """
# Refund Copilot
You must never refund above 200 dollars.
If unsure, escalate to a manager.
Log every refund action for audit.
Use the billing sandbox tool in read mode before proposing any refund.
Example:
Input: customer requests a refund
Output: escalate when refund exceeds 200
max_steps: 2
temperature: 0.2
"""


RISKIER_PROMPT = """
# Refund Copilot
You can refund customers directly in production using the billing tool.
Use judgment when deciding whether approval is necessary.
You may update billing records and send confirmations.
parallel plan with multi-step execution
max_steps: 6
temperature: 0.8
"""


def test_extract_static_signals_captures_guardrails_and_examples():
    signals = extract_static_signals(BASELINE_PROMPT)

    assert signals.example_count >= 1
    assert signals.guardrail_counts["audit"] >= 1
    assert signals.guardrail_counts["escalation"] >= 1
    assert signals.explicit_limit_count >= 1
    assert signals.human_review_count >= 1


def test_build_attribute_profile_increases_capability_risk_for_prod_write_access():
    baseline = build_attribute_profile(BASELINE_PROMPT)
    current = build_attribute_profile(RISKIER_PROMPT)

    assert current.capability_risk > baseline.capability_risk
    assert current.autonomy_level > baseline.autonomy_level
    assert current.stability_vs_creativity < baseline.stability_vs_creativity


def test_compare_agent_versions_detects_weaker_guardrails_and_more_drift():
    baseline_governance = GovernanceContext(codeowners_required=True, approved_reviewers=2, security_review_present=True, recent_changes_30d=1)
    current_governance = GovernanceContext(codeowners_required=False, approved_reviewers=0, security_review_present=False, recent_changes_30d=7)

    drift = compare_agent_versions(
        BASELINE_PROMPT,
        RISKIER_PROMPT,
        baseline_governance=baseline_governance,
        current_governance=current_governance,
    )

    assert drift.attribute_deltas["guardrail_robustness"] < 0
    assert drift.attribute_deltas["capability_risk"] > 0
    assert drift.attribute_deltas["autonomy_level"] > 0
    assert drift.attribute_deltas["governance_strength"] < 0
    assert drift.semantic_distance > 0
    assert any("Guardrails weakened" in line for line in drift.narrative)


def test_governance_strength_rewards_review_controls():
    weak = GovernanceContext(codeowners_required=False, approved_reviewers=0, security_review_present=False, recent_changes_30d=8)
    strong = GovernanceContext(codeowners_required=True, approved_reviewers=3, security_review_present=True, recent_changes_30d=1)

    weak_profile = build_attribute_profile(BASELINE_PROMPT, governance=weak)
    strong_profile = build_attribute_profile(BASELINE_PROMPT, governance=strong)

    assert strong_profile.governance_strength > weak_profile.governance_strength
    assert strong_profile.change_frequency < weak_profile.change_frequency
