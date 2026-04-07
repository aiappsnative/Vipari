import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import Settings
from services.audit_jobs import init_db
from services.entitlements import derive_entitlement_payload, get_plan_definition, resolve_price_id


def test_entitlement_catalog_supports_enterprise_and_business_alias():
    enterprise = get_plan_definition("enterprise")
    business_alias = get_plan_definition("business")

    assert enterprise.code == "enterprise"
    assert business_alias.code == "enterprise"
    assert enterprise.repo_limit == 100


def test_entitlement_payload_disables_dashboard_for_failed_billing():
    payload = derive_entitlement_payload("team", "payment_failed")

    assert payload["plan_code"] == "team"
    assert payload["dashboard_enabled"] is False
    assert payload["repo_limit"] == 20


def test_price_resolution_prefers_enterprise_key_but_supports_legacy_business_key():
    settings = Settings(
        stripe_price_enterprise="price_enterprise_live",
        stripe_price_business="price_business_legacy",
    )

    assert resolve_price_id(settings, "enterprise") == "price_enterprise_live"

    legacy_only = Settings(stripe_price_business="price_business_legacy")
    assert resolve_price_id(legacy_only, "business") == "price_business_legacy"


def test_init_db_creates_control_plane_tables(tmp_path):
    db_path = str(tmp_path / "promptdrift.db")

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "users" in tables
    assert "workspaces" in tables
    assert "subscriptions" in tables
    assert "repo_allocations" in tables
    assert "webhook_event_receipts" in tables