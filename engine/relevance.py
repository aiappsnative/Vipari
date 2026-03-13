from __future__ import annotations

from typing import List

from .context_selector import apply_context_mode
from .diff_parser import extract_changed_files
from .models import ChangedFile, RelevanceResult, SemanticContextMode


PATH_RULES = [
    (("prompt", "prompts"), "prompt", "Path indicates a prompt artifact."),
    (("system",), "system_prompt", "Path indicates a system instruction artifact."),
    (("policy",), "policy", "Path indicates a policy artifact."),
    (("guardrail", "safety"), "guardrail", "Path indicates a guardrail or safety artifact."),
    (("model",), "model_config", "Path indicates a model configuration artifact."),
    (("tool",), "tooling", "Path indicates a tool configuration artifact."),
    (("rag", "retriev"), "retrieval", "Path indicates a retrieval-related artifact."),
    (("ai", "llm", "assistant"), "ai_code", "Path indicates AI-related code or assets."),
]

CONTENT_RULES = [
    (("system prompt", "assistant behavior"), "system_prompt", "Content indicates a system prompt artifact."),
    (("refuse", "do not reveal", "safety"), "guardrail", "Content indicates safety or guardrail instructions."),
    (("temperature", "top_p", "model=", 'model:'), "model_config", "Content indicates model configuration."),
    (("tool", "function calling", "function_call"), "tooling", "Content indicates tool usage or configuration."),
    (("retrieval", "knowledge base", "rag"), "retrieval", "Content indicates retrieval configuration."),
]


def classify_changed_file(changed_file: ChangedFile) -> RelevanceResult:
    path = changed_file.path.lower()
    for keywords, artifact_type, reason in PATH_RULES:
        if any(keyword in path for keyword in keywords):
            return apply_context_mode(
                RelevanceResult(
                    path=changed_file.path,
                    ai_relevant=True,
                    artifact_type=artifact_type,
                    reason=reason,
                    context_mode=SemanticContextMode.DIFF_ONLY,
                )
            )

    content = changed_file.raw_diff.lower()
    for keywords, artifact_type, reason in CONTENT_RULES:
        if any(keyword in content for keyword in keywords):
            return apply_context_mode(
                RelevanceResult(
                    path=changed_file.path,
                    ai_relevant=True,
                    artifact_type=artifact_type,
                    reason=reason,
                    context_mode=SemanticContextMode.DIFF_ONLY,
                )
            )

    return apply_context_mode(
        RelevanceResult(
            path=changed_file.path,
            ai_relevant=False,
            artifact_type="generic",
            reason="No AI-specific path or content signal matched.",
            context_mode=SemanticContextMode.DIFF_ONLY,
        )
    )


def get_ai_relevance_results(diff_text: str) -> List[RelevanceResult]:
    changed_files = extract_changed_files(diff_text)
    return [result for result in (classify_changed_file(item) for item in changed_files) if result.ai_relevant]


def needs_audit(diff_text: str) -> bool:
    return bool(get_ai_relevance_results(diff_text))
