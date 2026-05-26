from __future__ import annotations

import json

import pytest

from engine.models import RiskLevel
from services.operational_policy import (
    MergeAction,
    PolicyScope,
    SemanticRunStrategy,
    VerifierRunStrategy,
    canonical_policy_json,
    compute_policy_hash,
    default_policy_for_preset,
    normalize_operational_policy,
    normalize_repo_policy_override,
    resolve_effective_policy,
    supported_policy_categories,
)


def test_supported_policy_categories_are_backed_by_engine_signals():
    categories = {definition.key: definition for definition in supported_policy_categories()}

    assert categories["secrets"].finding_ids == ("sensitive_data_drift",)
    assert categories["guardrail_weakening"].finding_ids == ("guardrail_drift", "guardrail_weakening")
    assert categories["tool_authority"].finding_ids == ("tooling_drift", "sensitive_tooling_drift")
    assert categories["autonomy_increase"].attribute_keys == ("autonomy_level",)


def test_normalize_operational_policy_enforces_immutable_safety_floors():
    policy = normalize_operational_policy(
        {
            "preset_key": "permissive",
            "categories": {
                "secrets": {
                    "default_severity": "low",
                    "min_final_risk_level": "low",
                    "merge_action": "warn",
                },
                "pii": {
                    "default_severity": "low",
                    "min_final_risk_level": "low",
                    "merge_action": "warn",
                },
            },
        }
    )

    assert policy.categories["secrets"].default_severity == RiskLevel.MEDIUM
    assert policy.categories["secrets"].min_final_risk_level == RiskLevel.MEDIUM
    assert policy.categories["pii"].default_severity == RiskLevel.MEDIUM
    assert policy.categories["pii"].min_final_risk_level == RiskLevel.MEDIUM


def test_normalize_operational_policy_rejects_unknown_keys():
    with pytest.raises(ValueError, match="Unsupported policy keys"):
        normalize_operational_policy({"surprise": True})

    with pytest.raises(ValueError, match="Unknown policy category"):
        normalize_operational_policy({"categories": {"totally_unknown": {"merge_action": "warn"}}})


def test_canonical_policy_json_and_hash_are_stable_for_semantically_identical_content():
    first = normalize_operational_policy(
        {
            "preset_key": "balanced",
            "gating": {"medium_risk_action": "require_escalation", "high_risk_action": "block"},
            "llm_strategy": {
                "when_to_run_verifier": "on_high_only",
                "when_to_run_semantic": "on_medium_plus",
            },
            "categories": {
                "tool_authority": {
                    "merge_action": "block",
                    "min_final_risk_level": "high",
                    "default_severity": "high",
                }
            },
            "attribute_templates": ["autonomy_increase_above_threshold", "guardrail_robustness_drop"],
        }
    )
    second = normalize_operational_policy(
        {
            "attribute_templates": ["guardrail_robustness_drop", "autonomy_increase_above_threshold"],
            "categories": {
                "tool_authority": {
                    "default_severity": "high",
                    "merge_action": "block",
                    "min_final_risk_level": "high",
                }
            },
            "llm_strategy": {
                "when_to_run_semantic": "on_medium_plus",
                "when_to_run_verifier": "on_high_only",
            },
            "gating": {"high_risk_action": "block", "medium_risk_action": "require_escalation"},
            "preset_key": "balanced",
        }
    )

    assert canonical_policy_json(first) == canonical_policy_json(second)
    assert compute_policy_hash(first) == compute_policy_hash(second)
    assert json.loads(canonical_policy_json(first))["attribute_templates"] == [
        "autonomy_increase_above_threshold",
        "guardrail_robustness_drop",
    ]


def test_repo_override_resolution_merges_workspace_defaults_without_mutation():
    workspace_policy = default_policy_for_preset("balanced")
    repo_override = normalize_repo_policy_override(
        {
            "categories": {
                "tool_authority": {
                    "default_severity": "high",
                    "min_final_risk_level": "high",
                    "merge_action": "block",
                }
            },
            "llm_strategy": {
                "when_to_run_semantic": "on_all_ai_relevant",
                "when_to_run_verifier": "on_medium_plus",
            },
            "gating": {"high_risk_action": "block", "medium_risk_action": "warn"},
            "attribute_templates": ["tool_authority_increase_with_sensitive_access"],
        }
    )

    resolved = resolve_effective_policy(workspace_policy, repo_override)

    assert resolved.source == PolicyScope.REPO.value
    assert resolved.policy.categories["tool_authority"].default_severity == RiskLevel.HIGH
    assert resolved.policy.categories["tool_authority"].merge_action == MergeAction.BLOCK
    assert resolved.policy.categories["model_drift"] == workspace_policy.categories["model_drift"]
    assert resolved.policy.llm_strategy.when_to_run_semantic == SemanticRunStrategy.ON_ALL_AI_RELEVANT
    assert resolved.policy.llm_strategy.when_to_run_verifier == VerifierRunStrategy.ON_MEDIUM_PLUS
    assert resolved.policy.gating.medium_risk_action == MergeAction.WARN
    assert workspace_policy.attribute_templates == ()
    assert resolved.policy.attribute_templates == ("tool_authority_increase_with_sensitive_access",)


def test_normalize_repo_policy_override_rejects_unknown_template_and_keys():
    with pytest.raises(ValueError, match="Unsupported repo override keys"):
        normalize_repo_policy_override({"unknown": True})

    with pytest.raises(ValueError, match="Unknown policy attribute template"):
        normalize_repo_policy_override({"attribute_templates": ["not-a-real-template"]})