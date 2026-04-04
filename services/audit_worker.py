from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from engine.analysis import DiffAnalysis, analyze_diff
from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile, compare_attribute_profiles
from engine.semantic_review import build_semantic_review_packages, format_semantic_review_packages
from .dashboard_views import ArtifactAttributeProfile, build_artifact_attribute_profile
from .audit_jobs import (
    AuditJob,
    claim_next_job,
    mark_job_completed,
    mark_job_failed,
    mark_job_fallback_posted,
    mark_job_retry,
)
from .audit_records import (
    PrCommentEpisodeRecord,
    get_audit_comment_episode_for_pr_head_sha,
    get_previous_audit_comment_episode_for_pr,
    record_audit_result,
)
from .github_integration import ensure_pr_label, fetch_file_content, generate_jwt, get_installation_token, upsert_pr_comment
from .onboarding_records import get_latest_onboarding_baseline_for_repo_artifact


@dataclass(frozen=True)
class WorkerSettings:
    db_path: str
    github_app_id: str
    github_private_key_path: str
    llm_client: object
    model: str
    github_app_private_key: str = ""
    llm_timeout_seconds: float = 30.0
    max_attempts: int = 5
    max_retry_window_seconds: float = 5400.0
    poll_interval_seconds: float = 2.0


RISK_BADGES = {
    "Low": "✅ Risk: Low",
    "Medium": "⚠️ Risk: Medium",
    "High": "❌ Risk: High",
}

ESCALATION_REASON_BY_RULE_ID = {
    "guardrail_drift": "guardrail or policy weakening",
    "guardrail_weakening": "guardrail or policy weakening",
    "sensitive_data_drift": "capability or blast-radius expansion",
    "capability_drift": "capability or blast-radius expansion",
    "tooling_drift": "critical-surface modification",
    "retrieval_drift": "critical-surface modification",
    "model_drift": "critical-surface modification",
}


@dataclass(frozen=True)
class EscalationRecommendation:
    decision: str
    reasons: tuple[str, ...] = ()
    label_name: str | None = None

    @property
    def requires_label(self) -> bool:
        return self.decision == "escalate_before_merge" and self.label_name is not None


@dataclass(frozen=True)
class CanonicalCommentDetails:
    risk_level: str
    analysis_bullets: tuple[str, ...]
    recommendation: str


@dataclass(frozen=True)
class SignalFusionAssessment:
    risk_level: str
    escalation_recommendation: EscalationRecommendation


@dataclass(frozen=True)
class PrCommentEpisodeContext:
    head_sha: str
    analyzed_at: float
    previous_episode: PrCommentEpisodeRecord | None = None


@dataclass(frozen=True)
class PrCommentReview:
    decision: str
    risk_level: str
    context_line: str
    what_changed: tuple[str, ...]
    key_deltas: tuple[str, ...]
    evidence: tuple[str, ...]
    governance_signals: tuple[str, ...]
    recommended_next_step: str
    episode_context: PrCommentEpisodeContext


def build_llm_comment(
    diff_text: str,
    deterministic_analysis: DiffAnalysis,
    *,
    llm_client: object,
    model: str,
    timeout_seconds: float,
    escalation_recommendation: EscalationRecommendation | None = None,
    attribute_profiles: list[ArtifactAttributeProfile] | None = None,
    episode_context: PrCommentEpisodeContext | None = None,
) -> str:
    recommendation = escalation_recommendation or _build_escalation_recommendation(deterministic_analysis)
    semantic_packages = build_semantic_review_packages(deterministic_analysis)
    system_prompt = (
        "You are an AI Security Auditor. Analyze this code diff. "
        "You will receive deterministic pre-analysis findings, structured semantic review packages, and the raw diff. "
        "Use the semantic review packages as the primary review frame, use deterministic findings as grounding evidence, and use the raw diff as reference detail. "
        "Return reviewer notes in Markdown using this structure exactly: 'Summary: ...', 'Risk Level: Low|Medium|High', 'Detailed Analysis:', 2-4 bullet points, and 'Recommendation: ...'. "
        "Include a one-sentence line in the form 'Summary: ...' describing what changed and why the risk level fits. "
        "Include an explicit line in the form 'Risk Level: Low|Medium|High'. "
        "Under 'Detailed Analysis:' provide grounded reviewer reasoning, not generic advice. "
        "Include a short 'Recommendation:' line. "
        "Keep the detailed section compact but substantive, and do not use code fences."
    )
    response = llm_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"{deterministic_analysis.format_for_prompt()}\n\n"
                    f"{format_semantic_review_packages(semantic_packages)}\n\n"
                    f"Raw diff:\n{diff_text}"
                ),
            },
        ],
        temperature=0.0,
        timeout=timeout_seconds,
    )
    raw_comment = response.choices[0].message.content or "Audit failed: empty response from AI model."
    summary = _extract_summary(
        raw_comment,
        default=_build_fallback_summary(deterministic_analysis),
    )
    fusion_assessment = _build_signal_fusion_assessment(raw_comment, deterministic_analysis)
    canonical_details = _build_semantic_comment_details(
        raw_comment,
        deterministic_analysis,
        risk_level=fusion_assessment.risk_level,
    )
    review = _build_pr_comment_review(
        deterministic_analysis,
        risk_level=fusion_assessment.risk_level,
        summary=summary,
        semantic_recommendation=canonical_details.recommendation,
        escalation_recommendation=fusion_assessment.escalation_recommendation or recommendation,
        attribute_profiles=attribute_profiles,
        episode_context=episode_context,
    )
    return _render_pr_comment_review(review)


