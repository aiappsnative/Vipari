from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import zipfile
from dataclasses import asdict, dataclass
from typing import Literal

from .audit_records import (
    ArtifactVersionRecord,
    ChangedArtifactRecord,
    FindingRecord,
    PullRequestAuditRecord,
    StaticArtifactProfileRecord,
    list_artifact_versions_for_repo_artifact,
    list_changed_artifacts_for_audit,
    list_findings_for_audit,
    list_pull_request_audits_for_repo,
    list_static_profiles_for_repo_artifact,
)
from .baseline_provenance import BASELINE_SOURCE_NONE
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

    audit_by_id = {audit.id: audit for audit in pr_audits}
    changed_artifacts: list[ChangedArtifactRecord] = []
    for audit in pr_audits:
        changed_artifacts.extend(list_changed_artifacts_for_audit(db_path, audit.id))
    changed_artifact_by_id = {artifact.id: artifact for artifact in changed_artifacts}

    baseline_artifact_types_by_version_id = {
        baseline.id: baseline.artifact_type for baseline in baseline_versions
    }
    baseline_artifact_types_by_path = {
        baseline.artifact_path: baseline.artifact_type for baseline in baseline_versions
    }

    artifact_versions: list[ArtifactVersionRecord] = []
    static_profiles: list[StaticArtifactProfileRecord] = []
    artifact_version_by_id: dict[int, ArtifactVersionRecord] = {}
    if request.export_mode == "compliance_plus_drift" or request.include_artifact_content:
        artifact_paths = sorted(
            {
                baseline.artifact_path for baseline in baseline_versions
            }
            | {
                artifact.artifact_path for artifact in changed_artifacts
            }
        )
        for artifact_path in artifact_paths:
            for version in list_artifact_versions_for_repo_artifact(db_path, request.repo_full, artifact_path):
                if request.from_ts <= version.created_at <= request.to_ts and version.audit_id in audit_by_id:
                    artifact_versions.append(version)
                    artifact_version_by_id[version.id] = version
            for profile in list_static_profiles_for_repo_artifact(db_path, request.repo_full, artifact_path):
                if request.from_ts <= profile.created_at <= request.to_ts and profile.audit_id in audit_by_id:
                    static_profiles.append(profile)

    # Gather findings
    findings = []
    for audit in pr_audits:
        audit_findings = list_findings_for_audit(db_path, audit.id)
        for finding in audit_findings:
            findings.append((finding, audit.pr_number))

    # Build files
    files = {}
    files.update(_build_core_compliance_files(
        request,
        baseline_versions,
        baseline_audit_log,
        pr_audits,
        findings,
        posture_snapshots,
        changed_artifact_by_id,
        baseline_artifact_types_by_version_id,
        baseline_artifact_types_by_path,
    ))

    if request.export_mode == "compliance_plus_drift":
        files.update(_build_drift_files(posture_snapshots, static_profiles, artifact_version_by_id, audit_by_id))

    if request.include_artifact_content:
        files["09-artifact-content.json"] = json.dumps(
            _build_artifact_content_payload(baseline_versions, artifact_versions, audit_by_id),
            indent=2,
        )

    # Build README and manifest
    readme_content = _build_readme(request.export_mode, request.include_artifact_content)
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
    changed_artifact_by_id: dict[int, ChangedArtifactRecord],
    baseline_artifact_types_by_version_id: dict[int, str],
    baseline_artifact_types_by_path: dict[str, str],
) -> dict[str, str]:
    files = {}

    # 01-baseline-registry.csv
    files["01-baseline-registry.csv"] = _build_baseline_registry_csv(baseline_versions)

    # 02-baseline-audit-log.csv
    files["02-baseline-audit-log.csv"] = _build_baseline_audit_log_csv(
        baseline_audit_log,
        baseline_artifact_types_by_version_id,
        baseline_artifact_types_by_path,
    )

    # 03-version-history.csv
    files["03-version-history.csv"] = _build_version_history_csv(posture_snapshots)

    # 04-pr-scan-history.csv
    files["04-pr-scan-history.csv"] = _build_pr_scan_history_csv(pr_audits)

    # 05-findings.csv
    files["05-findings.csv"] = _build_findings_csv(findings, changed_artifact_by_id)

    # 06-risk-events.csv
    files["06-risk-events.csv"] = _build_risk_events_csv(posture_snapshots)

    # 08-control-mapping.md
    files["08-control-mapping.md"] = _build_control_mapping_md(request.export_mode)

    return files


