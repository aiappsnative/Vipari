from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import tokenize

from .analysis_budget import consume_analysis_budget, estimate_feature_units, release_analysis_budget, reserve_analysis_budget
from .hybrid_analysis import HybridAnalysisPlan


_ANALYZER_PATTERNS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "prompt_policy_static_scan": (
        ("high", "internal_policy_disclosure", "reveal internal policy"),
        ("high", "instruction_override", "ignore previous instructions"),
        ("medium", "guardrail_bypass", "bypass safety"),
        ("medium", "guardrail_disable", "disable guardrails"),
    ),
    "config_contract_scan": (
        ("medium", "high_temperature", "temperature: 1.0"),
        ("high", "embedded_secret", "api_key:"),
        ("medium", "unsafe_mode", "allow_unsafe:"),
        ("medium", "unbounded_tool_choice", "tool_choice: required"),
    ),
    "tooling_surface_scan": (
        ("high", "subprocess_shell", "shell=True"),
        ("high", "dynamic_exec", "exec("),
        ("medium", "remote_post", "requests.post("),
        ("medium", "unsafe_subprocess", "subprocess.Popen("),
    ),
}

_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class HybridExecutionFinding:
    finding_key: str
    severity: str
    evidence: str


@dataclass(frozen=True)
class HybridExecutionResult:
    analyzer_key: str
    artifact_path: str
    artifact_type: str
    finding_count: int
    highest_severity: str | None
    findings: tuple[HybridExecutionFinding, ...]


@dataclass(frozen=True)
class HybridExecutionSummary:
    rollout_mode: str
    attempted: bool
    executed: bool
    reason: str
    executions: tuple[HybridExecutionResult, ...]

    @property
    def execution_count(self) -> int:
        return len(self.executions)


def _normalized_snapshot_for_scan(snapshot_text: str, artifact_path: str) -> str:
    if not artifact_path.lower().endswith(".py"):
        return snapshot_text

    try:
        filtered_tokens = [
            token_info
            for token_info in tokenize.generate_tokens(StringIO(snapshot_text).readline)
            if token_info.type not in {tokenize.COMMENT, tokenize.STRING}
        ]
        return tokenize.untokenize(filtered_tokens)
    except (tokenize.TokenError, IndentationError):
        return snapshot_text


def _scan_snapshot(analyzer_key: str, snapshot_text: str, artifact_path: str) -> tuple[HybridExecutionFinding, ...]:
    findings: list[HybridExecutionFinding] = []
    lowered_snapshot = _normalized_snapshot_for_scan(snapshot_text, artifact_path).lower()
    for severity, finding_key, needle in _ANALYZER_PATTERNS.get(analyzer_key, ()): 
        if needle in lowered_snapshot:
            findings.append(
                HybridExecutionFinding(
                    finding_key=finding_key,
                    severity=severity,
                    evidence=needle,
                )
            )
    return tuple(findings)


def _highest_severity(findings: tuple[HybridExecutionFinding, ...]) -> str | None:
    if not findings:
        return None
    return max(findings, key=lambda item: _SEVERITY_RANK.get(item.severity, 0)).severity


def execute_hybrid_analysis_plan(
    plan: HybridAnalysisPlan,
    *,
    artifact_snapshots: dict[str, str],
    db_path: str | None = None,
    workspace_id: int | None = None,
    audit_job_id: int | None = None,
    audit_job_attempt_count: int | None = None,
) -> HybridExecutionSummary:
    if not plan.should_run:
        return HybridExecutionSummary(
            rollout_mode=plan.rollout_mode,
            attempted=False,
            executed=False,
            reason=plan.reason,
            executions=(),
        )

    reservation_key = None
    if db_path and workspace_id is not None and audit_job_id is not None:
        attempt_count = max(1, int(audit_job_attempt_count or 1))
        budget = reserve_analysis_budget(
            db_path,
            workspace_id=workspace_id,
            feature_key="hybrid",
            reservation_key=f"audit-job:{audit_job_id}:attempt:{attempt_count}:hybrid-analysis",
            estimated_units=estimate_feature_units("hybrid", request_count=max(1, len(plan.requests))),
            audit_job_id=audit_job_id,
        )
        if not budget.allowed:
            return HybridExecutionSummary(
                rollout_mode=plan.rollout_mode,
                attempted=False,
                executed=False,
                reason=f"Hybrid static analysis execution skipped because {budget.reason}.",
                executions=(),
            )
        reservation_key = budget.reservation_key

    executions: list[HybridExecutionResult] = []
    skipped_paths: list[str] = []
    try:
        for request in plan.requests:
            snapshot_text = artifact_snapshots.get(request.artifact_path)
            if not snapshot_text:
                skipped_paths.append(request.artifact_path)
                continue
            findings = _scan_snapshot(request.analyzer_key, snapshot_text, request.artifact_path)
            executions.append(
                HybridExecutionResult(
                    analyzer_key=request.analyzer_key,
                    artifact_path=request.artifact_path,
                    artifact_type=request.artifact_type,
                    finding_count=len(findings),
                    highest_severity=_highest_severity(findings),
                    findings=findings,
                )
            )
    except Exception:
        release_analysis_budget(
            db_path or "",
            reservation_key=reservation_key,
            note="hybrid static analysis failed before completion",
        )
        raise

    if not executions:
        release_analysis_budget(
            db_path or "",
            reservation_key=reservation_key,
            note="hybrid static analysis skipped because no selected artifacts had snapshots",
        )
        reason = "Hybrid static analysis could not execute because no selected artifacts had available snapshots."
        if skipped_paths:
            reason = f"Hybrid static analysis skipped {len(skipped_paths)} artifact snapshots because content was unavailable."
        return HybridExecutionSummary(
            rollout_mode=plan.rollout_mode,
            attempted=True,
            executed=False,
            reason=reason,
            executions=(),
        )

    executed_label = "artifact" if len(executions) == 1 else "artifacts"
    if skipped_paths:
        reason = (
            f"Shadow-mode hybrid static analysis executed {len(executions)} {executed_label} "
            f"and skipped {len(skipped_paths)} unavailable snapshots."
        )
    else:
        reason = f"Shadow-mode hybrid static analysis executed {len(executions)} {executed_label}."
    consume_analysis_budget(
        db_path or "",
        reservation_key=reservation_key,
        consumed_units=estimate_feature_units("hybrid", request_count=max(1, len(executions))),
        note="hybrid static analysis completed",
    )
    return HybridExecutionSummary(
        rollout_mode=plan.rollout_mode,
        attempted=True,
        executed=True,
        reason=reason,
        executions=tuple(executions),
    )