def build_fallback_comment(
    deterministic_analysis: DiffAnalysis,
    *,
    error_message: str,
    escalation_recommendation: EscalationRecommendation | None = None,
    attribute_profiles: list[ArtifactAttributeProfile] | None = None,
    episode_context: PrCommentEpisodeContext | None = None,
) -> str:
    recommendation = escalation_recommendation or _build_escalation_recommendation(deterministic_analysis)
    summary = _build_fallback_summary(deterministic_analysis)
    canonical_details = _build_fallback_comment_details(deterministic_analysis)
    review = _build_pr_comment_review(
        deterministic_analysis,
        risk_level=deterministic_analysis.suggested_risk_level.value,
        summary=summary,
        semantic_recommendation=canonical_details.recommendation,
        escalation_recommendation=recommendation,
        attribute_profiles=attribute_profiles,
        episode_context=episode_context,
    )
    return _render_pr_comment_review(review)


def _build_pr_comment_review(
    deterministic_analysis: DiffAnalysis,
    *,
    risk_level: str,
    summary: str,
    semantic_recommendation: str,
    escalation_recommendation: EscalationRecommendation,
    attribute_profiles: list[ArtifactAttributeProfile] | None = None,
    episode_context: PrCommentEpisodeContext | None = None,
) -> PrCommentReview:
    profiles = [profile for profile in (attribute_profiles or []) if profile.dimensions]
    primary_profile = _select_primary_attribute_profile(profiles)
    normalized_risk = _normalize_risk_level(risk_level)
    decision = _build_comment_decision(primary_profile, escalation_recommendation)
    return PrCommentReview(
        decision=decision,
        risk_level=normalized_risk,
        context_line=_build_context_line(normalized_risk, primary_profile),
        what_changed=_build_what_changed_lines(summary, decision, primary_profile),
        key_deltas=_build_key_delta_bullets(profiles, deterministic_analysis),
        evidence=_build_evidence_bullets(profiles, deterministic_analysis),
        governance_signals=_build_governance_signals(profiles, decision),
        recommended_next_step=_build_recommended_next_step(
            decision,
            semantic_recommendation,
            profiles,
        ),
        episode_context=episode_context or PrCommentEpisodeContext(head_sha="unknown", analyzed_at=time.time()),
    )


def _render_pr_comment_review(review: PrCommentReview) -> str:
    lines = [
        f"## PromptDrift: {_decision_header(review.decision)}",
        "",
        review.context_line,
        "",
        "### What changed",
    ]
    lines.extend(review.what_changed)
    lines.extend(
        [
            "",
            "<details>",
            "<summary>PromptDrift review details</summary>",
            "",
            "### Key deltas",
        ]
    )
    lines.extend(f"- {bullet}" for bullet in review.key_deltas)
    lines.extend(["", "### Evidence"])
    lines.extend(f"- {bullet}" for bullet in review.evidence)
    if review.governance_signals:
        lines.extend(["", "### Governance signals"])
        lines.extend(f"- {bullet}" for bullet in review.governance_signals)
    lines.extend(
        [
            "",
            "### Recommended next step",
            review.recommended_next_step,
            "",
            "</details>",
            "",
            _episode_metadata_line(review.episode_context),
        ]
    )
    return "\n".join(lines)


def _build_comment_decision(
    primary_profile: ArtifactAttributeProfile | None,
    escalation_recommendation: EscalationRecommendation,
) -> str:
    if escalation_recommendation.decision == "escalate_before_merge":
        return "escalate_before_merge"
    if primary_profile is None or not primary_profile.has_authoritative_baseline:
        return "rebaseline_follow_up_after_merge"
    return "normal_review"


def _decision_header(decision: str) -> str:
    if decision == "escalate_before_merge":
        return "Escalate before merge"
    if decision == "rebaseline_follow_up_after_merge":
        return "Re-baseline follow-up after merge"
    return "Keep in normal review lane"


def _build_context_line(risk_level: str, primary_profile: ArtifactAttributeProfile | None) -> str:
    control_surface = (primary_profile.control_surface_label if primary_profile is not None else "Unknown control surface").lower()
    baseline_reference = primary_profile.baseline_reference if primary_profile is not None else "none-yet"
    return f"{risk_level} risk · {control_surface} · vs approved baseline `{baseline_reference}`"


def _build_what_changed_lines(
    summary: str,
    decision: str,
    primary_profile: ArtifactAttributeProfile | None,
) -> tuple[str, ...]:
    lines = [_normalize_summary(summary, default=summary)]
    if primary_profile is None or not primary_profile.has_authoritative_baseline:
        lines.append("No approved baseline exists yet for this control surface, so treat the accepted version as a baseline candidate after review.")
    elif decision == "escalate_before_merge":
        lines.append("It moves the control surface farther from the approved baseline rather than tightening it.")
    return tuple(lines[:2])


def _build_key_delta_bullets(
    attribute_profiles: list[ArtifactAttributeProfile],
    deterministic_analysis: DiffAnalysis,
) -> tuple[str, ...]:
    priority = ["guardrail_robustness", "capability_risk", "autonomy_level", "governance_strength"]
    bullets: list[str] = []
    seen: set[tuple[str, str]] = set()

    for profile in attribute_profiles:
        for attribute_key in priority:
            dimension = next((item for item in profile.dimensions if item.attribute_key == attribute_key and item.state != "no_change"), None)
            if dimension is None:
                continue
            signature = (profile.artifact_path, dimension.attribute_key)
            if signature in seen:
                continue
            seen.add(signature)
            bullets.append(_format_key_delta_bullet(dimension))
            if len(bullets) >= 3:
                return tuple(bullets)

    for finding in deterministic_analysis.findings[:3]:
        bullets.append(f"{finding.title}: {_normalize_sentence(finding.rationale, default=finding.rationale)}")
        if len(bullets) >= 3:
            break

    if not bullets:
        bullets.append("No material attribute shift was detected beyond the files touched in this PR.")
    return tuple(bullets[:3])


