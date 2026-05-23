from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
from types import SimpleNamespace
from urllib.parse import quote, urlencode, urljoin, urlparse

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from config import get_settings

from engine.analysis import DiffAnalysis, analyze_diff
from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import build_attribute_profile, compare_attribute_profiles
from engine.policy import PolicyContext, default_policy_rules, evaluate_policy_rules
from engine.semantic_review import build_semantic_review_packages, format_semantic_review_packages
from engine.verifier import build_verifier_review_requests, should_invoke_verifier
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
    refresh_audit_reaction_feedback_for_audit,
    record_audit_result,
)
from .control_plane_records import get_repo_allocation_for_installation, get_workspace_by_id, get_workspace_entitlement
from .github_integration import create_pr_review, fetch_file_content, generate_jwt, get_installation_token, post_check_run, post_commit_status, sync_pr_label, upsert_pr_comment
from .governance_policy import (
    GOVERNANCE_ROLLOUT_OFF,
    build_governance_ci_outcome,
    evaluate_governance_decision,
    normalize_governance_rollout_mode,
)
from .hybrid_analysis import HybridAnalysisPlan, build_hybrid_analysis_plan
from .hybrid_execution import HybridExecutionSummary, execute_hybrid_analysis_plan
from .onboarding_records import get_latest_onboarding_baseline_for_repo_artifact
from .pr_feedback_mode import PR_FEEDBACK_MODE_OFF, PR_FEEDBACK_MODE_REVIEWS, resolve_pr_feedback_mode
from .scenario_execution import ScenarioEvalExecutionSummary, execute_scenario_eval_plan
from .scenario_evaluation import ScenarioEvalPlan, build_scenario_eval_plan


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
    verifier_rollout_mode: str = "off"
    verifier_max_requests_per_review: int = 3
    governance_status_rollout_mode: str = "off"
    governance_status_context: str = "vipari/governance"
    governance_check_run_rollout_mode: str = "off"
    governance_check_run_name: str = "Vipari Governance"
    scenario_eval_rollout_mode: str = "off"
    scenario_eval_max_artifacts_per_review: int = 2
    scenario_eval_allowed_repos: str = ""
    scenario_eval_allowed_artifact_types: str = ""
    scenario_eval_output_root: str = ""
    hybrid_static_analysis_rollout_mode: str = "off"
    hybrid_static_analysis_max_artifacts_per_review: int = 2
    hybrid_static_analysis_allowed_repos: str = ""
    hybrid_static_analysis_allowed_artifact_types: str = ""


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
    policy_floor: str | None = None
    policy_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifierPlan:
    rollout_mode: str
    should_invoke: bool
    trigger: str | None
    reason: str
    request_count: int


@dataclass(frozen=True)
class LlmCommentBuildResult:
    comment_body: str
    fusion_assessment: SignalFusionAssessment
    verifier_plan: VerifierPlan


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
    verifier_note: str | None
    episode_context: PrCommentEpisodeContext
    dashboard_deep_link: str | None = None
    feedback_link: str | None = None


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
    verifier_rollout_mode: str = "off",
    verifier_max_requests_per_review: int = 3,
    return_metadata: bool = False,
) -> str | LlmCommentBuildResult:
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
    fusion_assessment = _build_signal_fusion_assessment(
        raw_comment,
        deterministic_analysis,
        attribute_profiles=attribute_profiles,
    )
    canonical_details = _build_semantic_comment_details(
        raw_comment,
        deterministic_analysis,
        risk_level=fusion_assessment.risk_level,
    )
    verifier_plan = _build_verifier_plan(
        deterministic_analysis,
        raw_comment,
        semantic_packages=semantic_packages,
        proposed_summary=summary,
        proposed_recommendation=canonical_details.recommendation,
        rollout_mode=verifier_rollout_mode,
        max_requests_per_review=verifier_max_requests_per_review,
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
            policy_floor=fusion_assessment.policy_floor,
            policy_reasons=fusion_assessment.policy_reasons,
        ),
        semantic_recommendation=canonical_details.recommendation,
        escalation_recommendation=fusion_assessment.escalation_recommendation or recommendation,
        attribute_profiles=attribute_profiles,
        verifier_plan=verifier_plan,
        episode_context=episode_context,
        repo_full=repo_full,
        pr_number=pr_number,
    )
    comment_body = _render_pr_comment_review(review)
    if return_metadata:
        return LlmCommentBuildResult(
            comment_body=comment_body,
            fusion_assessment=fusion_assessment,
            verifier_plan=verifier_plan,
        )
    return comment_body


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
    verifier_plan: VerifierPlan | None = None,
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
        verifier_note=_build_verifier_note(verifier_plan),
        episode_context=episode_context or PrCommentEpisodeContext(head_sha="unknown", analyzed_at=time.time()),
        dashboard_deep_link=_build_pr_comment_dashboard_deep_link(repo_full, pr_number, profiles, episode_context),
        feedback_link=_build_pr_comment_feedback_link(repo_full, pr_number, episode_context),
    )


