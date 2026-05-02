from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class GovernanceFinding:
    finding_type: str
    severity: str
    artifact_id: str
    repo: str
    pr_number: int | None
    evidence_summary: str
    recommended_action: str
    confidence: str


@dataclass(frozen=True)
class RepoGovernancePosture:
    ownership_confidence: str
    review_quality: str
    repeated_drift_without_refresh_count: int
    baseline_freshness_status: str
    top_governance_anomalies: tuple[GovernanceFinding, ...]


@dataclass(frozen=True)
class GovernanceAttentionSummary:
    repos_with_anomalies_count: int
    high_risk_missing_review_count: int
    repeated_drift_surfaces: tuple[str, ...]
    ranked_issues_now: tuple[GovernanceFinding, ...]


def build_pr_comment_governance_findings(
    attribute_profiles: list[object] | None,
    *,
    repo: str = "unknown",
    pr_number: int | None = None,
    decision: str | None = None,
    include_missing_profile_fallback: bool = True,
) -> tuple[GovernanceFinding, ...]:
    profiles = [profile for profile in (attribute_profiles or []) if _dimensions(profile)]
    findings: list[GovernanceFinding] = []

    if not profiles:
        if not include_missing_profile_fallback:
            return ()
        return (
            GovernanceFinding(
                finding_type="low_governance_confidence",
                severity="warning",
                artifact_id="unknown artifact",
                repo=repo,
                pr_number=pr_number,
                evidence_summary="Ownership and review quality could not be determined confidently from the stored evidence for this change.",
                recommended_action="Confirm the expected owner or security reviewer manually before accepting this AI control change.",
                confidence="low",
            ),
        )

    seen: set[tuple[str, str]] = set()
    for profile in profiles:
        governance_dimension = _dimension(profile, "governance_strength")
        if governance_dimension is None or governance_dimension.state == "unknown":
            _append_finding(
                findings,
                seen,
                GovernanceFinding(
                    finding_type="low_governance_confidence",
                    severity="warning",
                    artifact_id=profile.artifact_path,
                    repo=repo,
                    pr_number=pr_number,
                    evidence_summary=f"Ownership or review coverage for `{profile.artifact_path}` is not clear enough in the stored governance evidence.",
                    recommended_action="Confirm the expected owner, review path, and approval intent manually before merge.",
                    confidence="low",
                ),
            )
            continue

        if _is_high_risk_profile(profile) and _is_governance_weakened(governance_dimension):
            _append_finding(
                findings,
                seen,
                GovernanceFinding(
                    finding_type=_governance_gap_type(governance_dimension),
                    severity="high",
                    artifact_id=profile.artifact_path,
                    repo=repo,
                    pr_number=pr_number,
                    evidence_summary=_normalized_reason(governance_dimension.reason),
                    recommended_action=_recommended_action(governance_dimension, decision=decision),
                    confidence=_confidence_token(governance_dimension.confidence_label),
                ),
            )

        if not _has_authoritative_baseline(profile) and _is_high_risk_profile(profile):
            _append_finding(
                findings,
                seen,
                GovernanceFinding(
                    finding_type="low_governance_confidence",
                    severity="warning",
                    artifact_id=profile.artifact_path,
                    repo=repo,
                    pr_number=pr_number,
                    evidence_summary=f"`{profile.artifact_path}` has no approved baseline yet, so governance expectations for this higher-risk surface are not fully anchored.",
                    recommended_action="Promote or refresh the approved baseline after review so future governance drift is measured against an authoritative reference.",
                    confidence="medium",
                ),
            )

    return tuple(findings[:4])


def _append_finding(findings: list[GovernanceFinding], seen: set[tuple[str, str]], finding: GovernanceFinding) -> None:
    signature = (finding.artifact_id, finding.finding_type)
    if signature in seen:
        return
    seen.add(signature)
    findings.append(finding)


def _dimension(profile: object, attribute_key: str):
    return next((dimension for dimension in _dimensions(profile) if dimension.attribute_key == attribute_key), None)


def _dimensions(profile: object) -> list[object]:
    dimensions = getattr(profile, "dimensions", None)
    if dimensions:
        return list(dimensions)
    attribute_profile = getattr(profile, "attribute_profile", None)
    if attribute_profile:
        return list(attribute_profile)
    return []


