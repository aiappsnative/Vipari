from __future__ import annotations

from typing import Literal


PR_FEEDBACK_MODE_COMMENTS = "comments"
PR_FEEDBACK_MODE_REVIEWS = "reviews"
PR_FEEDBACK_MODE_OFF = "off"

PrFeedbackMode = Literal[
    "comments",
    "reviews",
    "off",
]

PR_FEEDBACK_MODES: tuple[PrFeedbackMode, ...] = (
    PR_FEEDBACK_MODE_COMMENTS,
    PR_FEEDBACK_MODE_REVIEWS,
    PR_FEEDBACK_MODE_OFF,
)


def normalize_pr_feedback_mode(value: str | None, *, default: PrFeedbackMode = PR_FEEDBACK_MODE_COMMENTS) -> PrFeedbackMode:
    normalized = str(value or "").strip().lower()
    if normalized in PR_FEEDBACK_MODES:
        return normalized  # type: ignore[return-value]
    return default


def resolve_pr_feedback_mode(
    workspace_mode: str | None,
    repo_override: str | None,
    *,
    default: PrFeedbackMode = PR_FEEDBACK_MODE_COMMENTS,
) -> PrFeedbackMode:
    if repo_override is not None and str(repo_override).strip():
        return normalize_pr_feedback_mode(repo_override, default=default)
    return normalize_pr_feedback_mode(workspace_mode, default=default)