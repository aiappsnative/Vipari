import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.analysis_budget import consume_analysis_budget, release_analysis_budget, reserve_analysis_budget
from services.analysis_budget_reporting import get_workspace_budget_status
from services.audit_jobs import init_db
from services.control_plane_records import create_user, create_workspace, upsert_entitlement
from services.entitlements import derive_entitlement_payload


def test_workspace_budget_status_returns_current_window_breakdown(tmp_path):
    db_path = str(tmp_path / "budget-reporting.db")
    init_db(db_path)
    owner = create_user(db_path, display_name="Budget Owner", primary_email="budget@example.com")
    workspace = create_workspace(db_path, slug="budget-reporting", display_name="Budget Reporting", billing_owner_user_id=owner.id)
    payload = derive_entitlement_payload("team", "active")
    payload["feature_flags_json"] = '{"advanced_analysis_units_limit": 10, "advanced_analysis_window_seconds": 86400}'
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=payload)

    semantic = reserve_analysis_budget(
        db_path,
        workspace_id=workspace.id,
        feature_key="semantic_review",
        reservation_key="semantic-1",
        estimated_units=5,
        now=86410.0,
    )
    assert semantic.allowed is True
    consume_analysis_budget(db_path, reservation_key=semantic.reservation_key, consumed_units=5, note="semantic used")

    classifier = reserve_analysis_budget(
        db_path,
        workspace_id=workspace.id,
        feature_key="micro_classifier",
        reservation_key="classifier-1",
        estimated_units=1,
        now=86420.0,
    )
    assert classifier.allowed is True
    consume_analysis_budget(db_path, reservation_key=classifier.reservation_key, consumed_units=1, note="classifier used")

    released = reserve_analysis_budget(
        db_path,
        workspace_id=workspace.id,
        feature_key="verifier",
        reservation_key="verifier-1",
        estimated_units=5,
        now=86430.0,
    )
    assert released.allowed is False

    status = get_workspace_budget_status(db_path, workspace_id=workspace.id, now=86440.0)

    assert status.unit_limit == 10
    assert status.used_units == 6
    assert status.remaining_units == 4
    assert status.utilization_percent == 60.0
    assert [item.feature_key for item in status.feature_breakdown] == ["semantic_review", "micro_classifier"]
    assert status.feature_breakdown[0].used_units == 5
    assert status.feature_breakdown[1].used_units == 1


def test_workspace_budget_status_supports_unlimited_plan_defaults(tmp_path):
    db_path = str(tmp_path / "budget-reporting-unlimited.db")
    init_db(db_path)
    owner = create_user(db_path, display_name="Budget Owner", primary_email="budget@example.com")
    workspace = create_workspace(db_path, slug="budget-unlimited", display_name="Budget Unlimited", billing_owner_user_id=owner.id)
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=derive_entitlement_payload("enterprise", "active"))

    reservation = reserve_analysis_budget(
        db_path,
        workspace_id=workspace.id,
        feature_key="semantic_review",
        reservation_key="enterprise-semantic",
        estimated_units=5,
        now=172800.0,
    )
    assert reservation.allowed is True
    consume_analysis_budget(db_path, reservation_key=reservation.reservation_key, consumed_units=7, note="semantic used")
    release_analysis_budget(db_path, reservation_key=None)

    status = get_workspace_budget_status(db_path, workspace_id=workspace.id, now=172860.0)

    assert status.unit_limit is None
    assert status.used_units == 0
    assert status.remaining_units is None
    assert status.utilization_percent is None
    assert status.feature_breakdown == ()


def test_workspace_budget_status_includes_cost_alerts_from_provider_tables(tmp_path):
    db_path = str(tmp_path / "budget-reporting-alerts.db")
    init_db(db_path)
    owner = create_user(db_path, display_name="Budget Owner", primary_email="budget@example.com")
    workspace = create_workspace(db_path, slug="budget-alerts", display_name="Budget Alerts", billing_owner_user_id=owner.id)
    payload = derive_entitlement_payload("team", "active")
    payload["feature_flags_json"] = (
        '{'
        '"advanced_analysis_units_limit": 10,'
        '"advanced_analysis_window_seconds": 86400,'
        '"advanced_analysis_price_threshold_usd": 0.01,'
        '"advanced_analysis_window_price_threshold_usd": 0.02,'
        '"advanced_analysis_provider_costs": {'
        '  "openai": {'
        '    "gpt-4o": {"prompt_per_1k_usd": 0.01, "completion_per_1k_usd": 0.03}'
        '  }'
        '}'
        '}'
    )
    upsert_entitlement(db_path, workspace_id=workspace.id, payload=payload)

    semantic = reserve_analysis_budget(
        db_path,
        workspace_id=workspace.id,
        feature_key="semantic_review",
        reservation_key="semantic-cost-1",
        estimated_units=5,
        now=86410.0,
    )
    consume_analysis_budget(
        db_path,
        reservation_key=semantic.reservation_key,
        consumed_units=5,
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=1000),
        provider="openai",
        model="gpt-4o",
        note="semantic used",
    )

    status = get_workspace_budget_status(db_path, workspace_id=workspace.id, now=86440.0)

    assert status.estimated_cost_usd == 0.04
    assert status.alert_state == "warning"
    assert [item.code for item in status.alerts] == [
        "provider_price_threshold_exceeded",
        "window_price_threshold_exceeded",
    ]