def _has_authoritative_baseline(profile: object) -> bool:
    if hasattr(profile, "has_authoritative_baseline"):
        return bool(getattr(profile, "has_authoritative_baseline"))
    baseline_provenance = getattr(profile, "baseline_provenance", None)
    if baseline_provenance is None:
        return False
    return bool(getattr(baseline_provenance, "is_authoritative", False))


def _is_high_risk_profile(profile: object) -> bool:
    dimensions = {dimension.attribute_key: dimension for dimension in _dimensions(profile)}
    guardrails = dimensions.get("guardrail_robustness")
    capability = dimensions.get("capability_risk")
    autonomy = dimensions.get("autonomy_level")
    return any(
        (
            guardrails is not None and _direction_matches(guardrails, {"weakened"}, {"weaker", "weaken", "removed", "no longer"}),
            capability is not None and _direction_matches(capability, {"expanded"}, {"broader", "expanded", "write", "sensitive", "billing"}),
            autonomy is not None and _direction_matches(autonomy, {"increased"}, {"automatic", "skip manual review", "self-directed", "higher autonomy"}),
        )
    )


def _is_governance_weakened(dimension: object) -> bool:
    return _direction_matches(dimension, {"weakened"}, {"weaker", "missing", "removed", "review", "approval", "owner", "stale", "baseline"})


def _direction_matches(dimension: object, expected: set[str], tokens: set[str]) -> bool:
    direction = str(dimension.direction or "").strip().lower()
    reason = str(dimension.reason or "").strip().lower()
    return direction in expected or any(token in reason for token in tokens)


def _governance_gap_type(dimension: object) -> str:
    reason = str(dimension.reason or "").lower()
    evidence = " ".join(str(item).lower() for item in (dimension.evidence or []))
    combined = f"{reason} {evidence}"
    if any(token in combined for token in ("owner", "codeowners", "missing review", "missing expected reviewer")):
        return "missing_required_owner_review"
    return "weak_review_for_high_risk_change"


def _recommended_action(dimension: object, *, decision: str | None) -> str:
    if dimension.remediation:
        return _normalized_reason(dimension.remediation)
    if decision == "rebaseline_follow_up_after_merge":
        return "Promote or refresh the approved baseline after review."
    return "Require AI platform review before merge."


