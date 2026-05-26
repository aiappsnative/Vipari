from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from engine.models import RiskLevel


class MergeAction(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    REQUIRE_ESCALATION = "require_escalation"
    BLOCK = "block"


class SemanticRunStrategy(str, Enum):
    NEVER = "never"
    ON_MEDIUM_PLUS = "on_medium_plus"
    ON_ALL_AI_RELEVANT = "on_all_ai_relevant"


class VerifierRunStrategy(str, Enum):
    NEVER = "never"
    ON_HIGH_ONLY = "on_high_only"
    ON_MEDIUM_PLUS = "on_medium_plus"


class PolicyPreset(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    PERMISSIVE = "permissive"


class PolicyScope(str, Enum):
    WORKSPACE = "workspace"
    REPO = "repo"


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


@dataclass(frozen=True)
class PolicyCategoryDefinition:
    key: str
    label: str
    finding_ids: tuple[str, ...] = field(default_factory=tuple)
    attribute_keys: tuple[str, ...] = field(default_factory=tuple)
    immutable_minimum: RiskLevel | None = None


@dataclass(frozen=True)
class PolicyCategoryConfig:
    default_severity: RiskLevel
    min_final_risk_level: RiskLevel
    merge_action: MergeAction


@dataclass(frozen=True)
class PartialPolicyCategoryConfig:
    default_severity: RiskLevel | None = None
    min_final_risk_level: RiskLevel | None = None
    merge_action: MergeAction | None = None


@dataclass(frozen=True)
class PolicyLlmStrategy:
    when_to_run_semantic: SemanticRunStrategy
    when_to_run_verifier: VerifierRunStrategy


@dataclass(frozen=True)
class PartialPolicyLlmStrategy:
    when_to_run_semantic: SemanticRunStrategy | None = None
    when_to_run_verifier: VerifierRunStrategy | None = None


@dataclass(frozen=True)
class PolicyGatingConfig:
    high_risk_action: MergeAction
    medium_risk_action: MergeAction


@dataclass(frozen=True)
class PartialPolicyGatingConfig:
    high_risk_action: MergeAction | None = None
    medium_risk_action: MergeAction | None = None


@dataclass(frozen=True)
class OperationalPolicy:
    preset_key: PolicyPreset
    categories: dict[str, PolicyCategoryConfig]
    attribute_templates: tuple[str, ...]
    llm_strategy: PolicyLlmStrategy
    gating: PolicyGatingConfig


@dataclass(frozen=True)
class RepoPolicyOverride:
    categories: dict[str, PartialPolicyCategoryConfig] = field(default_factory=dict)
    attribute_templates: tuple[str, ...] = field(default_factory=tuple)
    llm_strategy: PartialPolicyLlmStrategy = field(default_factory=PartialPolicyLlmStrategy)
    gating: PartialPolicyGatingConfig = field(default_factory=PartialPolicyGatingConfig)


@dataclass(frozen=True)
class ResolvedEffectivePolicy:
    policy: OperationalPolicy
    source: str


_CATEGORY_DEFINITIONS: tuple[PolicyCategoryDefinition, ...] = (
    PolicyCategoryDefinition(
        key="secrets",
        label="Secrets",
        finding_ids=("sensitive_data_drift",),
        attribute_keys=("guardrail_robustness",),
        immutable_minimum=RiskLevel.MEDIUM,
    ),
    PolicyCategoryDefinition(
        key="pii",
        label="PII",
        finding_ids=("sensitive_data_drift",),
        attribute_keys=("guardrail_robustness",),
        immutable_minimum=RiskLevel.MEDIUM,
    ),
    PolicyCategoryDefinition(
        key="internal_policies",
        label="Internal Policies",
        finding_ids=("guardrail_drift", "guardrail_weakening"),
        attribute_keys=("guardrail_robustness",),
    ),
    PolicyCategoryDefinition(
        key="guardrail_weakening",
        label="Guardrail Weakening",
        finding_ids=("guardrail_drift", "guardrail_weakening"),
        attribute_keys=("guardrail_robustness",),
    ),
    PolicyCategoryDefinition(
        key="tool_authority",
        label="Tool Authority",
        finding_ids=("tooling_drift", "sensitive_tooling_drift"),
        attribute_keys=("capability_risk",),
    ),
    PolicyCategoryDefinition(
        key="model_drift",
        label="Model Drift",
        finding_ids=("model_drift",),
    ),
    PolicyCategoryDefinition(
        key="retrieval_scope",
        label="Retrieval Scope",
        finding_ids=("retrieval_drift",),
    ),
    PolicyCategoryDefinition(
        key="autonomy_increase",
        label="Autonomy Increase",
        finding_ids=("orchestration_drift",),
        attribute_keys=("autonomy_level",),
    ),
    PolicyCategoryDefinition(
        key="capability_risk",
        label="Capability Risk",
        finding_ids=("capability_drift", "sensitive_tooling_drift"),
        attribute_keys=("capability_risk",),
    ),
)

_CATEGORY_DEFINITION_BY_KEY = {definition.key: definition for definition in _CATEGORY_DEFINITIONS}
_ATTRIBUTE_TEMPLATE_KEYS = frozenset(
    {
        "guardrail_robustness_drop",
        "capability_increase_with_weaker_guardrails",
        "sensitive_data_without_privacy_controls",
        "tool_authority_increase_with_sensitive_access",
        "autonomy_increase_above_threshold",
    }
)

_PRESET_CATEGORY_OVERRIDES: dict[PolicyPreset, dict[str, tuple[str, str, str]]] = {
    PolicyPreset.CONSERVATIVE: {
        "secrets": ("high", "high", "block"),
        "pii": ("high", "high", "block"),
        "internal_policies": ("high", "high", "require_escalation"),
        "guardrail_weakening": ("high", "high", "block"),
        "tool_authority": ("high", "high", "block"),
        "model_drift": ("medium", "medium", "require_escalation"),
        "retrieval_scope": ("high", "high", "require_escalation"),
        "autonomy_increase": ("high", "high", "block"),
        "capability_risk": ("high", "high", "block"),
    },
    PolicyPreset.BALANCED: {
        "secrets": ("high", "high", "block"),
        "pii": ("medium", "medium", "require_escalation"),
        "internal_policies": ("medium", "medium", "warn"),
        "guardrail_weakening": ("medium", "medium", "require_escalation"),
        "tool_authority": ("medium", "medium", "require_escalation"),
        "model_drift": ("medium", "medium", "warn"),
        "retrieval_scope": ("medium", "medium", "require_escalation"),
        "autonomy_increase": ("medium", "medium", "require_escalation"),
        "capability_risk": ("medium", "medium", "warn"),
    },
    PolicyPreset.PERMISSIVE: {
        "secrets": ("medium", "medium", "warn"),
        "pii": ("low", "low", "warn"),
        "internal_policies": ("low", "low", "warn"),
        "guardrail_weakening": ("medium", "medium", "warn"),
        "tool_authority": ("low", "low", "warn"),
        "model_drift": ("low", "low", "warn"),
        "retrieval_scope": ("low", "low", "warn"),
        "autonomy_increase": ("low", "low", "warn"),
        "capability_risk": ("low", "low", "warn"),
    },
}

_PRESET_LLM_STRATEGIES: dict[PolicyPreset, PolicyLlmStrategy] = {
    PolicyPreset.CONSERVATIVE: PolicyLlmStrategy(
        when_to_run_semantic=SemanticRunStrategy.ON_ALL_AI_RELEVANT,
        when_to_run_verifier=VerifierRunStrategy.ON_MEDIUM_PLUS,
    ),
    PolicyPreset.BALANCED: PolicyLlmStrategy(
        when_to_run_semantic=SemanticRunStrategy.ON_MEDIUM_PLUS,
        when_to_run_verifier=VerifierRunStrategy.ON_HIGH_ONLY,
    ),
    PolicyPreset.PERMISSIVE: PolicyLlmStrategy(
        when_to_run_semantic=SemanticRunStrategy.ON_MEDIUM_PLUS,
        when_to_run_verifier=VerifierRunStrategy.NEVER,
    ),
}

_PRESET_GATING: dict[PolicyPreset, PolicyGatingConfig] = {
    PolicyPreset.CONSERVATIVE: PolicyGatingConfig(
        high_risk_action=MergeAction.BLOCK,
        medium_risk_action=MergeAction.REQUIRE_ESCALATION,
    ),
    PolicyPreset.BALANCED: PolicyGatingConfig(
        high_risk_action=MergeAction.BLOCK,
        medium_risk_action=MergeAction.REQUIRE_ESCALATION,
    ),
    PolicyPreset.PERMISSIVE: PolicyGatingConfig(
        high_risk_action=MergeAction.WARN,
        medium_risk_action=MergeAction.WARN,
    ),
}


def supported_policy_categories() -> tuple[PolicyCategoryDefinition, ...]:
    return _CATEGORY_DEFINITIONS


def get_policy_category_definition(category_key: str) -> PolicyCategoryDefinition:
    try:
        return _CATEGORY_DEFINITION_BY_KEY[category_key]
    except KeyError as exc:
        raise ValueError(f"Unknown policy category: {category_key}") from exc


def _normalize_risk_level(value: str | RiskLevel, *, immutable_minimum: RiskLevel | None = None) -> RiskLevel:
    if isinstance(value, RiskLevel):
        candidate = value
    else:
        normalized = str(value or "").strip().lower()
        if normalized == "high":
            candidate = RiskLevel.HIGH
        elif normalized == "medium":
            candidate = RiskLevel.MEDIUM
        elif normalized == "low":
            candidate = RiskLevel.LOW
        else:
            raise ValueError(f"Unsupported risk level: {value}")

    if immutable_minimum is not None and _RISK_ORDER[candidate] < _RISK_ORDER[immutable_minimum]:
        return immutable_minimum
    return candidate


def _normalize_merge_action(value: str | MergeAction) -> MergeAction:
    if isinstance(value, MergeAction):
        return value
    normalized = str(value or "").strip().lower()
    for candidate in MergeAction:
        if candidate.value == normalized:
            return candidate
    raise ValueError(f"Unsupported merge action: {value}")


def _normalize_semantic_strategy(value: str | SemanticRunStrategy) -> SemanticRunStrategy:
    if isinstance(value, SemanticRunStrategy):
        return value
    normalized = str(value or "").strip().lower()
    for candidate in SemanticRunStrategy:
        if candidate.value == normalized:
            return candidate
    raise ValueError(f"Unsupported semantic run strategy: {value}")


def _normalize_verifier_strategy(value: str | VerifierRunStrategy) -> VerifierRunStrategy:
    if isinstance(value, VerifierRunStrategy):
        return value
    normalized = str(value or "").strip().lower()
    for candidate in VerifierRunStrategy:
        if candidate.value == normalized:
            return candidate
    raise ValueError(f"Unsupported verifier run strategy: {value}")


def _normalize_preset(value: str | PolicyPreset | None) -> PolicyPreset:
    if value is None:
        return PolicyPreset.BALANCED
    if isinstance(value, PolicyPreset):
        return value
    normalized = str(value or "").strip().lower()
    for candidate in PolicyPreset:
        if candidate.value == normalized:
            return candidate
    raise ValueError(f"Unsupported policy preset: {value}")


def _normalize_attribute_templates(raw_templates: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if raw_templates is None:
        return tuple()
    normalized = []
    for template_key in raw_templates:
        candidate = str(template_key or "").strip().lower()
        if candidate not in _ATTRIBUTE_TEMPLATE_KEYS:
            raise ValueError(f"Unknown policy attribute template: {template_key}")
        if candidate not in normalized:
            normalized.append(candidate)
    return tuple(sorted(normalized))


def default_policy_for_preset(preset: str | PolicyPreset = PolicyPreset.BALANCED) -> OperationalPolicy:
    normalized_preset = _normalize_preset(preset)
    categories: dict[str, PolicyCategoryConfig] = {}
    for definition in _CATEGORY_DEFINITIONS:
        severity, floor, action = _PRESET_CATEGORY_OVERRIDES[normalized_preset][definition.key]
        categories[definition.key] = PolicyCategoryConfig(
            default_severity=_normalize_risk_level(severity, immutable_minimum=definition.immutable_minimum),
            min_final_risk_level=_normalize_risk_level(floor, immutable_minimum=definition.immutable_minimum),
            merge_action=_normalize_merge_action(action),
        )
    return OperationalPolicy(
        preset_key=normalized_preset,
        categories=categories,
        attribute_templates=tuple(),
        llm_strategy=_PRESET_LLM_STRATEGIES[normalized_preset],
        gating=_PRESET_GATING[normalized_preset],
    )


def normalize_operational_policy(raw_policy: Mapping[str, object] | None) -> OperationalPolicy:
    payload = dict(raw_policy or {})
    preset = _normalize_preset(payload.pop("preset_key", None))
    base_policy = default_policy_for_preset(preset)

    unsupported_keys = set(payload) - {"categories", "attribute_templates", "llm_strategy", "gating"}
    if unsupported_keys:
        raise ValueError(f"Unsupported policy keys: {sorted(unsupported_keys)}")

    categories = dict(base_policy.categories)
    raw_categories = payload.get("categories")
    if raw_categories is not None:
        if not isinstance(raw_categories, Mapping):
            raise ValueError("Policy categories must be an object.")
        for category_key, raw_config in raw_categories.items():
            definition = get_policy_category_definition(str(category_key))
            if not isinstance(raw_config, Mapping):
                raise ValueError(f"Policy category '{category_key}' must be an object.")
            unsupported_config_keys = set(raw_config) - {"default_severity", "min_final_risk_level", "merge_action"}
            if unsupported_config_keys:
                raise ValueError(
                    f"Unsupported config keys for category '{category_key}': {sorted(unsupported_config_keys)}"
                )
            categories[definition.key] = PolicyCategoryConfig(
                default_severity=_normalize_risk_level(
                    raw_config.get("default_severity", categories[definition.key].default_severity),
                    immutable_minimum=definition.immutable_minimum,
                ),
                min_final_risk_level=_normalize_risk_level(
                    raw_config.get("min_final_risk_level", categories[definition.key].min_final_risk_level),
                    immutable_minimum=definition.immutable_minimum,
                ),
                merge_action=_normalize_merge_action(raw_config.get("merge_action", categories[definition.key].merge_action)),
            )

    raw_llm_strategy = payload.get("llm_strategy")
    llm_strategy = base_policy.llm_strategy
    if raw_llm_strategy is not None:
        if not isinstance(raw_llm_strategy, Mapping):
            raise ValueError("Policy llm_strategy must be an object.")
        unsupported_llm_keys = set(raw_llm_strategy) - {"when_to_run_semantic", "when_to_run_verifier"}
        if unsupported_llm_keys:
            raise ValueError(f"Unsupported llm_strategy keys: {sorted(unsupported_llm_keys)}")
        llm_strategy = PolicyLlmStrategy(
            when_to_run_semantic=_normalize_semantic_strategy(
                raw_llm_strategy.get("when_to_run_semantic", llm_strategy.when_to_run_semantic)
            ),
            when_to_run_verifier=_normalize_verifier_strategy(
                raw_llm_strategy.get("when_to_run_verifier", llm_strategy.when_to_run_verifier)
            ),
        )

    raw_gating = payload.get("gating")
    gating = base_policy.gating
    if raw_gating is not None:
        if not isinstance(raw_gating, Mapping):
            raise ValueError("Policy gating must be an object.")
        unsupported_gating_keys = set(raw_gating) - {"high_risk_action", "medium_risk_action"}
        if unsupported_gating_keys:
            raise ValueError(f"Unsupported gating keys: {sorted(unsupported_gating_keys)}")
        gating = PolicyGatingConfig(
            high_risk_action=_normalize_merge_action(raw_gating.get("high_risk_action", gating.high_risk_action)),
            medium_risk_action=_normalize_merge_action(raw_gating.get("medium_risk_action", gating.medium_risk_action)),
        )

    return OperationalPolicy(
        preset_key=preset,
        categories=categories,
        attribute_templates=_normalize_attribute_templates(payload.get("attribute_templates")),
        llm_strategy=llm_strategy,
        gating=gating,
    )


def normalize_repo_policy_override(raw_override: Mapping[str, object] | None) -> RepoPolicyOverride:
    payload = dict(raw_override or {})
    unsupported_keys = set(payload) - {"categories", "attribute_templates", "llm_strategy", "gating"}
    if unsupported_keys:
        raise ValueError(f"Unsupported repo override keys: {sorted(unsupported_keys)}")

    categories: dict[str, PartialPolicyCategoryConfig] = {}
    raw_categories = payload.get("categories")
    if raw_categories is not None:
        if not isinstance(raw_categories, Mapping):
            raise ValueError("Repo override categories must be an object.")
        for category_key, raw_config in raw_categories.items():
            definition = get_policy_category_definition(str(category_key))
            if not isinstance(raw_config, Mapping):
                raise ValueError(f"Repo override category '{category_key}' must be an object.")
            unsupported_config_keys = set(raw_config) - {"default_severity", "min_final_risk_level", "merge_action"}
            if unsupported_config_keys:
                raise ValueError(
                    f"Unsupported config keys for repo category '{category_key}': {sorted(unsupported_config_keys)}"
                )
            categories[definition.key] = PartialPolicyCategoryConfig(
                default_severity=(
                    _normalize_risk_level(raw_config["default_severity"], immutable_minimum=definition.immutable_minimum)
                    if "default_severity" in raw_config
                    else None
                ),
                min_final_risk_level=(
                    _normalize_risk_level(raw_config["min_final_risk_level"], immutable_minimum=definition.immutable_minimum)
                    if "min_final_risk_level" in raw_config
                    else None
                ),
                merge_action=_normalize_merge_action(raw_config["merge_action"]) if "merge_action" in raw_config else None,
            )

    raw_llm_strategy = payload.get("llm_strategy")
    llm_strategy = PartialPolicyLlmStrategy()
    if raw_llm_strategy is not None:
        if not isinstance(raw_llm_strategy, Mapping):
            raise ValueError("Repo override llm_strategy must be an object.")
        unsupported_llm_keys = set(raw_llm_strategy) - {"when_to_run_semantic", "when_to_run_verifier"}
        if unsupported_llm_keys:
            raise ValueError(f"Unsupported repo llm_strategy keys: {sorted(unsupported_llm_keys)}")
        llm_strategy = PartialPolicyLlmStrategy(
            when_to_run_semantic=(
                _normalize_semantic_strategy(raw_llm_strategy["when_to_run_semantic"])
                if "when_to_run_semantic" in raw_llm_strategy
                else None
            ),
            when_to_run_verifier=(
                _normalize_verifier_strategy(raw_llm_strategy["when_to_run_verifier"])
                if "when_to_run_verifier" in raw_llm_strategy
                else None
            ),
        )

    raw_gating = payload.get("gating")
    gating = PartialPolicyGatingConfig()
    if raw_gating is not None:
        if not isinstance(raw_gating, Mapping):
            raise ValueError("Repo override gating must be an object.")
        unsupported_gating_keys = set(raw_gating) - {"high_risk_action", "medium_risk_action"}
        if unsupported_gating_keys:
            raise ValueError(f"Unsupported repo gating keys: {sorted(unsupported_gating_keys)}")
        gating = PartialPolicyGatingConfig(
            high_risk_action=_normalize_merge_action(raw_gating["high_risk_action"]) if "high_risk_action" in raw_gating else None,
            medium_risk_action=_normalize_merge_action(raw_gating["medium_risk_action"]) if "medium_risk_action" in raw_gating else None,
        )

    return RepoPolicyOverride(
        categories=categories,
        attribute_templates=_normalize_attribute_templates(payload.get("attribute_templates")),
        llm_strategy=llm_strategy,
        gating=gating,
    )


def resolve_effective_policy(
    workspace_policy: OperationalPolicy,
    repo_override: RepoPolicyOverride | None = None,
) -> ResolvedEffectivePolicy:
    if repo_override is None:
        return ResolvedEffectivePolicy(policy=workspace_policy, source=PolicyScope.WORKSPACE.value)

    categories = dict(workspace_policy.categories)
    for category_key, override in repo_override.categories.items():
        base = categories[category_key]
        categories[category_key] = PolicyCategoryConfig(
            default_severity=override.default_severity or base.default_severity,
            min_final_risk_level=override.min_final_risk_level or base.min_final_risk_level,
            merge_action=override.merge_action or base.merge_action,
        )

    llm_strategy = PolicyLlmStrategy(
        when_to_run_semantic=repo_override.llm_strategy.when_to_run_semantic or workspace_policy.llm_strategy.when_to_run_semantic,
        when_to_run_verifier=repo_override.llm_strategy.when_to_run_verifier or workspace_policy.llm_strategy.when_to_run_verifier,
    )
    gating = PolicyGatingConfig(
        high_risk_action=repo_override.gating.high_risk_action or workspace_policy.gating.high_risk_action,
        medium_risk_action=repo_override.gating.medium_risk_action or workspace_policy.gating.medium_risk_action,
    )
    attribute_templates = tuple(sorted(set(workspace_policy.attribute_templates) | set(repo_override.attribute_templates)))
    return ResolvedEffectivePolicy(
        policy=OperationalPolicy(
            preset_key=workspace_policy.preset_key,
            categories=categories,
            attribute_templates=attribute_templates,
            llm_strategy=llm_strategy,
            gating=gating,
        ),
        source=PolicyScope.REPO.value,
    )


def canonical_policy_dict(policy: OperationalPolicy) -> dict[str, object]:
    return {
        "preset_key": policy.preset_key.value,
        "categories": {
            definition.key: {
                "default_severity": policy.categories[definition.key].default_severity.value,
                "min_final_risk_level": policy.categories[definition.key].min_final_risk_level.value,
                "merge_action": policy.categories[definition.key].merge_action.value,
            }
            for definition in _CATEGORY_DEFINITIONS
        },
        "attribute_templates": list(policy.attribute_templates),
        "llm_strategy": {
            "when_to_run_semantic": policy.llm_strategy.when_to_run_semantic.value,
            "when_to_run_verifier": policy.llm_strategy.when_to_run_verifier.value,
        },
        "gating": {
            "high_risk_action": policy.gating.high_risk_action.value,
            "medium_risk_action": policy.gating.medium_risk_action.value,
        },
    }


def canonical_policy_json(policy: OperationalPolicy) -> str:
    return json.dumps(canonical_policy_dict(policy), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_policy_hash(policy: OperationalPolicy) -> str:
    return hashlib.sha256(canonical_policy_json(policy).encode("utf-8")).hexdigest()


def canonical_repo_policy_override_dict(policy_override: RepoPolicyOverride) -> dict[str, object]:
    return {
        "categories": {
            definition.key: {
                key: value
                for key, value in {
                    "default_severity": (
                        policy_override.categories[definition.key].default_severity.value
                        if definition.key in policy_override.categories and policy_override.categories[definition.key].default_severity is not None
                        else None
                    ),
                    "min_final_risk_level": (
                        policy_override.categories[definition.key].min_final_risk_level.value
                        if definition.key in policy_override.categories and policy_override.categories[definition.key].min_final_risk_level is not None
                        else None
                    ),
                    "merge_action": (
                        policy_override.categories[definition.key].merge_action.value
                        if definition.key in policy_override.categories and policy_override.categories[definition.key].merge_action is not None
                        else None
                    ),
                }.items()
                if value is not None
            }
            for definition in _CATEGORY_DEFINITIONS
            if definition.key in policy_override.categories
        },
        "attribute_templates": list(policy_override.attribute_templates),
        "llm_strategy": {
            key: value
            for key, value in {
                "when_to_run_semantic": (
                    policy_override.llm_strategy.when_to_run_semantic.value
                    if policy_override.llm_strategy.when_to_run_semantic is not None
                    else None
                ),
                "when_to_run_verifier": (
                    policy_override.llm_strategy.when_to_run_verifier.value
                    if policy_override.llm_strategy.when_to_run_verifier is not None
                    else None
                ),
            }.items()
            if value is not None
        },
        "gating": {
            key: value
            for key, value in {
                "high_risk_action": (
                    policy_override.gating.high_risk_action.value
                    if policy_override.gating.high_risk_action is not None
                    else None
                ),
                "medium_risk_action": (
                    policy_override.gating.medium_risk_action.value
                    if policy_override.gating.medium_risk_action is not None
                    else None
                ),
            }.items()
            if value is not None
        },
    }


def canonical_repo_policy_override_json(policy_override: RepoPolicyOverride) -> str:
    return json.dumps(
        canonical_repo_policy_override_dict(policy_override),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_repo_policy_override_hash(policy_override: RepoPolicyOverride) -> str:
    return hashlib.sha256(canonical_repo_policy_override_json(policy_override).encode("utf-8")).hexdigest()