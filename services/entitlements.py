from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class PlanDefinition:
    code: str
    label: str
    price_lookup_keys: tuple[str, ...]
    repo_limit: int
    org_limit: int
    seat_limit: int
    retention_policy: str
    support_tier: str
    requires_billing: bool = True
    dashboard_enabled: bool = True
    pr_comments_enabled: bool = True


PLAN_DEFINITIONS = {
    "free": PlanDefinition(
        code="free",
        label="Free",
        price_lookup_keys=(),
        repo_limit=1,
        org_limit=1,
        seat_limit=1,
        retention_policy="basic",
        support_tier="community",
        requires_billing=False,
        dashboard_enabled=False,
        pr_comments_enabled=True,
    ),
    "starter": PlanDefinition(
        code="starter",
        label="Starter",
        price_lookup_keys=("stripe_price_starter",),
        repo_limit=5,
        org_limit=1,
        seat_limit=5,
        retention_policy="standard",
        support_tier="community",
    ),
    "team": PlanDefinition(
        code="team",
        label="Team",
        price_lookup_keys=("stripe_price_team",),
        repo_limit=20,
        org_limit=3,
        seat_limit=25,
        retention_policy="extended",
        support_tier="priority",
    ),
    "enterprise": PlanDefinition(
        code="enterprise",
        label="Enterprise",
        price_lookup_keys=("stripe_price_enterprise", "stripe_price_business"),
        repo_limit=100,
        org_limit=20,
        seat_limit=250,
        retention_policy="enterprise",
        support_tier="white-glove",
    ),
}

PLAN_ALIASES = {
    "business": "enterprise",
}

DEFAULT_ANALYSIS_BUDGET_WINDOW_SECONDS = 30 * 24 * 60 * 60
PLAN_ANALYSIS_BUDGET_LIMITS: dict[str, int | None] = {
    "free": 100,
    "starter": 2000,
    "team": 20000,
    "enterprise": None,
}


def _parse_feature_flags(feature_flags_json: str | None) -> dict[str, object]:
    if not feature_flags_json:
        return {}
    try:
        parsed = json.loads(feature_flags_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_plan_code(plan_code: str) -> str:
    normalized = (plan_code or "").strip().lower()
    if not normalized:
        raise ValueError("Plan code is required.")
    return PLAN_ALIASES.get(normalized, normalized)


def get_plan_definition(plan_code: str) -> PlanDefinition:
    normalized = normalize_plan_code(plan_code)
    plan = PLAN_DEFINITIONS.get(normalized)
    if plan is None:
        raise ValueError(f"Unknown plan code: {plan_code}")
    return plan


def get_analysis_budget_limit(plan_code: str, feature_flags_json: str | None = None) -> int | None:
    flags = _parse_feature_flags(feature_flags_json)
    raw_limit = flags.get("advanced_analysis_units_limit")
    if raw_limit is not None:
        try:
            return max(0, int(raw_limit))
        except (TypeError, ValueError):
            pass
    plan = get_plan_definition(plan_code)
    return PLAN_ANALYSIS_BUDGET_LIMITS[plan.code]


def get_analysis_budget_window_seconds(feature_flags_json: str | None = None) -> int:
    flags = _parse_feature_flags(feature_flags_json)
    raw_window = flags.get("advanced_analysis_window_seconds")
    if raw_window is None:
        return DEFAULT_ANALYSIS_BUDGET_WINDOW_SECONDS
    try:
        return max(3600, int(raw_window))
    except (TypeError, ValueError):
        return DEFAULT_ANALYSIS_BUDGET_WINDOW_SECONDS


def _coerce_non_negative_float(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    return numeric


def get_analysis_budget_price_threshold_usd(feature_flags_json: str | None = None) -> float | None:
    flags = _parse_feature_flags(feature_flags_json)
    return _coerce_non_negative_float(flags.get("advanced_analysis_price_threshold_usd"))


def get_analysis_budget_window_price_threshold_usd(feature_flags_json: str | None = None) -> float | None:
    flags = _parse_feature_flags(feature_flags_json)
    return _coerce_non_negative_float(flags.get("advanced_analysis_window_price_threshold_usd"))


def get_analysis_budget_alert_utilization_percent(feature_flags_json: str | None = None) -> float:
    flags = _parse_feature_flags(feature_flags_json)
    configured = _coerce_non_negative_float(flags.get("advanced_analysis_alert_utilization_percent"))
    if configured is None:
        return 80.0
    return min(max(configured, 1.0), 100.0)


def resolve_analysis_budget_provider_rates(
    feature_flags_json: str | None,
    *,
    provider: str | None,
    model: str | None,
) -> tuple[float, float] | None:
    flags = _parse_feature_flags(feature_flags_json)
    raw_tables = flags.get("advanced_analysis_provider_costs")
    if not isinstance(raw_tables, dict):
        return None

    normalized_provider = (provider or "").strip().lower()
    if not normalized_provider:
        return None
    provider_table = raw_tables.get(normalized_provider)
    if not isinstance(provider_table, dict):
        return None

    normalized_model = (model or "").strip().lower()
    rate_source: object | None = None
    if normalized_model:
        rate_source = provider_table.get(normalized_model)
    if rate_source is None:
        rate_source = provider_table.get("*")
    if rate_source is None and "prompt_per_1k_usd" in provider_table and "completion_per_1k_usd" in provider_table:
        rate_source = provider_table
    if not isinstance(rate_source, dict):
        return None

    prompt_rate = _coerce_non_negative_float(rate_source.get("prompt_per_1k_usd"))
    completion_rate = _coerce_non_negative_float(rate_source.get("completion_per_1k_usd"))
    if prompt_rate is None or completion_rate is None:
        return None
    return prompt_rate, completion_rate


def resolve_price_id(settings, plan_code: str) -> str:
    plan = get_plan_definition(plan_code)
    if not plan.requires_billing:
        return f"local_{plan.code}"
    for lookup_key in plan.price_lookup_keys:
        configured = getattr(settings, lookup_key, "")
        if configured:
            return configured
    return f"local_{plan.code}"


def derive_entitlement_payload(plan_code: str, subscription_status: str) -> dict[str, object]:
    plan = get_plan_definition(plan_code)
    normalized_status = (subscription_status or "").strip().lower()
    active_statuses = {"active", "trialing", "free_active"}
    warning_statuses = {"canceled"}
    status_allows_access = normalized_status in active_statuses or normalized_status in warning_statuses
    dashboard_enabled = plan.dashboard_enabled and status_allows_access
    pr_comments_enabled = plan.pr_comments_enabled and status_allows_access
    return {
        "plan_code": plan.code,
        "subscription_status": normalized_status,
        "dashboard_enabled": dashboard_enabled,
        "pr_comments_enabled": pr_comments_enabled,
        "repo_limit": plan.repo_limit,
        "org_limit": plan.org_limit,
        "seat_limit": plan.seat_limit,
        "retention_policy": plan.retention_policy,
        "support_tier": plan.support_tier,
        "feature_flags_json": "{}",
    }