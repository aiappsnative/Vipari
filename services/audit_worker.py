from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urljoin

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from config import get_settings

from engine.analysis import DiffAnalysis, analyze_diff
from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile, compare_attribute_profiles
from engine.semantic_review import build_semantic_review_packages, format_semantic_review_packages
from .dashboard_views import ArtifactAttributeProfile, build_artifact_attribute_profile
from .governance_signals import GovernanceFinding, build_pr_comment_governance_findings
from .signal_fusion import fuse_risk_levels, normalize_confidence_level, normalize_risk_level
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
from .github_integration import fetch_file_content, generate_jwt, get_installation_token, sync_pr_label, upsert_pr_comment
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
    confidence: str
    semantic_risk: str
    semantic_requires_escalation: bool
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
    confidence: str | None
    context_line: str
    attribute_table_rows: tuple[tuple[str, str, str], ...]
    what_changed: tuple[str, ...]
    key_deltas: tuple[str, ...]
    evidence: tuple[str, ...]
    governance_findings: tuple[GovernanceFinding, ...]
    recommended_next_step: str
    episode_context: PrCommentEpisodeContext
    dashboard_deep_link: str | None = None


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
    repo_full: str | None = None,
    pr_number: int | None = None,
) -> str:
    recommendation = escalation_recommendation or _build_escalation_recommendation(deterministic_analysis)
    semantic_packages = build_semantic_review_packages(deterministic_analysis)
    system_prompt = (
        "You are an AI Security Auditor. Analyze this code diff. "
        "You will receive deterministic pre-analysis findings, structured semantic review packages, and the raw diff. "
        "Use the semantic review packages as the primary review frame, use deterministic findings as grounding evidence, and use the raw diff as reference detail. "
        "Return reviewer notes in Markdown using this structure exactly: 'Summary: ...', 'Risk Level: Low|Medium|High', 'Confidence: Low|Medium|High', 'Detailed Analysis:', 2-4 bullet points, and 'Recommendation: ...'. "
        "Include a one-sentence line in the form 'Summary: ...' describing what changed and why the risk level fits. "
        "Include an explicit line in the form 'Risk Level: Low|Medium|High'. "
        "Include an explicit line in the form 'Confidence: Low|Medium|High'. "
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
        confidence=fusion_assessment.confidence,
        summary=summary,
        fusion_summary=_build_signal_fusion_summary(
            deterministic_analysis.suggested_risk_level.value,
            fusion_assessment.semantic_risk,
            fusion_assessment.risk_level,
            confidence=fusion_assessment.confidence,
            semantic_requires_escalation=fusion_assessment.semantic_requires_escalation,
        ),
        semantic_recommendation=canonical_details.recommendation,
        escalation_recommendation=fusion_assessment.escalation_recommendation or recommendation,
        attribute_profiles=attribute_profiles,
        episode_context=episode_context,
        repo_full=repo_full,
        pr_number=pr_number,
    )
    return _render_pr_comment_review(review)


def build_fallback_comment(
    deterministic_analysis: DiffAnalysis,
    *,
    error_message: str,
    escalation_recommendation: EscalationRecommendation | None = None,
    attribute_profiles: list[ArtifactAttributeProfile] | None = None,
    episode_context: PrCommentEpisodeContext | None = None,
    repo_full: str | None = None,
    pr_number: int | None = None,
) -> str:
    recommendation = escalation_recommendation or _build_escalation_recommendation(deterministic_analysis)
    summary = _build_fallback_summary(deterministic_analysis)
    canonical_details = _build_fallback_comment_details(deterministic_analysis)
    review = _build_pr_comment_review(
        deterministic_analysis,
        risk_level=deterministic_analysis.suggested_risk_level.value,
        confidence=None,
        summary=summary,
        fusion_summary=None,
        semantic_recommendation=canonical_details.recommendation,
        escalation_recommendation=recommendation,
        attribute_profiles=attribute_profiles,
        episode_context=episode_context,
        repo_full=repo_full,
        pr_number=pr_number,
    )
    return _render_pr_comment_review(review)


