from __future__ import annotations

from functools import lru_cache
from html import escape as html_escape
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_TEMPLATES_DIR = BASE_DIR / "templates"
DASHBOARD_STATIC_DIR = BASE_DIR / "static"


@lru_cache(maxsize=None)
def _load_template(name: str) -> str:
    return (DASHBOARD_TEMPLATES_DIR / name).read_text(encoding="utf-8")


def render_dashboard_index_page() -> str:
    return _load_template("dashboard_index.html")


def render_repo_dashboard_page(repo_full: str) -> str:
    return _load_template("dashboard_repo.html").replace("{{REPO_FULL}}", html_escape(repo_full))
