"""Core DriftGuard detection engine package."""

from .analysis import analyze_diff
from .context_selector import determine_context_mode
from .diff_parser import extract_changed_files, extract_structured_change
from .relevance import get_ai_relevance_results, needs_audit
from .semantic_review import build_semantic_review_package, build_semantic_review_packages

__all__ = [
    "analyze_diff",
    "build_semantic_review_package",
    "build_semantic_review_packages",
    "determine_context_mode",
    "extract_changed_files",
    "extract_structured_change",
    "get_ai_relevance_results",
    "needs_audit",
]