def _build_pr_comment_review(
    deterministic_analysis: DiffAnalysis,
    *,
    risk_level: str,
    confidence: str | None,
    summary: str,
    fusion_summary: str | None,
    semantic_recommendation: str,
    escalation_recommendation: EscalationRecommendation,
    attribute_profiles: list[ArtifactAttributeProfile] | None = None,
    episode_context: PrCommentEpisodeContext | None = None,
    repo_full: str | None = None,
    pr_number: int | None = None,
) -> PrCommentReview:
    profiles = [profile for profile in (attribute_profiles or []) if profile.dimensions]
    primary_profile = _select_primary_attribute_profile(profiles)
    selected_key_deltas = _select_key_delta_dimensions(profiles)
    normalized_risk = _normalize_risk_level(risk_level)
    decision = _build_comment_decision(primary_profile, escalation_recommendation)
    return PrCommentReview(
        decision=decision,
        risk_level=normalized_risk,
        confidence=confidence,
        context_line=_build_context_line(normalized_risk, primary_profile, confidence=confidence),
        attribute_table_rows=_build_attribute_table_rows(profiles),
        what_changed=_build_what_changed_lines(summary, fusion_summary, decision, primary_profile),
        key_deltas=_build_key_delta_bullets(selected_key_deltas, deterministic_analysis),
        evidence=_build_evidence_bullets(selected_key_deltas, deterministic_analysis),
        governance_findings=build_pr_comment_governance_findings(
            profiles,
            decision=decision,
        ),
        recommended_next_step=_build_recommended_next_step(
            decision,
            semantic_recommendation,
            profiles,
        ),
        episode_context=episode_context or PrCommentEpisodeContext(head_sha="unknown", analyzed_at=time.time()),
        dashboard_deep_link=_build_pr_comment_dashboard_deep_link(repo_full, pr_number, profiles),
    )


def _render_pr_comment_review(review: PrCommentReview) -> str:
    lines = [
        f"## {_risk_indicator_emoji(review.risk_level)} DriftGuard: {_decision_header(review.decision)}",
        "",
        review.context_line,
        "",
        "### Attribute profile",
        "| Attribute | Baseline -> Current | Reason |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| {_markdown_table_cell(attribute)} | {_markdown_table_cell(transition)} | {_markdown_table_cell(reason)} |"
        for attribute, transition, reason in review.attribute_table_rows
    )
    lines.extend(
        [
            "",
        "### What changed",
        ]
    )
    lines.extend(review.what_changed)
    lines.extend(
        [
            "",
            "<details>",
            "<summary>DriftGuard review details</summary>",
            "",
            "### Key deltas",
        ]
    )
    lines.extend(f"- {bullet}" for bullet in review.key_deltas)
    lines.extend(["", "### Evidence"])
    lines.extend(f"- {bullet}" for bullet in review.evidence)
    if review.governance_findings:
        lines.extend(["", "### Governance signals"])
        lines.extend(f"- {finding.evidence_summary}" for finding in review.governance_findings)
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
    if review.dashboard_deep_link:
        lines.extend(
            [
                "",
                f"[Open this review in DriftGuard dashboard]({review.dashboard_deep_link})",
            ]
        )
    return "\n".join(lines)


def _risk_indicator_emoji(risk_level: str) -> str:
    normalized = _normalize_risk_level(risk_level)
    if normalized == "High":
        return "❌"
    if normalized == "Medium":
        return "⚠️"
    return "✅"


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


def _build_context_line(
    risk_level: str,
    primary_profile: ArtifactAttributeProfile | None,
    *,
    confidence: str | None = None,
) -> str:
    control_surface = (primary_profile.control_surface_label if primary_profile is not None else "Unknown control surface").lower()
    baseline_reference = primary_profile.baseline_reference if primary_profile is not None else "none-yet"
    if confidence:
        return f"{risk_level} risk · {confidence.lower()} confidence · {control_surface} · vs approved baseline `{baseline_reference}`"
    return f"{risk_level} risk · {control_surface} · vs approved baseline `{baseline_reference}`"