def _build_drift_files(
    posture_snapshots: list[RepoPostureSnapshotRecord],
    static_profiles: list[StaticArtifactProfileRecord],
    artifact_version_by_id: dict[int, ArtifactVersionRecord],
    audit_by_id: dict[int, PullRequestAuditRecord],
) -> dict[str, str]:
    files = {}

    # 07-drift/repo-posture-snapshots.json
    files["07-drift/repo-posture-snapshots.json"] = json.dumps(
        [asdict(s) for s in posture_snapshots], indent=2
    )

    # 07-drift/artifact-drift-history.csv
    files["07-drift/artifact-drift-history.csv"] = _build_artifact_drift_history_csv(
        static_profiles,
        artifact_version_by_id,
        audit_by_id,
    )

    # 07-drift/drift-leaderboard.csv
    files["07-drift/drift-leaderboard.csv"] = _build_drift_leaderboard_csv(static_profiles)

    # 07-drift/posture-summary.json
    summary = _build_posture_summary(static_profiles)
    files["07-drift/posture-summary.json"] = json.dumps(summary, indent=2)

    return files


def _build_artifact_drift_history_csv(
    static_profiles: list[StaticArtifactProfileRecord],
    artifact_version_by_id: dict[int, ArtifactVersionRecord],
    audit_by_id: dict[int, PullRequestAuditRecord],
) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "artifact_path", "artifact_type", "audit_id", "pr_number", "head_sha", "created_at", "version_hash", "semantic_distance", "guardrail_robustness_delta", "capability_risk_delta", "autonomy_level_delta", "governance_strength_delta", "stability_vs_creativity_delta", "semantic_density_delta", "narrative"
    ])
    writer.writeheader()
    for profile in sorted(static_profiles, key=lambda item: (item.created_at, item.id)):
        audit = audit_by_id.get(profile.audit_id)
        version = artifact_version_by_id.get(profile.artifact_version_id)
        writer.writerow({
            "artifact_path": profile.artifact_path,
            "artifact_type": profile.artifact_type,
            "audit_id": profile.audit_id,
            "pr_number": audit.pr_number if audit is not None else "",
            "head_sha": audit.head_sha if audit is not None else "",
            "created_at": _ts_to_iso(profile.created_at),
            "version_hash": version.version_hash if version is not None else "",
            "semantic_distance": profile.semantic_distance,
            "guardrail_robustness_delta": profile.attribute_deltas.get("guardrail_robustness", 0.0),
            "capability_risk_delta": profile.attribute_deltas.get("capability_risk", 0.0),
            "autonomy_level_delta": profile.attribute_deltas.get("autonomy_level", 0.0),
            "governance_strength_delta": profile.attribute_deltas.get("governance_strength", 0.0),
            "stability_vs_creativity_delta": profile.attribute_deltas.get("stability_vs_creativity", 0.0),
            "semantic_density_delta": profile.attribute_deltas.get("semantic_density", 0.0),
            "narrative": " | ".join(profile.narrative),
        })
    return output.getvalue()


def _build_drift_leaderboard_csv(static_profiles: list[StaticArtifactProfileRecord]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "artifact_path", "artifact_type", "sample_count", "latest_created_at", "semantic_distance", "guardrail_shift", "capability_shift", "autonomy_shift", "drift_magnitude", "narrative"
    ])
    writer.writeheader()
    grouped_profiles: dict[str, list[StaticArtifactProfileRecord]] = {}
    for profile in sorted(static_profiles, key=lambda item: (item.created_at, item.id)):
        grouped_profiles.setdefault(profile.artifact_path, []).append(profile)

    leaderboard_rows = []
    for artifact_path, profiles in grouped_profiles.items():
        latest = profiles[-1]
        guardrail_shift = round(float(latest.attribute_deltas.get("guardrail_robustness", 0.0)), 4)
        capability_shift = round(float(latest.attribute_deltas.get("capability_risk", 0.0)), 4)
        autonomy_shift = round(float(latest.attribute_deltas.get("autonomy_level", 0.0)), 4)
        semantic_distance = round(float(latest.semantic_distance), 4)
        leaderboard_rows.append({
            "artifact_path": artifact_path,
            "artifact_type": latest.artifact_type,
            "sample_count": len(profiles),
            "latest_created_at": _ts_to_iso(latest.created_at),
            "semantic_distance": semantic_distance,
            "guardrail_shift": guardrail_shift,
            "capability_shift": capability_shift,
            "autonomy_shift": autonomy_shift,
            "drift_magnitude": round(abs(guardrail_shift) + abs(capability_shift) + abs(autonomy_shift) + semantic_distance, 4),
            "narrative": " | ".join(latest.narrative),
        })

    leaderboard_rows.sort(key=lambda row: (-float(row["drift_magnitude"]), row["artifact_path"]))
    for row in leaderboard_rows:
        writer.writerow(row)
    return output.getvalue()


