from __future__ import annotations

from typing import Literal


BASELINE_APPROVAL_MODE_MANUAL = "manual"
BASELINE_APPROVAL_MODE_AUTO = "auto"

BaselineApprovalMode = Literal[
    "manual",
    "auto",
]

BASELINE_APPROVAL_MODES: tuple[BaselineApprovalMode, ...] = (
    BASELINE_APPROVAL_MODE_MANUAL,
    BASELINE_APPROVAL_MODE_AUTO,
)


def normalize_baseline_approval_mode(
    value: str | None,
    *,
    default: BaselineApprovalMode = BASELINE_APPROVAL_MODE_MANUAL,
) -> BaselineApprovalMode:
    normalized = str(value or "").strip().lower()
    if normalized in BASELINE_APPROVAL_MODES:
        return normalized  # type: ignore[return-value]
    return default