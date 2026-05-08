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


def _shell_data_value(value: str | int | None) -> str:
    return html_escape("" if value is None else str(value), quote=True)


def _dashboard_shell_notice_html(*, shell_title: str, shell_body: str, shell_cta_href: str | None, shell_cta_label: str | None) -> str:
    cta_markup = ""
    if shell_cta_href and shell_cta_label:
        cta_markup = f'<a class="filter-add" href="{html_escape(shell_cta_href, quote=True)}">{html_escape(shell_cta_label)}</a>'
    return (
        '<section class="card-shell dashboard-shell-notice" id="dashboard-shell-notice" aria-label="Dashboard access status">'
        f'<div class="secondary-panel-title">{html_escape(shell_title)}</div>'
        f'<div class="muted">{html_escape(shell_body)}</div>'
        f'{cta_markup}'
        '</section>'
    )


def render_dashboard_index_page(
    theme_preference: str = "dark",
    active_range: str = "7d",
    active_filter: str = "all",
    *,
    sidebar_profile_initial: str = "V",
    shell_state: str = "active",
    shell_title: str = "",
    shell_body: str = "",
    shell_cta_href: str | None = None,
    shell_cta_label: str | None = None,
    deep_link_artifact: str = "",
    deep_link_pr: str = "",
    deep_link_head_sha: str = "",
) -> str:
    shell_notice = ""
    blocked_class = ""
    if shell_state != "active":
        blocked_class = " dashboard-shell-blocked"
        shell_notice = _dashboard_shell_notice_html(
            shell_title=shell_title,
            shell_body=shell_body,
            shell_cta_href=shell_cta_href,
            shell_cta_label=shell_cta_label,
        )
    return (
        _load_template("dashboard_index.html")
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
        .replace("{{ACTIVE_OVERVIEW_RANGE}}", html_escape(active_range))
        .replace("{{ACTIVE_OVERVIEW_FILTER}}", html_escape(active_filter))
        .replace("{{DASHBOARD_SHELL_STATE}}", _shell_data_value(shell_state))
        .replace("{{DASHBOARD_SHELL_TITLE}}", _shell_data_value(shell_title))
        .replace("{{DASHBOARD_SHELL_BODY}}", _shell_data_value(shell_body))
        .replace("{{DASHBOARD_SHELL_CTA_HREF}}", _shell_data_value(shell_cta_href))
        .replace("{{DASHBOARD_SHELL_CTA_LABEL}}", _shell_data_value(shell_cta_label))
        .replace("{{DASHBOARD_DEEP_LINK_ARTIFACT}}", _shell_data_value(deep_link_artifact))
        .replace("{{DASHBOARD_DEEP_LINK_PR}}", _shell_data_value(deep_link_pr))
        .replace("{{DASHBOARD_DEEP_LINK_HEAD_SHA}}", _shell_data_value(deep_link_head_sha))
        .replace("{{DASHBOARD_SHELL_NOTICE}}", shell_notice)
        .replace("{{DASHBOARD_BLOCKED_CLASS}}", blocked_class)
        .replace("{{OVERVIEW_RANGE_24H_URL}}", _dashboard_index_url(active_range="24h", active_filter=active_filter))
        .replace("{{OVERVIEW_RANGE_7D_URL}}", _dashboard_index_url(active_range="7d", active_filter=active_filter))
        .replace("{{OVERVIEW_RANGE_30D_URL}}", _dashboard_index_url(active_range="30d", active_filter=active_filter))
        .replace("{{OVERVIEW_FILTER_ALL_URL}}", _dashboard_index_url(active_range=active_range, active_filter="all"))
        .replace("{{OVERVIEW_FILTER_CRITICAL_URL}}", _dashboard_index_url(active_range=active_range, active_filter="critical"))
        .replace("{{OVERVIEW_FILTER_MINE_URL}}", _dashboard_index_url(active_range=active_range, active_filter="mine"))
    )


def render_repo_dashboard_page(
    repo_full: str,
    theme_preference: str = "dark",
    active_tab: str = "audit",
    *,
    sidebar_profile_initial: str = "V",
    shell_state: str = "active",
    shell_title: str = "",
    shell_body: str = "",
    shell_cta_href: str | None = None,
    shell_cta_label: str | None = None,
    deep_link_artifact: str = "",
    deep_link_pr: str = "",
    deep_link_head_sha: str = "",
) -> str:
    encoded_repo_full = quote(repo_full, safe="")
    base_url = f"/dashboard/{encoded_repo_full}"
    shell_notice = ""
    blocked_class = ""
    if shell_state != "active":
        blocked_class = " dashboard-shell-blocked"
        shell_notice = _dashboard_shell_notice_html(
            shell_title=shell_title,
            shell_body=shell_body,
            shell_cta_href=shell_cta_href,
            shell_cta_label=shell_cta_label,
        )
    query_suffix = ""
    if deep_link_artifact or deep_link_pr or deep_link_head_sha:
        params: list[str] = []
        if deep_link_artifact:
            params.append(f"artifact={quote(deep_link_artifact, safe='')}")
        if deep_link_pr:
            params.append(f"pr={quote(deep_link_pr, safe='')}")
        if deep_link_head_sha:
            params.append(f"head_sha={quote(deep_link_head_sha, safe='')}")
        query_suffix = "&" + "&".join(params)
    template = (
        _load_template("dashboard_repo.html")
        .replace("{{REPO_FULL}}", html_escape(repo_full))
        .replace("{{THEME_PREFERENCE}}", html_escape(theme_preference))
        .replace("{{SIDEBAR_PROFILE_INITIAL}}", html_escape(sidebar_profile_initial or "V"))
        .replace("{{ACTIVE_REPO_TAB}}", html_escape(active_tab))
        .replace("{{DASHBOARD_SHELL_STATE}}", _shell_data_value(shell_state))
        .replace("{{DASHBOARD_SHELL_TITLE}}", _shell_data_value(shell_title))
        .replace("{{DASHBOARD_SHELL_BODY}}", _shell_data_value(shell_body))
        .replace("{{DASHBOARD_SHELL_CTA_HREF}}", _shell_data_value(shell_cta_href))
        .replace("{{DASHBOARD_SHELL_CTA_LABEL}}", _shell_data_value(shell_cta_label))
        .replace("{{DASHBOARD_DEEP_LINK_ARTIFACT}}", _shell_data_value(deep_link_artifact))
        .replace("{{DASHBOARD_DEEP_LINK_PR}}", _shell_data_value(deep_link_pr))
        .replace("{{DASHBOARD_DEEP_LINK_HEAD_SHA}}", _shell_data_value(deep_link_head_sha))
        .replace("{{DASHBOARD_SHELL_NOTICE}}", shell_notice)
        .replace("{{DASHBOARD_BLOCKED_CLASS}}", blocked_class)
        .replace("{{REPO_TAB_AUDIT_URL}}", f"{base_url}/audit{query_suffix.replace('&', '?', 1)}" if query_suffix else f"{base_url}/audit")
        .replace("{{REPO_TAB_DRIFT_URL}}", f"{base_url}?tab=drift{query_suffix}")
        .replace("{{REPO_TAB_VERSION_CONTROL_URL}}", f"{base_url}?tab=version-control{query_suffix}")
        .replace("{{REPO_TAB_BASELINE_URL}}", f"{base_url}?tab=baseline{query_suffix}")
        .replace("{{REPO_TAB_COMPLIANCE_URL}}", f"{base_url}?tab=compliance{query_suffix}")
        .replace("{{REPO_TAB_REPORTS_URL}}", f"{base_url}?tab=reports{query_suffix}")
    )
    return template
