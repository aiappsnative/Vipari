import io
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from config import Settings
from services.billing_service import create_billing_portal_session, create_checkout_session, derive_billing_projection


def test_create_checkout_session_uses_live_stripe_when_configured():
    settings = Settings(
        app_base_url="http://127.0.0.1:8014",
        stripe_secret_key="sk_test_123",
        stripe_webhook_secret="whsec_123",
        stripe_price_team="price_team_live",
    )

    with patch(
        "services.billing_service.urllib.request.urlopen",
        return_value=io.StringIO(
            json.dumps(
                {
                    "id": "cs_live_123",
                    "url": "https://checkout.stripe.com/c/pay/cs_live_123",
                    "customer": "cus_live_123",
                    "subscription": "sub_live_123",
                }
            )
        ),
    ):
        checkout = create_checkout_session(
            settings=settings,
            workspace_id=7,
            workspace_slug="driftguard-team",
            plan_code="team",
            stripe_customer_id="cus_existing",
        )

    assert checkout.session_id == "cs_live_123"
    assert checkout.checkout_url == "https://checkout.stripe.com/c/pay/cs_live_123"
    assert checkout.stripe_customer_id == "cus_live_123"
    assert checkout.stripe_subscription_id == "sub_live_123"


def test_create_billing_portal_session_uses_live_stripe_when_configured():
    settings = Settings(
        stripe_secret_key="sk_test_123",
        stripe_webhook_secret="whsec_123",
        stripe_portal_configuration_id="bpc_123",
    )

    with patch(
        "services.billing_service.urllib.request.urlopen",
        return_value=io.StringIO(json.dumps({"url": "https://billing.stripe.com/session/test"})),
    ):
        portal_url = create_billing_portal_session(
            settings=settings,
            stripe_customer_id="cus_live_123",
            return_url="http://127.0.0.1:8014/app/billing",
        )

    assert portal_url == "https://billing.stripe.com/session/test"


def test_derive_billing_projection_handles_checkout_completed_metadata():
    projection = derive_billing_projection(
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_live_123",
                    "customer": "cus_live_123",
                    "subscription": "sub_live_123",
                    "metadata": {
                        "workspace_id": "5",
                        "plan_code": "team",
                        "price_id": "price_team_live",
                    },
                }
            },
        }
    )

    assert projection is not None
    assert projection["workspace_id"] == 5
    assert projection["stripe_subscription_id"] == "sub_live_123"
    assert projection["stripe_customer_id"] == "cus_live_123"
    assert projection["plan_code"] == "team"