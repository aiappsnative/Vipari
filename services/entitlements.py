from __future__ import annotations

from dataclasses import dataclass


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
    dashboard_enabled: bool = True


PLAN_DEFINITIONS = {
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


def resolve_price_id(settings, plan_code: str) -> str:
    plan = get_plan_definition(plan_code)
    for lookup_key in plan.price_lookup_keys:
        configured = getattr(settings, lookup_key, "")
        if configured:
            return configured
    return f"local_{plan.code}"


def derive_entitlement_payload(plan_code: str, subscription_status: str) -> dict[str, object]:
    plan = get_plan_definition(plan_code)
    normalized_status = (subscription_status or "").strip().lower()
    active_statuses = {"active", "trialing"}
    warning_statuses = {"canceled"}
    dashboard_enabled = normalized_status in active_statuses or normalized_status in warning_statuses
    return {
        "plan_code": plan.code,
        "subscription_status": normalized_status,
        "dashboard_enabled": dashboard_enabled,
        "repo_limit": plan.repo_limit,
        "org_limit": plan.org_limit,
        "seat_limit": plan.seat_limit,
        "retention_policy": plan.retention_policy,
        "support_tier": plan.support_tier,
        "feature_flags_json": "{}",
    }