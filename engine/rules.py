from __future__ import annotations

from typing import Iterable, List

from .models import FindingSeverity, RuleFinding, StructuredChange


SENSITIVE_DATA_TERMS = (
    "credit score",
    "customer data",
    "financial",
    "ssn",
    "social security",
    "bank account",
    "internal policy",
    "internal policies",
)

GUARDRAIL_TERMS = (
    "do not",
    "never",
    "refuse",
    "must not",
    "should not",
    "cannot",
)

MODEL_CONFIG_TERMS = (
    "model",
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
)

CAPABILITY_EXPANSION_TERMS = (
    "comply",
    "override",
    "ignore prior",
    "share internal",
    "reveal",
    "execute",
    "tool",
    "function calling",
)

RETRIEVAL_TERMS = (
    "retrieval",
    "knowledge base",
    "rag",
    "search index",
)


def _contains_any(texts: Iterable[str], terms: Iterable[str]) -> bool:
    lowered = "\n".join(texts).lower()
    return any(term in lowered for term in terms)


def _matching_terms(texts: Iterable[str], terms: Iterable[str]) -> set[str]:
    lowered = "\n".join(texts).lower()
    return {term for term in terms if term in lowered}


def evaluate_structured_change(change: StructuredChange) -> List[RuleFinding]:
    findings: List[RuleFinding] = []

    newly_added_sensitive_terms = _matching_terms(change.added_lines, SENSITIVE_DATA_TERMS) - _matching_terms(change.removed_lines, SENSITIVE_DATA_TERMS)
    newly_added_capability_terms = _matching_terms(change.added_lines, CAPABILITY_EXPANSION_TERMS) - _matching_terms(change.removed_lines, CAPABILITY_EXPANSION_TERMS)
    newly_added_guardrail_weakening_terms = _matching_terms(change.added_lines, ("reveal", "share internal", "ignore", "bypass", "comply")) - _matching_terms(
        change.removed_lines, ("reveal", "share internal", "ignore", "bypass", "comply")
    )

    if newly_added_sensitive_terms:
        findings.append(
            RuleFinding(
                rule_id="sensitive_data_drift",
                title="Sensitive data or internal policy access added",
                severity=FindingSeverity.HIGH,
                rationale="Added lines mention customer-sensitive data access or internal policy disclosure.",
                evidence=change.added_lines[:3],
            )
        )

    if change.artifact_type in {"prompt", "system_prompt", "policy"} and newly_added_capability_terms:
        findings.append(
            RuleFinding(
                rule_id="capability_drift",
                title="Potential capability or authority expansion detected",
                severity=FindingSeverity.MEDIUM,
                rationale="Added lines suggest broader authority, compliance, or disclosure behavior in an AI instruction artifact.",
                evidence=change.added_lines[:3],
            )
        )

    if _contains_any(change.removed_lines, GUARDRAIL_TERMS):
        findings.append(
            RuleFinding(
                rule_id="guardrail_drift",
                title="Potential guardrail removal detected",
                severity=FindingSeverity.HIGH,
                rationale="Removed lines contain refusal or restrictive guardrail language.",
                evidence=change.removed_lines[:3],
            )
        )

    if change.artifact_type in {"guardrail", "policy", "system_prompt", "prompt"} and newly_added_guardrail_weakening_terms:
        findings.append(
            RuleFinding(
                rule_id="guardrail_weakening",
                title="Guardrail wording may have been weakened",
                severity=FindingSeverity.MEDIUM,
                rationale="Added lines in a guardrail artifact introduce potentially permissive or bypass language.",
                evidence=change.added_lines[:3],
            )
        )

    if change.artifact_type == "model_config" and (
        _contains_any(change.added_lines, MODEL_CONFIG_TERMS) or _contains_any(change.removed_lines, MODEL_CONFIG_TERMS)
    ):
        findings.append(
            RuleFinding(
                rule_id="model_drift",
                title="Model configuration changed",
                severity=FindingSeverity.MEDIUM,
                rationale="Model-related settings changed and should be reviewed for behavioral impact.",
                evidence=(change.removed_lines + change.added_lines)[:4],
            )
        )

    if change.artifact_type == "tooling" and _contains_any(change.added_lines, CAPABILITY_EXPANSION_TERMS):
        findings.append(
            RuleFinding(
                rule_id="tooling_drift",
                title="Tooling or execution scope changed",
                severity=FindingSeverity.MEDIUM,
                rationale="Tool-related configuration now appears more permissive or execution-capable.",
                evidence=change.added_lines[:3],
            )
        )

    if change.artifact_type == "retrieval" and (
        _contains_any(change.added_lines, RETRIEVAL_TERMS) or _contains_any(change.removed_lines, RETRIEVAL_TERMS)
    ):
        findings.append(
            RuleFinding(
                rule_id="retrieval_drift",
                title="Retrieval or knowledge-source configuration changed",
                severity=FindingSeverity.MEDIUM,
                rationale="Retrieval-related configuration changed and may alter available model context or data access.",
                evidence=(change.removed_lines + change.added_lines)[:4],
            )
        )

    return findings
