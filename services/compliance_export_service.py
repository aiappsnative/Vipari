from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass
from typing import Literal

from .audit_records import (
    FindingRecord,
    PullRequestAuditRecord,
    list_findings_for_audit,
    list_pull_request_audits_for_repo,
)
from .onboarding_records import (
    BaselineAuditLogRecord,
    OnboardingBaselineVersionRecord,
    RepositoryOnboardingRecord,
    get_latest_repository_onboarding,
    list_baseline_audit_log_for_onboarding,
    list_latest_approved_onboarding_baseline_versions_for_onboarding,
)
from .persistence import connect_sqlite
from .repo_journey_records import (
    RepoPostureSnapshotRecord,
    list_repo_posture_snapshots_for_repo,
)


ExportMode = Literal["compliance", "compliance_plus_drift"]


@dataclass(frozen=True)
class ComplianceExportRequest:
    repo_full: str
    from_ts: float
    to_ts: float
    export_mode: ExportMode
    include_artifact_content: bool
    export_version: str = "1"


@dataclass(frozen=True)
class ComplianceExportResult:
    zip_bytes: bytes
    manifest: dict
    file_count: int
    total_size_bytes: int


def build_compliance_export(
    db_path: str,
    request: ComplianceExportRequest,
) -> ComplianceExportResult:
    # Gather data
    onboarding = get_latest_repository_onboarding(db_path, request.repo_full)
    if not onboarding:
        raise ValueError(f"No onboarding found for repo {request.repo_full}")

    baseline_versions = list_latest_approved_onboarding_baseline_versions_for_onboarding(
        db_path, onboarding.id
    )
    baseline_audit_log = list_baseline_audit_log_for_onboarding(db_path, onboarding.id)
    pr_audits = list_pull_request_audits_for_repo(db_path, request.repo_full)
    # Filter by date range
    pr_audits = [a for a in pr_audits if request.from_ts <= a.created_at <= request.to_ts]
    posture_snapshots = list_repo_posture_snapshots_for_repo(db_path, request.repo_full)
    posture_snapshots = [s for s in posture_snapshots if request.from_ts <= s.created_at <= request.to_ts]

    # Gather findings
    findings = []
    for audit in pr_audits:
        audit_findings = list_findings_for_audit(db_path, audit.id)
        for finding in audit_findings:
            findings.append((finding, audit.pr_number))

    # Build files
    files = {}
    files.update(_build_core_compliance_files(
        request, baseline_versions, baseline_audit_log, pr_audits, findings, posture_snapshots
    ))

    if request.export_mode == "compliance_plus_drift":
        files.update(_build_drift_files(request, posture_snapshots))

    # Build README and manifest
    readme_content = _build_readme(request.export_mode)
    files["README.txt"] = readme_content

    manifest = _build_manifest(files, request)
    manifest_json = json.dumps(manifest, indent=2)
    files["manifest.json"] = manifest_json

    # Update manifest with its own hash
    manifest["file_hashes"]["manifest.json"] = hashlib.sha256(manifest_json.encode()).hexdigest()
    manifest_json = json.dumps(manifest, indent=2)
    files["manifest.json"] = manifest_json

    # Build ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for filename, content in files.items():
            zip_file.writestr(filename, content)

    zip_bytes = zip_buffer.getvalue()

    return ComplianceExportResult(
        zip_bytes=zip_bytes,
        manifest=manifest,
        file_count=len(files),
        total_size_bytes=len(zip_bytes),
    )


