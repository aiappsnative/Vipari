from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


SECTION_SPLIT_RE = re.compile(r"\n\s*#{1,6}\s+|\n\s*[-*]\s+|\n\s*\d+\.\s+")
TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
EXAMPLE_RE = re.compile(r"\b(example|few-shot|input:|output:)\b", re.IGNORECASE)
LIMIT_RE = re.compile(
    r"\b(up to|at most|no more than|less than|more than|above|below|under|over|maximum|max(?:imum)?\s+of|limit(?:ed)?\s+to)\b",
    re.IGNORECASE,
)
WRITE_RE = re.compile(r"\b(write|delete|update|create|send|refund|approve|merge|execute|deploy|publish|modify)\b", re.IGNORECASE)
READ_RE = re.compile(r"\b(read|view|list|search|fetch|query|get)\b", re.IGNORECASE)
PROD_RE = re.compile(r"\b(prod|production|live environment)\b", re.IGNORECASE)
SANDBOX_RE = re.compile(r"\b(sandbox|staging|test environment|dry run)\b", re.IGNORECASE)
APPROVAL_RE = re.compile(r"\b(ask manager|await approval|requires approval|human review|escalate|confirm with user|confirm with manager|handoff)\b", re.IGNORECASE)
PARALLEL_RE = re.compile(r"\b(parallel|concurrent|multi-step|multi agent|planner|plan first)\b", re.IGNORECASE)
AMBIGUITY_RE = re.compile(r"\b(if appropriate|if needed|try your best|use judgment|generally|usually|as needed)\b", re.IGNORECASE)
TEMPERATURE_RE = re.compile(r"temperature\s*[:=]\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
TOP_P_RE = re.compile(r"top_p\s*[:=]\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
MAX_STEPS_RE = re.compile(r"(?:max(?:imum)?[_\s-]*steps?|steps?)\s*[:=]?\s*(\d+)", re.IGNORECASE)
SYSTEM_RE = re.compile(r"\b(system|instruction|policy|guardrail|tool|model|temperature|approval)\b", re.IGNORECASE)
SENSITIVE_TOOL_TERMS = (
    "refund",
    "payment",
    "billing",
    "secret",
    "credential",
    "token",
    "database",
    "sql",
    "customer data",
    "pii",
    "ssn",
    "payroll",
    "code execution",
    "shell",
)

RULE_BUCKET_PATTERNS = {
    "safety": re.compile(r"\b(safety|harm|unsafe|dangerous|refuse)\b", re.IGNORECASE),
    "privacy": re.compile(r"\b(privacy|pii|personal data|ssn|sensitive data|secret)\b", re.IGNORECASE),
    "compliance": re.compile(r"\b(compliance|policy|legal|regulation|audit trail)\b", re.IGNORECASE),
    "escalation": APPROVAL_RE,
    "audit": re.compile(r"\b(log|record this action|audit|traceability|trace)\b", re.IGNORECASE),
}

CONSTRAINT_MARKERS = (
    "must",
    "must not",
    "never",
    "always",
    "do not",
    "only",
    "required",
)

SYSTEMS = (
    "crm",
    "billing",
    "payment",
    "hr",
    "warehouse",
    "ticket",
    "slack",
    "github",
    "database",
    "salesforce",
    "zendesk",
    "jira",
)


@dataclass(frozen=True)
class GovernanceContext:
    codeowners_required: bool = False
    approved_reviewers: int = 0
    security_review_present: bool = False
    recent_changes_30d: int = 0


@dataclass(frozen=True)
class StaticSignals:
    token_count: int
    char_count: int
    section_count: int
    example_count: int
    instruction_density: float
    constraint_count: int
    explicit_limit_count: int
    ambiguity_count: int
    guardrail_counts: dict[str, int] = field(default_factory=dict)
    write_signal_count: int = 0
    read_signal_count: int = 0
    sensitive_tool_count: int = 0
    prod_signal_count: int = 0
    sandbox_signal_count: int = 0
    systems_touched_count: int = 0
    human_review_count: int = 0
    parallelism_signal_count: int = 0
    max_steps: int = 0
    temperature: float | None = None
    top_p: float | None = None


@dataclass(frozen=True)
class AgentAttributeProfile:
    guardrail_robustness: float
    capability_risk: float
    autonomy_level: float
    stability_vs_creativity: float
    governance_strength: float
    change_frequency: float
    semantic_density: float
    signals: StaticSignals


@dataclass(frozen=True)
class AgentDriftDelta:
    baseline: AgentAttributeProfile
    current: AgentAttributeProfile
    semantic_similarity: float
    semantic_distance: float
    attribute_deltas: dict[str, float]
    narrative: list[str]


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, round(value, 4)))