def _render_pr_comment_review(review: PrCommentReview) -> str:
    lines = [
        f"## {_risk_indicator_emoji(review.risk_level)} Vipari: {_decision_header(review.decision)}",
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
            "<summary>Vipari review details</summary>",
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
    if review.verifier_note:
        lines.extend(["", "### Verifier gate", f"- {review.verifier_note}"])
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
                f"[Open this review in Vipari dashboard]({review.dashboard_deep_link})",
            ]
        )
    if review.feedback_link:
        lines.extend(
            [
                f"[Send feedback on this Vipari review]({review.feedback_link})",
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
    policy_floor: str | None = None,
    policy_reasons: tuple[str, ...] = (),
) -> str | None:
    normalized_deterministic = _normalize_risk_level(deterministic_risk)
    normalized_semantic = _normalize_risk_level(semantic_risk)
    normalized_fused = _normalize_risk_level(fused_risk)
    confidence_label = normalize_confidence_level(confidence, default="Medium").lower()

    if policy_floor is not None and _normalize_risk_level(policy_floor) == normalized_fused and normalized_fused != normalized_deterministic:
        reason_text = policy_reasons[0] if policy_reasons else "workspace policy raised the minimum review floor"
        return f"Signal fusion honored a {normalized_fused.lower()} policy floor because {reason_text.rstrip('.').lower()}"

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


def _normalize_verifier_rollout_mode(mode: str | None) -> str:
    candidate = str(mode or "off").strip().lower()
    if candidate == "shadow":
        return "shadow"
    return "off"


def _build_verifier_plan(
    deterministic_analysis: DiffAnalysis,
    comment_body: str,
    *,
    semantic_packages: list[object],
    proposed_summary: str,
    proposed_recommendation: str,
    rollout_mode: str,
    max_requests_per_review: int,
) -> VerifierPlan:
    normalized_mode = _normalize_verifier_rollout_mode(rollout_mode)
    if normalized_mode == "off":
        return VerifierPlan(
            rollout_mode=normalized_mode,
            should_invoke=False,
            trigger=None,
            reason="Verifier rollout is disabled for this worker.",
            request_count=0,
        )

    semantic_risk = _extract_risk_level(comment_body, default=deterministic_analysis.suggested_risk_level.value)
    semantic_confidence = _extract_confidence_level(comment_body, default="Medium")
    semantic_recommendation = _extract_recommendation(
        comment_body,
        default=proposed_recommendation,
    )
    decision = should_invoke_verifier(
        deterministic_analysis.suggested_risk_level.value,
        semantic_risk,
        semantic_confidence,
        semantic_requires_escalation=_semantic_recommendation_requires_escalation(semantic_recommendation),
    )
    if not decision.should_invoke:
        return VerifierPlan(
            rollout_mode=normalized_mode,
            should_invoke=False,
            trigger=None,
            reason=decision.reason,
            request_count=0,
        )

    requested = build_verifier_review_requests(
        semantic_packages[: max(0, max_requests_per_review)],
        proposed_risk_level=semantic_risk,
        proposed_confidence=semantic_confidence,
        proposed_summary=proposed_summary,
        proposed_recommendation=proposed_recommendation,
    )
    return VerifierPlan(
        rollout_mode=normalized_mode,
        should_invoke=bool(requested),
        trigger=decision.trigger.value if decision.trigger is not None else None,
        reason=decision.reason,
        request_count=len(requested),
    )


def _build_verifier_note(verifier_plan: VerifierPlan | None) -> str | None:
    if verifier_plan is None or verifier_plan.rollout_mode in {"off", "shadow"}:
        return None
    if verifier_plan.should_invoke:
        request_label = "artifact" if verifier_plan.request_count == 1 else "artifacts"
        return (
            f"Shadow-mode verifier would review {verifier_plan.request_count} {request_label} "
            f"via `{verifier_plan.trigger or 'unknown'}` because {verifier_plan.reason.rstrip('.').lower()}; "
            "this does not change the merge lane until verifier rollout is promoted beyond shadow mode."
        )
    return f"Shadow-mode verifier stayed idle because {verifier_plan.reason.rstrip('.').lower()}."


def _select_primary_attribute_profile(attribute_profiles: list[ArtifactAttributeProfile]) -> ArtifactAttributeProfile | None:
    if not attribute_profiles:
        return None
    ranked = sorted(
        attribute_profiles,
        key=lambda profile: sum(1 for item in profile.dimensions if item.state != "no_change"),
        reverse=True,
    )
    return ranked[0]


def _build_scenario_eval_plan_for_job(
    job: AuditJob,
    deterministic_analysis: DiffAnalysis,
    settings: WorkerSettings,
) -> ScenarioEvalPlan:
    return build_scenario_eval_plan(
        deterministic_analysis,
        repo_full=job.repo_full,
        rollout_mode=settings.scenario_eval_rollout_mode,
        max_artifacts_per_review=settings.scenario_eval_max_artifacts_per_review,
        allowed_repos=settings.scenario_eval_allowed_repos,
        allowed_artifact_types=settings.scenario_eval_allowed_artifact_types,
    )


def _build_hybrid_analysis_plan_for_job(
    job: AuditJob,
    deterministic_analysis: DiffAnalysis,
    settings: WorkerSettings,
) -> HybridAnalysisPlan:
    return build_hybrid_analysis_plan(
        deterministic_analysis,
        repo_full=job.repo_full,
        rollout_mode=settings.hybrid_static_analysis_rollout_mode,
        max_artifacts_per_review=settings.hybrid_static_analysis_max_artifacts_per_review,
        allowed_repos=settings.hybrid_static_analysis_allowed_repos,
        allowed_artifact_types=settings.hybrid_static_analysis_allowed_artifact_types,
    )


def _execute_scenario_eval_for_job(
    job: AuditJob,
    settings: WorkerSettings,
    scenario_eval_plan: ScenarioEvalPlan,
) -> ScenarioEvalExecutionSummary:
    if not scenario_eval_plan.should_run:
        return ScenarioEvalExecutionSummary(
            rollout_mode=scenario_eval_plan.rollout_mode,
            attempted=False,
            executed=False,
            reason=scenario_eval_plan.reason,
            executions=(),
        )

    try:
        installation_token = _get_installation_token_for_job(job, settings)
    except Exception as exc:
        return ScenarioEvalExecutionSummary(
            rollout_mode=scenario_eval_plan.rollout_mode,
            attempted=False,
            executed=False,
            reason=f"Scenario eval token acquisition failed: {type(exc).__name__}: {exc}",
            executions=(),
        )

    try:
        return execute_scenario_eval_plan(
            scenario_eval_plan,
            db_path=settings.db_path,
            repo_full=job.repo_full,
            installation_id=job.installation_id,
            token=installation_token,
            output_root=settings.scenario_eval_output_root,
            branch_name=f"pr-{job.pr_number}",
            run_label=f"audit-job-{job.id}",
            verifier_rollout_mode=settings.verifier_rollout_mode,
            verifier_max_requests_per_review=settings.verifier_max_requests_per_review,
        )
    except Exception as exc:
        return ScenarioEvalExecutionSummary(
            rollout_mode=scenario_eval_plan.rollout_mode,
            attempted=True,
            executed=False,
            reason=f"Scenario eval execution failed: {type(exc).__name__}: {exc}",
            executions=(),
        )


def _execute_hybrid_analysis_for_job(
    hybrid_analysis_plan: HybridAnalysisPlan,
    artifact_snapshots: dict[str, str],
) -> HybridExecutionSummary:
    try:
        return execute_hybrid_analysis_plan(
            hybrid_analysis_plan,
            artifact_snapshots=artifact_snapshots,
        )
    except Exception as exc:
        return HybridExecutionSummary(
            rollout_mode=hybrid_analysis_plan.rollout_mode,
            attempted=True,
            executed=False,
            reason=f"Hybrid static analysis execution failed: {type(exc).__name__}: {exc}",
            executions=(),
        )


def _episode_metadata_line(context: PrCommentEpisodeContext) -> str:
    timestamp = datetime.fromtimestamp(context.analyzed_at, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    base = f"_Vipari analysis for head `{_short_sha(context.head_sha)}` at {timestamp}._"
    previous_episode = context.previous_episode
    if previous_episode is None:
        return base

    previous_recommendation = _extract_previous_episode_recommendation(previous_episode.audit_comment.comment_body)
    return (
        f"{base[:-2]} Previous Vipari analysis for `{_short_sha(previous_episode.head_sha)}` "
        f"recommended {previous_recommendation.lower()}._"
    )


def _build_pr_comment_dashboard_deep_link(
    repo_full: str | None,
    pr_number: int | None,
    attribute_profiles: list[ArtifactAttributeProfile],
    episode_context: PrCommentEpisodeContext | None = None,
) -> str | None:
    normalized_repo_full = (repo_full or "").strip()
    if not normalized_repo_full:
        return None

    app_base_url = _public_dashboard_base_url()
    if app_base_url is None:
        return None

    query_params: list[tuple[str, str]] = []
    primary_profile = _select_primary_attribute_profile(attribute_profiles)
    artifact_path = (primary_profile.artifact_path if primary_profile is not None else "").strip()
    if artifact_path:
        query_params.append(("artifact", artifact_path))
    query_params.append(("tab", "pr-reviews"))
    if pr_number is not None and pr_number > 0:
        query_params.append(("pr", str(pr_number)))
    episode_head_sha = (episode_context.head_sha if episode_context is not None else "").strip()
    if episode_head_sha:
        query_params.append(("head_sha", episode_head_sha))

    path = f"/dashboard/{quote(normalized_repo_full, safe='')}"
    if not query_params:
        return urljoin(app_base_url.rstrip('/') + '/', path.lstrip('/'))
    return urljoin(app_base_url.rstrip('/') + '/', f"{path.lstrip('/')}?{urlencode(query_params)}")


def _build_pr_comment_feedback_link(
    repo_full: str | None,
    pr_number: int | None,
    episode_context: PrCommentEpisodeContext | None = None,
) -> str | None:
    normalized_repo_full = (repo_full or "").strip()
    if not normalized_repo_full or "/" not in normalized_repo_full or pr_number is None or pr_number <= 0:
        return None

    app_base_url = _public_dashboard_base_url()
    if app_base_url is None:
        return None

    owner, repo = normalized_repo_full.split("/", 1)
    query_params: list[tuple[str, str]] = []
    episode_head_sha = (episode_context.head_sha if episode_context is not None else "").strip()
    if episode_head_sha:
        query_params.append(("head_sha", episode_head_sha))

    path = f"/feedback/pr/{quote(owner, safe='')}/{quote(repo, safe='')}/{pr_number}"
    if not query_params:
        return urljoin(app_base_url.rstrip('/') + '/', path.lstrip('/'))
    return urljoin(app_base_url.rstrip('/') + '/', f"{path.lstrip('/')}?{urlencode(query_params)}")


def _public_dashboard_base_url() -> str | None:
    configured_url = get_settings().app_base_url.strip()
    parsed = urlparse(configured_url)
    if not parsed.scheme or not parsed.netloc:
        return None

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname or hostname in {"localhost", "::1"} or hostname.endswith(".local"):
        return None

    try:
        candidate_ip = ip_address(hostname)
    except ValueError:
        candidate_ip = None

    if candidate_ip is not None and (
        candidate_ip.is_loopback
        or candidate_ip.is_private
        or candidate_ip.is_link_local
        or candidate_ip.is_reserved
        or candidate_ip.is_unspecified
    ):
        return None

    return configured_url


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
            label_name="vipari: escalate-before-merge",
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
    policy_floor: str | None = None,
) -> str:
    return fuse_risk_levels(
        deterministic_risk,
        semantic_risk,
        semantic_requires_escalation=semantic_requires_escalation,
        semantic_confidence=semantic_confidence,
        policy_floor=policy_floor,
    )


def _build_policy_evaluation(
    deterministic_analysis: DiffAnalysis,
    attribute_profiles: list[ArtifactAttributeProfile] | None = None,
):
    policy_rules = default_policy_rules()
    profiles = attribute_profiles or []
    evaluations = []
    risk_order = {"Low": 0, "Medium": 1, "High": 2}

    for profile in profiles:
        attribute_deltas = {
            dimension.attribute_key: dimension.delta
            for dimension in profile.dimensions
            if dimension.delta is not None
        }
        evaluations.append(
            evaluate_policy_rules(
                PolicyContext(
                    attribute_deltas=attribute_deltas,
                    findings=tuple(deterministic_analysis.findings) if len(profiles) == 1 else tuple(),
                ),
                policy_rules,
            )
        )

    if not evaluations:
        evaluations.append(
            evaluate_policy_rules(
                PolicyContext(findings=tuple(deterministic_analysis.findings)),
                policy_rules,
            )
        )

    return max(evaluations, key=lambda evaluation: (risk_order[evaluation.minimum_risk.value], len(evaluation.matched_rules)))


def _build_signal_fusion_assessment(
    comment_body: str,
    deterministic_analysis: DiffAnalysis,
    *,
    attribute_profiles: list[ArtifactAttributeProfile] | None = None,
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
    policy_evaluation = _build_policy_evaluation(deterministic_analysis, attribute_profiles)
    policy_floor = policy_evaluation.minimum_risk.value if policy_evaluation.minimum_risk.value != "Low" else None
    fused_risk = _fuse_risk_levels(
        deterministic_risk,
        semantic_risk,
        semantic_requires_escalation=semantic_requires_escalation,
        semantic_confidence=semantic_confidence,
        policy_floor=policy_floor,
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
            label_name="vipari: escalate-before-merge",
        )
    else:
        escalation_recommendation = EscalationRecommendation(decision="normal_review")

    return SignalFusionAssessment(
        risk_level=fused_risk,
        confidence=semantic_confidence,
        semantic_risk=semantic_risk,
        semantic_requires_escalation=semantic_requires_escalation,
        escalation_recommendation=escalation_recommendation,
        policy_floor=policy_floor,
        policy_reasons=policy_evaluation.rationale,
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


def _post_review_for_job(
    job: AuditJob,
    body: str,
    event: str,
    settings: WorkerSettings,
    *,
    installation_token: str | None = None,
) -> int:
    token = installation_token or _get_installation_token_for_job(job, settings)
    existing_comment = get_audit_comment_episode_for_pr_head_sha(
        settings.db_path,
        job.repo_full,
        job.pr_number,
        job.head_sha,
    )
    if existing_comment is not None and existing_comment.audit_comment.github_review_id is not None:
        return existing_comment.audit_comment.github_review_id
    return create_pr_review(
        job.repo_full,
        job.pr_number,
        token,
        body,
        event=event,
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
        label_name=recommendation.label_name or "vipari: escalate-before-merge",
    )


def _append_audit_error_message(existing: str | None, additional: str) -> str:
    if existing:
        return f"{existing}; {additional}"
    return additional


def _governance_status_state(conclusion: str) -> str:
    normalized = str(conclusion or "").strip().lower()
    if normalized == "failure":
        return "failure"
    return "success"


def _governance_status_description(outcome: dict[str, object]) -> str:
    recommended_gate = str(outcome.get("recommended_gate") or "pass").strip().lower()
    if recommended_gate == "block":
        return "Vipari governance blocked merge pending escalation."
    if recommended_gate == "warn":
        return "Vipari governance recommends escalation review before merge."
    return "Vipari governance found no merge-blocking escalation signal."


def _governance_check_run_title(outcome: dict[str, object]) -> str:
    recommended_gate = str(outcome.get("recommended_gate") or "pass").strip().lower()
    if recommended_gate == "block":
        return "Governance blocked merge"
    if recommended_gate == "warn":
        return "Governance recommends escalation"
    return "Governance passed"


def _governance_reason_evidence_lines(reason: object, *, limit: int = 2) -> list[str]:
    evidence = getattr(reason, "evidence", ()) or ()
    lines: list[str] = []
    for item in tuple(str(value).strip() for value in evidence if str(value).strip())[:limit]:
        lines.append(f"  Evidence: {item}")
    return lines


def _governance_check_run_text(decision: object) -> str:
    rationale = getattr(decision, "rationale", ()) or ()
    lines = [
        f"Decision lane: {getattr(decision, 'decision_lane', 'inactive')}",
        f"Rollout mode: {getattr(decision, 'rollout_mode', 'off')}",
    ]
    if rationale:
        lines.append("")
        lines.append("Rationale:")
        for reason in rationale:
            lines.append(f"- {reason.summary}")
            lines.extend(_governance_reason_evidence_lines(reason))
    return "\n".join(lines)


def _fallback_governance_check_run_text(
    deterministic_analysis: DiffAnalysis,
    *,
    error_message: str,
    recommendation: EscalationRecommendation,
) -> str:
    lines = [
        f"Fallback reason: {error_message}",
        f"Deterministic risk: {_normalize_risk_level(deterministic_analysis.suggested_risk_level.value)}",
        f"Escalation decision: {recommendation.decision}",
    ]
    if deterministic_analysis.findings:
        lines.append("")
        lines.append("Deterministic findings:")
        for finding in deterministic_analysis.findings[:2]:
            lines.append(f"- {finding.title}")
    return "\n".join(lines)


def _build_governance_signal_for_job(
    job: AuditJob,
    deterministic_analysis: DiffAnalysis,
    *,
    rollout_mode: str,
    suggested_risk_level: str,
    fused_confidence: str | None,
    verifier_mode: str | None,
    verifier_trigger: str | None,
    verifier_request_count: int,
    attribute_profiles: list[ArtifactAttributeProfile],
    episode_context: PrCommentEpisodeContext,
) -> tuple[object, dict[str, object], str | None] | None:
    normalized_rollout_mode = normalize_governance_rollout_mode(rollout_mode)
    if normalized_rollout_mode == GOVERNANCE_ROLLOUT_OFF:
        return None

    transient_audit = SimpleNamespace(
        status="completed",
        suggested_risk_level=suggested_risk_level,
        fused_confidence=fused_confidence,
        verifier_mode=verifier_mode,
        verifier_trigger=verifier_trigger,
        verifier_request_count=verifier_request_count,
    )
    decision = evaluate_governance_decision(
        transient_audit,
        findings=deterministic_analysis.findings,
        rollout_mode=normalized_rollout_mode,
    )
    outcome = build_governance_ci_outcome(decision)
    target_url = _build_pr_comment_dashboard_deep_link(
        job.repo_full,
        job.pr_number,
        attribute_profiles,
        episode_context,
    )
    return decision, outcome, target_url


def _post_governance_status_for_job(
    job: AuditJob,
    deterministic_analysis: DiffAnalysis,
    settings: WorkerSettings,
    *,
    suggested_risk_level: str,
    fused_confidence: str | None,
    verifier_mode: str | None,
    verifier_trigger: str | None,
    verifier_request_count: int,
    attribute_profiles: list[ArtifactAttributeProfile],
    episode_context: PrCommentEpisodeContext,
    installation_token: str | None = None,
) -> None:
    signal = _build_governance_signal_for_job(
        job,
        deterministic_analysis,
        rollout_mode=settings.governance_status_rollout_mode,
        suggested_risk_level=suggested_risk_level,
        fused_confidence=fused_confidence,
        verifier_mode=verifier_mode,
        verifier_trigger=verifier_trigger,
        verifier_request_count=verifier_request_count,
        attribute_profiles=attribute_profiles,
        episode_context=episode_context,
    )
    if signal is None:
        return
    _decision, outcome, target_url = signal
    token = installation_token or _get_installation_token_for_job(job, settings)
    post_commit_status(
        job.repo_full,
        job.head_sha,
        token,
        state=_governance_status_state(str(outcome.get("conclusion") or "")),
        description=_governance_status_description(outcome),
        context=(settings.governance_status_context or "vipari/governance").strip() or "vipari/governance",
        target_url=target_url,
    )


def _post_governance_check_run_for_job(
    job: AuditJob,
    deterministic_analysis: DiffAnalysis,
    settings: WorkerSettings,
    *,
    suggested_risk_level: str,
    fused_confidence: str | None,
    verifier_mode: str | None,
    verifier_trigger: str | None,
    verifier_request_count: int,
    attribute_profiles: list[ArtifactAttributeProfile],
    episode_context: PrCommentEpisodeContext,
    installation_token: str | None = None,
) -> None:
    signal = _build_governance_signal_for_job(
        job,
        deterministic_analysis,
        rollout_mode=settings.governance_check_run_rollout_mode,
        suggested_risk_level=suggested_risk_level,
        fused_confidence=fused_confidence,
        verifier_mode=verifier_mode,
        verifier_trigger=verifier_trigger,
        verifier_request_count=verifier_request_count,
        attribute_profiles=attribute_profiles,
        episode_context=episode_context,
    )
    if signal is None:
        return

    decision, outcome, target_url = signal
    token = installation_token or _get_installation_token_for_job(job, settings)
    post_check_run(
        job.repo_full,
        job.head_sha,
        token,
        name=(settings.governance_check_run_name or "Vipari Governance").strip() or "Vipari Governance",
        conclusion=str(outcome.get("conclusion") or "success").strip().lower(),
        title=_governance_check_run_title(outcome),
        summary=_governance_status_description(outcome),
        text=_governance_check_run_text(decision),
        details_url=target_url,
    )


def _post_fallback_governance_check_run_for_job(
    job: AuditJob,
    deterministic_analysis: DiffAnalysis,
    settings: WorkerSettings,
    *,
    error_message: str,
    recommendation: EscalationRecommendation,
    attribute_profiles: list[ArtifactAttributeProfile],
    episode_context: PrCommentEpisodeContext,
    installation_token: str | None = None,
) -> None:
    rollout_mode = normalize_governance_rollout_mode(settings.governance_check_run_rollout_mode)
    if rollout_mode == GOVERNANCE_ROLLOUT_OFF:
        return

    token = installation_token or _get_installation_token_for_job(job, settings)
    post_check_run(
        job.repo_full,
        job.head_sha,
        token,
        name=(settings.governance_check_run_name or "Vipari Governance").strip() or "Vipari Governance",
        conclusion="neutral",
        title="Governance fallback review posted",
        summary="Vipari posted a deterministic fallback review because full semantic review was unavailable.",
        text=_fallback_governance_check_run_text(
            deterministic_analysis,
            error_message=error_message,
            recommendation=recommendation,
        ),
        details_url=_build_pr_comment_dashboard_deep_link(
            job.repo_full,
            job.pr_number,
            attribute_profiles,
            episode_context,
        ),
    )


def _pending_governance_check_run_text(error_message: str, *, retry_at: float | None) -> str:
    lines = [f"Retry reason: {error_message}"]
    if retry_at is not None:
        lines.append(f"Retry scheduled at: {int(retry_at)}")
    return "\n".join(lines)


def _post_pending_governance_check_run_for_job(
    job: AuditJob,
    settings: WorkerSettings,
    *,
    error_message: str,
    retry_at: float | None,
    attribute_profiles: list[ArtifactAttributeProfile],
    episode_context: PrCommentEpisodeContext,
    installation_token: str | None = None,
) -> None:
    rollout_mode = normalize_governance_rollout_mode(settings.governance_check_run_rollout_mode)
    if rollout_mode == GOVERNANCE_ROLLOUT_OFF:
        return

    token = installation_token or _get_installation_token_for_job(job, settings)
    post_check_run(
        job.repo_full,
        job.head_sha,
        token,
        name=(settings.governance_check_run_name or "Vipari Governance").strip() or "Vipari Governance",
        status="in_progress",
        conclusion=None,
        title="Governance review pending retry",
        summary="Vipari will retry governance review after a transient analysis failure.",
        text=_pending_governance_check_run_text(error_message, retry_at=retry_at),
        details_url=_build_pr_comment_dashboard_deep_link(
            job.repo_full,
            job.pr_number,
            attribute_profiles,
            episode_context,
        ),
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
    github_review_id: int | None = None,
    artifact_snapshots: dict[str, str] | None = None,
    pr_feedback_mode: str | None = None,
    verifier_mode: str | None = None,
    verifier_trigger: str | None = None,
    verifier_request_count: int = 0,
    scenario_eval_plan: ScenarioEvalPlan | None = None,
    scenario_eval_execution_summary: ScenarioEvalExecutionSummary | None = None,
    hybrid_analysis_plan: HybridAnalysisPlan | None = None,
    hybrid_execution_summary: HybridExecutionSummary | None = None,
) :
    scenario_eval_plan = scenario_eval_plan or ScenarioEvalPlan(
        rollout_mode="off",
        should_run=False,
        reason="Scenario eval rollout is disabled for this worker.",
        artifact_paths=(),
    )
    scenario_eval_execution_summary = scenario_eval_execution_summary or ScenarioEvalExecutionSummary(
        rollout_mode=scenario_eval_plan.rollout_mode,
        attempted=False,
        executed=False,
        reason=scenario_eval_plan.reason,
        executions=(),
    )
    hybrid_analysis_plan = hybrid_analysis_plan or HybridAnalysisPlan(
        rollout_mode="off",
        should_run=False,
        reason="Hybrid static analysis rollout is disabled for this worker.",
        requests=(),
    )
    hybrid_execution_summary = hybrid_execution_summary or HybridExecutionSummary(
        rollout_mode=hybrid_analysis_plan.rollout_mode,
        attempted=False,
        executed=False,
        reason=hybrid_analysis_plan.reason,
        executions=(),
    )
    return record_audit_result(
        settings.db_path,
        job_id=job.id,
        repo_full=job.repo_full,
        pr_number=job.pr_number,
        pr_title=job.pr_title,
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
        pr_feedback_mode=pr_feedback_mode,
        comment_body=comment_body,
        comment_mode=comment_mode,
        semantic_review_completed=semantic_review_completed,
        suggested_risk_level=suggested_risk_level,
        fused_confidence=fused_confidence,
        error_message=error_message,
        artifact_snapshots=artifact_snapshots or _fetch_artifact_snapshots(job, deterministic_analysis, settings),
        github_comment_id=github_comment_id,
        github_review_id=github_review_id,
        verifier_mode=verifier_mode,
        verifier_trigger=verifier_trigger,
        verifier_request_count=verifier_request_count,
        scenario_eval_mode=scenario_eval_plan.rollout_mode,
        scenario_eval_artifact_count=scenario_eval_plan.artifact_count,
        scenario_eval_selection_reason=scenario_eval_plan.reason,
        scenario_eval_artifact_paths=list(scenario_eval_plan.artifact_paths),
        scenario_eval_execution_count=scenario_eval_execution_summary.execution_count,
        scenario_eval_execution_reason=scenario_eval_execution_summary.reason,
        scenario_eval_executions=[execution.__dict__ for execution in scenario_eval_execution_summary.executions],
        hybrid_analysis_mode=hybrid_analysis_plan.rollout_mode,
        hybrid_analysis_request_count=hybrid_analysis_plan.request_count,
        hybrid_analysis_selection_reason=hybrid_analysis_plan.reason,
        hybrid_analysis_requests=[request.__dict__ for request in hybrid_analysis_plan.requests],
        hybrid_analysis_execution_count=hybrid_execution_summary.execution_count,
        hybrid_analysis_execution_reason=hybrid_execution_summary.reason,
        hybrid_analysis_executions=[execution.__dict__ for execution in hybrid_execution_summary.executions],
    )


def _refresh_reaction_feedback_for_audit(
    audit_id: int,
    job: AuditJob,
    settings: WorkerSettings,
    *,
    installation_token: str | None = None,
) -> None:
    token = installation_token or _get_installation_token_for_job(job, settings)
    refresh_audit_reaction_feedback_for_audit(settings.db_path, audit_id=audit_id, token=token)


def _resolve_job_pr_feedback_mode(job: AuditJob, settings: WorkerSettings) -> str:
    allocation = get_repo_allocation_for_installation(settings.db_path, job.installation_id, job.repo_full)
    if allocation is None:
        return resolve_pr_feedback_mode(None, None)
    workspace = get_workspace_by_id(settings.db_path, allocation.workspace_id)
    if workspace is not None and not workspace.pr_comments_setting_enabled:
        return PR_FEEDBACK_MODE_OFF
    entitlement = get_workspace_entitlement(settings.db_path, allocation.workspace_id)
    if entitlement is not None and not entitlement.pr_comments_enabled:
        return PR_FEEDBACK_MODE_OFF
    workspace_mode = workspace.pr_feedback_mode if workspace is not None else None
    return resolve_pr_feedback_mode(workspace_mode, allocation.pr_feedback_mode)


def _review_event_for_risk_level(risk_level: str) -> str | None:
    normalized_risk = _normalize_risk_level(risk_level)
    if normalized_risk == "High":
        return "REQUEST_CHANGES"
    if normalized_risk == "Medium":
        return "COMMENT"
    return None


def _review_comment_mode(event: str) -> str:
    return "review_request_changes" if event == "REQUEST_CHANGES" else "review_comment"


def _handle_fallback(
    job: AuditJob,
    settings: WorkerSettings,
    deterministic_analysis: DiffAnalysis,
    *,
    error_message: str,
    artifact_snapshots: dict[str, str] | None = None,
    escalation_recommendation: EscalationRecommendation | None = None,
    pr_feedback_mode: str | None = None,
    scenario_eval_plan: ScenarioEvalPlan | None = None,
    scenario_eval_execution_summary: ScenarioEvalExecutionSummary | None = None,
    hybrid_analysis_plan: HybridAnalysisPlan | None = None,
    hybrid_execution_summary: HybridExecutionSummary | None = None,
) -> str:
    effective_feedback_mode = pr_feedback_mode or _resolve_job_pr_feedback_mode(job, settings)
    effective_scenario_eval_plan = scenario_eval_plan or _build_scenario_eval_plan_for_job(job, deterministic_analysis, settings)
    effective_scenario_eval_execution_summary = scenario_eval_execution_summary or _execute_scenario_eval_for_job(
        job,
        settings,
        effective_scenario_eval_plan,
    )
    effective_hybrid_analysis_plan = hybrid_analysis_plan or _build_hybrid_analysis_plan_for_job(job, deterministic_analysis, settings)
    effective_hybrid_execution_summary = hybrid_execution_summary or _execute_hybrid_analysis_for_job(
        effective_hybrid_analysis_plan,
        artifact_snapshots or {},
    )
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
    if effective_feedback_mode == PR_FEEDBACK_MODE_OFF:
        try:
            audit = _persist_audit_result(
                job,
                deterministic_analysis,
                settings,
                status="completed",
                completion_mode="completed",
                output_mode="suppressed",
                comment_body=None,
                comment_mode=None,
                semantic_review_completed=False,
                error_message=error_message,
                artifact_snapshots=artifact_snapshots,
                pr_feedback_mode=effective_feedback_mode,
                scenario_eval_plan=effective_scenario_eval_plan,
                scenario_eval_execution_summary=effective_scenario_eval_execution_summary,
                hybrid_analysis_plan=effective_hybrid_analysis_plan,
                hybrid_execution_summary=effective_hybrid_execution_summary,
            )
        except Exception as persist_exc:
            combined_error = f"{error_message}; persistence failed during silent fallback: {type(persist_exc).__name__}: {persist_exc}"
            mark_job_failed(settings.db_path, job.id, error_message=combined_error)
            return "failed"

        mark_job_completed(settings.db_path, job.id, comment_body=None)
        return "completed"
    if effective_feedback_mode == PR_FEEDBACK_MODE_REVIEWS:
        review_event = _review_event_for_risk_level(deterministic_analysis.suggested_risk_level.value)
        github_review_id = None
        combined_error_message = error_message
        if review_event is not None:
            try:
                installation_token = _get_installation_token_for_job(job, settings)
                github_review_id = _post_review_for_job(
                    job,
                    fallback_comment,
                    review_event,
                    settings,
                    installation_token=installation_token,
                )
            except Exception as fallback_exc:
                combined_error = f"{error_message}; fallback review failed: {type(fallback_exc).__name__}: {fallback_exc}"
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
                        pr_feedback_mode=effective_feedback_mode,
                        scenario_eval_plan=effective_scenario_eval_plan,
                        scenario_eval_execution_summary=effective_scenario_eval_execution_summary,
                        hybrid_analysis_plan=effective_hybrid_analysis_plan,
                        hybrid_execution_summary=effective_hybrid_execution_summary,
                    )
                except Exception as persist_exc:
                    combined_error = f"{combined_error}; persistence failed: {type(persist_exc).__name__}: {persist_exc}"
                mark_job_failed(settings.db_path, job.id, error_message=combined_error)
                return "failed"
        else:
            installation_token = _get_installation_token_for_job(job, settings)

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
            _post_fallback_governance_check_run_for_job(
                job,
                deterministic_analysis,
                settings,
                error_message=error_message,
                recommendation=recommendation,
                attribute_profiles=comment_attribute_profiles,
                episode_context=episode_context,
                installation_token=installation_token,
            )
        except Exception as exc:
            combined_error_message = _append_audit_error_message(
                combined_error_message,
                f"Governance fallback check run not applied: {type(exc).__name__}: {exc}",
            )

        try:
            audit = _persist_audit_result(
                job,
                deterministic_analysis,
                settings,
                status="fallback_posted" if review_event is not None else "completed",
                completion_mode="fallback_posted" if review_event is not None else "completed",
                output_mode="preliminary_review_fallback" if review_event is not None else "suppressed",
                comment_body=fallback_comment if review_event is not None else None,
                comment_mode=_review_comment_mode(review_event) if review_event is not None else None,
                semantic_review_completed=False,
                error_message=combined_error_message,
                github_review_id=github_review_id,
                artifact_snapshots=artifact_snapshots,
                pr_feedback_mode=effective_feedback_mode,
                scenario_eval_plan=effective_scenario_eval_plan,
                scenario_eval_execution_summary=effective_scenario_eval_execution_summary,
                hybrid_analysis_plan=effective_hybrid_analysis_plan,
                hybrid_execution_summary=effective_hybrid_execution_summary,
            )
        except Exception as persist_exc:
            combined_error = f"{combined_error_message}; persistence failed after fallback review path: {type(persist_exc).__name__}: {persist_exc}"
            mark_job_failed(settings.db_path, job.id, error_message=combined_error)
            return "failed"

        if review_event is not None:
            try:
                _refresh_reaction_feedback_for_audit(audit.id, job, settings, installation_token=installation_token)
            except Exception:
                pass

        if review_event is not None:
            mark_job_fallback_posted(
                settings.db_path,
                job.id,
                comment_body=fallback_comment,
                error_message=combined_error_message,
            )
            return "fallback_posted"

        mark_job_completed(settings.db_path, job.id, comment_body=None)
        return "completed"
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
                pr_feedback_mode=effective_feedback_mode,
                scenario_eval_plan=effective_scenario_eval_plan,
                scenario_eval_execution_summary=effective_scenario_eval_execution_summary,
                hybrid_analysis_plan=effective_hybrid_analysis_plan,
                hybrid_execution_summary=effective_hybrid_execution_summary,
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
        _post_fallback_governance_check_run_for_job(
            job,
            deterministic_analysis,
            settings,
            error_message=error_message,
            recommendation=recommendation,
            attribute_profiles=comment_attribute_profiles,
            episode_context=episode_context,
            installation_token=installation_token,
        )
    except Exception as exc:
        combined_error_message = _append_audit_error_message(
            combined_error_message,
            f"Governance fallback check run not applied: {type(exc).__name__}: {exc}",
        )

    try:
        audit = _persist_audit_result(
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
            pr_feedback_mode=effective_feedback_mode,
            scenario_eval_plan=effective_scenario_eval_plan,
            scenario_eval_execution_summary=effective_scenario_eval_execution_summary,
            hybrid_analysis_plan=effective_hybrid_analysis_plan,
            hybrid_execution_summary=effective_hybrid_execution_summary,
        )
    except Exception as persist_exc:
        combined_error = (
            f"{combined_error_message}; persistence failed after fallback comment post: {type(persist_exc).__name__}: {persist_exc}"
        )
        mark_job_failed(settings.db_path, job.id, error_message=combined_error)
        return "failed"

    try:
        _refresh_reaction_feedback_for_audit(audit.id, job, settings, installation_token=installation_token)
    except Exception:
        pass

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
    pr_feedback_mode = _resolve_job_pr_feedback_mode(job, settings)
    scenario_eval_plan = _build_scenario_eval_plan_for_job(job, deterministic_analysis, settings)
    scenario_eval_execution_summary = _execute_scenario_eval_for_job(job, settings, scenario_eval_plan)
    hybrid_analysis_plan = _build_hybrid_analysis_plan_for_job(job, deterministic_analysis, settings)
    hybrid_execution_summary = _execute_hybrid_analysis_for_job(hybrid_analysis_plan, artifact_snapshots)
    try:
        comment_result = build_llm_comment(
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
            verifier_rollout_mode=settings.verifier_rollout_mode,
            verifier_max_requests_per_review=settings.verifier_max_requests_per_review,
            return_metadata=True,
        )
        if isinstance(comment_result, LlmCommentBuildResult):
            comment_body = comment_result.comment_body
            fusion_assessment = comment_result.fusion_assessment
            verifier_plan = comment_result.verifier_plan
        else:
            comment_body = comment_result
            fusion_assessment = _build_signal_fusion_assessment(
                comment_body,
                deterministic_analysis,
                attribute_profiles=attribute_profiles,
            )
            verifier_plan = _build_verifier_plan(
                deterministic_analysis,
                comment_body,
                semantic_packages=build_semantic_review_packages(deterministic_analysis),
                proposed_summary=_extract_summary(comment_body, default=_build_fallback_summary(deterministic_analysis)),
                proposed_recommendation=_extract_recommendation(
                    comment_body,
                    default=_default_recommendation_for_risk(fusion_assessment.risk_level),
                ),
                rollout_mode=settings.verifier_rollout_mode,
                max_requests_per_review=settings.verifier_max_requests_per_review,
            )
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        if _is_retryable_llm_error(exc) and _should_retry(job, settings):
            retry_delay_seconds = _extract_retry_after_seconds(exc) or _retry_delay_seconds(job.attempt_count)
            retry_at = time.time() + retry_delay_seconds
            retry_error_message = error_message
            try:
                _post_pending_governance_check_run_for_job(
                    job,
                    settings,
                    error_message=error_message,
                    retry_at=retry_at,
                    attribute_profiles=attribute_profiles,
                    episode_context=episode_context,
                )
            except Exception as pending_exc:
                retry_error_message = _append_audit_error_message(
                    retry_error_message,
                    f"Governance pending check run not applied: {type(pending_exc).__name__}: {pending_exc}",
                )
            mark_job_retry(settings.db_path, job.id, error_message=retry_error_message, retry_at=retry_at)
            return "retry_wait"

        return _handle_fallback(
            job,
            settings,
            deterministic_analysis,
            error_message=error_message,
            artifact_snapshots=artifact_snapshots,
            escalation_recommendation=escalation_recommendation,
            pr_feedback_mode=pr_feedback_mode,
            scenario_eval_plan=scenario_eval_plan,
            scenario_eval_execution_summary=scenario_eval_execution_summary,
            hybrid_analysis_plan=hybrid_analysis_plan,
            hybrid_execution_summary=hybrid_execution_summary,
        )

    if pr_feedback_mode == PR_FEEDBACK_MODE_OFF:
        try:
            audit = _persist_audit_result(
                job,
                deterministic_analysis,
                settings,
                status="completed",
                completion_mode="completed",
                output_mode="suppressed",
                comment_body=None,
                comment_mode=None,
                semantic_review_completed=True,
                suggested_risk_level=fusion_assessment.risk_level,
                fused_confidence=fusion_assessment.confidence,
                error_message=None,
                artifact_snapshots=artifact_snapshots,
                pr_feedback_mode=pr_feedback_mode,
                verifier_mode=verifier_plan.rollout_mode,
                verifier_trigger=verifier_plan.trigger,
                verifier_request_count=verifier_plan.request_count,
                scenario_eval_plan=scenario_eval_plan,
                scenario_eval_execution_summary=scenario_eval_execution_summary,
                hybrid_analysis_plan=hybrid_analysis_plan,
                hybrid_execution_summary=hybrid_execution_summary,
            )
        except Exception as persist_exc:
            error_message = f"Persistence failure after silent audit completion: {type(persist_exc).__name__}: {persist_exc}"
            mark_job_failed(settings.db_path, job.id, error_message=error_message)
            return "failed"

        mark_job_completed(settings.db_path, job.id, comment_body=None)
        return "completed"
    if pr_feedback_mode == PR_FEEDBACK_MODE_REVIEWS:
        review_event = _review_event_for_risk_level(fusion_assessment.risk_level)
        github_review_id = None
        installation_token = _get_installation_token_for_job(job, settings)

        if review_event is not None:
            try:
                github_review_id = _post_review_for_job(
                    job,
                    comment_body,
                    review_event,
                    settings,
                    installation_token=installation_token,
                )
            except Exception as exc:
                error_message = f"{type(exc).__name__}: {exc}"
                return _handle_fallback(
                    job,
                    settings,
                    deterministic_analysis,
                    error_message=error_message,
                    artifact_snapshots=artifact_snapshots,
                    escalation_recommendation=escalation_recommendation,
                    pr_feedback_mode=pr_feedback_mode,
                    scenario_eval_plan=scenario_eval_plan,
                    scenario_eval_execution_summary=scenario_eval_execution_summary,
                    hybrid_analysis_plan=hybrid_analysis_plan,
                    hybrid_execution_summary=hybrid_execution_summary,
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
            _post_governance_status_for_job(
                job,
                deterministic_analysis,
                settings,
                suggested_risk_level=fusion_assessment.risk_level,
                fused_confidence=fusion_assessment.confidence,
                verifier_mode=verifier_plan.rollout_mode,
                verifier_trigger=verifier_plan.trigger,
                verifier_request_count=verifier_plan.request_count,
                attribute_profiles=attribute_profiles,
                episode_context=episode_context,
                installation_token=installation_token,
            )
        except Exception as exc:
            audit_error_message = _append_audit_error_message(
                audit_error_message,
                f"Governance status not applied: {type(exc).__name__}: {exc}",
            )
        try:
            _post_governance_check_run_for_job(
                job,
                deterministic_analysis,
                settings,
                suggested_risk_level=fusion_assessment.risk_level,
                fused_confidence=fusion_assessment.confidence,
                verifier_mode=verifier_plan.rollout_mode,
                verifier_trigger=verifier_plan.trigger,
                verifier_request_count=verifier_plan.request_count,
                attribute_profiles=attribute_profiles,
                episode_context=episode_context,
                installation_token=installation_token,
            )
        except Exception as exc:
            audit_error_message = _append_audit_error_message(
                audit_error_message,
                f"Governance check run not applied: {type(exc).__name__}: {exc}",
            )

        try:
            _persist_audit_result(
                job,
                deterministic_analysis,
                settings,
                status="completed",
                completion_mode="completed",
                output_mode="formal_review" if review_event is not None else "suppressed",
                comment_body=comment_body if review_event is not None else None,
                comment_mode=_review_comment_mode(review_event) if review_event is not None else None,
                semantic_review_completed=True,
                suggested_risk_level=fusion_assessment.risk_level,
                fused_confidence=fusion_assessment.confidence,
                error_message=audit_error_message,
                github_review_id=github_review_id,
                artifact_snapshots=artifact_snapshots,
                pr_feedback_mode=pr_feedback_mode,
                verifier_mode=verifier_plan.rollout_mode,
                verifier_trigger=verifier_plan.trigger,
                verifier_request_count=verifier_plan.request_count,
                scenario_eval_plan=scenario_eval_plan,
                scenario_eval_execution_summary=scenario_eval_execution_summary,
                hybrid_analysis_plan=hybrid_analysis_plan,
                hybrid_execution_summary=hybrid_execution_summary,
            )
        except Exception as persist_exc:
            error_message = f"Persistence failure after review post: {type(persist_exc).__name__}: {persist_exc}"
            mark_job_failed(settings.db_path, job.id, error_message=error_message)
            return "failed"

        if review_event is not None:
            try:
                _refresh_reaction_feedback_for_audit(audit.id, job, settings, installation_token=installation_token)
            except Exception:
                pass

        mark_job_completed(settings.db_path, job.id, comment_body=comment_body if review_event is not None else None)
        return "completed"

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
            pr_feedback_mode=pr_feedback_mode,
            scenario_eval_plan=scenario_eval_plan,
            scenario_eval_execution_summary=scenario_eval_execution_summary,
            hybrid_analysis_plan=hybrid_analysis_plan,
            hybrid_execution_summary=hybrid_execution_summary,
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
        _post_governance_status_for_job(
            job,
            deterministic_analysis,
            settings,
            suggested_risk_level=fusion_assessment.risk_level,
            fused_confidence=fusion_assessment.confidence,
            verifier_mode=verifier_plan.rollout_mode,
            verifier_trigger=verifier_plan.trigger,
            verifier_request_count=verifier_plan.request_count,
            attribute_profiles=attribute_profiles,
            episode_context=episode_context,
            installation_token=installation_token,
        )
    except Exception as exc:
        audit_error_message = _append_audit_error_message(
            audit_error_message,
            f"Governance status not applied: {type(exc).__name__}: {exc}",
        )
    try:
        _post_governance_check_run_for_job(
            job,
            deterministic_analysis,
            settings,
            suggested_risk_level=fusion_assessment.risk_level,
            fused_confidence=fusion_assessment.confidence,
            verifier_mode=verifier_plan.rollout_mode,
            verifier_trigger=verifier_plan.trigger,
            verifier_request_count=verifier_plan.request_count,
            attribute_profiles=attribute_profiles,
            episode_context=episode_context,
            installation_token=installation_token,
        )
    except Exception as exc:
        audit_error_message = _append_audit_error_message(
            audit_error_message,
            f"Governance check run not applied: {type(exc).__name__}: {exc}",
        )

    try:
        audit = _persist_audit_result(
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
            pr_feedback_mode=pr_feedback_mode,
            verifier_mode=verifier_plan.rollout_mode,
            verifier_trigger=verifier_plan.trigger,
            verifier_request_count=verifier_plan.request_count,
            scenario_eval_plan=scenario_eval_plan,
            scenario_eval_execution_summary=scenario_eval_execution_summary,
            hybrid_analysis_plan=hybrid_analysis_plan,
            hybrid_execution_summary=hybrid_execution_summary,
        )
    except Exception as persist_exc:
        error_message = f"Persistence failure after comment post: {type(persist_exc).__name__}: {persist_exc}"
        mark_job_failed(settings.db_path, job.id, error_message=error_message)
        return "failed"

    try:
        _refresh_reaction_feedback_for_audit(audit.id, job, settings, installation_token=installation_token)
    except Exception:
        pass

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