def _build_what_changed_lines(
    summary: str,
    fusion_summary: str | None,
    decision: str,
    primary_profile: ArtifactAttributeProfile | None,
) -> tuple[str, ...]:
    lines = [_normalize_summary(summary, default=summary)]
    if fusion_summary:
        lines.append(_normalize_sentence(fusion_summary, default=fusion_summary))
    if primary_profile is None or not primary_profile.has_authoritative_baseline:
        lines.append("No approved baseline exists yet for this control surface, so treat the accepted version as a baseline candidate after review.")
    elif decision == "escalate_before_merge":
        lines.append("It moves the control surface farther from the approved baseline rather than tightening it.")
    return tuple(lines[:3])


def _build_signal_fusion_summary(
    deterministic_risk: str,
    semantic_risk: str,
    fused_risk: str,
    *,
    confidence: str | None,
    semantic_requires_escalation: bool,
) -> str | None:
    normalized_deterministic = _normalize_risk_level(deterministic_risk)
    normalized_semantic = _normalize_risk_level(semantic_risk)
    normalized_fused = _normalize_risk_level(fused_risk)
    confidence_label = normalize_confidence_level(confidence, default="Medium").lower()

    if confidence_label == "low" and normalized_semantic != normalized_deterministic and normalized_fused == normalized_deterministic:
        return (
            f"Signal fusion kept the deterministic {normalized_deterministic.lower()} risk assessment because the semantic escalation was only {confidence_label} confidence"
        )

    if normalized_deterministic == normalized_semantic == "Medium" and normalized_fused == "High":
        return (
            f"Signal fusion elevated this to high risk because deterministic and semantic review agreed on a medium-risk change with {confidence_label} confidence"
        )

    if semantic_requires_escalation and normalized_fused == "High":
        return (
            f"Signal fusion honored the semantic merge-blocking recommendation with {confidence_label} confidence"
        )

    if normalized_fused != normalized_deterministic and normalized_semantic != normalized_deterministic:
        return (
            f"Signal fusion raised this above the deterministic {normalized_deterministic.lower()} baseline because semantic review found materially riskier behavior with {confidence_label} confidence"
        )

    return None


def _build_key_delta_bullets(
    selected_dimensions: list[object],
    deterministic_analysis: DiffAnalysis,
) -> tuple[str, ...]:
    bullets: list[str] = []

    for dimension in selected_dimensions:
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


def _build_attribute_table_rows(attribute_profiles: list[ArtifactAttributeProfile]) -> tuple[tuple[str, str, str], ...]:
    row_specs = (
        ("guardrail_robustness", "Guardrails"),
        ("capability_risk", "Capability"),
        ("autonomy_level", "Autonomy"),
        ("governance_strength", "Governance"),
        ("model_config_posture", "Model/config"),
    )
    primary_profile = _select_primary_attribute_profile(attribute_profiles)
    dimensions_by_key = {
        dimension.attribute_key: dimension
        for dimension in (primary_profile.dimensions if primary_profile is not None else [])
    }
    rows: list[tuple[str, str, str]] = []
    for attribute_key, label in row_specs:
        dimension = dimensions_by_key.get(attribute_key)
        if dimension is None:
            rows.append((label, "unknown -> unknown", "No normalized attribute evidence was available for this dimension; treat it as low-confidence unknown."))
            continue
        rows.append((label, _attribute_table_transition(dimension), _attribute_table_reason(dimension)))
    return tuple(rows)


def _attribute_table_transition(dimension) -> str:
    baseline_value = (dimension.baseline_value or "unknown").strip() or "unknown"
    current_value = (dimension.current_value or "unknown").strip() or "unknown"
    return f"{baseline_value} -> {current_value}"


def _attribute_table_reason(dimension) -> str:
    reason = _normalize_sentence(dimension.reason, default=dimension.reason).rstrip(".")
    return reason or "No normalized attribute evidence was available."


def _markdown_table_cell(value: str) -> str:
    return str(value or "").replace("|", "\\|")


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


