#!/usr/bin/env python
from __future__ import annotations

import os
from urllib.parse import urlparse

from dotenv import load_dotenv


def _mask(name: str, value: str | None) -> str:
    if value is None or value == "":
        return "MISSING"
    if any(token in name for token in ("KEY", "SECRET", "TOKEN", "PRIVATE")):
        return f"set length={len(value)}"
    return value


def _print_group(title: str, keys: list[str]) -> list[str]:
    missing: list[str] = []
    print(f"\n--- {title} ---")
    for key in keys:
        value = os.getenv(key)
        print(f"{key}: {_mask(key, value)}")
        if not value:
            missing.append(key)
    return missing


def _print_group_with_defaults(title: str, defaults: dict[str, str]) -> None:
    print(f"\n--- {title} ---")
    for key, default_value in defaults.items():
        value = os.getenv(key)
        if value:
            print(f"{key}: {_mask(key, value)}")
        else:
            print(f"{key}: default -> {default_value}")


def _same_origin(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return (urlparse(left).scheme, urlparse(left).netloc) == (urlparse(right).scheme, urlparse(right).netloc)


def main() -> None:
    load_dotenv()

    app_missing = _print_group(
        "Core app settings",
        ["APP_BASE_URL", "APP_ENCRYPTION_KEY"],
    )
    _print_group_with_defaults(
        "Session settings with safe defaults",
        {"SESSION_COOKIE_NAME": "promptdrift_session", "SESSION_TTL_SECONDS": "604800", "SESSION_COOKIE_SECURE": "false"},
    )
    oauth_missing = _print_group(
        "GitHub OAuth settings",
        ["GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET", "GITHUB_OAUTH_CALLBACK_URL"],
    )
    github_app_missing = _print_group(
        "GitHub App settings",
        ["GITHUB_APP_ID", "GITHUB_PRIVATE_KEY_PATH", "GITHUB_WEBHOOK_SECRET"],
    )
    stripe_missing = _print_group(
        "Stripe billing settings",
        [
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "STRIPE_PRICE_STARTER",
            "STRIPE_PRICE_TEAM",
            "STRIPE_PRICE_ENTERPRISE",
        ],
    )

    print("\n--- Consistency checks ---")
    app_base_url = os.getenv("APP_BASE_URL")
    oauth_callback_url = os.getenv("GITHUB_OAUTH_CALLBACK_URL")
    if app_base_url and oauth_callback_url:
        if _same_origin(app_base_url, oauth_callback_url):
            print("APP_BASE_URL and GITHUB_OAUTH_CALLBACK_URL share the same origin: OK")
        else:
            print("APP_BASE_URL and GITHUB_OAUTH_CALLBACK_URL do not share the same origin: CHECK")

    if oauth_callback_url and not oauth_callback_url.endswith("/auth/github/callback"):
        print("GITHUB_OAUTH_CALLBACK_URL should normally end with /auth/github/callback: CHECK")

    install_callback_url = f"{app_base_url.rstrip('/')}/app/setup/install/callback" if app_base_url else None
    if install_callback_url:
        print(f"Recommended GitHub App setup URL: {install_callback_url}")
        print(f"Recommended Stripe forward target: {app_base_url.rstrip('/')}/webhooks/stripe")

    total_missing = app_missing + oauth_missing + github_app_missing + stripe_missing
    print("\n--- Summary ---")
    if total_missing:
        print(f"Missing {len(total_missing)} setting(s):")
        for key in total_missing:
            print(f"- {key}")
    else:
        print("All core control-plane settings are present.")


if __name__ == "__main__":
    main()