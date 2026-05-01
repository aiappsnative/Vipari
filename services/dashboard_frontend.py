from __future__ import annotations

from html import escape as html_escape
from pathlib import Path
from urllib.parse import quote

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
    template = template.replace('/static/theme-toggle.js', _asset_url('/static/theme-toggle.js'))
    return template


def _dashboard_index_url(*, active_range: str, active_filter: str) -> str:
    return f"/dashboard?range={active_range}&filter={active_filter}"


def render_dashboard_index_page(theme_preference: str = "dark", active_range: str = "7d", active_filter: str = "all") -> str:
    return (
        _load_template("dashboard_index.html")
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{ACTIVE_OVERVIEW_RANGE}}", html_escape(active_range))
        .replace("{{ACTIVE_OVERVIEW_FILTER}}", html_escape(active_filter))
        .replace("{{OVERVIEW_RANGE_24H_URL}}", _dashboard_index_url(active_range="24h", active_filter=active_filter))
        .replace("{{OVERVIEW_RANGE_7D_URL}}", _dashboard_index_url(active_range="7d", active_filter=active_filter))
        .replace("{{OVERVIEW_RANGE_30D_URL}}", _dashboard_index_url(active_range="30d", active_filter=active_filter))
        .replace("{{OVERVIEW_FILTER_ALL_URL}}", _dashboard_index_url(active_range=active_range, active_filter="all"))
        .replace("{{OVERVIEW_FILTER_CRITICAL_URL}}", _dashboard_index_url(active_range=active_range, active_filter="critical"))
        .replace("{{OVERVIEW_FILTER_MINE_URL}}", _dashboard_index_url(active_range=active_range, active_filter="mine"))
    )


def render_repo_dashboard_page(repo_full: str, theme_preference: str = "dark", active_tab: str = "drift") -> str:
    encoded_repo_full = quote(repo_full, safe="")
    base_url = f"/dashboard/{encoded_repo_full}"
    template = (
        _load_template("dashboard_repo.html")
        .replace("{{REPO_FULL}}", html_escape(repo_full))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{ACTIVE_REPO_TAB}}", html_escape(active_tab))
        .replace("{{REPO_TAB_DRIFT_URL}}", f"{base_url}?tab=drift")
        .replace("{{REPO_TAB_VERSION_CONTROL_URL}}", f"{base_url}?tab=version-control")
        .replace("{{REPO_TAB_BASELINE_URL}}", f"{base_url}?tab=baseline")
        .replace("{{REPO_TAB_COMPLIANCE_URL}}", f"{base_url}?tab=compliance")
        .replace("{{REPO_TAB_REPORTS_URL}}", f"{base_url}?tab=reports")
    )
    return template
