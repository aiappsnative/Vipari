from __future__ import annotations

from html import escape as html_escape
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_TEMPLATES_DIR = BASE_DIR / "templates"
DASHBOARD_STATIC_DIR = BASE_DIR / "static"


def _asset_url(path: str) -> str:
    asset_path = BASE_DIR / path.lstrip("/")
    try:
        version = asset_path.stat().st_mtime_ns
    except OSError:
        version = 0
    return f"{path}?v={version}"


def _load_template(name: str) -> str:
    template = (DASHBOARD_TEMPLATES_DIR / name).read_text(encoding="utf-8")
    template = template.replace('/static/dashboard.css', _asset_url('/static/dashboard.css'))
    template = template.replace('/static/dashboard-index.js', _asset_url('/static/dashboard-index.js'))
    template = template.replace('/static/dashboard-repo.js', _asset_url('/static/dashboard-repo.js'))
    return template


def render_dashboard_index_page(theme_preference: str = "dark") -> str:
    return _load_template("dashboard_index.html").replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))


def render_repo_dashboard_page(repo_full: str, theme_preference: str = "dark") -> str:
    return (
        _load_template("dashboard_repo.html")
        .replace("{{REPO_FULL}}", html_escape(repo_full))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
    )
