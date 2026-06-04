import os
import sys
import json
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.analysis_budget import consume_analysis_budget, reserve_analysis_budget
from services.audit_jobs import init_db
from services.control_plane_records import (
    create_user,
    create_workspace,
    get_all_workspace_budget_summary,
    get_workspace_budget_status,
    upsert_entitlement,
)
from services.entitlements import derive_entitlement_payload


def _seed_workspace(db_path: str, *, slug: str, display_name: str, plan_code: str, limit: int):
    owner = create_user(db_path, display_name=f"{display_name} Owner", primary_email=f"{slug}@example.com")
    workspace = create_workspace(db_path, slug=slug, display_name=display_name, billing_owner_user_id=owner.id)
    payload = derive_entitlement_payload(plan_code, "active")
    payload["feature_flags_json"] = (
        '{"advanced_analysis_units_limit": %d, "advanced_analysis_window_seconds": 86400}' % limit
        if limit >= 0
        else "{}"
    )
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=payload)
    return workspace


def test_get_workspace_budget_status_returns_control_plane_summary(tmp_path):
    db_path = str(tmp_path / "cp-budget-summary.db")
    init_db(db_path)
    workspace = _seed_workspace(db_path, slug="alpha", display_name="Alpha", plan_code="team", limit=10)

    reservation = reserve_analysis_budget(
        db_path,
        workspace_id=workspace.id,
        feature_key="semantic_review",
        reservation_key="alpha-semantic",
        estimated_units=5,
        now=86410.0,
    )
    assert reservation.allowed is True
    consume_analysis_budget(db_path, reservation_key=reservation.reservation_key, consumed_units=5, note="semantic used")

    summary = get_workspace_budget_status(db_path, workspace.id, now=86440.0)

    assert summary is not None
    assert summary.workspace_slug == "alpha"
    assert summary.plan_code == "team"
    assert summary.unit_limit == 10
    assert summary.used_units == 5
    assert summary.remaining_units == 5
    assert summary.feature_breakdown[0]["feature_key"] == "semantic_review"


def test_get_workspace_budget_status_includes_alerts_and_estimated_cost(tmp_path):
    db_path = str(tmp_path / "cp-budget-alerts.db")
    init_db(db_path)
    workspace = _seed_workspace(db_path, slug="alerts", display_name="Alerts", plan_code="team", limit=10)
    payload = derive_entitlement_payload("team", "active")
    payload["feature_flags_json"] = json.dumps(
        {
            "advanced_analysis_units_limit": 10,
            "advanced_analysis_window_seconds": 86400,
            "advanced_analysis_price_threshold_usd": 0.01,
            "advanced_analysis_provider_costs": {
                "openai": {
                    "gpt-4o": {
                        "prompt_per_1k_usd": 0.01,
                        "completion_per_1k_usd": 0.03,
                    }
                }
            },
        }
    )
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=payload)

    reservation = reserve_analysis_budget(
        db_path,
        workspace_id=workspace.id,
        feature_key="semantic_review",
        reservation_key="alerts-semantic",
        estimated_units=5,
        now=86410.0,
    )
    consume_analysis_budget(
        db_path,
        reservation_key=reservation.reservation_key,
        consumed_units=5,
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=1000),
        provider="openai",
        model="gpt-4o",
        note="semantic used",
    )

    summary = get_workspace_budget_status(db_path, workspace.id, now=86440.0)

    assert summary is not None
    assert summary.estimated_cost_usd == 0.04
    assert summary.alert_state == "warning"
    assert summary.alerts[0]["code"] == "provider_price_threshold_exceeded"


def test_get_all_workspace_budget_summary_sorts_by_used_units_desc(tmp_path):
    db_path = str(tmp_path / "cp-budget-list.db")
    init_db(db_path)
    alpha = _seed_workspace(db_path, slug="alpha", display_name="Alpha", plan_code="team", limit=20)
    beta = _seed_workspace(db_path, slug="beta", display_name="Beta", plan_code="starter", limit=10)

    reservation = reserve_analysis_budget(
        db_path,
        workspace_id=alpha.id,
        feature_key="semantic_review",
        reservation_key="alpha-semantic",
        estimated_units=5,
        now=86410.0,
    )
    consume_analysis_budget(db_path, reservation_key=reservation.reservation_key, consumed_units=5, note="semantic used")

    reservation = reserve_analysis_budget(
        db_path,
        workspace_id=beta.id,
        feature_key="micro_classifier",
        reservation_key="beta-classifier",
        estimated_units=1,
        now=86420.0,
    )
    consume_analysis_budget(db_path, reservation_key=reservation.reservation_key, consumed_units=1, note="classifier used")

    summaries = get_all_workspace_budget_summary(db_path, limit=10, now=86440.0)

    assert [item.workspace_slug for item in summaries[:2]] == ["alpha", "beta"]
    assert summaries[0].used_units == 5
    assert summaries[1].used_units == 1