def _build_core_compliance_files(
    request: ComplianceExportRequest,
    baseline_versions: list[OnboardingBaselineVersionRecord],
    baseline_audit_log: list[BaselineAuditLogRecord],
    pr_audits: list[PullRequestAuditRecord],
    findings: list[tuple[FindingRecord, int]],  # finding, pr_number
    posture_snapshots: list[RepoPostureSnapshotRecord],
) -> dict[str, str]:
    files = {}

    # 01-baseline-registry.csv
    files["01-baseline-registry.csv"] = _build_baseline_registry_csv(baseline_versions)

    # 02-baseline-audit-log.csv
    files["02-baseline-audit-log.csv"] = _build_baseline_audit_log_csv(baseline_audit_log)

    # 03-version-history.csv
    files["03-version-history.csv"] = _build_version_history_csv(posture_snapshots)

    # 04-pr-scan-history.csv
    files["04-pr-scan-history.csv"] = _build_pr_scan_history_csv(pr_audits)

    # 05-findings.csv
    files["05-findings.csv"] = _build_findings_csv(findings)

    # 06-risk-events.csv
    files["06-risk-events.csv"] = _build_risk_events_csv(posture_snapshots)

    # 08-control-mapping.md
    files["08-control-mapping.md"] = _build_control_mapping_md(request.export_mode)

    return files


def _build_drift_files(
    request: ComplianceExportRequest,
    posture_snapshots: list[RepoPostureSnapshotRecord],
) -> dict[str, str]:
    files = {}

    # 07-drift/repo-posture-snapshots.json
    files["07-drift/repo-posture-snapshots.json"] = json.dumps(
        [asdict(s) for s in posture_snapshots], indent=2
    )

    # 07-drift/artifact-drift-history.csv
    files["07-drift/artifact-drift-history.csv"] = _build_artifact_drift_history_csv(request)

    # 07-drift/drift-leaderboard.csv
    files["07-drift/drift-leaderboard.csv"] = _build_drift_leaderboard_csv(request)

    # 07-drift/posture-summary.json
    summary = _build_posture_summary(posture_snapshots)
    files["07-drift/posture-summary.json"] = json.dumps(summary, indent=2)

    return files


def _build_artifact_drift_history_csv(request: ComplianceExportRequest) -> str:
    # Placeholder - would need to query artifact_versions and profiles
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "artifact_path", "artifact_type", "audit_id", "pr_number", "head_sha", "created_at", "version_hash", "semantic_distance", "guardrail_robustness_delta", "capability_risk_delta", "autonomy_level_delta", "governance_strength_delta", "stability_vs_creativity_delta", "semantic_density_delta", "narrative"
    ])
    writer.writeheader()
    # For now, empty
    return output.getvalue()


def _build_drift_leaderboard_csv(request: ComplianceExportRequest) -> str:
    # Placeholder
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "artifact_path", "artifact_type", "sample_count", "latest_created_at", "semantic_distance", "guardrail_shift", "capability_shift", "autonomy_shift", "drift_magnitude", "narrative"
    ])
    writer.writeheader()
    return output.getvalue()


def _build_posture_summary(posture_snapshots: list[RepoPostureSnapshotRecord]) -> dict:
    if not posture_snapshots:
        return {
            "artifact_count": 0,
            "profile_count": 0,
            "baseline_linked_profile_count": 0,
            "avg_semantic_distance": 0.0,
            "avg_guardrail_shift": 0.0,
            "avg_capability_shift": 0.0,
            "highest_capability_artifact_path": "",
            "highest_capability_delta": 0.0,
        }

    # Simple aggregation
    semantic_distances = [s.distance_from_baseline for s in posture_snapshots if s.distance_from_baseline is not None]
    avg_semantic_distance = sum(semantic_distances) / len(semantic_distances) if semantic_distances else 0.0

    return {
        "artifact_count": len(set(s.repo_full for s in posture_snapshots)),  # placeholder
        "profile_count": len(posture_snapshots),
        "baseline_linked_profile_count": sum(1 for s in posture_snapshots if s.baseline_reference),
        "avg_semantic_distance": avg_semantic_distance,
        "avg_guardrail_shift": 0.0,  # placeholder
        "avg_capability_shift": 0.0,  # placeholder
        "highest_capability_artifact_path": "",
        "highest_capability_delta": 0.0,
    }


