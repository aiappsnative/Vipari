from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .audit_records import list_pull_request_audits_for_repo, list_static_profiles_for_repo_artifact
from .baseline_provenance import BaselineProvenance, approved_onboarding_provenance
from .onboarding_records import (
    OnboardingBaselineVersionRecord,
    get_latest_repository_onboarding,
    list_effective_onboarding_baseline_versions_for_onboarding,
    list_historical_static_profiles_for_repo_artifact,
    list_latest_approved_onboarding_baseline_versions_for_onboarding,
    list_latest_onboarding_baseline_versions_for_onboarding,
    list_onboarded_artifacts_for_onboarding,
    list_onboarding_baseline_versions_for_onboarding,
)
from .persistence import connect_sqlite
from .repo_journey_records import (
    RepoPostureSnapshotRecord,
    delete_repo_posture_snapshots_not_in_keys,
    get_repo_posture_snapshot,
    get_repo_posture_snapshot_for_repo,
    init_repo_journey_db,
    list_repo_posture_snapshots_for_repo,
    upsert_repo_posture_snapshot,
)


REPO_JOURNEY_MATERIALIZER_VERSION = 2


def _repo_snapshot_key(repo_full: str, snapshot_key: str) -> str:
    return f"{repo_full}::{snapshot_key}"


@dataclass(frozen=True)
class RepoJourneyComparison:
    repo_full: str
    comparison_kind: str
    left: dict[str, Any]
    right: dict[str, Any]
    vector_delta: dict[str, float]
    change_breakdown: dict[str, Any]
    drift_summary: dict[str, Any]
    risk_summary: dict[str, Any]
    change_labels: list[str]


@dataclass(frozen=True)
class _ArtifactState:
    artifact_path: str
    artifact_type: str
    profile: dict[str, float]
    source_type: str
    source_ref: str | None
    source_url: str | None
    baseline_provenance: BaselineProvenance | None


@dataclass(frozen=True)
class _SnapshotEvent:
    snapshot_key: str
    snapshot_type: str
    created_at: float
    commit_sha: str | None
    pr_number: int | None
    author: str | None
    source_ref: str | None
    source_url: str | None
    artifact_path: str
    artifact_type: str
    profile: dict[str, float]
    baseline_provenance: BaselineProvenance | None
    branch_ref: str | None = None
    triggered_by: str | None = None


