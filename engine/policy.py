from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .models import RiskLevel, RuleFinding


RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


@dataclass(frozen=True)
class PolicyContext:
    attribute_deltas: Mapping[str, float] = field(default_factory=dict)
    findings: tuple[RuleFinding, ...] = field(default_factory=tuple)

    @property
    def finding_ids(self) -> set[str]:
        return {finding.rule_id for finding in self.findings}


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    title: str
    minimum_risk: RiskLevel
    rationale: str
    required_finding_ids: tuple[str, ...] = field(default_factory=tuple)
    min_attribute_deltas: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyEvaluation:
    minimum_risk: RiskLevel
    matched_rules: tuple[PolicyRule, ...] = field(default_factory=tuple)

    @property
    def rationale(self) -> tuple[str, ...]:
        return tuple(rule.rationale for rule in self.matched_rules)


def _rule_matches(context: PolicyContext, rule: PolicyRule) -> bool:
    if rule.required_finding_ids and not set(rule.required_finding_ids).issubset(context.finding_ids):
        return False

    for attribute_name, minimum_delta in rule.min_attribute_deltas.items():
        current_delta = context.attribute_deltas.get(attribute_name, 0.0)
        if minimum_delta >= 0:
            if current_delta < minimum_delta:
                return False
            continue
        if current_delta > minimum_delta:
            return False

    return True


def evaluate_policy_rules(context: PolicyContext, rules: Iterable[PolicyRule]) -> PolicyEvaluation:
    matched_rules = tuple(rule for rule in rules if _rule_matches(context, rule))
    if not matched_rules:
        return PolicyEvaluation(minimum_risk=RiskLevel.LOW)

    minimum_risk = max(matched_rules, key=lambda rule: RISK_ORDER[rule.minimum_risk]).minimum_risk
    return PolicyEvaluation(minimum_risk=minimum_risk, matched_rules=matched_rules)


def default_policy_rules() -> tuple[PolicyRule, ...]:
    return (
        PolicyRule(
            rule_id="policy.capability_guardrail_conflict",
            title="Capability expansion with weaker guardrails",
            minimum_risk=RiskLevel.MEDIUM,
            rationale="Escalate to at least Medium when capability risk rises while guardrail robustness drops.",
            min_attribute_deltas={"capability_risk": 0.25, "guardrail_robustness": -0.0001},
        ),
        PolicyRule(
            rule_id="policy.sensitive_access_without_privacy_controls",
            title="Sensitive access without privacy controls",
            minimum_risk=RiskLevel.HIGH,
            rationale="Escalate to High when sensitive data drift appears without matching privacy guardrails.",
            required_finding_ids=("sensitive_data_drift",),
            min_attribute_deltas={"guardrail_robustness": -0.0001},
        ),
    )