def _build_posture_summary(static_profiles: list[StaticArtifactProfileRecord]) -> dict:
    if not static_profiles:
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

    baseline_linked_profiles = [
        profile
        for profile in static_profiles
        if profile.baseline_provenance is not None
        and profile.baseline_provenance.source_type != BASELINE_SOURCE_NONE
    ]
    semantic_distances = [float(profile.semantic_distance) for profile in baseline_linked_profiles]
    avg_semantic_distance = round(sum(semantic_distances) / len(semantic_distances), 4) if semantic_distances else 0.0

    guardrail_shifts = [abs(float(profile.attribute_deltas.get("guardrail_robustness", 0.0))) for profile in baseline_linked_profiles]
    capability_shifts = [abs(float(profile.attribute_deltas.get("capability_risk", 0.0))) for profile in baseline_linked_profiles]
    highest_capability_profile = max(
        baseline_linked_profiles,
        key=lambda profile: float(profile.attribute_deltas.get("capability_risk", 0.0)),
        default=None,
    )

    return {
        "artifact_count": len({profile.artifact_path for profile in static_profiles}),
        "profile_count": len(static_profiles),
        "baseline_linked_profile_count": len(baseline_linked_profiles),
        "avg_semantic_distance": avg_semantic_distance,
        "avg_guardrail_shift": round(sum(guardrail_shifts) / len(guardrail_shifts), 4) if guardrail_shifts else 0.0,
        "avg_capability_shift": round(sum(capability_shifts) / len(capability_shifts), 4) if capability_shifts else 0.0,
        "highest_capability_artifact_path": highest_capability_profile.artifact_path if highest_capability_profile is not None else "",
        "highest_capability_delta": round(float(highest_capability_profile.attribute_deltas.get("capability_risk", 0.0)), 4) if highest_capability_profile is not None else 0.0,
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
            "approval_source": "repo_baseline_review" if bv.approval_status == "approved" else "baseline_candidate",
            "approval_status": bv.approval_status,
        })
    return output.getvalue()


