from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import List

from .context_selector import apply_context_mode
from .diff_parser import extract_changed_files
from .models import ChangedFile, MicroClassifierResult, RelevanceConfidenceTier, RelevanceResult, RelevanceSignal, SemanticContextMode


CLEAR_YES_THRESHOLD = 70
UNCERTAIN_THRESHOLD = 1
MICRO_CLASSIFIER_DIFF_CHAR_LIMIT = 4000


PATH_RULES = [
    (("prompt", "prompts"), "prompt", "Path indicates a prompt artifact.", 90),
    (("system",), "system_prompt", "Path indicates a system instruction artifact.", 85),
    (("policy",), "policy", "Path indicates a policy artifact.", 80),
    (("guardrail", "safety"), "guardrail", "Path indicates a guardrail or safety artifact.", 80),
    (("model",), "model_config", "Path indicates a model configuration artifact.", 75),
    (("tool",), "tooling", "Path indicates a tool configuration artifact.", 75),
    (("rag", "retriev"), "retrieval", "Path indicates a retrieval-related artifact.", 75),
    (("ai", "llm", "assistant"), "ai_code", "Path indicates AI-related code or assets.", 55),
]

CONTENT_RULES = [
    (("system prompt", "assistant behavior"), "system_prompt", "Content indicates a system prompt artifact.", 80),
    (("refuse", "do not reveal", "safety"), "guardrail", "Content indicates safety or guardrail instructions.", 75),
    (("temperature", "top_p", "model=", 'model:'), "model_config", "Content indicates model configuration.", 75),
    (("tool", "function calling", "function_call"), "tooling", "Content indicates tool usage or configuration.", 70),
    (("retrieval", "knowledge base", "rag"), "retrieval", "Content indicates retrieval configuration.", 70),
]


@dataclass(frozen=True)
class AuditDecision:
    should_audit: bool
    relevant_results: List[RelevanceResult] = field(default_factory=list)
    skipped_results: List[RelevanceResult] = field(default_factory=list)

    @property
    def all_results(self) -> List[RelevanceResult]:
        return [*self.relevant_results, *self.skipped_results]


def _classify_confidence_tier(score: int) -> RelevanceConfidenceTier:
    if score >= CLEAR_YES_THRESHOLD:
        return RelevanceConfidenceTier.CLEAR_YES
    if score >= UNCERTAIN_THRESHOLD:
        return RelevanceConfidenceTier.UNCERTAIN
    return RelevanceConfidenceTier.CLEAR_NO


def _build_relevance_result(changed_file: ChangedFile, signals: List[RelevanceSignal]) -> RelevanceResult:
    if signals:
        top_signal = max(signals, key=lambda item: item.weight)
        score = min(sum(signal.weight for signal in signals), 100)
        reason = " ".join(dict.fromkeys(signal.reason for signal in sorted(signals, key=lambda item: item.weight, reverse=True)[:2]))
        artifact_type = top_signal.artifact_type
    else:
        score = 0
        reason = "No AI-specific path or content signal matched."
        artifact_type = "generic"
    return apply_context_mode(
        RelevanceResult(
            path=changed_file.path,
            artifact_type=artifact_type,
            reason=reason,
            context_mode=SemanticContextMode.DIFF_ONLY,
            heuristic_score=score,
            confidence_tier=_classify_confidence_tier(score),
            matched_signals=signals,
        )
    )


def resolve_relevance_with_micro_classifier(
    relevance: RelevanceResult,
    *,
    is_relevant: bool,
    reason: str,
    provider: str | None = None,
    model: str | None = None,
    latency_ms: float | None = None,
    status: str = "completed",
) -> RelevanceResult:
    combined_reason = relevance.reason
    if status != "completed" or not is_relevant:
        combined_reason = f"{relevance.reason} Micro-classifier: {reason}"
    return RelevanceResult(
        path=relevance.path,
        artifact_type=relevance.artifact_type,
        reason=combined_reason,
        context_mode=relevance.context_mode,
        heuristic_score=relevance.heuristic_score,
        confidence_tier=relevance.confidence_tier,
        matched_signals=list(relevance.matched_signals),
        micro_classifier=MicroClassifierResult(
            is_relevant=is_relevant,
            reason=reason,
            status=status,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
        ),
    )


def get_relevance_results(diff_text: str) -> List[RelevanceResult]:
    changed_files = extract_changed_files(diff_text)
    return [classify_changed_file(item) for item in changed_files]