def _format_key_delta_bullet(dimension) -> str:
    prefix = _key_delta_prefix(dimension)

    if (
        dimension.direction == "unknown"
        or dimension.baseline_value == "unknown"
        or dimension.current_value == "unknown"
        or dimension.baseline_value == dimension.current_value
    ):
        return f"{prefix}: {_attribute_reason_fragment(dimension.reason)}"

    transition = f"{dimension.baseline_value} → {dimension.current_value}"
    return f"{prefix}: {transition}."


def _key_delta_prefix(dimension) -> str:
    reason_text = (dimension.reason or "").lower()
    if dimension.attribute_key == "guardrail_robustness":
        weakened = dimension.direction == "weakened" or any(token in reason_text for token in ("weaker", "weaken", "removed", "dropped", "no longer"))
        return "Guardrails weakened" if weakened else "Guardrails strengthened"
    if dimension.attribute_key == "capability_risk":
        expanded = dimension.direction == "expanded" or any(token in reason_text for token in ("broader", "expanded", "rose", "added", "write", "sensitive-tool"))
        return "Capability expanded" if expanded else "Capability reduced"
    if dimension.attribute_key == "autonomy_level":
        increased = dimension.direction == "increased" or any(token in reason_text for token in ("higher autonomy", "increased", "reduced review", "automatic", "skip review"))
        return "Autonomy increased" if increased else "Autonomy decreased"
    weakened = dimension.direction == "weakened" or any(token in reason_text for token in ("weaker", "missing", "stale", "reduced governance", "no approved baseline"))
    return "Governance weakened" if weakened else "Governance strengthened"


