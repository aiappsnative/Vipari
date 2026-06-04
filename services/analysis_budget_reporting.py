from __future__ import annotations

import time
from dataclasses import dataclass

from .analysis_budget import resolve_analysis_budget_policy
from .control_plane_records import EntitlementRecord, get_workspace_entitlement
from .entitlements import (
    get_analysis_budget_alert_utilization_percent,
    get_analysis_budget_price_threshold_usd,
    get_analysis_budget_window_price_threshold_usd,
    resolve_analysis_budget_provider_rates,
)
from .persistence import connect_sqlite


@dataclass(frozen=True)
class BudgetFeatureUsage:
    feature_key: str
    used_units: int
    reserved_units: int
    consumed_units: int
    event_count: int


@dataclass(frozen=True)
class BudgetAlert:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class WorkspaceBudgetStatus:
    workspace_id: int
    unit_limit: int | None
    used_units: int
    remaining_units: int | None
    utilization_percent: float | None
    estimated_cost_usd: float | None
    alert_state: str
    window_start: float
    window_end: float
    feature_breakdown: tuple[BudgetFeatureUsage, ...]
    alerts: tuple[BudgetAlert, ...]


def get_workspace_budget_status(
    db_path: str,
    *,
    workspace_id: int,
    entitlement: EntitlementRecord | None = None,
    now: float | None = None,
) -> WorkspaceBudgetStatus:
    resolved_entitlement = entitlement or get_workspace_entitlement(db_path, workspace_id)
    policy = resolve_analysis_budget_policy(db_path, workspace_id=workspace_id, entitlement=resolved_entitlement)
    current_time = now or time.time()
    window_start = _window_start_for_time(current_time, policy.window_seconds)
    window_end = window_start + policy.window_seconds

    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                feature_key,
                COUNT(*) AS event_count,
                COALESCE(SUM(units_reserved), 0) AS reserved_units,
                COALESCE(SUM(units_consumed), 0) AS consumed_units,
                COALESCE(SUM(
                    CASE
                        WHEN status = 'released' THEN 0
                        WHEN status = 'consumed' THEN CASE WHEN units_consumed > units_reserved THEN units_consumed ELSE units_reserved END
                        ELSE units_reserved
                    END
                ), 0) AS used_units
            FROM workspace_analysis_budget_events
            WHERE workspace_id = ? AND window_start = ?
            GROUP BY feature_key
            ORDER BY used_units DESC, feature_key ASC
            """,
            (workspace_id, window_start),
        ).fetchall()

        event_rows = conn.execute(
            """
            SELECT feature_key, status, prompt_tokens, completion_tokens, provider, model
            FROM workspace_analysis_budget_events
            WHERE workspace_id = ? AND window_start = ?
            ORDER BY created_at ASC, id ASC
            """,
            (workspace_id, window_start),
        ).fetchall()

    feature_breakdown = tuple(
        BudgetFeatureUsage(
            feature_key=row["feature_key"],
            used_units=int(row["used_units"] or 0),
            reserved_units=int(row["reserved_units"] or 0),
            consumed_units=int(row["consumed_units"] or 0),
            event_count=int(row["event_count"] or 0),
        )
        for row in rows
    )
    used_units = sum(item.used_units for item in feature_breakdown)
    if policy.unit_limit is None:
        remaining_units = None
        utilization_percent = None
    else:
        remaining_units = max(policy.unit_limit - used_units, 0)
        if policy.unit_limit == 0:
            utilization_percent = 0.0 if used_units == 0 else 100.0
        else:
            utilization_percent = round((used_units / policy.unit_limit) * 100.0, 2)

    estimated_cost_usd, alerts, alert_state = _build_budget_alerts(
        event_rows,
        feature_flags_json=resolved_entitlement.feature_flags_json if resolved_entitlement is not None else None,
        unit_limit=policy.unit_limit,
        remaining_units=remaining_units,
        utilization_percent=utilization_percent,
    )

    return WorkspaceBudgetStatus(
        workspace_id=workspace_id,
        unit_limit=policy.unit_limit,
        used_units=used_units,
        remaining_units=remaining_units,
        utilization_percent=utilization_percent,
        estimated_cost_usd=estimated_cost_usd,
        alert_state=alert_state,
        window_start=window_start,
        window_end=window_end,
        feature_breakdown=feature_breakdown,
        alerts=alerts,
    )


def _window_start_for_time(now: float, window_seconds: int) -> float:
    return float(int(now // window_seconds) * window_seconds)


def _build_budget_alerts(
    event_rows,
    *,
    feature_flags_json: str | None,
    unit_limit: int | None,
    remaining_units: int | None,
    utilization_percent: float | None,
) -> tuple[float | None, tuple[BudgetAlert, ...], str]:
    price_threshold = get_analysis_budget_price_threshold_usd(feature_flags_json)
    window_price_threshold = get_analysis_budget_window_price_threshold_usd(feature_flags_json)
    utilization_threshold = get_analysis_budget_alert_utilization_percent(feature_flags_json)

    total_estimated_cost = 0.0
    has_cost_data = False
    alerts: list[BudgetAlert] = []

    for row in event_rows:
        if row["status"] == "released":
            continue
        estimated_cost = _estimate_event_cost_usd(
            feature_flags_json,
            provider=row["provider"],
            model=row["model"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
        )
        if estimated_cost is None:
            continue
        has_cost_data = True
        total_estimated_cost += estimated_cost
        if price_threshold is not None and estimated_cost > price_threshold:
            alerts.append(
                BudgetAlert(
                    code="provider_price_threshold_exceeded",
                    severity="warning",
                    message=(
                        f"{str(row['feature_key'] or 'analysis')} on {str(row['provider'] or 'unknown')}"
                        f"/{str(row['model'] or '*')} estimated ${estimated_cost:.4f}, above the"
                        f" configured per-event threshold of ${price_threshold:.4f}."
                    ),
                )
            )

    estimated_cost_usd = round(total_estimated_cost, 4) if has_cost_data else None
    if estimated_cost_usd is not None and window_price_threshold is not None and estimated_cost_usd > window_price_threshold:
        alerts.append(
            BudgetAlert(
                code="window_price_threshold_exceeded",
                severity="warning",
                message=(
                    f"Current-window estimated advanced-analysis spend is ${estimated_cost_usd:.4f}, above the"
                    f" configured window threshold of ${window_price_threshold:.4f}."
                ),
            )
        )

    if unit_limit is not None and (remaining_units or 0) <= 0:
        alerts.append(
            BudgetAlert(
                code="budget_exhausted",
                severity="high",
                message="Current-window advanced-analysis budget is exhausted for this workspace.",
            )
        )
    elif utilization_percent is not None and utilization_percent >= utilization_threshold:
        alerts.append(
            BudgetAlert(
                code="budget_low",
                severity="warning",
                message=f"Current-window advanced-analysis budget is {utilization_percent:.1f}% used.",
            )
        )

    alert_state = "healthy"
    if any(alert.severity == "high" for alert in alerts):
        alert_state = "high"
    elif alerts:
        alert_state = "warning"
    return estimated_cost_usd, tuple(alerts), alert_state


def _estimate_event_cost_usd(
    feature_flags_json: str | None,
    *,
    provider: str | None,
    model: str | None,
    prompt_tokens: object,
    completion_tokens: object,
) -> float | None:
    rates = resolve_analysis_budget_provider_rates(feature_flags_json, provider=provider, model=model)
    if rates is None:
        return None
    try:
        prompt = max(int(prompt_tokens or 0), 0)
        completion = max(int(completion_tokens or 0), 0)
    except (TypeError, ValueError):
        return None
    prompt_rate, completion_rate = rates
    estimated_cost = ((prompt / 1000.0) * prompt_rate) + ((completion / 1000.0) * completion_rate)
    return round(estimated_cost, 6)