def _normalized_reason(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.rstrip(".") + "." if text else "Governance evidence needs human review."


def _confidence_token(confidence_label: str | None) -> str:
    normalized = str(confidence_label or "low confidence").strip().lower()
    if normalized.startswith("high"):
        return "high"
    if normalized.startswith("medium"):
        return "medium"
    return "low"


def build_repo_governance_findings(
    repo_full: str,
    *,
    design_profiles: list[object] | None,
    artifacts: list[object] | None,
    history_cues: list[object] | None,
    insights: list[object] | None,
) -> tuple[GovernanceFinding, ...]:
    findings = list(
        build_pr_comment_governance_findings(
            design_profiles,
            repo=repo_full,
            include_missing_profile_fallback=False,
        )
    )
    seen = {(finding.artifact_id, finding.finding_type) for finding in findings}
    insight_by_path = {str(getattr(insight, "artifact_path", "")): insight for insight in (insights or [])}
    repeated_paths = {
        path
        for cue in (history_cues or [])
        if getattr(cue, "cue_key", "") == "repeated_drift"
        for path in getattr(cue, "artifact_paths", [])
    }
    stale_paths = {
        path
        for cue in (history_cues or [])
        if getattr(cue, "cue_key", "") == "stale_baseline"
        for path in getattr(cue, "artifact_paths", [])
    }

    for artifact in artifacts or []:
        artifact_path = str(getattr(artifact, "artifact_path", ""))
        drift_magnitude = float(getattr(artifact, "latest_historical_drift_magnitude", 0.0) or 0.0)
        if artifact_path in repeated_paths and drift_magnitude >= 0.2:
            _append_finding(
                findings,
                seen,
                GovernanceFinding(
                    finding_type="repeated_high_risk_drift_same_surface",
                    severity="high",
                    artifact_id=artifact_path,
                    repo=repo_full,
                    pr_number=_review_target_pr_number(insight_by_path.get(artifact_path)),
                    evidence_summary=f"`{artifact_path}` has repeated higher-drift episodes in recent stored history and should be treated as a recurring governance risk.",
                    recommended_action="Escalate repeated drift on this artifact.",
                    confidence="medium",
                ),
            )
        if artifact_path in repeated_paths and artifact_path in stale_paths:
            _append_finding(
                findings,
                seen,
                GovernanceFinding(
                    finding_type="baseline_stale_after_repeated_change",
                    severity="warning",
                    artifact_id=artifact_path,
                    repo=repo_full,
                    pr_number=_review_target_pr_number(insight_by_path.get(artifact_path)),
                    evidence_summary=f"`{artifact_path}` keeps changing materially without a fresh approved baseline reference in the visible history.",
                    recommended_action="Promote or refresh approved baseline after review.",
                    confidence="medium",
                ),
            )

    return tuple(findings[:8])


def build_repo_governance_posture(
    repo_full: str,
    *,
    design_profiles: list[object] | None,
    artifacts: list[object] | None,
    history_cues: list[object] | None,
    insights: list[object] | None,
) -> RepoGovernancePosture:
    findings = build_repo_governance_findings(
        repo_full,
        design_profiles=design_profiles,
        artifacts=artifacts,
        history_cues=history_cues,
        insights=insights,
    )
    repeated_count = sum(1 for finding in findings if finding.finding_type in {"repeated_high_risk_drift_same_surface", "baseline_stale_after_repeated_change"})
    if any(finding.finding_type == "low_governance_confidence" for finding in findings):
        ownership_confidence = "low confidence"
    elif any(finding.finding_type == "missing_required_owner_review" for finding in findings):
        ownership_confidence = "owner review gap"
    else:
        ownership_confidence = "established"

    if any(finding.finding_type in {"missing_required_owner_review", "weak_review_for_high_risk_change"} and finding.severity == "high" for finding in findings):
        review_quality = "weak for recent high-risk change"
    elif findings:
        review_quality = "mixed"
    else:
        review_quality = "adequate"

    if any(finding.finding_type == "baseline_stale_after_repeated_change" for finding in findings):
        baseline_freshness_status = "stale after repeated change"
    elif repeated_count:
        baseline_freshness_status = "watch repeated drift"
    else:
        baseline_freshness_status = "current"

    return RepoGovernancePosture(
        ownership_confidence=ownership_confidence,
        review_quality=review_quality,
        repeated_drift_without_refresh_count=repeated_count,
        baseline_freshness_status=baseline_freshness_status,
        top_governance_anomalies=findings[:5],
    )


def build_overview_governance_attention(repo_views: list[object]) -> GovernanceAttentionSummary:
    repo_findings: list[GovernanceFinding] = []
    repeated_surfaces: list[str] = []
    repos_with_anomalies = 0
    high_risk_missing_review_count = 0

    for view in repo_views:
        posture = build_repo_governance_posture(
            getattr(view, "repo_full", "unknown"),
            design_profiles=getattr(view, "design_profiles", None),
            artifacts=getattr(view, "artifacts", None),
            history_cues=getattr(view, "history_cues", None),
            insights=getattr(view, "insights", None),
        )
        if posture.top_governance_anomalies:
            repos_with_anomalies += 1
            repo_findings.extend(posture.top_governance_anomalies)
        for finding in posture.top_governance_anomalies:
            if finding.finding_type in {"missing_required_owner_review", "weak_review_for_high_risk_change"} and finding.severity == "high":
                high_risk_missing_review_count += 1
            if finding.finding_type in {"repeated_high_risk_drift_same_surface", "baseline_stale_after_repeated_change"}:
                repeated_surfaces.append(finding.artifact_id)

    ranked = sorted(
        repo_findings,
        key=lambda finding: (
            0 if finding.severity == "high" else 1 if finding.severity == "warning" else 2,
            0 if finding.confidence == "high" else 1 if finding.confidence == "medium" else 2,
            finding.repo,
            finding.artifact_id,
        ),
    )
    return GovernanceAttentionSummary(
        repos_with_anomalies_count=repos_with_anomalies,
        high_risk_missing_review_count=high_risk_missing_review_count,
        repeated_drift_surfaces=tuple(dict.fromkeys(repeated_surfaces))[:3],
        ranked_issues_now=tuple(ranked[:5]),
    )


def _review_target_pr_number(insight: object | None) -> int | None:
    if insight is None:
        return None
    review_target = str(getattr(insight, "review_target", ""))
    match = re.search(r"PR\s+#?(\d+)", review_target, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None