def _attribute_reason_fragment(reason: str) -> str:
    cleaned = _normalize_sentence(reason, default=reason).rstrip(".")
    cleaned = re.sub(r"^PromptDrift detected\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^PromptDrift classifies.+?because\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^this artifact\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned[:1].lower() + cleaned[1:] if cleaned else cleaned
    return f"{cleaned}." if cleaned else "meaningful drift relative to the approved baseline."


def _build_evidence_bullets(
    attribute_profiles: list[ArtifactAttributeProfile],
    deterministic_analysis: DiffAnalysis,
) -> tuple[str, ...]:
    bullets: list[str] = []
    seen: set[str] = set()
    for profile in attribute_profiles:
        for dimension in profile.dimensions:
            if dimension.attribute_key == "control_surface_type" or dimension.state == "no_change":
                continue
            for evidence in dimension.evidence[:2]:
                if _append_unique_evidence(bullets, seen, evidence):
                    if len(bullets) >= 4:
                        return tuple(bullets)

    for finding in deterministic_analysis.findings:
        for evidence in finding.evidence[:2]:
            if _append_unique_evidence(bullets, seen, evidence):
                if len(bullets) >= 4:
                    return tuple(bullets)

    for artifact in deterministic_analysis.artifacts[:2]:
        detail = (
            f"Touched `{artifact.relevance.path}` [{artifact.relevance.artifact_type}] with "
            f"{artifact.change.added_count} additions and {artifact.change.removed_count} removals."
        )
        if _append_unique_evidence(bullets, seen, detail) and len(bullets) >= 2:
            break

    if not bullets:
        bullets.append("Concrete supporting evidence was unavailable from the changed AI artifacts.")
    return tuple(bullets[:4])


def _append_unique_evidence(bullets: list[str], seen: set[str], raw_evidence: str) -> bool:
    normalized = _normalize_sentence(raw_evidence, default=raw_evidence)
    normalized_key = re.sub(r"\s+", " ", normalized).strip().lower()
    if not normalized_key:
        return False
    if normalized_key in seen:
        return False
    if any(normalized_key in existing or existing in normalized_key for existing in seen):
        return False
    seen.add(normalized_key)
    bullets.append(normalized)
    return True


def _build_governance_signals(
    attribute_profiles: list[ArtifactAttributeProfile],
    decision: str,
) -> tuple[str, ...]:
    signals: list[str] = []
    for profile in attribute_profiles:
        governance_dimension = next((item for item in profile.dimensions if item.attribute_key == "governance_strength"), None)
        if governance_dimension is not None and governance_dimension.state != "no_change" and governance_dimension.evidence:
            signals.append(_normalize_sentence(governance_dimension.reason, default=governance_dimension.reason))
            break

    if not signals and decision == "rebaseline_follow_up_after_merge":
        for profile in attribute_profiles:
            if profile.has_authoritative_baseline:
                continue
            signals.append(
                f"No approved baseline exists yet for `{profile.artifact_path}`, so reviewers should explicitly promote the intended version after merge."
            )
            break
    return tuple(signals[:3])


def _build_recommended_next_step(
    decision: str,
    semantic_recommendation: str,
    attribute_profiles: list[ArtifactAttributeProfile],
) -> str:
    if decision == "escalate_before_merge":
        for profile in attribute_profiles:
            guardrails = next((item for item in profile.dimensions if item.attribute_key == "guardrail_robustness" and item.state != "no_change"), None)
            if guardrails is not None and _key_delta_prefix(guardrails) == "Guardrails weakened":
                return "Restore explicit safety or approval guardrails before merge."
        return "Add AI platform review before merge."
    if decision == "rebaseline_follow_up_after_merge":
        return "Promote the updated artifact to approved baseline after merge."

    normalized = _normalize_sentence(semantic_recommendation, default="Safe to merge after normal review")
    if normalized.lower().startswith("safe to merge"):
        return normalized
    return "Safe to merge after normal review."


def _select_primary_attribute_profile(attribute_profiles: list[ArtifactAttributeProfile]) -> ArtifactAttributeProfile | None:
    if not attribute_profiles:
        return None
    ranked = sorted(
        attribute_profiles,
        key=lambda profile: sum(1 for item in profile.dimensions if item.state != "no_change"),
        reverse=True,
    )
    return ranked[0]


def _episode_metadata_line(context: PrCommentEpisodeContext) -> str:
    timestamp = datetime.fromtimestamp(context.analyzed_at, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    base = f"_PromptDrift analysis for head `{_short_sha(context.head_sha)}` at {timestamp}._"
    previous_episode = context.previous_episode
    if previous_episode is None:
        return base

    previous_recommendation = _extract_previous_episode_recommendation(previous_episode.audit_comment.comment_body)
    return (
        f"{base[:-2]} Previous PromptDrift analysis for `{_short_sha(previous_episode.head_sha)}` "
        f"recommended {previous_recommendation.lower()}._"
    )


def _extract_previous_episode_recommendation(comment_body: str) -> str:
    recommendation = _extract_recommendation(comment_body, default="normal review")
    recommendation = recommendation.strip().rstrip(".")
    return recommendation or "normal review"


def _short_sha(value: str) -> str:
    cleaned = (value or "unknown").strip()
    return cleaned[:7] if len(cleaned) > 7 else cleaned


def _format_escalation_line(recommendation: EscalationRecommendation) -> str:
    if recommendation.decision == "escalate_before_merge":
        if recommendation.reasons:
            return f"Escalation: **Recommended before merge** — {'; '.join(recommendation.reasons)}"
        return "Escalation: **Recommended before merge**"
    return "Escalation: **Not recommended** — stays in the normal review lane"


def _build_semantic_comment_details(
    raw_comment: str,
    deterministic_analysis: DiffAnalysis,
    *,
    risk_level: str,
) -> CanonicalCommentDetails:
    analysis_bullets = _extract_analysis_bullets(raw_comment)
    if len(analysis_bullets) < 2:
        analysis_bullets = _build_default_analysis_bullets(deterministic_analysis)

    recommendation = _extract_recommendation(
        raw_comment,
        default=_default_recommendation_for_risk(risk_level),
    )

    return CanonicalCommentDetails(
        risk_level=_normalize_risk_level(risk_level),
        analysis_bullets=tuple(analysis_bullets[:4]),
        recommendation=recommendation,
    )


def _build_fallback_comment_details(deterministic_analysis: DiffAnalysis) -> CanonicalCommentDetails:
    bullets = [
        "This review is based on deterministic risk signals while semantic review is still pending or unavailable.",
    ]

    if not deterministic_analysis.findings:
        bullets.append("AI-relevant files were detected, but no deterministic rule findings were triggered.")
    else:
        for finding in deterministic_analysis.findings[:3]:
            evidence = "; ".join(finding.evidence[:2]) if finding.evidence else "no evidence excerpt"
            bullets.append(f"{finding.title}: {evidence}")

    return CanonicalCommentDetails(
        risk_level=_normalize_risk_level(deterministic_analysis.suggested_risk_level.value),
        analysis_bullets=tuple(bullets),
        recommendation="Review the changed AI artifacts directly. Further semantic review may refine this assessment when model capacity is available.",
    )


def _render_canonical_detail_markdown(details: CanonicalCommentDetails) -> str:
    lines = [f"Risk Level: {details.risk_level}", "Detailed Analysis:"]
    lines.extend(f"- {bullet}" for bullet in details.analysis_bullets)
    lines.append(f"Recommendation: {details.recommendation}")
    return "\n".join(lines)


def _extract_analysis_bullets(comment_body: str) -> list[str]:
    lines = comment_body.splitlines()
    bullets: list[str] = []
    in_detailed_section = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        normalized = re.sub(r"^[#>*\s]+", "", stripped).strip()
        if re.match(r"^(\*\*)?detailed analysis(\*\*)?\s*[:\-]?$", normalized, re.IGNORECASE):
            in_detailed_section = True
            continue
        if re.match(r"^(\*\*)?recommendation(\*\*)?\s*[:\-]", normalized, re.IGNORECASE):
            break

        if in_detailed_section:
            bullet = re.sub(r"^[-*]\s*", "", stripped).strip()
            if bullet:
                bullets.append(_normalize_sentence(bullet))

    if bullets:
        return bullets

    fallback_lines: list[str] = []
    skip_patterns = (
        r"^(\*\*)?summary(\*\*)?\s*[:\-]",
        r"^(\*\*)?risk level(\*\*)?\s*[:\-]",
        r"^(\*\*)?recommendation(\*\*)?\s*[:\-]",
        r"^(\*\*)?reviewer notes(\*\*)?\s*$",
        r"^(\*\*)?detailed analysis(\*\*)?\s*[:\-]?$",
    )
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        normalized = re.sub(r"^[#>*\s]+", "", stripped).strip()
        if any(re.match(pattern, normalized, re.IGNORECASE) for pattern in skip_patterns):
            continue
        fallback_lines.append(_normalize_sentence(re.sub(r"^[-*]\s*", "", normalized).strip()))

    return fallback_lines[:4]


def _build_default_analysis_bullets(deterministic_analysis: DiffAnalysis) -> list[str]:
    bullets: list[str] = []

    for artifact in deterministic_analysis.artifacts[:2]:
        bullets.append(
            f"`{artifact.relevance.path}` [{artifact.relevance.artifact_type}] changed with {artifact.change.added_count} additions, {artifact.change.removed_count} removals, and {artifact.change.changed_hunks} touched hunks in an AI control surface."
        )

    for finding in deterministic_analysis.findings[:3]:
        evidence = f" Evidence: {finding.evidence[0]}" if finding.evidence else ""
        bullets.append(f"{finding.title}: {finding.rationale}{evidence}")

    if not bullets:
        bullets.append("AI-relevant artifacts changed, so reviewers should confirm the intended behavior and disclosure boundaries still match the approved design.")

    return bullets


def _extract_recommendation(comment_body: str, *, default: str) -> str:
    patterns = (
        r"^(\*\*)?recommendation(\*\*)?\s*[:\-]\s*(.+)$",
        r"^\*\*recommendation\s*[:\-]\*\*\s*(.+)$",
    )
    lines = comment_body.splitlines()
    in_next_step_section = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        if re.match(r"^#{1,6}\s*recommended next step\s*$", stripped, re.IGNORECASE):
            in_next_step_section = True
            continue
        if in_next_step_section:
            return _normalize_sentence(stripped, default=default)
        for pattern in patterns:
            match = re.match(pattern, stripped, re.IGNORECASE)
            if match:
                value = match.group(match.lastindex)
                return _normalize_sentence(value, default=default)
    return _normalize_sentence(default, default=default)


def _default_recommendation_for_risk(risk_level: str) -> str:
    normalized = _normalize_risk_level(risk_level)
    if normalized == "High":
        return "Escalate before merge and revert or narrow the permissive change until safeguards are restored."
    if normalized == "Medium":
        return "Review the changed AI control surface closely and confirm the new behavior is intended before merge."
    return "Confirm the change is intended and keep the normal review lane."


def _normalize_sentence(value: str, *, default: str | None = None) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" -*_`\t\r\n")
    cleaned = re.sub(r"^\*\*(.+?)\*\*$", r"\1", cleaned)
    if not cleaned and default is not None:
        cleaned = default.strip()
    cleaned = cleaned.rstrip(".")
    return f"{cleaned}." if cleaned else ""


def _build_escalation_recommendation(deterministic_analysis: DiffAnalysis) -> EscalationRecommendation:
    reasons: list[str] = []
    for finding in deterministic_analysis.findings:
        if finding.severity.value != "High":
            continue
        reason = ESCALATION_REASON_BY_RULE_ID.get(finding.rule_id)
        if reason is None or reason in reasons:
            continue
        reasons.append(reason)

    if reasons:
        return EscalationRecommendation(
            decision="escalate_before_merge",
            reasons=tuple(reasons),
            label_name="promptdrift: escalate-before-merge",
        )

    return EscalationRecommendation(decision="normal_review")


def _semantic_recommendation_requires_escalation(recommendation: str) -> bool:
    lowered = recommendation.lower()
    escalation_hints = (
        "escalate before merge",
        "revert before merge",
        "do not merge",
        "block merge",
        "hold before merge",
    )
    return any(hint in lowered for hint in escalation_hints)


def _fuse_risk_levels(deterministic_risk: str, semantic_risk: str) -> str:
    normalized_deterministic = _normalize_risk_level(deterministic_risk)
    normalized_semantic = _normalize_risk_level(semantic_risk)
    order = {"Low": 0, "Medium": 1, "High": 2}
    if order[normalized_semantic] >= order[normalized_deterministic]:
        return normalized_semantic
    return normalized_deterministic


def _build_signal_fusion_assessment(
    comment_body: str,
    deterministic_analysis: DiffAnalysis,
) -> SignalFusionAssessment:
    deterministic_risk = deterministic_analysis.suggested_risk_level.value
    semantic_risk = _extract_risk_level(comment_body, default=deterministic_risk)
    fused_risk = _fuse_risk_levels(deterministic_risk, semantic_risk)
    semantic_recommendation = _extract_recommendation(
        comment_body,
        default=_default_recommendation_for_risk(fused_risk),
    )

    base_recommendation = _build_escalation_recommendation(deterministic_analysis)
    reasons = list(base_recommendation.reasons)
    if fused_risk == "High" and _semantic_recommendation_requires_escalation(semantic_recommendation):
        semantic_reason = "semantic review flagged merge-blocking risk"
        if semantic_reason not in reasons:
            reasons.append(semantic_reason)

    if reasons:
        escalation_recommendation = EscalationRecommendation(
            decision="escalate_before_merge",
            reasons=tuple(reasons),
            label_name="promptdrift: escalate-before-merge",
        )
    else:
        escalation_recommendation = EscalationRecommendation(decision="normal_review")

    return SignalFusionAssessment(
        risk_level=fused_risk,
        escalation_recommendation=escalation_recommendation,
    )


def _ensure_escalation_guidance(comment_body: str, recommendation: EscalationRecommendation) -> str:
    escalation_line = _format_escalation_line(recommendation)
    if "Escalation:" in comment_body:
        lines = comment_body.splitlines()
        updated_lines: list[str] = []
        replaced = False
        for line in lines:
            if line.startswith("Escalation:"):
                if not replaced:
                    updated_lines.append(escalation_line)
                    replaced = True
                continue
            updated_lines.append(line)
        if replaced:
            return "\n".join(updated_lines)

    details_marker = "<details>"
    if details_marker not in comment_body:
        return f"{comment_body.rstrip()}\n{escalation_line}"

    summary_prefix, detail_suffix = comment_body.split(details_marker, 1)
    return f"{summary_prefix.rstrip()}\n{escalation_line}\n\n{details_marker}{detail_suffix}"


def _extract_summary(comment_body: str, *, default: str) -> str:
    summary_patterns = [
        r"^#{0,6}\s*summary\s*[:\-]\s*(.+)$",
        r"^\*\*summary\*\*\s*[:\-]\s*(.+)$",
        r"^\*\*summary\s*[:\-]\*\*\s*(.+)$",
    ]
    for raw_line in comment_body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for pattern in summary_patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                return _normalize_summary(match.group(1), default=default)

    skip_patterns = (
        r"^#{1,6}\s*summary\s*$",
        r"^#{1,6}\s*reviewer notes\s*$",
        r"^summary\s*$",
        r"^reviewer notes\s*$",
        r"^risk level\s*[:\-]",
        r"^\*\*risk level\*\*\s*[:\-]",
        r"^recommendation\s*[:\-]",
        r"^\*\*recommendation\*\*\s*[:\-]",
        r"^detailed analysis\s*[:\-]?$",
        r"^\*\*detailed analysis\*\*\s*[:\-]?$",
    )
    for raw_line in comment_body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        normalized = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if not normalized:
            continue
        if any(re.match(pattern, normalized, re.IGNORECASE) for pattern in skip_patterns):
            continue
        return _normalize_summary(normalized, default=default)

    return _normalize_summary(default, default=default)


def _normalize_summary(summary: str, *, default: str) -> str:
    cleaned = re.sub(r"\s+", " ", summary).strip(" -*_`\t\r\n")
    cleaned = re.sub(r"^\*{0,2}summary\*{0,2}\s*[:\-]?\*{0,2}\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\*\*(.+?)\*\*$", r"\1", cleaned)
    cleaned = cleaned.rstrip(".")
    if not cleaned:
        cleaned = default.rstrip(".")
    return cleaned + "."


def _build_fallback_summary(deterministic_analysis: DiffAnalysis) -> str:
    risk_level = _normalize_risk_level(deterministic_analysis.suggested_risk_level.value).lower()
    if deterministic_analysis.findings:
        primary_finding = deterministic_analysis.findings[0]
        return (
            f"{primary_finding.title} was detected, driving a {risk_level} risk assessment"
        )

    artifact_count = len(deterministic_analysis.artifacts)
    if artifact_count == 0:
        return f"No AI-relevant artifacts were identified, so the change remains {risk_level} risk"
    if artifact_count == 1:
        artifact = deterministic_analysis.artifacts[0].relevance.path
        return f"AI-relevant changes were found in {artifact}, so the change remains {risk_level} risk"
    return f"AI-relevant changes were found across {artifact_count} artifacts, so the change remains {risk_level} risk"


def _extract_risk_level(comment_body: str, *, default: str) -> str:
    match = re.search(r"risk level\s*[:\-]\s*\**(low|medium|high)\**", comment_body, re.IGNORECASE)
    if match:
        return _normalize_risk_level(match.group(1))

    context_match = re.search(r"^(low|medium|high) risk\b", comment_body, re.IGNORECASE | re.MULTILINE)
    if context_match:
        return _normalize_risk_level(context_match.group(1))
    return _normalize_risk_level(default)


def _normalize_risk_level(risk_level: str) -> str:
    lowered = risk_level.strip().lower()
    if lowered == "low":
        return "Low"
    if lowered == "medium":
        return "Medium"
    return "High"


def _is_retryable_llm_error(exc: Exception) -> bool:
    return isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError))