def _count_pattern(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text))


def _extract_optional_float(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    if not match:
        return None
    return float(match.group(1))


def _extract_optional_int(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    if not match:
        return 0
    return int(match.group(1))


def _unique_term_hits(terms: Iterable[str], text: str) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text) if len(token) > 2}


def lexical_similarity(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return round(len(left_tokens & right_tokens) / len(left_tokens | right_tokens), 4)


def extract_static_signals(text: str) -> StaticSignals:
    token_count = len(TOKEN_RE.findall(text))
    char_count = len(text)
    section_count = max(1, len([chunk for chunk in SECTION_SPLIT_RE.split(text) if chunk.strip()]))
    example_count = _count_pattern(EXAMPLE_RE, text)
    constraint_count = sum(text.lower().count(marker) for marker in CONSTRAINT_MARKERS)
    explicit_limit_count = _count_pattern(LIMIT_RE, text)
    ambiguity_count = _count_pattern(AMBIGUITY_RE, text)
    instruction_density = 0.0 if token_count == 0 else round(constraint_count / token_count, 4)
    guardrail_counts = {name: _count_pattern(pattern, text) for name, pattern in RULE_BUCKET_PATTERNS.items()}
    write_signal_count = _count_pattern(WRITE_RE, text)
    read_signal_count = _count_pattern(READ_RE, text)
    sensitive_tool_count = _unique_term_hits(SENSITIVE_TOOL_TERMS, text)
    prod_signal_count = _count_pattern(PROD_RE, text)
    sandbox_signal_count = _count_pattern(SANDBOX_RE, text)
    systems_touched_count = _unique_term_hits(SYSTEMS, text)
    human_review_count = _count_pattern(APPROVAL_RE, text)
    parallelism_signal_count = _count_pattern(PARALLEL_RE, text)
    max_steps = _extract_optional_int(MAX_STEPS_RE, text)
    temperature = _extract_optional_float(TEMPERATURE_RE, text)
    top_p = _extract_optional_float(TOP_P_RE, text)

    return StaticSignals(
        token_count=token_count,
        char_count=char_count,
        section_count=section_count,
        example_count=example_count,
        instruction_density=instruction_density,
        constraint_count=constraint_count,
        explicit_limit_count=explicit_limit_count,
        ambiguity_count=ambiguity_count,
        guardrail_counts=guardrail_counts,
        write_signal_count=write_signal_count,
        read_signal_count=read_signal_count,
        sensitive_tool_count=sensitive_tool_count,
        prod_signal_count=prod_signal_count,
        sandbox_signal_count=sandbox_signal_count,
        systems_touched_count=systems_touched_count,
        human_review_count=human_review_count,
        parallelism_signal_count=parallelism_signal_count,
        max_steps=max_steps,
        temperature=temperature,
        top_p=top_p,
    )


def build_attribute_profile(text: str, governance: GovernanceContext | None = None) -> AgentAttributeProfile:
    governance = governance or GovernanceContext()
    signals = extract_static_signals(text)

    guardrail_signal_total = sum(signals.guardrail_counts.values())
    guardrail_robustness = _clamp(
        0.2
        + (signals.constraint_count * 0.03)
        + (signals.explicit_limit_count * 0.06)
        + (guardrail_signal_total * 0.04)
        + (signals.example_count * 0.02)
        - (signals.ambiguity_count * 0.05)
    )

    constrained_writes = min(signals.explicit_limit_count, signals.write_signal_count)
    capability_risk = _clamp(
        0.02
        + (signals.write_signal_count * 0.04)
        + (signals.sensitive_tool_count * 0.1)
        + (signals.prod_signal_count * 0.12)
        + (signals.systems_touched_count * 0.04)
        - (signals.sandbox_signal_count * 0.08)
        - (constrained_writes * 0.07)
        - (signals.human_review_count * 0.08)
        - (guardrail_signal_total * 0.03)
    )

    max_steps_factor = min(signals.max_steps, 10) / 10 if signals.max_steps else 0.1
    autonomy_level = _clamp(
        0.1
        + (max_steps_factor * 0.35)
        + (signals.parallelism_signal_count * 0.08)
        + (signals.write_signal_count * 0.02)
        - (signals.human_review_count * 0.08)
    )

    temperature = 0.2 if signals.temperature is None else min(max(signals.temperature, 0.0), 1.0)
    top_p = 0.2 if signals.top_p is None else min(max(signals.top_p, 0.0), 1.0)
    stability_vs_creativity = _clamp(1 - ((temperature * 0.7) + (top_p * 0.3)))

    governance_strength = _clamp(
        0.2
        + (0.2 if governance.codeowners_required else 0.0)
        + min(governance.approved_reviewers, 3) * 0.12
        + (0.2 if governance.security_review_present else 0.0)
        - min(governance.recent_changes_30d, 10) * 0.03
    )
    change_frequency = _clamp(governance.recent_changes_30d / 10)
    semantic_density = _clamp(_count_pattern(SYSTEM_RE, text) / max(signals.token_count, 1) * 20)

    return AgentAttributeProfile(
        guardrail_robustness=guardrail_robustness,
        capability_risk=capability_risk,
        autonomy_level=autonomy_level,
        stability_vs_creativity=stability_vs_creativity,
        governance_strength=governance_strength,
        change_frequency=change_frequency,
        semantic_density=semantic_density,
        signals=signals,
    )


def compare_attribute_profiles(
    baseline: AgentAttributeProfile,
    current: AgentAttributeProfile,
    *,
    semantic_similarity: float,
) -> AgentDriftDelta:
    attribute_deltas = {
        "guardrail_robustness": round(current.guardrail_robustness - baseline.guardrail_robustness, 4),
        "capability_risk": round(current.capability_risk - baseline.capability_risk, 4),
        "autonomy_level": round(current.autonomy_level - baseline.autonomy_level, 4),
        "stability_vs_creativity": round(current.stability_vs_creativity - baseline.stability_vs_creativity, 4),
        "governance_strength": round(current.governance_strength - baseline.governance_strength, 4),
        "change_frequency": round(current.change_frequency - baseline.change_frequency, 4),
        "semantic_density": round(current.semantic_density - baseline.semantic_density, 4),
    }
    narrative: list[str] = []
    if attribute_deltas["guardrail_robustness"] < 0:
        narrative.append("Guardrails weakened relative to baseline.")
    elif attribute_deltas["guardrail_robustness"] > 0:
        narrative.append("Guardrails strengthened relative to baseline.")

    if attribute_deltas["capability_risk"] > 0:
        narrative.append("Capability risk increased due to broader or more sensitive actions.")
    if attribute_deltas["autonomy_level"] > 0:
        narrative.append("Autonomy increased through higher step depth or fewer review gates.")
    if attribute_deltas["governance_strength"] < 0:
        narrative.append("Governance weakened due to lighter review or higher churn.")
    if not narrative:
        narrative.append("Static attribute profile stayed broadly consistent with baseline.")

    return AgentDriftDelta(
        baseline=baseline,
        current=current,
        semantic_similarity=round(semantic_similarity, 4),
        semantic_distance=round(1 - semantic_similarity, 4),
        attribute_deltas=attribute_deltas,
        narrative=narrative,
    )


def compare_agent_versions(
    baseline_text: str,
    current_text: str,
    *,
    baseline_governance: GovernanceContext | None = None,
    current_governance: GovernanceContext | None = None,
) -> AgentDriftDelta:
    similarity = lexical_similarity(baseline_text, current_text)
    baseline = build_attribute_profile(baseline_text, governance=baseline_governance)
    current = build_attribute_profile(current_text, governance=current_governance)
    return compare_attribute_profiles(baseline, current, semantic_similarity=similarity)
