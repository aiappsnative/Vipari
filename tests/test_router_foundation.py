import os
import sys

from fastapi import FastAPI


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import main
from services.api_service import create_api_app


def _route_inventory(app: FastAPI) -> set[tuple[str, tuple[str, ...]]]:
    inventory: set[tuple[str, tuple[str, ...]]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or methods is None:
            continue
        normalized_methods = tuple(sorted(method for method in methods if method not in {"HEAD", "OPTIONS"}))
        inventory.add((path, normalized_methods))
    return inventory


def _count_routes(app: FastAPI, path: str, method: str) -> int:
    return sum(1 for route_path, methods in _route_inventory(app) if route_path == path and method in methods)


def test_main_app_health_routes_exist_once():
    assert _count_routes(main.app, "/health", "GET") == 1
    assert _count_routes(main.app, "/health/ready", "GET") == 1


def test_api_service_health_routes_exist_once(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "admin-token")
    app = create_api_app()

    assert _count_routes(app, "/health", "GET") == 1
    assert _count_routes(app, "/health/ready", "GET") == 1


def test_main_and_api_apps_keep_expected_foundation_routes(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "admin-token")
    api_app = create_api_app()

    assert ("/health", ("GET",)) in _route_inventory(main.app)
    assert ("/health/ready", ("GET",)) in _route_inventory(main.app)
    assert ("/static", ()) not in _route_inventory(main.app)

    assert ("/health", ("GET",)) in _route_inventory(api_app)
    assert ("/health/ready", ("GET",)) in _route_inventory(api_app)
    assert any(getattr(route, "path", None) == "/static" for route in main.app.routes)
    assert any(getattr(route, "path", None) == "/static" for route in api_app.routes)