def _build_baseline_registry_csv(baseline_versions: list[OnboardingBaselineVersionRecord]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "artifact_path", "artifact_type", "version_hash", "approved_by", "approved_at", "approval_note", "approval_source", "approval_status"
    ])
    writer.writeheader()
    for bv in baseline_versions:
        writer.writerow({
            "artifact_path": bv.artifact_path,
            "artifact_type": bv.artifact_type,
            "version_hash": bv.version_hash,
            "approved_by": bv.approved_by,
            "approved_at": _ts_to_iso(bv.approved_at) if bv.approved_at else "",
            "approval_note": bv.approval_note or "",
            "approval_source": bv.approval_source,
            "approval_status": bv.approval_status,
        })
    return output.getvalue()


def _build_baseline_audit_log_csv(baseline_audit_log: list[BaselineAuditLogRecord]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "actor", "action", "artifact_path", "artifact_type", "timestamp", "rationale"
    ])
    writer.writeheader()
    for log in baseline_audit_log:
        writer.writerow({
            "actor": log.actor_login or "",
            "action": log.action,
            "artifact_path": log.artifact_path or "",
            "artifact_type": "",  # placeholder, may need to derive
            "timestamp": _ts_to_iso(log.created_at),
            "rationale": log.note or "",
        })
    return output.getvalue()


def _build_version_history_csv(posture_snapshots: list[RepoPostureSnapshotRecord]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "snapshot_key", "repo_full", "commit_sha", "pr_number", "author", "created_at", "snapshot_type", "baseline_reference", "source_ref", "source_url", "distance_from_baseline", "distance_from_previous", "high-level_risk_status", "change_labels"
    ])
    writer.writeheader()
    for s in posture_snapshots:
        writer.writerow({
            "snapshot_key": s.snapshot_key,
            "repo_full": s.repo_full,
            "commit_sha": s.commit_sha,
            "pr_number": s.pr_number or "",
            "author": s.author,
            "created_at": _ts_to_iso(s.created_at),
            "snapshot_type": s.snapshot_type,
            "baseline_reference": s.baseline_reference or "",
            "source_ref": s.source_ref,
            "source_url": s.source_url or "",
            "distance_from_baseline": s.distance_from_baseline,
            "distance_from_previous": s.distance_from_previous,
            "high-level_risk_status": s.risk_summary.get('level', '') if s.risk_summary else "",
            "change_labels": json.dumps(s.change_labels) if s.change_labels else "",
        })
    return output.getvalue()


def _build_pr_scan_history_csv(pr_audits: list[PullRequestAuditRecord]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "pr_number", "head_sha", "pr_state", "pr_merged", "pr_merged_at", "status", "completion_mode", "deterministic_score", "suggested_risk_level", "semantic_review_completed", "error_message", "created_at", "updated_at"
    ])
    writer.writeheader()
    for a in pr_audits:
        writer.writerow({
            "pr_number": a.pr_number,
            "head_sha": a.head_sha,
            "pr_state": a.pr_state,
            "pr_merged": a.pr_merged,
            "pr_merged_at": _ts_to_iso(a.pr_merged_at) if a.pr_merged_at else "",
            "status": a.status,
            "completion_mode": a.completion_mode,
            "deterministic_score": a.deterministic_score,
            "suggested_risk_level": a.suggested_risk_level,
            "semantic_review_completed": a.semantic_review_completed,
            "error_message": a.error_message or "",
            "created_at": _ts_to_iso(a.created_at),
            "updated_at": _ts_to_iso(a.updated_at),
        })
    return output.getvalue()