def _build_baseline_audit_log_csv(
    baseline_audit_log: list[BaselineAuditLogRecord],
    baseline_artifact_types_by_version_id: dict[int, str],
    baseline_artifact_types_by_path: dict[str, str],
) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "actor", "action", "artifact_path", "artifact_type", "timestamp", "rationale"
    ])
    writer.writeheader()
    for log in baseline_audit_log:
        artifact_type = ""
        if log.baseline_version_id is not None:
            artifact_type = baseline_artifact_types_by_version_id.get(log.baseline_version_id, "")
        if not artifact_type and log.artifact_path:
            artifact_type = baseline_artifact_types_by_path.get(log.artifact_path, "")
        writer.writerow({
            "actor": log.actor_login or "",
            "action": log.action,
            "artifact_path": log.artifact_path or "",
            "artifact_type": artifact_type,
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
            "high-level_risk_status": _extract_risk_level(s),
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


def _build_findings_csv(
    findings: list[tuple[FindingRecord, int]],
    changed_artifact_by_id: dict[int, ChangedArtifactRecord],
) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "audit_id", "pr_number", "artifact_path", "source", "rule_id", "title", "severity", "rationale", "created_at"
    ])
    writer.writeheader()
    for finding, pr_number in findings:
        artifact_path = ""
        if finding.changed_artifact_id is not None:
            artifact = changed_artifact_by_id.get(finding.changed_artifact_id)
            if artifact is not None:
                artifact_path = artifact.artifact_path
        writer.writerow({
            "audit_id": finding.audit_id,
            "pr_number": pr_number,
            "artifact_path": artifact_path,
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
        risk_level = _extract_risk_level(s)
        if risk_level in ["high", "critical"]:
            writer.writerow({
                "snapshot_key": s.snapshot_key,
                "commit_sha": s.commit_sha,
                "pr_number": s.pr_number or "",
                "artifact": _extract_primary_artifact(s),
                "created_at": _ts_to_iso(s.created_at),
                "risk_level": risk_level,
                "reason": _extract_risk_reason(s),
                "baseline_reference": s.baseline_reference or "",
                "source_url": s.source_url or "",
            })
    return output.getvalue()


def _build_artifact_content_payload(
    baseline_versions: list[OnboardingBaselineVersionRecord],
    artifact_versions: list[ArtifactVersionRecord],
    audit_by_id: dict[int, PullRequestAuditRecord],
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for baseline in baseline_versions:
        if baseline.content_text is None:
            continue
        payload.append({
            "source_kind": "approved_baseline",
            "artifact_path": baseline.artifact_path,
            "artifact_type": baseline.artifact_type,
            "version_hash": baseline.version_hash,
            "approved_by": baseline.approved_by or "",
            "approved_at": _ts_to_iso(baseline.approved_at) if baseline.approved_at else "",
            "created_at": _ts_to_iso(baseline.created_at),
            "content_text": baseline.content_text,
        })
    for version in sorted(artifact_versions, key=lambda item: (item.created_at, item.id)):
        if version.content_text is None:
            continue
        audit = audit_by_id.get(version.audit_id)
        payload.append({
            "source_kind": "pr_scan",
            "artifact_path": version.artifact_path,
            "artifact_type": version.artifact_type,
            "version_hash": version.version_hash,
            "audit_id": version.audit_id,
            "pr_number": audit.pr_number if audit is not None else None,
            "head_sha": audit.head_sha if audit is not None else None,
            "created_at": _ts_to_iso(version.created_at),
            "content_text": version.content_text,
        })
    return payload


def _build_readme(export_mode: ExportMode, include_artifact_content: bool) -> str:
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
{'- 09-artifact-content.json: Raw approved baseline and scanned artifact content included by request' if include_artifact_content else ''}

## Timestamps

All timestamps are in ISO 8601 UTC format.

## Interpreting the Data

- Baseline files describe the currently approved artifact inventory and approval trail.
- Version and PR scan history show when PromptDrift evaluated repository changes during the requested window.
- Findings and risk events are derived from persisted scan and posture records; no synthetic placeholder rows are added.
- Drift files, when present, summarize recorded static-profile deltas for artifacts scanned during the requested window.
"""


def _extract_risk_level(snapshot: RepoPostureSnapshotRecord) -> str:
    if not snapshot.risk_summary:
        return ""
    return str(snapshot.risk_summary.get("risk_level") or snapshot.risk_summary.get("level") or "")


def _extract_primary_artifact(snapshot: RepoPostureSnapshotRecord) -> str:
    for key in ("changed_artifact_paths", "added_artifact_paths", "removed_artifact_paths"):
        values = snapshot.change_breakdown.get(key) if snapshot.change_breakdown else None
        if isinstance(values, list) and values:
            return str(values[0])
    tracked_paths = snapshot.artifact_coverage.get("tracked_paths") if snapshot.artifact_coverage else None
    if isinstance(tracked_paths, list) and tracked_paths:
        return str(tracked_paths[0])
    if snapshot.artifact_state:
        return sorted(snapshot.artifact_state)[0]
    return ""


def _extract_risk_reason(snapshot: RepoPostureSnapshotRecord) -> str:
    if snapshot.risk_summary and snapshot.risk_summary.get("reason"):
        return str(snapshot.risk_summary["reason"])

    parts: list[str] = []
    critical_surfaces = 0
    if snapshot.risk_summary:
        critical_surfaces = int(snapshot.risk_summary.get("critical_surfaces_changed") or 0)
    if not critical_surfaces and snapshot.change_breakdown:
        critical_surfaces = int(snapshot.change_breakdown.get("critical_surfaces_changed") or 0)
    if critical_surfaces:
        parts.append(f"{critical_surfaces} critical surfaces changed")
    if snapshot.change_labels:
        parts.append("labels: " + ", ".join(snapshot.change_labels))
    if snapshot.distance_from_baseline:
        parts.append(f"baseline distance {snapshot.distance_from_baseline:.4f}")
    if not parts:
        risk_level = _extract_risk_level(snapshot)
        if risk_level:
            return f"{risk_level} risk posture recorded"
        return ""
    return "; ".join(parts)


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