def materialize_repo_journey(db_path: str, repo_full: str) -> list[RepoPostureSnapshotRecord]:
    init_repo_journey_db(db_path)
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    if onboarding is None:
        delete_repo_posture_snapshots_not_in_keys(db_path, repo_full, set())
        return []

    onboarded_artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
    baseline_versions = list_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    latest_baseline_versions = list_latest_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    effective_baseline_versions = list_effective_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    latest_approved_baseline_versions = list_latest_approved_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
    baseline_by_path = {baseline.artifact_path: baseline for baseline in effective_baseline_versions}

    merged_audits = {
        audit.id: audit
        for audit in list_pull_request_audits_for_repo(db_path, repo_full)
        if audit.pr_merged and audit.status == "completed"
    }

    artifact_types_by_path = {artifact.artifact_path: artifact.artifact_type for artifact in onboarded_artifacts}
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ca.artifact_path, ca.artifact_type
            FROM changed_artifacts ca
            INNER JOIN pull_request_audits pra ON pra.id = ca.audit_id
            WHERE pra.repo_full = ?
            """,
            (repo_full,),
        ).fetchall()
    for row in rows:
        artifact_types_by_path.setdefault(row["artifact_path"], row["artifact_type"])

    baseline_state: dict[str, _ArtifactState] = {}
    for baseline in effective_baseline_versions:
        baseline_state[baseline.artifact_path] = _artifact_state_from_baseline(baseline)

    latest_paths = {artifact.artifact_path for artifact in onboarded_artifacts}
    approved_latest_count = len(latest_approved_baseline_versions)
    pending_latest_count = sum(1 for baseline in latest_baseline_versions if baseline.approval_status == "pending")
    rejected_latest_count = sum(1 for baseline in latest_baseline_versions if baseline.approval_status == "rejected")
    baseline_verified = bool(latest_paths) and approved_latest_count == len(latest_paths) and onboarding.status == "baseline_approved"
    tracked_count = len(latest_paths)
    # classify critical artifacts by artifact_type hints
    def _is_critical_type(artifact_type: str) -> bool:
        if artifact_type is None:
            return False
        lowered = artifact_type.lower()
        for hint in ("prompt", "policy", "guard", "model", "config"):
            if hint in lowered:
                return True
        return False
    critical_artifact_count = sum(1 for p, t in artifact_types_by_path.items() if _is_critical_type(t))
    approved_critical_count = sum(1 for b in latest_approved_baseline_versions if _is_critical_type(b.artifact_type))
    coverage_percent = round((approved_latest_count / tracked_count) * 100.0, 2) if tracked_count else 0.0
    critical_coverage_percent = round((approved_critical_count / critical_artifact_count) * 100.0, 2) if critical_artifact_count else 0.0

    snapshot_keys: set[str] = set()
    snapshots: list[RepoPostureSnapshotRecord] = []
    previous_snapshot: RepoPostureSnapshotRecord | None = None
    baseline_snapshot: RepoPostureSnapshotRecord | None = None
    latest_branch_head_snapshot: RepoPostureSnapshotRecord | None = None

    if baseline_state:
        baseline_snapshot = _persist_snapshot(
            db_path,
            repo_full=repo_full,
            snapshot_key=_repo_snapshot_key(repo_full, "baseline-approved"),
            snapshot_type="baseline_approved",
            created_at=0.0,
            commit_sha=None,
            pr_number=None,
            author=None,
            default_branch=onboarding.default_branch,
            source_ref=f"approved baseline @ {onboarding.default_branch}",
            source_url=None,
            artifact_state=baseline_state,
            previous_snapshot=None,
            baseline_snapshot=None,
                input_summary={
                    "baseline_artifact_count": len(baseline_versions),
                    "historical_event_count": 0,
                    "merged_event_count": 0,
                    "baseline_verified": baseline_verified,
                    "approved_baseline_count": approved_latest_count,
                    "pending_baseline_count": pending_latest_count,
                    "rejected_baseline_count": rejected_latest_count,
                    "approved_by": onboarding.approved_by,
                    "approved_at": onboarding.approved_at,
                    "tracked_count": tracked_count,
                    "coverage_percent": coverage_percent,
                    "critical_artifact_count": critical_artifact_count,
                    "approved_critical_count": approved_critical_count,
                    "critical_coverage_percent": critical_coverage_percent,
                    "drifting_artifact_count": 0,
                    "last_baseline_at": onboarding.approved_at,
                },
        )
        snapshot_keys.add(baseline_snapshot.snapshot_key)
        snapshots.append(baseline_snapshot)
        previous_snapshot = baseline_snapshot

    events_by_key: dict[str, dict[str, Any]] = {}
    for artifact_path, artifact_type in sorted(artifact_types_by_path.items()):
        for profile in list_historical_static_profiles_for_repo_artifact(db_path, repo_full, artifact_path):
            key = _repo_snapshot_key(repo_full, f"historical:{profile.commit_sha}")
            bucket = events_by_key.setdefault(
                key,
                {
                    "snapshot_key": key,
                    "snapshot_type": _historical_snapshot_type(profile, onboarding.default_branch),
                    "created_at": profile.created_at,
                    "commit_sha": profile.commit_sha,
                    "pr_number": None,
                    "author": None,
                    "source_ref": _historical_source_ref(profile, onboarding.default_branch),
                    "source_url": f"https://github.com/{repo_full}/commit/{profile.commit_sha}",
                    "events": [],
                    "historical_event_count": 0,
                    "merged_event_count": 0,
                },
            )
            bucket["created_at"] = max(bucket["created_at"], profile.created_at)
            bucket["historical_event_count"] += 1
            bucket["events"].append(
                _SnapshotEvent(
                    snapshot_key=key,
                    snapshot_type=_historical_snapshot_type(profile, onboarding.default_branch),
                    created_at=profile.created_at,
                    commit_sha=profile.commit_sha,
                    pr_number=None,
                    author=None,
                    source_ref=_historical_source_ref(profile, onboarding.default_branch),
                    source_url=f"https://github.com/{repo_full}/commit/{profile.commit_sha}",
                    artifact_path=artifact_path,
                    artifact_type=artifact_type,
                    profile=_profile_dict(profile.profile),
                    baseline_provenance=profile.baseline_provenance,
                    branch_ref=profile.branch_ref,
                    triggered_by=profile.triggered_by,
                )
            )

        for profile in list_static_profiles_for_repo_artifact(db_path, repo_full, artifact_path):
            audit = merged_audits.get(profile.audit_id)
            if audit is None:
                continue
            commit_sha = audit.pr_merge_commit_sha or audit.head_sha
            key = _repo_snapshot_key(repo_full, f"merge:{commit_sha}")
            bucket = events_by_key.setdefault(
                key,
                {
                    "snapshot_key": key,
                    "snapshot_type": "merge",
                    "created_at": audit.pr_merged_at or audit.updated_at or profile.created_at,
                    "commit_sha": commit_sha,
                    "pr_number": audit.pr_number,
                    "author": None,
                    "source_ref": f"PR #{audit.pr_number}",
                    "source_url": f"https://github.com/{repo_full}/pull/{audit.pr_number}",
                    "events": [],
                    "historical_event_count": 0,
                    "merged_event_count": 0,
                },
            )
            bucket["created_at"] = max(bucket["created_at"], audit.pr_merged_at or audit.updated_at or profile.created_at)
            bucket["merged_event_count"] += 1
            bucket["events"].append(
                _SnapshotEvent(
                    snapshot_key=key,
                    snapshot_type="merge",
                    created_at=profile.created_at,
                    commit_sha=commit_sha,
                    pr_number=audit.pr_number,
                    author=None,
                    source_ref=f"PR #{audit.pr_number}",
                    source_url=f"https://github.com/{repo_full}/pull/{audit.pr_number}",
                    artifact_path=artifact_path,
                    artifact_type=artifact_type,
                    profile=_profile_dict(profile.profile),
                    baseline_provenance=profile.baseline_provenance,
                )
            )

    current_state = dict(baseline_state)
    for bucket in sorted(events_by_key.values(), key=lambda item: (item["created_at"], item["snapshot_key"])):
        for event in sorted(bucket["events"], key=lambda item: (item.artifact_path, item.created_at)):
            current_state[event.artifact_path] = _ArtifactState(
                artifact_path=event.artifact_path,
                artifact_type=event.artifact_type,
                profile=event.profile,
                source_type=event.snapshot_type,
                source_ref=event.source_ref,
                source_url=event.source_url,
                baseline_provenance=event.baseline_provenance,
            )

        snapshot = _persist_snapshot(
            db_path,
            repo_full=repo_full,
            snapshot_key=bucket["snapshot_key"],
            snapshot_type=bucket["snapshot_type"],
            created_at=bucket["created_at"],
            commit_sha=bucket["commit_sha"],
            pr_number=bucket["pr_number"],
            author=bucket["author"],
            default_branch=onboarding.default_branch,
            source_ref=bucket["source_ref"],
            source_url=bucket["source_url"],
            artifact_state=current_state,
            previous_snapshot=previous_snapshot,
            baseline_snapshot=baseline_snapshot,
            input_summary={
                "baseline_artifact_count": len(baseline_versions),
                "historical_event_count": bucket["historical_event_count"],
                "merged_event_count": bucket["merged_event_count"],
                "baseline_verified": baseline_verified,
                "approved_baseline_count": approved_latest_count,
                "pending_baseline_count": pending_latest_count,
                "rejected_baseline_count": rejected_latest_count,
                "approved_by": onboarding.approved_by,
                "approved_at": onboarding.approved_at,
                "tracked_count": tracked_count,
                "coverage_percent": coverage_percent,
                "critical_artifact_count": critical_artifact_count,
                "approved_critical_count": approved_critical_count,
                "critical_coverage_percent": critical_coverage_percent,
                "drifting_artifact_count": 0,
                "last_baseline_at": onboarding.approved_at,
            },
        )
        snapshot_keys.add(snapshot.snapshot_key)
        snapshots.append(snapshot)
        previous_snapshot = snapshot
        if snapshot.snapshot_type == "branch_head":
            latest_branch_head_snapshot = snapshot

    if current_state and latest_branch_head_snapshot is None:
        latest = previous_snapshot or baseline_snapshot
        if latest is not None:
            current_snapshot = _persist_snapshot(
                db_path,
                repo_full=repo_full,
                snapshot_key=_repo_snapshot_key(repo_full, "current"),
                snapshot_type="current",
                created_at=latest.created_at + 0.001,
                commit_sha=latest.commit_sha,
                pr_number=latest.pr_number,
                author=latest.author,
                default_branch=onboarding.default_branch,
                source_ref=latest.source_ref,
                source_url=latest.source_url,
                artifact_state=current_state,
                previous_snapshot=previous_snapshot,
                baseline_snapshot=baseline_snapshot,
                input_summary={
                    "baseline_artifact_count": len(baseline_versions),
                    "historical_event_count": sum(bucket["historical_event_count"] for bucket in events_by_key.values()),
                    "merged_event_count": sum(bucket["merged_event_count"] for bucket in events_by_key.values()),
                    "baseline_verified": baseline_verified,
                    "approved_baseline_count": approved_latest_count,
                    "pending_baseline_count": pending_latest_count,
                    "rejected_baseline_count": rejected_latest_count,
                    "approved_by": onboarding.approved_by,
                    "approved_at": onboarding.approved_at,
                    "tracked_count": tracked_count,
                    "coverage_percent": coverage_percent,
                    "critical_artifact_count": critical_artifact_count,
                    "approved_critical_count": approved_critical_count,
                    "critical_coverage_percent": critical_coverage_percent,
                    "drifting_artifact_count": 0,
                    "last_baseline_at": onboarding.approved_at,
                },
            )
            snapshot_keys.add(current_snapshot.snapshot_key)
            if previous_snapshot is None or current_snapshot.id != previous_snapshot.id:
                snapshots.append(current_snapshot)

    delete_repo_posture_snapshots_not_in_keys(db_path, repo_full, snapshot_keys)
    return list_repo_posture_snapshots_for_repo(db_path, repo_full)


def build_repo_journey(db_path: str, repo_full: str) -> list[RepoPostureSnapshotRecord]:
    return materialize_repo_journey(db_path, repo_full)


def get_repo_snapshot_detail(db_path: str, repo_full: str, snapshot_id: int) -> RepoPostureSnapshotRecord | None:
    snapshots = materialize_repo_journey(db_path, repo_full)
    for snapshot in snapshots:
        if snapshot.id == snapshot_id:
            return snapshot
    return get_repo_posture_snapshot_for_repo(db_path, repo_full, snapshot_id)


def compare_repo_snapshots(db_path: str, repo_full: str, left_snapshot_id: int, right_snapshot_id: int) -> RepoJourneyComparison:
    snapshots = materialize_repo_journey(db_path, repo_full)
    snapshot_by_id = {snapshot.id: snapshot for snapshot in snapshots}
    left = snapshot_by_id.get(left_snapshot_id)
    right = snapshot_by_id.get(right_snapshot_id)
    if left is None or right is None:
        raise ValueError("One or both repo posture snapshots could not be found.")
    if left.repo_full != right.repo_full:
        raise ValueError("Repo posture snapshots belong to different repositories.")

    vector_delta = {
        key: round(float(right.attribute_vector.get(key, 0.0)) - float(left.attribute_vector.get(key, 0.0)), 4)
        for key in sorted(set(left.attribute_vector) | set(right.attribute_vector))
    }
    change_breakdown = _build_change_breakdown(left.artifact_state, right.artifact_state)
    change_labels = _derive_change_labels(vector_delta, change_breakdown)
    drift_summary = {
        "left_distance_from_baseline": left.distance_from_baseline,
        "right_distance_from_baseline": right.distance_from_baseline,
        "drift_delta": round(right.distance_from_baseline - left.distance_from_baseline, 4),
    }
    risk_summary = _build_risk_summary(
        right.attribute_vector,
        vector_delta,
        change_breakdown,
        right.distance_from_baseline,
    )
    comparison_kind = "arbitrary"
    if left.snapshot_type == "baseline_approved" and right.snapshot_type == "current":
        comparison_kind = "baseline_vs_current"
    elif left.snapshot_type == "baseline_approved" and right.snapshot_type == "branch_head":
        comparison_kind = "baseline_vs_current"
    elif right.snapshot_type in {"current", "branch_head"}:
        comparison_kind = "previous_vs_current"
    return RepoJourneyComparison(
        repo_full=repo_full,
        comparison_kind=comparison_kind,
        left=_snapshot_public_payload(left),
        right=_snapshot_public_payload(right),
        vector_delta=vector_delta,
        change_breakdown=change_breakdown,
        drift_summary=drift_summary,
        risk_summary=risk_summary,
        change_labels=change_labels,
    )


def _persist_snapshot(
    db_path: str,
    *,
    repo_full: str,
    snapshot_key: str,
    snapshot_type: str,
    created_at: float,
    commit_sha: str | None,
    pr_number: int | None,
    author: str | None,
    default_branch: str | None,
    source_ref: str | None,
    source_url: str | None,
    artifact_state: dict[str, _ArtifactState],
    previous_snapshot: RepoPostureSnapshotRecord | None,
    baseline_snapshot: RepoPostureSnapshotRecord | None,
    input_summary: dict[str, object],
) -> RepoPostureSnapshotRecord:
    vector = _build_attribute_vector(artifact_state)
    coverage = _build_artifact_coverage(artifact_state)
    baseline_authority = _build_baseline_authority(artifact_state)
    previous_state = previous_snapshot.artifact_state if previous_snapshot is not None else {}
    change_summary = _build_change_summary(previous_state, _artifact_state_payload(artifact_state))
    change_breakdown = _build_change_breakdown(previous_state, _artifact_state_payload(artifact_state))
    baseline_state = baseline_snapshot.artifact_state if baseline_snapshot is not None else {}
    distance_from_baseline = 0.0 if snapshot_type == "baseline_approved" else _vector_distance(
        baseline_snapshot.attribute_vector if baseline_snapshot is not None else {},
        vector,
    )
    distance_from_previous = 0.0 if previous_snapshot is None else _vector_distance(
        previous_snapshot.attribute_vector,
        vector,
    )
    vector_delta = {
        key: round(float(vector.get(key, 0.0)) - float(previous_snapshot.attribute_vector.get(key, 0.0)), 4)
        for key in vector
    } if previous_snapshot is not None else {key: 0.0 for key in vector}
    change_labels = _derive_change_labels(vector_delta, change_breakdown)
    drift_summary = {
        "baseline_snapshot_id": baseline_snapshot.id if baseline_snapshot is not None else None,
        "distance_from_baseline": distance_from_baseline,
        "changed_since_baseline": (
            _build_change_breakdown(baseline_state, _artifact_state_payload(artifact_state))
            if snapshot_type != "baseline_approved"
            else _build_change_breakdown(_artifact_state_payload(artifact_state), _artifact_state_payload(artifact_state))
        ),
    }
    risk_summary = _build_risk_summary(vector, vector_delta, change_breakdown, distance_from_baseline)
    baseline_reference = baseline_snapshot.source_ref if baseline_snapshot is not None else None
    return upsert_repo_posture_snapshot(
        db_path,
        snapshot_key=snapshot_key,
        repo_full=repo_full,
        commit_sha=commit_sha,
        pr_number=pr_number,
        author=author,
        created_at=created_at,
        snapshot_type=snapshot_type,
        baseline_reference=baseline_reference,
        default_branch=default_branch,
        source_ref=source_ref,
        source_url=source_url,
        attribute_vector=vector,
        artifact_coverage=coverage,
        artifact_state=_artifact_state_payload(artifact_state),
        change_summary=change_summary,
        change_breakdown=change_breakdown,
        drift_summary=drift_summary,
        risk_summary=risk_summary,
        change_labels=change_labels,
        baseline_authority=baseline_authority,
        input_summary=input_summary,
        distance_from_baseline=distance_from_baseline,
        distance_from_previous=distance_from_previous,
        materializer_version=REPO_JOURNEY_MATERIALIZER_VERSION,
    )


def _artifact_state_from_baseline(baseline: OnboardingBaselineVersionRecord) -> _ArtifactState:
    provenance = approved_onboarding_provenance(
        baseline.id,
        is_authoritative=baseline.approval_status == "approved",
        approval_status=baseline.approval_status,
        approved_by=baseline.approved_by,
        approved_at=baseline.approved_at,
        approval_note=baseline.approval_note,
    )
    return _ArtifactState(
        artifact_path=baseline.artifact_path,
        artifact_type=baseline.artifact_type,
        profile=_profile_dict(baseline.profile),
        source_type="baseline_approved",
        source_ref=(f"approved baseline @ {baseline.artifact_path}" if provenance.is_authoritative else f"baseline candidate @ {baseline.artifact_path}"),
        source_url=None,
        baseline_provenance=provenance,
    )


def _historical_snapshot_type(profile, default_branch: str | None) -> str:
    default_branch_ref = f"refs/heads/{default_branch}" if default_branch else None
    if profile.triggered_by in {"push_webhook", "scheduled", "manual"} and profile.branch_ref == default_branch_ref:
        return "branch_head"
    return "historical_commit"


def _historical_source_ref(profile, default_branch: str | None) -> str:
    if _historical_snapshot_type(profile, default_branch) == "branch_head":
        branch_name = (profile.branch_ref or "").removeprefix("refs/heads/") or (default_branch or "default")
        return f"{branch_name} @ {profile.commit_sha[:7]}"
    return f"commit {profile.commit_sha}"


def _profile_dict(profile) -> dict[str, float]:
    return {
        "guardrail_robustness": profile.guardrail_robustness,
        "capability_risk": profile.capability_risk,
        "autonomy_level": profile.autonomy_level,
        "stability_vs_creativity": profile.stability_vs_creativity,
        "governance_strength": profile.governance_strength,
        "change_frequency": profile.change_frequency,
        "semantic_density": profile.semantic_density,
    }


def _artifact_state_payload(artifact_state: dict[str, _ArtifactState]) -> dict[str, dict[str, object]]:
    payload: dict[str, dict[str, object]] = {}
    for artifact_path, state in sorted(artifact_state.items()):
        payload[artifact_path] = {
            "artifact_type": state.artifact_type,
            "profile": state.profile,
            "source_type": state.source_type,
            "source_ref": state.source_ref,
            "source_url": state.source_url,
            "baseline_provenance": asdict(state.baseline_provenance) if state.baseline_provenance is not None else None,
        }
    return payload


def _build_attribute_vector(artifact_state: dict[str, _ArtifactState]) -> dict[str, float]:
    if not artifact_state:
        return {
            "guardrails": 0.0,
            "capability": 0.0,
            "autonomy": 0.0,
            "governance": 0.0,
            "change_velocity": 0.0,
            "surface_criticality": 0.0,
        }
    profiles = [state.profile for state in artifact_state.values()]
    count = len(profiles)
    surface_criticality = sum(_artifact_criticality_weight(state.artifact_type, state.profile) for state in artifact_state.values()) / count
    return {
        "guardrails": round(sum(profile["guardrail_robustness"] for profile in profiles) / count, 4),
        "capability": round(sum(profile["capability_risk"] for profile in profiles) / count, 4),
        "autonomy": round(sum(profile["autonomy_level"] for profile in profiles) / count, 4),
        "governance": round(sum(profile["governance_strength"] for profile in profiles) / count, 4),
        "change_velocity": round(sum(profile["change_frequency"] for profile in profiles) / count, 4),
        "surface_criticality": round(surface_criticality, 4),
    }


def _artifact_criticality_weight(artifact_type: str, profile: dict[str, float]) -> float:
    lowered = (artifact_type or "").lower()
    base = 0.35
    if "prompt" in lowered:
        base = 0.6
    elif "tool" in lowered:
        base = 0.8
    elif "model" in lowered or "config" in lowered:
        base = 0.55
    elif "policy" in lowered or "guard" in lowered or "govern" in lowered:
        base = 0.7
    return min(1.0, round(base + (profile.get("capability_risk", 0.0) * 0.2) + (profile.get("autonomy_level", 0.0) * 0.1), 4))


def _build_artifact_coverage(artifact_state: dict[str, _ArtifactState]) -> dict[str, object]:
    by_type: dict[str, int] = {}
    for state in artifact_state.values():
        by_type[state.artifact_type] = by_type.get(state.artifact_type, 0) + 1
    return {
        "artifact_count": len(artifact_state),
        "artifact_types": by_type,
        "tracked_paths": sorted(artifact_state),
    }


def _build_baseline_authority(artifact_state: dict[str, _ArtifactState]) -> dict[str, object]:
    authority_counts = {
        "approved_baseline": 0,
        "pending_baseline": 0,
        "rejected_baseline": 0,
        "historical_fallback": 0,
        "none": 0,
    }
    for state in artifact_state.values():
        provenance = state.baseline_provenance
        if provenance is None:
            authority_counts["approved_baseline"] += 1 if state.source_type == "baseline_approved" else 0
            if state.source_type != "baseline_approved":
                authority_counts["none"] += 1
            continue
        if provenance.source_type == "approved_baseline":
            if provenance.is_authoritative:
                authority_counts["approved_baseline"] += 1
            elif provenance.approval_status == "rejected":
                authority_counts["rejected_baseline"] += 1
            else:
                authority_counts["pending_baseline"] += 1
        elif provenance.source_type == "historical_fallback":
            authority_counts["historical_fallback"] += 1
        else:
            authority_counts["none"] += 1
    return authority_counts


def _build_change_summary(left_state: dict[str, dict[str, object]], right_state: dict[str, dict[str, object]]) -> dict[str, object]:
    breakdown = _build_change_breakdown(left_state, right_state)
    return {
        "changed_artifact_count": breakdown["changed_artifact_count"],
        "added_artifact_count": breakdown["added_artifact_count"],
        "removed_artifact_count": breakdown["removed_artifact_count"],
        "critical_surfaces_changed": breakdown["critical_surfaces_changed"],
    }


def _build_change_breakdown(left_state: dict[str, dict[str, object]], right_state: dict[str, dict[str, object]]) -> dict[str, object]:
    left_paths = set(left_state)
    right_paths = set(right_state)
    added = sorted(right_paths - left_paths)
    removed = sorted(left_paths - right_paths)
    changed = []
    by_family = {
        "prompt": 0,
        "config": 0,
        "tool": 0,
        "governance": 0,
        "model": 0,
        "other": 0,
    }
    critical_surfaces_changed = 0

    for artifact_path in sorted(left_paths & right_paths):
        if left_state[artifact_path].get("profile") != right_state[artifact_path].get("profile"):
            changed.append(artifact_path)

    for artifact_path in added + removed + changed:
        state = right_state.get(artifact_path) or left_state.get(artifact_path) or {}
        family = _artifact_family(str(state.get("artifact_type") or ""))
        by_family[family] += 1
        if family in {"prompt", "tool", "governance", "model"}:
            critical_surfaces_changed += 1

    return {
        "changed_artifact_count": len(changed),
        "added_artifact_count": len(added),
        "removed_artifact_count": len(removed),
        "changed_artifact_paths": changed,
        "added_artifact_paths": added,
        "removed_artifact_paths": removed,
        "by_family": by_family,
        "critical_surfaces_changed": critical_surfaces_changed,
    }


def _artifact_family(artifact_type: str) -> str:
    lowered = artifact_type.lower()
    if "prompt" in lowered:
        return "prompt"
    if "tool" in lowered:
        return "tool"
    if "model" in lowered:
        return "model"
    if "config" in lowered:
        return "config"
    if "policy" in lowered or "guard" in lowered or "govern" in lowered:
        return "governance"
    return "other"


def _derive_change_labels(vector_delta: dict[str, float], change_breakdown: dict[str, object]) -> list[str]:
    labels: list[str] = []
    if vector_delta.get("capability", 0.0) > 0.05:
        labels.append("capability_expanded")
    if vector_delta.get("guardrails", 0.0) < -0.05:
        labels.append("guardrails_weakened")
    if abs(vector_delta.get("governance", 0.0)) > 0.05:
        labels.append("governance_changed")
    if vector_delta.get("autonomy", 0.0) > 0.05:
        labels.append("autonomy_increased")
    if change_breakdown["by_family"].get("tool", 0) > 0:
        labels.append("tooling_changed")
    if change_breakdown["by_family"].get("model", 0) > 0:
        labels.append("model_config_changed")
    return labels


def _build_risk_summary(
    attribute_vector: dict[str, float],
    vector_delta: dict[str, float],
    change_breakdown: dict[str, object],
    distance_from_baseline: float,
) -> dict[str, object]:
    score = 0.0
    score += max(0.0, vector_delta.get("capability", 0.0)) * 2.0
    score += abs(min(0.0, vector_delta.get("guardrails", 0.0))) * 2.0
    score += max(0.0, vector_delta.get("autonomy", 0.0)) * 1.5
    score += max(0.0, distance_from_baseline - 0.1)
    score += min(1.0, float(change_breakdown.get("critical_surfaces_changed", 0)) * 0.15)
    headline = "low"
    if score >= 1.25:
        headline = "high"
    elif score >= 0.55:
        headline = "medium"
    return {
        "risk_level": headline,
        "score": round(score, 4),
        "critical_surfaces_changed": change_breakdown.get("critical_surfaces_changed", 0),
        "capability": attribute_vector.get("capability", 0.0),
        "guardrails": attribute_vector.get("guardrails", 0.0),
        "autonomy": attribute_vector.get("autonomy", 0.0),
    }


def _vector_distance(left: dict[str, float], right: dict[str, float]) -> float:
    if not left:
        return round(sum(abs(value) for value in right.values()), 4) if right else 0.0
    total = 0.0
    for key in sorted(set(left) | set(right)):
        total += abs(float(right.get(key, 0.0)) - float(left.get(key, 0.0)))
    return round(total, 4)


def _snapshot_public_payload(snapshot: RepoPostureSnapshotRecord) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "snapshot_key": snapshot.snapshot_key,
        "repo_full": snapshot.repo_full,
        "commit_sha": snapshot.commit_sha,
        "pr_number": snapshot.pr_number,
        "author": snapshot.author,
        "created_at": snapshot.created_at,
        "snapshot_type": snapshot.snapshot_type,
        "baseline_reference": snapshot.baseline_reference,
        "default_branch": snapshot.default_branch,
        "source_ref": snapshot.source_ref,
        "source_url": snapshot.source_url,
        "attribute_vector": snapshot.attribute_vector,
        "artifact_coverage": snapshot.artifact_coverage,
        "change_summary": snapshot.change_summary,
        "change_breakdown": snapshot.change_breakdown,
        "drift_summary": snapshot.drift_summary,
        "risk_summary": snapshot.risk_summary,
        "change_labels": snapshot.change_labels,
        "baseline_authority": snapshot.baseline_authority,
        "input_summary": snapshot.input_summary,
        "distance_from_baseline": snapshot.distance_from_baseline,
        "distance_from_previous": snapshot.distance_from_previous,
        "materializer_version": snapshot.materializer_version,
    }


def snapshot_to_public_payload(snapshot: RepoPostureSnapshotRecord) -> dict[str, Any]:
    return _snapshot_public_payload(snapshot)