def _build_findings_csv(findings: list[tuple[FindingRecord, int]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "audit_id", "pr_number", "artifact_path", "source", "rule_id", "title", "severity", "rationale", "created_at"
    ])
    writer.writeheader()
    for finding, pr_number in findings:
        writer.writerow({
            "audit_id": finding.audit_id,
            "pr_number": pr_number,
            "artifact_path": "",  # placeholder
            "source": finding.source,
            "rule_id": finding.rule_id,
            "title": finding.title,
            "severity": finding.severity,
            "rationale": finding.rationale,
            "created_at": _ts_to_iso(finding.created_at),
        })
    return output.getvalue()


def _build_risk_events_csv(posture_snapshots: list[RepoPostureSnapshotRecord]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "snapshot_key", "commit_sha", "pr_number", "artifact", "created_at", "risk_level", "reason", "baseline_reference", "source_url"
    ])
    writer.writeheader()
    for s in posture_snapshots:
        if s.risk_summary and s.risk_summary.get('level') in ['high', 'critical']:
            writer.writerow({
                "snapshot_key": s.snapshot_key,
                "commit_sha": s.commit_sha,
                "pr_number": s.pr_number or "",
                "artifact": "",  # placeholder
                "created_at": _ts_to_iso(s.created_at),
                "risk_level": s.risk_summary['level'],
                "reason": s.risk_summary.get('reason', ''),
                "baseline_reference": s.baseline_reference or "",
                "source_url": s.source_url or "",
            })
    return output.getvalue()


def _build_readme(export_mode: ExportMode) -> str:
    return f"""# PromptDrift Compliance Export

This export contains evidence for SOC 2 and ISO 27001 readiness from PromptDrift monitoring.

Export Mode: {export_mode}
Generated: {_ts_to_iso(time.time())}

## Contents

- 01-baseline-registry.csv: Approved baseline inventory
- 02-baseline-audit-log.csv: Chain of custody and baseline decisions
- 03-version-history.csv: High-level version / posture timeline
- 04-pr-scan-history.csv: Proof that monitoring and review happened
- 05-findings.csv: Actionable issues surfaced by analysis
- 06-risk-events.csv: Auditor shortlist of policy-significant events
- 08-control-mapping.md: Mapping to SOC 2 / ISO controls

{'- 07-drift/: PromptDrift-specific drift analysis' if export_mode == 'compliance_plus_drift' else ''}

## Timestamps

All timestamps are in ISO 8601 UTC format.

## Interpreting the Data

[Placeholder for detailed explanation]
"""


def _build_control_mapping_md(export_mode: ExportMode) -> str:
    return """# Control Mapping

This document maps the contents of this export to specific SOC 2 and ISO 27001 controls.

## SOC 2 CC6.1 - Logical and Physical Access Controls

- 01-baseline-registry.csv demonstrates approved baselines
- 02-baseline-audit-log.csv shows approval decisions

## SOC 2 CC7.2 - System Operations

- 04-pr-scan-history.csv proves monitoring occurred
- 05-findings.csv shows issues were detected

## SOC 2 CC8.1 - Change Management

- 03-version-history.csv tracks changes over time
- 06-risk-events.csv highlights significant changes

## ISO 27001 A.5.36 - Compliance with Legal and Contractual Requirements

- All files demonstrate compliance monitoring

## ISO 27001 A.8.9 - Information Access Restriction

- Baseline approvals restrict access to approved versions

## ISO 27001 A.8.32 - Information Classification

- Findings and risk events classify information risks
"""


def _build_manifest(files: dict[str, str], request: ComplianceExportRequest) -> dict:
    manifest = {
        "export_mode": request.export_mode,
        "repo_full": request.repo_full,
        "date_range": {
            "from": _ts_to_iso(request.from_ts),
            "to": _ts_to_iso(request.to_ts),
        },
        "generated_at": _ts_to_iso(time.time()),
        "export_version": request.export_version,
        "included_files": list(files.keys()),
        "file_hashes": {},
    }
    for filename, content in files.items():
        manifest["file_hashes"][filename] = hashlib.sha256(content.encode()).hexdigest()
    return manifest


def _ts_to_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))