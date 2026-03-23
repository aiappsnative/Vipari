from __future__ import annotations

import json
from dataclasses import asdict, dataclass


BASELINE_SOURCE_NONE = "none"
BASELINE_SOURCE_APPROVED = "approved_baseline"
BASELINE_SOURCE_ONBOARDING = "onboarding_baseline"
BASELINE_SOURCE_HISTORICAL = "historical_reference"
BASELINE_SOURCE_PREVIOUS_PR = "previous_pr_reference"


@dataclass(frozen=True)
class BaselineProvenance:
    source_type: str
    source_profile_id: int | None
    source_version_id: int | None
    is_authoritative: bool
    label: str


def baseline_provenance_to_json(provenance: BaselineProvenance | None) -> str | None:
    if provenance is None:
        return None
    return json.dumps(asdict(provenance))


def baseline_provenance_from_json(payload: str | None) -> BaselineProvenance | None:
    if not payload:
        return None
    parsed = json.loads(payload)
    return BaselineProvenance(
        source_type=str(parsed["source_type"]),
        source_profile_id=parsed.get("source_profile_id"),
        source_version_id=parsed.get("source_version_id"),
        is_authoritative=bool(parsed.get("is_authoritative", False)),
        label=str(parsed.get("label", "")),
    )


def no_baseline_provenance() -> BaselineProvenance:
    return BaselineProvenance(
        source_type=BASELINE_SOURCE_NONE,
        source_profile_id=None,
        source_version_id=None,
        is_authoritative=False,
        label="no baseline yet",
    )


def approved_onboarding_provenance(version_id: int) -> BaselineProvenance:
    return BaselineProvenance(
        source_type=BASELINE_SOURCE_APPROVED,
        source_profile_id=None,
        source_version_id=version_id,
        is_authoritative=True,
        label="approved baseline (onboarding)",
    )


def historical_fallback_provenance(profile_id: int | None, version_id: int | None = None) -> BaselineProvenance:
    return BaselineProvenance(
        source_type=BASELINE_SOURCE_HISTORICAL,
        source_profile_id=profile_id,
        source_version_id=version_id,
        is_authoritative=False,
        label="historical fallback",
    )


def previous_pr_fallback_provenance(profile_id: int | None, version_id: int | None = None) -> BaselineProvenance:
    return BaselineProvenance(
        source_type=BASELINE_SOURCE_PREVIOUS_PR,
        source_profile_id=profile_id,
        source_version_id=version_id,
        is_authoritative=False,
        label="previous PR fallback",
    )