def _micro_classifier_prompt(changed_file: ChangedFile, relevance: RelevanceResult) -> str:
    signal_summary = ", ".join(
        f"{signal.source}:{signal.artifact_type}:{signal.weight}:{signal.reason}" for signal in relevance.matched_signals
    ) or "none"
    raw_diff = changed_file.raw_diff.strip()
    if len(raw_diff) > MICRO_CLASSIFIER_DIFF_CHAR_LIMIT:
        raw_diff = raw_diff[:MICRO_CLASSIFIER_DIFF_CHAR_LIMIT] + "\n..."
    return (
        "Classify whether this changed file belongs to the repository's AI control surface. "
        "Relevant changes include prompts, system instructions, guardrails, policy artifacts, model or retrieval config, AI tool wiring, and code that directly configures or routes model behavior. "
        "Return strict JSON with keys is_relevant (boolean) and reason (string).\n\n"
        f"Path: {changed_file.path}\n"
        f"Heuristic score: {relevance.heuristic_score}\n"
        f"Heuristic tier: {relevance.confidence_tier.value}\n"
        f"Signals: {signal_summary}\n"
        f"Diff:\n{raw_diff or '(empty diff)'}"
    )


def _call_micro_classifier(
    changed_file: ChangedFile,
    relevance: RelevanceResult,
    *,
    llm_client: object | None,
    model: str | None,
    timeout_seconds: float,
    provider: str | None,
) -> RelevanceResult:
    if llm_client is None or not model:
        return resolve_relevance_with_micro_classifier(
            relevance,
            is_relevant=True,
            reason="Micro-classifier unavailable; conservative fallback queued this artifact for audit.",
            provider=provider,
            model=model,
            status="unavailable",
        )

    started_at = time.perf_counter()
    try:
        response = llm_client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify repository diffs for AI control-surface relevance. "
                        "Return only JSON with keys is_relevant and reason."
                    ),
                },
                {"role": "user", "content": _micro_classifier_prompt(changed_file, relevance)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            timeout=timeout_seconds,
        )
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        payload = json.loads(response.choices[0].message.content or "{}")
        return resolve_relevance_with_micro_classifier(
            relevance,
            is_relevant=bool(payload.get("is_relevant")),
            reason=str(payload.get("reason") or "Micro-classifier returned no rationale.").strip(),
            provider=provider,
            model=model,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        return resolve_relevance_with_micro_classifier(
            relevance,
            is_relevant=True,
            reason=f"Micro-classifier failed; conservative fallback queued this artifact for audit ({type(exc).__name__}).",
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            status="error",
        )


def evaluate_diff_for_audit(
    diff_text: str,
    *,
    llm_client: object | None = None,
    model: str | None = None,
    timeout_seconds: float = 5.0,
    provider: str | None = None,
) -> AuditDecision:
    relevant_results: List[RelevanceResult] = []
    skipped_results: List[RelevanceResult] = []
    for changed_file in extract_changed_files(diff_text):
        relevance = classify_changed_file(changed_file)
        if relevance.confidence_tier == RelevanceConfidenceTier.CLEAR_NO:
            skipped_results.append(relevance)
            continue
        if relevance.confidence_tier == RelevanceConfidenceTier.UNCERTAIN:
            relevance = _call_micro_classifier(
                changed_file,
                relevance,
                llm_client=llm_client,
                model=model,
                timeout_seconds=timeout_seconds,
                provider=provider,
            )
        if relevance.ai_relevant:
            relevant_results.append(relevance)
        else:
            skipped_results.append(relevance)
    return AuditDecision(
        should_audit=bool(relevant_results),
        relevant_results=relevant_results,
        skipped_results=skipped_results,
    )


def classify_changed_file(changed_file: ChangedFile) -> RelevanceResult:
    path = changed_file.path.lower()
    signals: List[RelevanceSignal] = []
    for keywords, artifact_type, reason, weight in PATH_RULES:
        if any(keyword in path for keyword in keywords):
            signals.append(
                RelevanceSignal(
                    source="path",
                    label="/".join(keywords),
                    weight=weight,
                    artifact_type=artifact_type,
                    reason=reason,
                    matched_value=changed_file.path,
                )
            )

    content = changed_file.raw_diff.lower()
    for keywords, artifact_type, reason, weight in CONTENT_RULES:
        if any(keyword in content for keyword in keywords):
            signals.append(
                RelevanceSignal(
                    source="content",
                    label="/".join(keywords),
                    weight=weight,
                    artifact_type=artifact_type,
                    reason=reason,
                    matched_value=changed_file.path,
                )
            )
    return _build_relevance_result(changed_file, signals)


def get_ai_relevance_results(diff_text: str) -> List[RelevanceResult]:
    return [result for result in get_relevance_results(diff_text) if result.ai_relevant]


def needs_audit(diff_text: str) -> bool:
    return bool(get_ai_relevance_results(diff_text))
