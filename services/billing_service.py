from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .entitlements import derive_entitlement_payload, get_plan_definition, resolve_price_id


@dataclass(frozen=True)
class CheckoutSession:
    session_id: str
    checkout_url: str
    stripe_customer_id: str
    stripe_subscription_id: str
    stripe_price_id: str
    plan_code: str


def _can_use_live_stripe(settings, plan_code: str) -> bool:
    price_id = resolve_price_id(settings, plan_code)
    return bool(settings.stripe_secret_key and price_id and not price_id.startswith("local_"))


def _stripe_form_request(secret_key: str, path: str, payload: dict[str, str]) -> dict[str, object]:
    encoded = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(f"https://api.stripe.com{path}", data=encoded, method="POST")
    request.add_header("Authorization", f"Bearer {secret_key}")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def create_checkout_session(
    *,
    settings,
    workspace_id: int,
    workspace_slug: str,
    plan_code: str,
    stripe_customer_id: str | None = None,
) -> CheckoutSession:
    plan = get_plan_definition(plan_code)
    price_id = resolve_price_id(settings, plan.code)
    if _can_use_live_stripe(settings, plan.code):
        payload = {
            "mode": "subscription",
            "success_url": f"{settings.app_base_url}/app/billing?checkout_session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{settings.app_base_url}/app/billing?canceled=1",
            "client_reference_id": str(workspace_id),
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "metadata[workspace_id]": str(workspace_id),
            "metadata[plan_code]": plan.code,
        }
        if stripe_customer_id:
            payload["customer"] = stripe_customer_id
        response = _stripe_form_request(settings.stripe_secret_key, "/v1/checkout/sessions", payload)
        return CheckoutSession(
            session_id=str(response.get("id") or ""),
            checkout_url=str(response.get("url") or f"{settings.app_base_url}/app/billing"),
            stripe_customer_id=str(response.get("customer") or stripe_customer_id or ""),
            stripe_subscription_id=str(response.get("subscription") or ""),
            stripe_price_id=price_id,
            plan_code=plan.code,
        )

    now = int(time.time())
    seed = f"{workspace_id}:{workspace_slug}:{plan.code}:{now}"
    token = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    session_id = f"cs_test_{token}"
    customer_id = f"cus_{hashlib.sha256(f'customer:{seed}'.encode('utf-8')).hexdigest()[:18]}"
    subscription_id = f"sub_{hashlib.sha256(f'subscription:{seed}'.encode('utf-8')).hexdigest()[:18]}"
    checkout_url = f"/app/billing?checkout_session_id={session_id}&plan={plan.code}"
    return CheckoutSession(
        session_id=session_id,
        checkout_url=checkout_url,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        stripe_price_id=price_id,
        plan_code=plan.code,
    )


def build_portal_url(workspace_id: int) -> str:
    return f"/app/billing?workspace_id={workspace_id}&portal=1"


def create_billing_portal_session(*, settings, stripe_customer_id: str, return_url: str) -> str:
    if settings.stripe_secret_key and stripe_customer_id:
        payload = {"customer": stripe_customer_id, "return_url": return_url}
        if settings.stripe_portal_configuration_id:
            payload["configuration"] = settings.stripe_portal_configuration_id
        response = _stripe_form_request(settings.stripe_secret_key, "/v1/billing_portal/sessions", payload)
        portal_url = str(response.get("url") or "")
        if portal_url:
            return portal_url
    return return_url


def build_stripe_signature(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    issued_at = timestamp or int(time.time())
    signed_payload = f"{issued_at}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={issued_at},v1={digest}"


def verify_stripe_signature(payload: bytes, signature_header: str, secret: str, *, tolerance_seconds: int = 300) -> None:
    if not signature_header:
        raise ValueError("Missing Stripe signature header.")
    parsed: dict[str, str] = {}
    for part in signature_header.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        parsed[key.strip()] = value.strip()
    timestamp = int(parsed.get("t") or 0)
    provided = parsed.get("v1") or ""
    if not timestamp or not provided:
        raise ValueError("Invalid Stripe signature header.")
    if abs(int(time.time()) - timestamp) > tolerance_seconds:
        raise ValueError("Expired Stripe signature header.")
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise ValueError("Stripe signature verification failed.")


def parse_stripe_event(payload: bytes) -> dict[str, object]:
    event = json.loads(payload.decode("utf-8"))
    if not isinstance(event, dict) or "type" not in event or "data" not in event:
        raise ValueError("Invalid Stripe event payload.")
    return event


def derive_billing_projection(event: dict[str, object]) -> dict[str, object] | None:
    event_type = str(event.get("type") or "")
    data = event.get("data") or {}
    stripe_object = data.get("object") if isinstance(data, dict) else {}
    if not isinstance(stripe_object, dict):
        stripe_object = {}
    metadata = stripe_object.get("metadata") if isinstance(stripe_object.get("metadata"), dict) else {}

    workspace_id_raw = metadata.get("workspace_id") or stripe_object.get("workspace_id")
    if workspace_id_raw in (None, ""):
        return None

    workspace_id = int(workspace_id_raw)
    plan_code = str(metadata.get("plan_code") or stripe_object.get("plan_code") or "starter").lower()
    plan = get_plan_definition(plan_code)
    stripe_customer_id = str(stripe_object.get("customer") or metadata.get("customer_id") or "")
    items = stripe_object.get("items") if isinstance(stripe_object.get("items"), dict) else {}
    item_data = items.get("data") if isinstance(items.get("data"), list) else []
    first_item = item_data[0] if item_data else {}
    first_price = first_item.get("price") if isinstance(first_item.get("price"), dict) else {}
    stripe_subscription_id = str(stripe_object.get("subscription") or stripe_object.get("id") or metadata.get("subscription_id") or "")
    stripe_price_id = str(metadata.get("price_id") or first_price.get("id") or stripe_object.get("price") or "")
    status = str(stripe_object.get("status") or "")

    if event_type == "checkout.session.completed":
        status = status or "incomplete"
    elif event_type == "invoice.payment_failed":
        status = "payment_failed"
    elif event_type == "customer.subscription.deleted":
        status = "canceled"
    elif event_type in {"customer.subscription.created", "customer.subscription.updated", "invoice.paid"}:
        status = status or "active"
    else:
        return None

    entitlement_payload = derive_entitlement_payload(plan.code, status)
    return {
        "workspace_id": workspace_id,
        "plan_code": plan.code,
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "stripe_price_id": stripe_price_id,
        "status": status,
        "cancel_at_period_end": bool(stripe_object.get("cancel_at_period_end")),
        "current_period_start_at": float(stripe_object.get("current_period_start") or 0) or None,
        "current_period_end_at": float(stripe_object.get("current_period_end") or 0) or None,
        "trial_ends_at": float(stripe_object.get("trial_end") or 0) or None,
        "billing_email": str(stripe_object.get("customer_email") or metadata.get("billing_email") or "") or None,
        "entitlement": entitlement_payload,
    }