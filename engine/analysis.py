from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .diff_parser import extract_changed_files, extract_structured_change
from .models import FindingSeverity, RelevanceResult, RiskLevel, RuleFinding, StructuredChange
from .relevance import classify_changed_file
from .rules import evaluate_structured_change


SEVERITY_SCORES = {
    FindingSeverity.LOW: 20,
    FindingSeverity.MEDIUM: 50,
    FindingSeverity.HIGH: 85,
}


@dataclass(frozen=True)
class ArtifactAnalysis:
    relevance: RelevanceResult
    change: StructuredChange
    findings: List[RuleFinding] = field(default_factory=list)


@dataclass(frozen=True)
class DiffAnalysis:
    artifacts: List[ArtifactAnalysis] = field(default_factory=list)

    @property
    def findings(self) -> List[RuleFinding]:
        output: List[RuleFinding] = []
        for artifact in self.artifacts:
            output.extend(artifact.findings)
        return output

    @property
    def has_relevant_changes(self) -> bool:
        return bool(self.artifacts)

    @property
    def deterministic_score(self) -> int:
        if not self.findings:
            return 0
        return max(SEVERITY_SCORES[finding.severity] for finding in self.findings)

    @property
    def suggested_risk_level(self) -> RiskLevel:
        score = self.deterministic_score
        if score >= 80:
            return RiskLevel.HIGH
        if score >= 40:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def format_for_prompt(self) -> str:
        if not self.artifacts:
            return "No AI-relevant artifacts detected by deterministic analysis."

        lines: List[str] = [
            "Deterministic pre-analysis:",
            f"- Deterministic score: {self.deterministic_score}",
            f"- Suggested risk floor: {self.suggested_risk_level.value}",
        ]
        for artifact in self.artifacts:
            lines.append(
                f"- {artifact.relevance.path} [{artifact.relevance.artifact_type}] via {artifact.relevance.context_mode.value}: {artifact.relevance.reason} Added={artifact.change.added_count} Removed={artifact.change.removed_count} Hunks={artifact.change.changed_hunks}"
            )
            if artifact.findings:
                for finding in artifact.findings:
                    evidence = "; ".join(finding.evidence[:2]) if finding.evidence else "no evidence excerpt"
                    lines.append(
                        f"  - {finding.severity.value}: {finding.title} ({finding.rule_id}) -> {finding.rationale} Evidence: {evidence}"
                    )
            else:
                lines.append("  - No deterministic rule findings yet.")
        return "\n".join(lines)


def analyze_diff(diff_text: str) -> DiffAnalysis:
    artifacts: List[ArtifactAnalysis] = []
    for changed_file in extract_changed_files(diff_text):
        relevance = classify_changed_file(changed_file)
        if not relevance.ai_relevant:
            continue
        structured_change = extract_structured_change(changed_file, relevance)
        findings = evaluate_structured_change(structured_change)
        artifacts.append(ArtifactAnalysis(relevance=relevance, change=structured_change, findings=findings))
    return DiffAnalysis(artifacts=artifacts)
