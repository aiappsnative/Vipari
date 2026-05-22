import os
import sys


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.models import FindingSeverity, RiskLevel, RuleFinding
from engine.policy import PolicyContext, PolicyRule, default_policy_rules, evaluate_policy_rules


def test_evaluate_policy_rules_returns_low_when_no_rules_match():
    context = PolicyContext(attribute_deltas={"capability_risk": 0.1}, findings=tuple())

    evaluation = evaluate_policy_rules(context, default_policy_rules())

    assert evaluation.minimum_risk == RiskLevel.LOW
    assert evaluation.matched_rules == tuple()


def test_evaluate_policy_rules_uses_highest_matching_floor():
    findings = (
        RuleFinding(
            rule_id="sensitive_data_drift",
            title="Sensitive data drift",
            severity=FindingSeverity.HIGH,
            rationale="Sensitive access was added.",
        ),
    )
    context = PolicyContext(
        attribute_deltas={
            "capability_risk": 0.4,
            "guardrail_robustness": -0.2,
        },
        findings=findings,
    )

    evaluation = evaluate_policy_rules(context, default_policy_rules())

    assert evaluation.minimum_risk == RiskLevel.HIGH
    assert {rule.rule_id for rule in evaluation.matched_rules} == {
        "policy.capability_guardrail_conflict",
        "policy.sensitive_access_without_privacy_controls",
    }


def test_evaluate_policy_rules_supports_custom_rule_sets():
    custom_rule = PolicyRule(
        rule_id="policy.custom.autonomy",
        title="Custom autonomy floor",
        minimum_risk=RiskLevel.MEDIUM,
        rationale="Raise autonomy-heavy changes to Medium.",
        min_attribute_deltas={"autonomy_level": 0.3},
    )
    context = PolicyContext(attribute_deltas={"autonomy_level": 0.35}, findings=tuple())

    evaluation = evaluate_policy_rules(context, (custom_rule,))

    assert evaluation.minimum_risk == RiskLevel.MEDIUM
    assert evaluation.matched_rules == (custom_rule,)