def _retry_delay_seconds(attempt_count: int) -> int:
    schedule = {1: 120, 2: 600, 3: 1800, 4: 3600}
    return schedule.get(attempt_count, 3600)


def _extract_retry_after_seconds(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None

    retry_after_ms = headers.get("retry-after-ms") if hasattr(headers, "get") else None
    if retry_after_ms:
        try:
            return max(1, int(float(retry_after_ms) / 1000))
        except (TypeError, ValueError):
            return None

    retry_after = headers.get("retry-after") if hasattr(headers, "get") else None
    if retry_after:
        try:
            return max(1, int(float(retry_after)))
        except (TypeError, ValueError):
            return None

    return None


def _should_retry(job: AuditJob, settings: WorkerSettings) -> bool:
    if job.attempt_count >= settings.max_attempts:
        return False
    if job.attempt_count <= 1:
        return True
    job_age_seconds = max(0.0, time.time() - job.created_at)
    return job_age_seconds < settings.max_retry_window_seconds


def _get_installation_token_for_job(job: AuditJob, settings: WorkerSettings) -> str:
    jwt_token = generate_jwt(
        settings.github_app_id,
        settings.github_private_key_path,
        settings.github_app_private_key,
    )
    return get_installation_token(jwt_token, job.installation_id)


def _build_episode_context(job: AuditJob, settings: WorkerSettings) -> PrCommentEpisodeContext:
    previous_episode = get_previous_audit_comment_episode_for_pr(
        settings.db_path,
        job.repo_full,
        job.pr_number,
        job.head_sha,
    )
    return PrCommentEpisodeContext(
        head_sha=job.head_sha,
        analyzed_at=time.time(),
        previous_episode=previous_episode,
    )


def _post_comment_for_job(job: AuditJob, body: str, settings: WorkerSettings, *, installation_token: str | None = None) -> int:
    token = installation_token or _get_installation_token_for_job(job, settings)
    existing_comment = get_audit_comment_episode_for_pr_head_sha(
        settings.db_path,
        job.repo_full,
        job.pr_number,
        job.head_sha,
    )
    return upsert_pr_comment(
        job.repo_full,
        job.pr_number,
        token,
        body,
        existing_comment_id=(
            existing_comment.audit_comment.github_comment_id
            if existing_comment is not None
            else None
        ),
    )


def _apply_escalation_label_for_job(
    job: AuditJob,
    recommendation: EscalationRecommendation,
    settings: WorkerSettings,
    *,
    installation_token: str | None = None,
) -> None:
    if not recommendation.requires_label:
        return

    token = installation_token or _get_installation_token_for_job(job, settings)
    ensure_pr_label(
        job.repo_full,
        job.pr_number,
        token,
        label_name=recommendation.label_name,
    )


def _fetch_artifact_snapshots(job: AuditJob, deterministic_analysis: DiffAnalysis, settings: WorkerSettings) -> dict[str, str]:
    if not deterministic_analysis.artifacts:
        return {}

    try:
        installation_token = _get_installation_token_for_job(job, settings)
    except Exception:
        return {}

    snapshots: dict[str, str] = {}
    for artifact in deterministic_analysis.artifacts:
        try:
            snapshots[artifact.relevance.path] = fetch_file_content(
                job.repo_full,
                artifact.relevance.path,
                installation_token,
                ref=job.head_sha,
            )
        except Exception:
            continue
    return snapshots


def _persist_audit_result(
    job: AuditJob,
    deterministic_analysis: DiffAnalysis,
    settings: WorkerSettings,
    *,
    status: str,
    completion_mode: str,
    output_mode: str,
    comment_body: str | None,
    comment_mode: str | None,
    semantic_review_completed: bool,
    suggested_risk_level: str | None = None,
    error_message: str | None = None,
    github_comment_id: int | None = None,
    artifact_snapshots: dict[str, str] | None = None,
) -> None:
    record_audit_result(
        settings.db_path,
        job_id=job.id,
        repo_full=job.repo_full,
        pr_number=job.pr_number,
        installation_id=job.installation_id,
        head_sha=job.head_sha,
        pr_state=job.pr_state,
        pr_merged=job.pr_merged,
        pr_closed_at=job.pr_closed_at,
        pr_merged_at=job.pr_merged_at,
        pr_merge_commit_sha=job.pr_merge_commit_sha,
        pr_updated_at=job.pr_updated_at,
        deterministic_analysis=deterministic_analysis,
        status=status,
        completion_mode=completion_mode,
        output_mode=output_mode,
        comment_body=comment_body,
        comment_mode=comment_mode,
        semantic_review_completed=semantic_review_completed,
        suggested_risk_level=suggested_risk_level,
        error_message=error_message,
        artifact_snapshots=artifact_snapshots or _fetch_artifact_snapshots(job, deterministic_analysis, settings),
        github_comment_id=github_comment_id,
    )


def _handle_fallback(
    job: AuditJob,
    settings: WorkerSettings,
    deterministic_analysis: DiffAnalysis,
    *,
    error_message: str,
    artifact_snapshots: dict[str, str] | None = None,
    escalation_recommendation: EscalationRecommendation | None = None,
) -> str:
    recommendation = escalation_recommendation or _build_escalation_recommendation(deterministic_analysis)
    episode_context = _build_episode_context(job, settings)
    comment_attribute_profiles = _build_comment_attribute_profiles(
        job,
        deterministic_analysis,
        artifact_snapshots or {},
        settings,
    )
    fallback_comment = build_fallback_comment(
        deterministic_analysis,
        error_message=error_message,
        escalation_recommendation=recommendation,
        attribute_profiles=comment_attribute_profiles,
        episode_context=episode_context,
    )
    try:
        installation_token = _get_installation_token_for_job(job, settings)
        github_comment_id = _post_comment_for_job(job, fallback_comment, settings, installation_token=installation_token)
    except Exception as fallback_exc:
        combined_error = f"{error_message}; fallback post failed: {type(fallback_exc).__name__}: {fallback_exc}"
        try:
            _persist_audit_result(
                job,
                deterministic_analysis,
                settings,
                status="failed",
                completion_mode="failed",
                output_mode="no_comment",
                comment_body=None,
                comment_mode=None,
                semantic_review_completed=False,
                error_message=combined_error,
                artifact_snapshots=artifact_snapshots,
            )
        except Exception as persist_exc:
            combined_error = (
                f"{combined_error}; persistence failed: {type(persist_exc).__name__}: {persist_exc}"
            )
        mark_job_failed(settings.db_path, job.id, error_message=combined_error)
        return "failed"

    combined_error_message = error_message
    try:
        _apply_escalation_label_for_job(
            job,
            recommendation,
            settings,
            installation_token=installation_token,
        )
    except Exception as label_exc:
        combined_error_message = f"{error_message}; escalation label not applied: {type(label_exc).__name__}: {label_exc}"

    try:
        _persist_audit_result(
            job,
            deterministic_analysis,
            settings,
            status="fallback_posted",
            completion_mode="fallback_posted",
            output_mode="preliminary_fallback",
            comment_body=fallback_comment,
            comment_mode="preliminary_fallback",
            semantic_review_completed=False,
            error_message=combined_error_message,
            github_comment_id=github_comment_id,
            artifact_snapshots=artifact_snapshots,
        )
    except Exception as persist_exc:
        combined_error = (
            f"{combined_error_message}; persistence failed after fallback comment post: {type(persist_exc).__name__}: {persist_exc}"
        )
        mark_job_failed(settings.db_path, job.id, error_message=combined_error)
        return "failed"

    mark_job_fallback_posted(
        settings.db_path,
        job.id,
        comment_body=fallback_comment,
        error_message=combined_error_message,
    )
    return "fallback_posted"


def process_job(job: AuditJob, settings: WorkerSettings) -> str:
    deterministic_analysis = analyze_diff(job.diff_text)
    artifact_snapshots = _fetch_artifact_snapshots(job, deterministic_analysis, settings)
    attribute_profiles = _build_comment_attribute_profiles(job, deterministic_analysis, artifact_snapshots, settings)
    escalation_recommendation = _build_escalation_recommendation(deterministic_analysis)
    episode_context = _build_episode_context(job, settings)
    try:
        comment_body = build_llm_comment(
            job.diff_text,
            deterministic_analysis,
            llm_client=settings.llm_client,
            model=settings.model,
            timeout_seconds=settings.llm_timeout_seconds,
            escalation_recommendation=escalation_recommendation,
            attribute_profiles=attribute_profiles,
            episode_context=episode_context,
        )
        fusion_assessment = _build_signal_fusion_assessment(comment_body, deterministic_analysis)
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        if _is_retryable_llm_error(exc) and _should_retry(job, settings):
            retry_delay_seconds = _extract_retry_after_seconds(exc) or _retry_delay_seconds(job.attempt_count)
            retry_at = time.time() + retry_delay_seconds
            mark_job_retry(settings.db_path, job.id, error_message=error_message, retry_at=retry_at)
            return "retry_wait"

        return _handle_fallback(
            job,
            settings,
            deterministic_analysis,
            error_message=error_message,
            artifact_snapshots=artifact_snapshots,
            escalation_recommendation=escalation_recommendation,
        )

    try:
        installation_token = _get_installation_token_for_job(job, settings)
        github_comment_id = _post_comment_for_job(job, comment_body, settings, installation_token=installation_token)
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        return _handle_fallback(
            job,
            settings,
            deterministic_analysis,
            error_message=error_message,
            artifact_snapshots=artifact_snapshots,
            escalation_recommendation=escalation_recommendation,
        )

    audit_error_message = None
    try:
        _apply_escalation_label_for_job(
            job,
            fusion_assessment.escalation_recommendation,
            settings,
            installation_token=installation_token,
        )
    except Exception as exc:
        audit_error_message = f"Escalation label not applied: {type(exc).__name__}: {exc}"

    try:
        _persist_audit_result(
            job,
            deterministic_analysis,
            settings,
            status="completed",
            completion_mode="completed",
            output_mode="full_review",
            comment_body=comment_body,
            comment_mode="full_review",
            semantic_review_completed=True,
            suggested_risk_level=fusion_assessment.risk_level,
            error_message=audit_error_message,
            github_comment_id=github_comment_id,
            artifact_snapshots=artifact_snapshots,
        )
    except Exception as persist_exc:
        error_message = f"Persistence failure after comment post: {type(persist_exc).__name__}: {persist_exc}"
        mark_job_failed(settings.db_path, job.id, error_message=error_message)
        return "failed"

    mark_job_completed(settings.db_path, job.id, comment_body=comment_body)
    return "completed"


def process_next_job_once(settings: WorkerSettings) -> bool:
    job = claim_next_job(settings.db_path)
    if job is None:
        return False
    process_job(job, settings)
    return True


def _build_comment_attribute_profiles(
    job: AuditJob,
    deterministic_analysis: DiffAnalysis,
    artifact_snapshots: dict[str, str],
    settings: WorkerSettings,
) -> list[ArtifactAttributeProfile]:
    profiles: list[ArtifactAttributeProfile] = []
    for artifact in deterministic_analysis.artifacts[:3]:
        snapshot_text = artifact_snapshots.get(artifact.relevance.path)
        if not snapshot_text:
            continue
        current_profile = build_attribute_profile(snapshot_text)
        current_signal_terms = extract_signal_terms_from_text(snapshot_text)
        baseline = get_latest_onboarding_baseline_for_repo_artifact(settings.db_path, job.repo_full, artifact.relevance.path)
        if baseline is not None:
            drift_delta = compare_attribute_profiles(
                baseline.profile,
                current_profile,
                semantic_similarity=1.0,
            )
            profiles.append(
                build_artifact_attribute_profile(
                    artifact_path=artifact.relevance.path,
                    artifact_type=artifact.relevance.artifact_type,
                    baseline_profile=baseline.profile,
                    current_profile=current_profile,
                    attribute_deltas=drift_delta.attribute_deltas,
                    baseline_signal_terms=baseline.signal_terms,
                    current_signal_terms=current_signal_terms,
                    baseline_content=baseline.content_text,
                    current_content=snapshot_text,
                    baseline_reference=_baseline_reference_for_comment(artifact.relevance.path, baseline.created_at),
                    has_authoritative_baseline=True,
                )
            )
        else:
            profiles.append(
                build_artifact_attribute_profile(
                    artifact_path=artifact.relevance.path,
                    artifact_type=artifact.relevance.artifact_type,
                    baseline_profile=None,
                    current_profile=current_profile,
                    attribute_deltas={},
                    baseline_signal_terms=[],
                    current_signal_terms=current_signal_terms,
                    baseline_content=None,
                    current_content=snapshot_text,
                    baseline_reference=_baseline_reference_for_comment(artifact.relevance.path, None),
                    has_authoritative_baseline=False,
                )
            )
    return profiles


def _baseline_reference_for_comment(artifact_path: str, created_at: float | None) -> str:
    artifact_name = artifact_path.split("/")[-1] if artifact_path else "artifact"
    if created_at is None:
        return f"{artifact_name}@none-yet"
    baseline_date = datetime.fromtimestamp(created_at, timezone.utc).strftime("%Y-%m-%d")
    return f"{artifact_name}@{baseline_date}"


class AuditWorker:
    def __init__(self, settings: WorkerSettings):
        self.settings = settings
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="promptdrift-audit-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            processed = process_next_job_once(self.settings)
            if not processed:
                self._stop_event.wait(self.settings.poll_interval_seconds)