def _select_key_delta_dimensions(attribute_profiles: list[ArtifactAttributeProfile]) -> list[object]:
    priority = {"guardrail_robustness": 0, "capability_risk": 1, "autonomy_level": 2}
    ranked: list[tuple[tuple[float, float, float, str], object]] = []
    seen: set[tuple[str, str]] = set()

    for profile in attribute_profiles:
        for dimension in profile.dimensions:
            if dimension.attribute_key not in priority or dimension.state == "no_change":
                continue
            signature = (profile.artifact_path, dimension.attribute_key)
            if signature in seen:
                continue
            seen.add(signature)
            sort_key = (
                float(priority[dimension.attribute_key]),
                -(dimension.confidence_score or 0.0),
                -(abs(dimension.delta) if dimension.delta is not None else 0.0),
                profile.artifact_path,
            )
            ranked.append((sort_key, dimension))

    ranked.sort(key=lambda item: item[0])
    return [dimension for _, dimension in ranked[:3]]


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
    cleaned = re.sub(r"^DriftGuard detected\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^DriftGuard classifies.+?because\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^this artifact\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned[:1].lower() + cleaned[1:] if cleaned else cleaned
    return f"{cleaned}." if cleaned else "meaningful drift relative to the approved baseline."


def _build_evidence_bullets(
    selected_dimensions: list[object],
    deterministic_analysis: DiffAnalysis,
) -> tuple[str, ...]:
    bullets: list[str] = []
    seen: set[str] = set()
    for dimension in selected_dimensions:
        for evidence in (dimension.evidence or [])[:2]:
            if _append_unique_evidence(bullets, seen, evidence):
                if len(bullets) >= 3:
                    return tuple(bullets)

    for finding in deterministic_analysis.findings:
        for evidence in finding.evidence[:2]:
            if _append_unique_evidence(bullets, seen, evidence):
                if len(bullets) >= 4:
                    return tuple(bullets)

    for finding in deterministic_analysis.findings:
        rationale_detail = f"{finding.title}: {_normalize_sentence(finding.rationale, default=finding.rationale)}"
        if _append_unique_evidence(bullets, seen, rationale_detail):
            if len(bullets) >= 3:
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
    base = f"_DriftGuard analysis for head `{_short_sha(context.head_sha)}` at {timestamp}._"
    previous_episode = context.previous_episode
    if previous_episode is None:
        return base

    previous_recommendation = _extract_previous_episode_recommendation(previous_episode.audit_comment.comment_body)
    return (
        f"{base[:-2]} Previous DriftGuard analysis for `{_short_sha(previous_episode.head_sha)}` "
        f"recommended {previous_recommendation.lower()}._"
    )


def _build_pr_comment_dashboard_deep_link(
    repo_full: str | None,
    pr_number: int | None,
    attribute_profiles: list[ArtifactAttributeProfile],
) -> str | None:
    normalized_repo_full = (repo_full or "").strip()
    if not normalized_repo_full:
        return None

    query_params: list[tuple[str, str]] = []
    primary_profile = _select_primary_attribute_profile(attribute_profiles)
    artifact_path = (primary_profile.artifact_path if primary_profile is not None else "").strip()
    if artifact_path:
        query_params.append(("artifact", artifact_path))
    if pr_number is not None and pr_number > 0:
        query_params.append(("pr", str(pr_number)))

    path = f"/dashboard/{quote(normalized_repo_full, safe='')}"
    if not query_params:
        return urljoin(get_settings().app_base_url.rstrip('/') + '/', path.lstrip('/'))
    return urljoin(get_settings().app_base_url.rstrip('/') + '/', f"{path.lstrip('/')}?{urlencode(query_params)}")


def _extract_previous_episode_recommendation(comment_body: str) -> str:
    recommendation = _extract_recommendation(comment_body, default="normal review")
    recommendation = recommendation.strip().rstrip(".")
    return recommendation or "normal review"


def _short_sha(value: str) -> str:
    cleaned = (value or "unknown").strip()
    return cleaned[:7] if len(cleaned) > 7 else cleaned


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
            label_name="driftguard: escalate-before-merge",
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


def _fuse_risk_levels(
    deterministic_risk: str,
    semantic_risk: str,
    *,
    semantic_requires_escalation: bool = False,
    semantic_confidence: str | None = None,
) -> str:
    return fuse_risk_levels(
        deterministic_risk,
        semantic_risk,
        semantic_requires_escalation=semantic_requires_escalation,
        semantic_confidence=semantic_confidence,
    )


def _build_signal_fusion_assessment(
    comment_body: str,
    deterministic_analysis: DiffAnalysis,
) -> SignalFusionAssessment:
    deterministic_risk = deterministic_analysis.suggested_risk_level.value
    semantic_risk_explicit = _has_explicit_risk_level(comment_body)
    semantic_risk = _extract_risk_level(comment_body, default=deterministic_risk)
    semantic_confidence = _extract_confidence_level(comment_body, default="Medium")
    if not semantic_risk_explicit:
        semantic_confidence = "Low"
    semantic_recommendation = _extract_recommendation(
        comment_body,
        default=_default_recommendation_for_risk(semantic_risk),
    )
    semantic_requires_escalation = _semantic_recommendation_requires_escalation(semantic_recommendation)
    fused_risk = _fuse_risk_levels(
        deterministic_risk,
        semantic_risk,
        semantic_requires_escalation=semantic_requires_escalation,
        semantic_confidence=semantic_confidence,
    )

    base_recommendation = _build_escalation_recommendation(deterministic_analysis)
    reasons = list(base_recommendation.reasons)
    if fused_risk == "High" and semantic_requires_escalation:
        semantic_reason = "semantic review flagged merge-blocking risk"
        if semantic_reason not in reasons:
            reasons.append(semantic_reason)

    if reasons:
        escalation_recommendation = EscalationRecommendation(
            decision="escalate_before_merge",
            reasons=tuple(reasons),
            label_name="driftguard: escalate-before-merge",
        )
    else:
        escalation_recommendation = EscalationRecommendation(decision="normal_review")

    return SignalFusionAssessment(
        risk_level=fused_risk,
        confidence=semantic_confidence,
        semantic_risk=semantic_risk,
        semantic_requires_escalation=semantic_requires_escalation,
        escalation_recommendation=escalation_recommendation,
    )


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


def _has_explicit_risk_level(comment_body: str) -> bool:
    if re.search(r"risk level\s*[:\-]\s*\**(low|medium|high)\**", comment_body, re.IGNORECASE):
        return True
    if re.search(r"^(low|medium|high) risk\b", comment_body, re.IGNORECASE | re.MULTILINE):
        return True
    return False


def _extract_confidence_level(comment_body: str, *, default: str) -> str:
    match = re.search(r"confidence\s*[:\-]\s*\**(low|medium|high)\**", comment_body, re.IGNORECASE)
    if match:
        return normalize_confidence_level(match.group(1), default=default)
    return normalize_confidence_level(default, default="Medium")


def _normalize_risk_level(risk_level: str) -> str:
    return normalize_risk_level(risk_level, default="High")


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
    token = installation_token or _get_installation_token_for_job(job, settings)
    sync_pr_label(
        job.repo_full,
        job.pr_number,
        token,
        should_have_label=recommendation.requires_label,
        label_name=recommendation.label_name or "driftguard: escalate-before-merge",
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
    fused_confidence: str | None = None,
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
        fused_confidence=fused_confidence,
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
        repo_full=job.repo_full,
        pr_number=job.pr_number,
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
            repo_full=job.repo_full,
            pr_number=job.pr_number,
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
            fused_confidence=fusion_assessment.confidence,
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
        baseline = get_latest_onboarding_baseline_for_repo_artifact(
            settings.db_path,
            job.repo_full,
            artifact.relevance.path,
            only_approved=True,
        )
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
        self._thread = threading.Thread(target=self._run_loop, name="driftguard-audit-worker", daemon=True)
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
