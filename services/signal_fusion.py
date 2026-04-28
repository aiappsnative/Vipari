from __future__ import annotations


RISK_ORDER = {"Low": 0, "Medium": 1, "High": 2}
RISK_LEVELS = ("Low", "Medium", "High")
PRIORITY_ORDER = {"baseline_review": 0, "watch": 1, "review_now": 2}
PRIORITY_WEIGHT_BONUS = {"baseline_review": 0.0, "watch": 0.35, "review_now": 1.0}


def normalize_risk_level(risk_level: str | None, *, default: str = "Low") -> str:
    candidate = (risk_level or default).strip().lower()
    if candidate == "high":
        return "High"
    if candidate == "medium":
        return "Medium"
    if candidate == "low":
        return "Low"
    return normalize_risk_level(default, default="Low")


def fuse_risk_levels(
    deterministic_risk: str | None,
    semantic_risk: str | None,
    *,
    semantic_requires_escalation: bool = False,
) -> str:
    normalized_deterministic = normalize_risk_level(deterministic_risk)
    normalized_semantic = normalize_risk_level(semantic_risk)
    deterministic_order = RISK_ORDER[normalized_deterministic]
    semantic_order = RISK_ORDER[normalized_semantic]

    if normalized_deterministic == normalized_semantic == "Medium":
        return "High"

    if semantic_order > deterministic_order and not semantic_requires_escalation:
        bounded_order = min(deterministic_order + 1, semantic_order)
        return RISK_LEVELS[bounded_order]

    if semantic_order >= deterministic_order:
        return normalized_semantic
    return normalized_deterministic


def priority_from_fused_signals(score: float, *, risk_level: str | None = None) -> str:
    if score >= 1.25:
        base_priority = "review_now"
    elif score >= 0.6:
        base_priority = "watch"
    else:
        base_priority = "baseline_review"

    normalized_risk = normalize_risk_level(risk_level)
    if normalized_risk == "High":
        risk_priority = "review_now"
    elif normalized_risk == "Medium":
        risk_priority = "watch"
    else:
        risk_priority = "baseline_review"

    if PRIORITY_ORDER[risk_priority] > PRIORITY_ORDER[base_priority]:
        return risk_priority
    return base_priority


def priority_sort_rank(priority: str | None) -> int:
    normalized_priority = (priority or "").strip().lower()
    if normalized_priority == "review_now":
        return 0
    if normalized_priority == "watch":
        return 1
    if normalized_priority == "baseline_review":
        return 2
    return 9


def priority_weighted_risk(score: float, priority: str | None = None) -> float:
    normalized_priority = (priority or "baseline_review").strip().lower()
    bonus = PRIORITY_WEIGHT_BONUS.get(normalized_priority, 0.0)
    return round(score + bonus, 4)