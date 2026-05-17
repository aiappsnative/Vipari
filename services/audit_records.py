from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Optional

from engine.analysis import DiffAnalysis
from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import AgentAttributeProfile, StaticSignals, build_attribute_profile, compare_attribute_profiles
from .persistence import connect_sqlite
from .baseline_provenance import (
    BASELINE_SOURCE_NONE,
    BaselineProvenance,
    approved_onboarding_provenance,
    baseline_provenance_from_json,
    baseline_provenance_to_json,
    no_baseline_provenance,
    previous_pr_fallback_provenance,
)
from .github_integration import list_pr_comment_reactions, list_pr_review_reactions
from .onboarding_records import get_latest_onboarding_baseline_for_repo_artifact
from .pr_feedback_mode import PR_FEEDBACK_MODE_COMMENTS, normalize_pr_feedback_mode
from .signal_fusion import normalize_risk_level


@dataclass(frozen=True)
class PullRequestAuditRecord:
    id: int
    job_id: int
    repo_full: str
    pr_number: int
    pr_title: str | None
    installation_id: int
    head_sha: str
    pr_state: str | None
    pr_merged: bool | None
    pr_closed_at: float | None
    pr_merged_at: float | None
    pr_merge_commit_sha: str | None
    pr_updated_at: float | None
    status: str
    completion_mode: str
    output_mode: str
    pr_feedback_mode: str
    deterministic_score: int
    suggested_risk_level: str
    fused_confidence: str | None
    semantic_review_completed: bool
    error_message: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class ChangedArtifactRecord:
    id: int
    audit_id: int
    artifact_path: str
    artifact_type: str
    context_mode: str
    relevance_reason: str
    changed_hunks: int
    added_count: int
    removed_count: int
    created_at: float


@dataclass(frozen=True)
class PreAuditRelevanceDecisionRecord:
    id: int
    repo_full: str
    pr_number: int
    head_sha: str
    artifact_path: str
    artifact_type: str
    confidence_tier: str
    heuristic_score: int
    heuristic_reason: str
    matched_signals_json: str
    classifier_status: str | None
    classifier_is_relevant: bool | None
    classifier_reason: str | None
    provider: str | None
    model: str | None
    latency_ms: float | None
    changed_artifact_id: int | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class FindingRecord:
    id: int
    audit_id: int
    changed_artifact_id: int | None
    source: str
    rule_id: str | None
    title: str
    severity: str
    rationale: str
    evidence: list[str]
    created_at: float


@dataclass(frozen=True)
class AuditCommentRecord:
    id: int
    audit_id: int
    github_comment_id: int | None
    github_review_id: int | None
    comment_mode: str
    comment_body: str
    posted_at: float
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class AuditFeedbackEventRecord:
    id: int
    audit_id: int
    repo_full: str
    pr_number: int
    head_sha: str
    kind: str
    source: str
    actor_github_id: str | None
    actor_github_login: str | None
    event_key: str | None
    payload_json: str
    created_at: float


@dataclass(frozen=True)
class PrCommentEpisodeRecord:
    audit_comment: AuditCommentRecord
    repo_full: str
    pr_number: int
    head_sha: str
    audit_status: str
    audit_completion_mode: str
    audit_output_mode: str
    audit_created_at: float
    audit_updated_at: float


@dataclass(frozen=True)
class ArtifactHistoryRecord:
    audit_id: int
    job_id: int
    repo_full: str
    pr_number: int
    head_sha: str
    status: str
    completion_mode: str
    output_mode: str
    deterministic_score: int
    suggested_risk_level: str
    fused_confidence: str | None
    semantic_review_completed: bool
    artifact_path: str
    artifact_type: str
    context_mode: str
    changed_hunks: int
    added_count: int
    removed_count: int
    created_at: float


@dataclass(frozen=True)
class ArtifactVersionRecord:
    id: int
    audit_id: int
    changed_artifact_id: int
    normalized_artifact_id: str
    artifact_path: str
    artifact_type: str
    version_hash: str
    signal_terms: list[str]
    line_count: int
    previous_version_id: int | None
    created_at: float
    content_text: str | None = None


@dataclass(frozen=True)
class StaticArtifactProfileRecord:
    id: int
    audit_id: int
    changed_artifact_id: int
    artifact_version_id: int
    normalized_artifact_id: str
    artifact_path: str
    artifact_type: str
    profile: AgentAttributeProfile
    baseline_profile_id: int | None
    baseline_provenance: BaselineProvenance | None
    semantic_similarity: float
    semantic_distance: float
    attribute_deltas: dict[str, float]
    narrative: list[str]
    signal_terms: list[str]
    created_at: float


@dataclass(frozen=True)
class StaticArtifactDriftPreview:
    artifact_path: str
    artifact_type: str
    profile: AgentAttributeProfile
    baseline_profile_id: int | None
    baseline_provenance: BaselineProvenance | None
    semantic_similarity: float
    semantic_distance: float
    attribute_deltas: dict[str, float]
    narrative: list[str]
    signal_terms: list[str]


@dataclass(frozen=True)
class RepoStaticDriftSummary:
    repo_full: str
    artifact_count: int
    profile_count: int
    baseline_linked_profile_count: int
    avg_semantic_distance: float
    avg_guardrail_shift: float
    avg_capability_shift: float
    avg_autonomy_shift: float
    highest_capability_artifact_path: str | None
    highest_capability_delta: float


@dataclass(frozen=True)
class ArtifactDriftLeaderboardEntry:
    artifact_path: str
    artifact_type: str
    latest_profile_id: int
    sample_count: int
    latest_created_at: float
    semantic_distance: float
    guardrail_shift: float
    capability_shift: float
    autonomy_shift: float
    drift_magnitude: float
    narrative: list[str]


def _connect(db_path: str) -> sqlite3.Connection:
    return connect_sqlite(db_path, foreign_keys=True)


def _ensure_pull_request_audit_columns(conn: sqlite3.Connection) -> None:
    audit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(pull_request_audits)").fetchall()}
    if "pr_title" not in audit_columns:
        conn.execute("ALTER TABLE pull_request_audits ADD COLUMN pr_title TEXT")
    if "pr_state" not in audit_columns:
        conn.execute("ALTER TABLE pull_request_audits ADD COLUMN pr_state TEXT")
    if "pr_merged" not in audit_columns:
        conn.execute("ALTER TABLE pull_request_audits ADD COLUMN pr_merged INTEGER")
    if "pr_closed_at" not in audit_columns:
        conn.execute("ALTER TABLE pull_request_audits ADD COLUMN pr_closed_at REAL")
    if "pr_merged_at" not in audit_columns:
        conn.execute("ALTER TABLE pull_request_audits ADD COLUMN pr_merged_at REAL")
    if "pr_merge_commit_sha" not in audit_columns:
        conn.execute("ALTER TABLE pull_request_audits ADD COLUMN pr_merge_commit_sha TEXT")
    if "pr_updated_at" not in audit_columns:
        conn.execute("ALTER TABLE pull_request_audits ADD COLUMN pr_updated_at REAL")
    if "fused_confidence" not in audit_columns:
        conn.execute("ALTER TABLE pull_request_audits ADD COLUMN fused_confidence TEXT")
    if "pr_feedback_mode" not in audit_columns:
        conn.execute("ALTER TABLE pull_request_audits ADD COLUMN pr_feedback_mode TEXT NOT NULL DEFAULT 'comments'")


def ensure_pull_request_audit_schema(db_path: str) -> None:
    with _connect(db_path) as conn:
        _ensure_pull_request_audit_columns(conn)


def init_audit_record_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pull_request_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                repo_full TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                pr_title TEXT,
                installation_id INTEGER NOT NULL,
                head_sha TEXT NOT NULL,
                pr_state TEXT,
                pr_merged INTEGER,
                pr_closed_at REAL,
                pr_merged_at REAL,
                pr_merge_commit_sha TEXT,
                pr_updated_at REAL,
                status TEXT NOT NULL,
                completion_mode TEXT NOT NULL,
                output_mode TEXT NOT NULL,
                pr_feedback_mode TEXT NOT NULL DEFAULT 'comments',
                deterministic_score INTEGER NOT NULL,
                suggested_risk_level TEXT NOT NULL,
                fused_confidence TEXT,
                semantic_review_completed INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(repo_full, pr_number, head_sha)
            )
            """
        )
        _ensure_pull_request_audit_columns(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS changed_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                context_mode TEXT NOT NULL,
                relevance_reason TEXT NOT NULL,
                changed_hunks INTEGER NOT NULL,
                added_count INTEGER NOT NULL,
                removed_count INTEGER NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(audit_id) REFERENCES pull_request_audits(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL,
                changed_artifact_id INTEGER,
                source TEXT NOT NULL,
                rule_id TEXT,
                title TEXT NOT NULL,
                severity TEXT NOT NULL,
                rationale TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(audit_id) REFERENCES pull_request_audits(id) ON DELETE CASCADE,
                FOREIGN KEY(changed_artifact_id) REFERENCES changed_artifacts(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL UNIQUE,
                github_comment_id INTEGER,
                github_review_id INTEGER,
                comment_mode TEXT NOT NULL,
                comment_body TEXT NOT NULL,
                posted_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(audit_id) REFERENCES pull_request_audits(id) ON DELETE CASCADE
            )
            """
        )
        audit_comment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_comments)").fetchall()}
        if "github_review_id" not in audit_comment_columns:
            conn.execute("ALTER TABLE audit_comments ADD COLUMN github_review_id INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL,
                changed_artifact_id INTEGER NOT NULL,
                normalized_artifact_id TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                version_hash TEXT NOT NULL,
                signal_terms_json TEXT NOT NULL,
                line_count INTEGER NOT NULL,
                content_text TEXT,
                previous_version_id INTEGER,
                created_at REAL NOT NULL,
                FOREIGN KEY(audit_id) REFERENCES pull_request_audits(id) ON DELETE CASCADE,
                FOREIGN KEY(changed_artifact_id) REFERENCES changed_artifacts(id) ON DELETE CASCADE,
                FOREIGN KEY(previous_version_id) REFERENCES artifact_versions(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS static_artifact_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL,
                changed_artifact_id INTEGER NOT NULL,
                artifact_version_id INTEGER NOT NULL,
                normalized_artifact_id TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                baseline_profile_id INTEGER,
                baseline_provenance_json TEXT,
                semantic_similarity REAL NOT NULL,
                semantic_distance REAL NOT NULL,
                attribute_deltas_json TEXT NOT NULL,
                narrative_json TEXT NOT NULL,
                signal_terms_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(audit_id) REFERENCES pull_request_audits(id) ON DELETE CASCADE,
                FOREIGN KEY(changed_artifact_id) REFERENCES changed_artifacts(id) ON DELETE CASCADE,
                FOREIGN KEY(artifact_version_id) REFERENCES artifact_versions(id) ON DELETE CASCADE,
                FOREIGN KEY(baseline_profile_id) REFERENCES static_artifact_profiles(id) ON DELETE SET NULL
            )
            """
        )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pull_request_audits_repo_pr_sha ON pull_request_audits(repo_full, pr_number, head_sha)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pull_request_audits_created_at ON pull_request_audits(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_changed_artifacts_path ON changed_artifacts(artifact_path)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pre_audit_relevance_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                head_sha TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                confidence_tier TEXT NOT NULL,
                heuristic_score INTEGER NOT NULL,
                heuristic_reason TEXT NOT NULL,
                matched_signals_json TEXT NOT NULL,
                classifier_status TEXT,
                classifier_is_relevant INTEGER,
                classifier_reason TEXT,
                provider TEXT,
                model TEXT,
                latency_ms REAL,
                changed_artifact_id INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(repo_full, pr_number, head_sha, artifact_path),
                FOREIGN KEY(changed_artifact_id) REFERENCES changed_artifacts(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pre_audit_relevance_decisions_lookup ON pre_audit_relevance_decisions(repo_full, pr_number, head_sha)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_signal_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full TEXT NOT NULL,
                path_pattern TEXT NOT NULL,
                artifact_type TEXT,
                weight_adjustment INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_source_severity ON findings(source, severity)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_rule_id ON findings(rule_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_versions_normalized_id ON artifact_versions(normalized_artifact_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_versions_hash ON artifact_versions(version_hash)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_static_artifact_profiles_normalized_id ON static_artifact_profiles(normalized_artifact_id, created_at)"
        )

        artifact_version_columns = {row["name"] for row in conn.execute("PRAGMA table_info(artifact_versions)").fetchall()}
        if "content_text" not in artifact_version_columns:
            conn.execute("ALTER TABLE artifact_versions ADD COLUMN content_text TEXT")

        audit_comments_columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_comments)").fetchall()}
        if "github_comment_id" not in audit_comments_columns:
            conn.execute("ALTER TABLE audit_comments ADD COLUMN github_comment_id INTEGER")

        static_profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(static_artifact_profiles)").fetchall()}
        if "baseline_provenance_json" not in static_profile_columns:
            conn.execute("ALTER TABLE static_artifact_profiles ADD COLUMN baseline_provenance_json TEXT")


def has_completed_audit(db_path: str, *, repo_full: str, pr_number: int, head_sha: str) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM pull_request_audits
            WHERE repo_full = ? AND pr_number = ? AND head_sha = ? AND status = 'completed'
            LIMIT 1
            """,
            (repo_full, pr_number, head_sha),
        ).fetchone()
    return row is not None


def record_audit_result(
    db_path: str,
    *,
    job_id: int,
    repo_full: str,
    pr_number: int,
    pr_title: str | None = None,
    installation_id: int,
    head_sha: str,
    pr_state: str | None = None,
    pr_merged: bool | None = None,
    pr_closed_at: float | None = None,
    pr_merged_at: float | None = None,
    pr_merge_commit_sha: str | None = None,
    pr_updated_at: float | None = None,
    deterministic_analysis: DiffAnalysis,
    status: str,
    completion_mode: str,
    output_mode: str,
    pr_feedback_mode: str = PR_FEEDBACK_MODE_COMMENTS,
    comment_body: str | None,
    comment_mode: str | None,
    semantic_review_completed: bool,
    suggested_risk_level: str | None = None,
    fused_confidence: str | None = None,
    error_message: str | None = None,
    artifact_snapshots: dict[str, str] | None = None,
    github_comment_id: int | None = None,
    github_review_id: int | None = None,
) -> PullRequestAuditRecord:
    now = time.time()
    artifact_snapshots = artifact_snapshots or {}
    persisted_risk_level = suggested_risk_level or deterministic_analysis.suggested_risk_level.value
    (
        pr_state,
        pr_merged_value,
        pr_closed_at,
        pr_merged_at,
        pr_merge_commit_sha,
        pr_updated_at,
    ) = _normalize_pr_lifecycle_fields(
        pr_state=pr_state,
        pr_merged=pr_merged,
        pr_closed_at=pr_closed_at,
        pr_merged_at=pr_merged_at,
        pr_merge_commit_sha=pr_merge_commit_sha,
        pr_updated_at=pr_updated_at,
    )
    persisted_feedback_mode = normalize_pr_feedback_mode(pr_feedback_mode)
    with _connect(db_path) as conn:
        existing = conn.execute(
            """
            SELECT id, created_at, pr_state, pr_merged, pr_closed_at, pr_merged_at, pr_merge_commit_sha, pr_updated_at
            FROM pull_request_audits
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO pull_request_audits (
                    job_id, repo_full, pr_number, pr_title, installation_id, head_sha,
                    pr_state, pr_merged, pr_closed_at, pr_merged_at, pr_merge_commit_sha, pr_updated_at,
                    status, completion_mode, output_mode, pr_feedback_mode,
                    deterministic_score, suggested_risk_level, fused_confidence, semantic_review_completed,
                    error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    repo_full,
                    pr_number,
                    pr_title,
                    installation_id,
                    head_sha,
                    pr_state,
                    pr_merged_value,
                    pr_closed_at,
                    pr_merged_at,
                    pr_merge_commit_sha,
                    pr_updated_at,
                    status,
                    completion_mode,
                    output_mode,
                    persisted_feedback_mode,
                    deterministic_analysis.deterministic_score,
                    persisted_risk_level,
                    fused_confidence,
                    int(semantic_review_completed),
                    error_message,
                    now,
                    now,
                ),
            )
            audit_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        else:
            audit_id = existing["id"]
            conn.execute(
                """
                UPDATE pull_request_audits
                SET repo_full = ?,
                    pr_number = ?,
                    pr_title = ?,
                    installation_id = ?,
                    head_sha = ?,
                    pr_state = ?,
                    pr_merged = ?,
                    pr_closed_at = ?,
                    pr_merged_at = ?,
                    pr_merge_commit_sha = ?,
                    pr_updated_at = ?,
                    status = ?,
                    completion_mode = ?,
                    output_mode = ?,
                    pr_feedback_mode = ?,
                    deterministic_score = ?,
                    suggested_risk_level = ?,
                    fused_confidence = ?,
                    semantic_review_completed = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    repo_full,
                    pr_number,
                    pr_title,
                    installation_id,
                    head_sha,
                    pr_state,
                    pr_merged_value,
                    pr_closed_at,
                    pr_merged_at,
                    pr_merge_commit_sha,
                    pr_updated_at,
                    status,
                    completion_mode,
                    output_mode,
                    persisted_feedback_mode,
                    deterministic_analysis.deterministic_score,
                    persisted_risk_level,
                    fused_confidence,
                    int(semantic_review_completed),
                    error_message,
                    now,
                    audit_id,
                ),
            )
            conn.execute("DELETE FROM findings WHERE audit_id = ?", (audit_id,))
            conn.execute("DELETE FROM static_artifact_profiles WHERE audit_id = ?", (audit_id,))
            conn.execute("DELETE FROM artifact_versions WHERE audit_id = ?", (audit_id,))
            conn.execute("DELETE FROM changed_artifacts WHERE audit_id = ?", (audit_id,))

        for artifact in deterministic_analysis.artifacts:
            cursor = conn.execute(
                """
                INSERT INTO changed_artifacts (
                    audit_id, artifact_path, artifact_type, context_mode, relevance_reason,
                    changed_hunks, added_count, removed_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    artifact.relevance.path,
                    artifact.relevance.artifact_type,
                    artifact.relevance.context_mode.value,
                    artifact.relevance.reason,
                    artifact.change.changed_hunks,
                    artifact.change.added_count,
                    artifact.change.removed_count,
                    now,
                ),
            )
            changed_artifact_id = int(cursor.lastrowid)
            conn.execute(
                """
                UPDATE pre_audit_relevance_decisions
                SET changed_artifact_id = ?, updated_at = ?
                WHERE repo_full = ? AND pr_number = ? AND head_sha = ? AND artifact_path = ?
                """,
                (changed_artifact_id, now, repo_full, pr_number, head_sha, artifact.relevance.path),
            )

            for finding in artifact.findings:
                conn.execute(
                    """
                    INSERT INTO findings (
                        audit_id, changed_artifact_id, source, rule_id, title,
                        severity, rationale, evidence_json, created_at
                    ) VALUES (?, ?, 'deterministic', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_id,
                        changed_artifact_id,
                        finding.rule_id,
                        finding.title,
                        finding.severity.value,
                        finding.rationale,
                        json.dumps(finding.evidence),
                        now,
                    ),
                )

            snapshot_text = artifact_snapshots.get(artifact.relevance.path)
            if snapshot_text is not None:
                normalized_artifact_id = _build_normalized_artifact_id(repo_full, artifact.relevance.path)
                signal_terms = extract_signal_terms_from_text(snapshot_text)
                profile = build_attribute_profile(snapshot_text)
                previous_profile_row = conn.execute(
                    """
                    SELECT * FROM static_artifact_profiles
                    WHERE normalized_artifact_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (normalized_artifact_id,),
                ).fetchone()
                previous_version = conn.execute(
                    """
                    SELECT id FROM artifact_versions
                    WHERE normalized_artifact_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (normalized_artifact_id,),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO artifact_versions (
                        audit_id, changed_artifact_id, normalized_artifact_id, artifact_path, artifact_type,
                        version_hash, signal_terms_json, line_count, content_text, previous_version_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_id,
                        changed_artifact_id,
                        normalized_artifact_id,
                        artifact.relevance.path,
                        artifact.relevance.artifact_type,
                        hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest(),
                        json.dumps(signal_terms),
                        len([line for line in snapshot_text.splitlines() if line.strip()]),
                        snapshot_text,
                        previous_version["id"] if previous_version is not None else None,
                        now,
                    ),
                )
                artifact_version_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

                baseline_profile_id: int | None = None
                baseline_provenance = no_baseline_provenance()
                semantic_similarity = 1.0
                semantic_distance = 0.0
                attribute_deltas: dict[str, float] = {
                    "guardrail_robustness": 0.0,
                    "capability_risk": 0.0,
                    "autonomy_level": 0.0,
                    "stability_vs_creativity": 0.0,
                    "governance_strength": 0.0,
                    "change_frequency": 0.0,
                    "semantic_density": 0.0,
                }
                narrative = ["No approved baseline available; stored current profile as a new baseline candidate."]
                onboarding_baseline = get_latest_onboarding_baseline_for_repo_artifact(
                    db_path,
                    repo_full,
                    artifact.relevance.path,
                    only_approved=True,
                )

                if onboarding_baseline is not None:
                    baseline_provenance = approved_onboarding_provenance(onboarding_baseline.id)
                    semantic_similarity = _term_similarity(signal_terms, onboarding_baseline.signal_terms)
                    drift_delta = compare_attribute_profiles(
                        onboarding_baseline.profile,
                        profile,
                        semantic_similarity=semantic_similarity,
                    )
                    semantic_distance = drift_delta.semantic_distance
                    attribute_deltas = drift_delta.attribute_deltas
                    narrative = drift_delta.narrative
                elif previous_profile_row is not None:
                    baseline_profile_id = previous_profile_row["id"]
                    baseline_provenance = previous_pr_fallback_provenance(
                        previous_profile_row["id"],
                        previous_profile_row["artifact_version_id"],
                    )
                    baseline_profile = _profile_from_json(previous_profile_row["profile_json"])
                    previous_signal_terms = json.loads(previous_profile_row["signal_terms_json"])
                    semantic_similarity = _term_similarity(signal_terms, previous_signal_terms)
                    drift_delta = compare_attribute_profiles(
                        baseline_profile,
                        profile,
                        semantic_similarity=semantic_similarity,
                    )
                    semantic_distance = drift_delta.semantic_distance
                    attribute_deltas = drift_delta.attribute_deltas
                    narrative = drift_delta.narrative

                conn.execute(
                    """
                    INSERT INTO static_artifact_profiles (
                        audit_id, changed_artifact_id, artifact_version_id,
                        normalized_artifact_id, artifact_path, artifact_type,
                        profile_json, baseline_profile_id, baseline_provenance_json, semantic_similarity,
                        semantic_distance, attribute_deltas_json, narrative_json,
                        signal_terms_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_id,
                        changed_artifact_id,
                        artifact_version_id,
                        normalized_artifact_id,
                        artifact.relevance.path,
                        artifact.relevance.artifact_type,
                        json.dumps(asdict(profile)),
                        baseline_profile_id,
                        baseline_provenance_to_json(baseline_provenance),
                        semantic_similarity,
                        semantic_distance,
                        json.dumps(attribute_deltas),
                        json.dumps(narrative),
                        json.dumps(signal_terms),
                        now,
                    ),
                )

        if comment_body is not None and comment_mode is not None:
            existing_comment = conn.execute(
                "SELECT id, created_at FROM audit_comments WHERE audit_id = ?",
                (audit_id,),
            ).fetchone()
            if existing_comment is None:
                conn.execute(
                    """
                    INSERT INTO audit_comments (
                        audit_id, github_comment_id, github_review_id, comment_mode, comment_body, posted_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (audit_id, github_comment_id, github_review_id, comment_mode, comment_body, now, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE audit_comments
                    SET github_comment_id = ?,
                        github_review_id = ?,
                        comment_mode = ?,
                        comment_body = ?,
                        posted_at = ?,
                        updated_at = ?
                    WHERE audit_id = ?
                    """,
                    (github_comment_id, github_review_id, comment_mode, comment_body, now, now, audit_id),
                )
        else:
            conn.execute("DELETE FROM audit_comments WHERE audit_id = ?", (audit_id,))

        # After recording audit artifacts and profiles, if this PR was merged, attempt to reconcile
        # added/removed artifact paths into the repository onboarding so the 'current' state
        # discovery metrics and baseline coverage reflect the merge.
        try:
            from .onboarding import sync_on_pr_merge_artifact_changes
            added = set()
            removed = set()
            for art in deterministic_analysis.artifacts:
                if getattr(art.change, 'added_count', 0) > 0:
                    added.add(art.relevance.path)
                if getattr(art.change, 'removed_count', 0) > 0:
                    removed.add(art.relevance.path)
            # artifact_snapshots maps path -> text content when available
            sync_on_pr_merge_artifact_changes(
                db_path,
                repo_full=repo_full,
                artifact_snapshots=artifact_snapshots or {},
                added_paths=added,
                removed_paths=removed,
            )
        except Exception:
            # best-effort sync; do not fail the audit recording on sync errors
            pass

        row = conn.execute("SELECT * FROM pull_request_audits WHERE id = ?", (audit_id,)).fetchone()

    if row is None:
        raise RuntimeError("Failed to store or reload pull request audit record.")
    return _row_to_pull_request_audit(row)


def record_pre_audit_relevance_decision(
    db_path: str,
    *,
    repo_full: str,
    pr_number: int,
    head_sha: str,
    relevance,
) -> None:
    now = time.time()
    micro_classifier = getattr(relevance, "micro_classifier", None)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pre_audit_relevance_decisions (
                repo_full, pr_number, head_sha, artifact_path, artifact_type,
                confidence_tier, heuristic_score, heuristic_reason, matched_signals_json,
                classifier_status, classifier_is_relevant, classifier_reason,
                provider, model, latency_ms, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo_full, pr_number, head_sha, artifact_path) DO UPDATE SET
                artifact_type = excluded.artifact_type,
                confidence_tier = excluded.confidence_tier,
                heuristic_score = excluded.heuristic_score,
                heuristic_reason = excluded.heuristic_reason,
                matched_signals_json = excluded.matched_signals_json,
                classifier_status = excluded.classifier_status,
                classifier_is_relevant = excluded.classifier_is_relevant,
                classifier_reason = excluded.classifier_reason,
                provider = excluded.provider,
                model = excluded.model,
                latency_ms = excluded.latency_ms,
                updated_at = excluded.updated_at
            """,
            (
                repo_full,
                pr_number,
                head_sha,
                relevance.path,
                relevance.artifact_type,
                relevance.confidence_tier.value,
                relevance.heuristic_score,
                relevance.reason,
                json.dumps([asdict(signal) for signal in relevance.matched_signals]),
                micro_classifier.status if micro_classifier is not None else None,
                int(micro_classifier.is_relevant) if micro_classifier is not None else None,
                micro_classifier.reason if micro_classifier is not None else None,
                micro_classifier.provider if micro_classifier is not None else None,
                micro_classifier.model if micro_classifier is not None else None,
                micro_classifier.latency_ms if micro_classifier is not None else None,
                now,
                now,
            ),
        )


def list_pre_audit_relevance_decisions(
    db_path: str,
    *,
    repo_full: str,
    pr_number: int,
    head_sha: str,
) -> list[PreAuditRelevanceDecisionRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM pre_audit_relevance_decisions
            WHERE repo_full = ? AND pr_number = ? AND head_sha = ?
            ORDER BY artifact_path ASC, id ASC
            """,
            (repo_full, pr_number, head_sha),
        ).fetchall()
    return [
        PreAuditRelevanceDecisionRecord(
            id=row["id"],
            repo_full=row["repo_full"],
            pr_number=row["pr_number"],
            head_sha=row["head_sha"],
            artifact_path=row["artifact_path"],
            artifact_type=row["artifact_type"],
            confidence_tier=row["confidence_tier"],
            heuristic_score=row["heuristic_score"],
            heuristic_reason=row["heuristic_reason"],
            matched_signals_json=row["matched_signals_json"],
            classifier_status=row["classifier_status"],
            classifier_is_relevant=(bool(row["classifier_is_relevant"]) if row["classifier_is_relevant"] is not None else None),
            classifier_reason=row["classifier_reason"],
            provider=row["provider"],
            model=row["model"],
            latency_ms=row["latency_ms"],
            changed_artifact_id=row["changed_artifact_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def get_pull_request_audit_by_id(db_path: str, audit_id: int) -> Optional[PullRequestAuditRecord]:
    """Fetch a pull_request_audit record by its primary key."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM pull_request_audits WHERE id = ?", (audit_id,)).fetchone()
    return _row_to_pull_request_audit(row) if row is not None else None


def get_pull_request_audit_for_job(db_path: str, job_id: int) -> Optional[PullRequestAuditRecord]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM pull_request_audits WHERE job_id = ?", (job_id,)).fetchone()
    return _row_to_pull_request_audit(row) if row is not None else None


def list_changed_artifacts_for_audit(db_path: str, audit_id: int) -> list[ChangedArtifactRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM changed_artifacts WHERE audit_id = ? ORDER BY id ASC",
            (audit_id,),
        ).fetchall()
    return [_row_to_changed_artifact(row) for row in rows]


def list_findings_for_audit(db_path: str, audit_id: int) -> list[FindingRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM findings WHERE audit_id = ? ORDER BY id ASC",
            (audit_id,),
        ).fetchall()
    return [_row_to_finding(row) for row in rows]


def get_audit_comment_for_audit(db_path: str, audit_id: int) -> Optional[AuditCommentRecord]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM audit_comments WHERE audit_id = ?", (audit_id,)).fetchone()
    return _row_to_audit_comment(row) if row is not None else None


def record_audit_feedback_event(
    db_path: str,
    *,
    audit_id: int,
    kind: str,
    source: str,
    payload_json: str,
    actor_github_id: str | None = None,
    actor_github_login: str | None = None,
    event_key: str | None = None,
    created_at: float | None = None,
) -> AuditFeedbackEventRecord:
    timestamp = time.time() if created_at is None else created_at
    workspace_id = 0
    with _connect(db_path) as conn:
        audit_row = conn.execute(
            "SELECT id, repo_full, pr_number, head_sha FROM pull_request_audits WHERE id = ?",
            (audit_id,),
        ).fetchone()
        if audit_row is None:
            raise ValueError(f"Audit {audit_id} does not exist.")

        from .control_plane_records import get_active_repo_allocation_for_repo

        allocation = get_active_repo_allocation_for_repo(db_path, str(audit_row["repo_full"]))
        if allocation is not None:
            workspace_id = allocation.workspace_id

        if event_key:
            existing = conn.execute(
                "SELECT * FROM audit_feedback_events WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            if existing is not None:
                return _row_to_audit_feedback_event(existing)

        cursor = conn.execute(
            """
            INSERT INTO audit_feedback_events (
                audit_id, workspace_id, repo_full, pr_number, head_sha, kind, source,
                actor_github_id, actor_github_login, event_key, payload_json, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                workspace_id,
                audit_row["repo_full"],
                audit_row["pr_number"],
                audit_row["head_sha"],
                kind,
                source,
                actor_github_id,
                actor_github_login,
                event_key,
                payload_json,
                payload_json,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM audit_feedback_events WHERE id = ?", (int(cursor.lastrowid),)).fetchone()

    if row is None:
        raise RuntimeError("Failed to store or reload audit feedback event.")
    record = _row_to_audit_feedback_event(row)

    from .activity_records import record_activity_event_if_configured

    try:
        details = json.loads(record.payload_json) if record.payload_json else {}
    except (TypeError, ValueError):
        details = {"payload": record.payload_json}
    if not isinstance(details, dict):
        details = {"payload": details}
    details.setdefault("source", record.source)
    if record.actor_github_login:
        details.setdefault("actor_github_login", record.actor_github_login)
    if record.actor_github_id:
        details.setdefault("actor_github_id", record.actor_github_id)
    record_activity_event_if_configured(
        external_id=f"audit_feedback:{record.id}",
        occurred_at=record.created_at,
        source="audit_feedback",
        event_type=f"audit.feedback.{record.kind}",
        workspace_id=(workspace_id or None),
        actor_user_id=None,
        actor_label=record.actor_github_login or record.source,
        repo_full=record.repo_full,
        subject_type="audit",
        subject_id=str(record.audit_id),
        details=details,
    )
    return record


def list_audit_feedback_events_for_audit(db_path: str, audit_id: int) -> list[AuditFeedbackEventRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_feedback_events WHERE audit_id = ? ORDER BY created_at ASC, id ASC",
            (audit_id,),
        ).fetchall()
    return [_row_to_audit_feedback_event(row) for row in rows]


def list_audit_feedback_events_for_repo(
    db_path: str,
    repo_full: str,
    *,
    limit: int = 100,
) -> list[AuditFeedbackEventRecord]:
    safe_limit = max(1, min(int(limit), 1000))
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM audit_feedback_events
            WHERE repo_full = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (repo_full, safe_limit),
        ).fetchall()
    return [_row_to_audit_feedback_event(row) for row in rows]


def record_pr_outcome_feedback_events(
    db_path: str,
    *,
    repo_full: str,
    pr_number: int,
    head_sha: str | None,
    pr_state: str | None,
    pr_merged: bool | None,
) -> list[AuditFeedbackEventRecord]:
    if pr_state != "closed":
        return []

    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM pull_request_audits WHERE repo_full = ? AND pr_number = ? ORDER BY id ASC",
            (repo_full, pr_number),
        ).fetchall()

    recorded: list[AuditFeedbackEventRecord] = []
    for row in rows:
        audit = _row_to_pull_request_audit(row)
        recommendation_lane = _derive_audit_recommendation_lane(db_path, audit.id)
        outcome = _derive_pr_outcome_kind(audit, pr_merged=pr_merged, recommendation_lane=recommendation_lane)
        recorded.append(
            record_audit_feedback_event(
                db_path,
                audit_id=audit.id,
                kind="pr_outcome",
                source="lifecycle",
                event_key=f"pr_outcome:{audit.id}:{'merged' if pr_merged else 'closed'}",
                payload_json=json.dumps(
                    {
                        "outcome": outcome,
                        "repo_full": audit.repo_full,
                        "pr_number": audit.pr_number,
                        "head_sha": audit.head_sha,
                        "pr_state": pr_state,
                        "pr_merged": bool(pr_merged),
                        "suggested_risk_level": audit.suggested_risk_level,
                        "recommendation_lane": recommendation_lane,
                    }
                ),
            )
        )
    return recorded


def refresh_audit_reaction_feedback_for_audit(db_path: str, *, audit_id: int, token: str) -> list[AuditFeedbackEventRecord]:
    audit = get_pull_request_audit_by_id(db_path, audit_id)
    if audit is None:
        return []
    audit_comment = get_audit_comment_for_audit(db_path, audit_id)
    if audit_comment is None:
        return []

    recorded: list[AuditFeedbackEventRecord] = []
    if audit_comment.github_comment_id is not None:
        for reaction in list_pr_comment_reactions(
            audit.repo_full,
            audit.pr_number,
            token,
            comment_id=audit_comment.github_comment_id,
        ):
            recorded.append(_record_github_reaction_feedback_event(db_path, audit.id, reaction))
    if audit_comment.github_review_id is not None:
        for reaction in list_pr_review_reactions(
            audit.repo_full,
            audit.pr_number,
            token,
            review_id=audit_comment.github_review_id,
        ):
            recorded.append(_record_github_reaction_feedback_event(db_path, audit.id, reaction))
    return recorded


def refresh_audit_reaction_feedback_for_pr(
    db_path: str,
    *,
    repo_full: str,
    pr_number: int,
    head_sha: str | None,
    token: str,
) -> list[AuditFeedbackEventRecord]:
    with _connect(db_path) as conn:
        if head_sha:
            rows = conn.execute(
                "SELECT id FROM pull_request_audits WHERE repo_full = ? AND pr_number = ? AND head_sha = ? ORDER BY id ASC",
                (repo_full, pr_number, head_sha),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM pull_request_audits WHERE repo_full = ? AND pr_number = ? ORDER BY id ASC",
                (repo_full, pr_number),
            ).fetchall()

    recorded: list[AuditFeedbackEventRecord] = []
    for row in rows:
        recorded.extend(refresh_audit_reaction_feedback_for_audit(db_path, audit_id=row["id"], token=token))
    return recorded


def get_audit_comment_episode_for_pr_head_sha(
    db_path: str,
    repo_full: str,
    pr_number: int,
    head_sha: str,
) -> Optional[PrCommentEpisodeRecord]:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT ac.*, pra.repo_full, pra.pr_number, pra.head_sha,
                   pra.status AS audit_status,
                   pra.completion_mode AS audit_completion_mode,
                   pra.output_mode AS audit_output_mode,
                   pra.created_at AS audit_created_at,
                   pra.updated_at AS audit_updated_at
            FROM audit_comments ac
            INNER JOIN pull_request_audits pra ON pra.id = ac.audit_id
            WHERE pra.repo_full = ? AND pra.pr_number = ? AND pra.head_sha = ?
            ORDER BY ac.posted_at DESC, ac.id DESC
            LIMIT 1
            """,
            (repo_full, pr_number, head_sha),
        ).fetchone()
    return _row_to_pr_comment_episode(row) if row is not None else None


def get_previous_audit_comment_episode_for_pr(
    db_path: str,
    repo_full: str,
    pr_number: int,
    head_sha: str,
) -> Optional[PrCommentEpisodeRecord]:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT ac.*, pra.repo_full, pra.pr_number, pra.head_sha,
                   pra.status AS audit_status,
                   pra.completion_mode AS audit_completion_mode,
                   pra.output_mode AS audit_output_mode,
                   pra.created_at AS audit_created_at,
                   pra.updated_at AS audit_updated_at
            FROM audit_comments ac
            INNER JOIN pull_request_audits pra ON pra.id = ac.audit_id
            WHERE pra.repo_full = ? AND pra.pr_number = ? AND pra.head_sha <> ?
            ORDER BY ac.posted_at DESC, ac.id DESC
            LIMIT 1
            """,
            (repo_full, pr_number, head_sha),
        ).fetchone()
    return _row_to_pr_comment_episode(row) if row is not None else None


def get_latest_audit_comment_for_pr(db_path: str, repo_full: str, pr_number: int) -> Optional[AuditCommentRecord]:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT ac.*
            FROM audit_comments ac
            INNER JOIN pull_request_audits pra ON pra.id = ac.audit_id
            WHERE pra.repo_full = ? AND pra.pr_number = ?
            ORDER BY ac.posted_at DESC, ac.id DESC
            LIMIT 1
            """,
            (repo_full, pr_number),
        ).fetchone()
    return _row_to_audit_comment(row) if row is not None else None


def list_pull_request_audits_for_repo(db_path: str, repo_full: str) -> list[PullRequestAuditRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM pull_request_audits WHERE repo_full = ? ORDER BY created_at ASC, id ASC",
            (repo_full,),
        ).fetchall()
    return [_row_to_pull_request_audit(row) for row in rows]


def list_artifact_history_for_repo(db_path: str, repo_full: str, artifact_path: str) -> list[ArtifactHistoryRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                pra.id AS audit_id,
                pra.job_id AS job_id,
                pra.repo_full AS repo_full,
                pra.pr_number AS pr_number,
                pra.head_sha AS head_sha,
                pra.status AS status,
                pra.completion_mode AS completion_mode,
                pra.output_mode AS output_mode,
                pra.deterministic_score AS deterministic_score,
                pra.suggested_risk_level AS suggested_risk_level,
                pra.fused_confidence AS fused_confidence,
                pra.semantic_review_completed AS semantic_review_completed,
                ca.artifact_path AS artifact_path,
                ca.artifact_type AS artifact_type,
                ca.context_mode AS context_mode,
                ca.changed_hunks AS changed_hunks,
                ca.added_count AS added_count,
                ca.removed_count AS removed_count,
                ca.created_at AS created_at
            FROM changed_artifacts ca
            INNER JOIN pull_request_audits pra ON pra.id = ca.audit_id
            WHERE pra.repo_full = ? AND ca.artifact_path = ?
            ORDER BY pra.created_at ASC, pra.id ASC, ca.id ASC
            """,
            (repo_full, artifact_path),
        ).fetchall()
    return [_row_to_artifact_history(row) for row in rows]


def list_findings_for_repo_artifact(db_path: str, repo_full: str, artifact_path: str) -> list[FindingRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT f.*
            FROM findings f
            INNER JOIN changed_artifacts ca ON ca.id = f.changed_artifact_id
            INNER JOIN pull_request_audits pra ON pra.id = f.audit_id
            WHERE pra.repo_full = ? AND ca.artifact_path = ?
            ORDER BY f.created_at ASC, f.id ASC
            """,
            (repo_full, artifact_path),
        ).fetchall()
    return [_row_to_finding(row) for row in rows]


def list_artifact_versions_for_repo_artifact(db_path: str, repo_full: str, artifact_path: str) -> list[ArtifactVersionRecord]:
    normalized_artifact_id = _build_normalized_artifact_id(repo_full, artifact_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM artifact_versions
            WHERE normalized_artifact_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (normalized_artifact_id,),
        ).fetchall()
    return [_row_to_artifact_version(row) for row in rows]


def get_latest_artifact_version_for_repo_artifact(db_path: str, repo_full: str, artifact_path: str) -> Optional[ArtifactVersionRecord]:
    normalized_artifact_id = _build_normalized_artifact_id(repo_full, artifact_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM artifact_versions
            WHERE normalized_artifact_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (normalized_artifact_id,),
        ).fetchone()
    return _row_to_artifact_version(row) if row is not None else None


def list_static_profiles_for_repo_artifact(db_path: str, repo_full: str, artifact_path: str) -> list[StaticArtifactProfileRecord]:
    normalized_artifact_id = _build_normalized_artifact_id(repo_full, artifact_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM static_artifact_profiles
            WHERE normalized_artifact_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (normalized_artifact_id,),
        ).fetchall()
    return [_row_to_static_artifact_profile(row) for row in rows]


def list_static_profiles_for_repo(db_path: str, repo_full: str) -> list[StaticArtifactProfileRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT sap.*
            FROM static_artifact_profiles sap
            INNER JOIN pull_request_audits pra ON pra.id = sap.audit_id
            WHERE pra.repo_full = ?
            ORDER BY sap.artifact_path ASC, sap.created_at ASC, sap.id ASC
            """,
            (repo_full,),
        ).fetchall()
    return [_row_to_static_artifact_profile(row) for row in rows]


def get_latest_static_profile_for_repo_artifact(db_path: str, repo_full: str, artifact_path: str) -> Optional[StaticArtifactProfileRecord]:
    normalized_artifact_id = _build_normalized_artifact_id(repo_full, artifact_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM static_artifact_profiles
            WHERE normalized_artifact_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (normalized_artifact_id,),
        ).fetchone()
    return _row_to_static_artifact_profile(row) if row is not None else None


def preview_static_drift_for_artifacts(
    db_path: str,
    repo_full: str,
    artifact_snapshots: dict[str, str],
    artifact_types_by_path: dict[str, str],
) -> list[StaticArtifactDriftPreview]:
    previews: list[StaticArtifactDriftPreview] = []
    for artifact_path, snapshot_text in artifact_snapshots.items():
        artifact_type = artifact_types_by_path.get(artifact_path, "generic")
        signal_terms = extract_signal_terms_from_text(snapshot_text)
        profile = build_attribute_profile(snapshot_text)
        onboarding_baseline = get_latest_onboarding_baseline_for_repo_artifact(
            db_path,
            repo_full,
            artifact_path,
            only_approved=True,
        )
        baseline_profile = None if onboarding_baseline is not None else get_latest_static_profile_for_repo_artifact(db_path, repo_full, artifact_path)

        baseline_profile_id: int | None = None
        baseline_provenance = no_baseline_provenance()
        semantic_similarity = 1.0
        semantic_distance = 0.0
        attribute_deltas: dict[str, float] = {
            "guardrail_robustness": 0.0,
            "capability_risk": 0.0,
            "autonomy_level": 0.0,
            "stability_vs_creativity": 0.0,
            "governance_strength": 0.0,
            "change_frequency": 0.0,
            "semantic_density": 0.0,
        }
        narrative = ["No approved baseline available; current profile will establish the first baseline."]

        if onboarding_baseline is not None:
            baseline_provenance = approved_onboarding_provenance(onboarding_baseline.id)
            semantic_similarity = _term_similarity(signal_terms, onboarding_baseline.signal_terms)
            drift_delta = compare_attribute_profiles(
                onboarding_baseline.profile,
                profile,
                semantic_similarity=semantic_similarity,
            )
            semantic_distance = drift_delta.semantic_distance
            attribute_deltas = drift_delta.attribute_deltas
            narrative = drift_delta.narrative
        elif baseline_profile is not None:
            baseline_profile_id = baseline_profile.id
            baseline_provenance = previous_pr_fallback_provenance(
                baseline_profile.id,
                baseline_profile.artifact_version_id,
            )
            semantic_similarity = _term_similarity(signal_terms, baseline_profile.signal_terms)
            drift_delta = compare_attribute_profiles(
                baseline_profile.profile,
                profile,
                semantic_similarity=semantic_similarity,
            )
            semantic_distance = drift_delta.semantic_distance
            attribute_deltas = drift_delta.attribute_deltas
            narrative = drift_delta.narrative

        previews.append(
            StaticArtifactDriftPreview(
                artifact_path=artifact_path,
                artifact_type=artifact_type,
                profile=profile,
                baseline_profile_id=baseline_profile_id,
                baseline_provenance=baseline_provenance,
                semantic_similarity=semantic_similarity,
                semantic_distance=semantic_distance,
                attribute_deltas=attribute_deltas,
                narrative=narrative,
                signal_terms=signal_terms,
            )
        )

    return previews


def get_repo_static_drift_summary(db_path: str, repo_full: str) -> RepoStaticDriftSummary:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT artifact_path, semantic_distance, attribute_deltas_json, baseline_provenance_json
            FROM historical_static_profiles
            WHERE normalized_artifact_id LIKE ?
            ORDER BY created_at ASC, id ASC
            """,
            (f"{repo_full.lower()}::%",),
        ).fetchall()

    profiles: list[dict[str, object]] = []
    for row in rows:
        attribute_deltas = json.loads(row["attribute_deltas_json"])
        baseline_provenance = baseline_provenance_from_json(row["baseline_provenance_json"])
        profiles.append(
            {
                "artifact_path": row["artifact_path"],
                "semantic_distance": float(row["semantic_distance"]),
                "attribute_deltas": attribute_deltas,
                "baseline_provenance": baseline_provenance,
            }
        )

    artifact_paths = {profile["artifact_path"] for profile in profiles}
    baseline_linked = [
        profile
        for profile in profiles
        if profile["baseline_provenance"] is not None
        and profile["baseline_provenance"].source_type != BASELINE_SOURCE_NONE
    ]
    avg_semantic_distance = _average([float(profile["semantic_distance"]) for profile in baseline_linked])
    avg_guardrail_shift = _average(
        [abs(float(profile["attribute_deltas"]["guardrail_robustness"])) for profile in baseline_linked]
    )
    avg_capability_shift = _average(
        [abs(float(profile["attribute_deltas"]["capability_risk"])) for profile in baseline_linked]
    )
    avg_autonomy_shift = _average(
        [abs(float(profile["attribute_deltas"]["autonomy_level"])) for profile in baseline_linked]
    )

    highest_capability_artifact_path: str | None = None
    highest_capability_delta = 0.0
    if baseline_linked:
        highest_capability = max(
            baseline_linked,
            key=lambda profile: float(profile["attribute_deltas"]["capability_risk"]),
        )
        highest_capability_artifact_path = str(highest_capability["artifact_path"])
        highest_capability_delta = round(float(highest_capability["attribute_deltas"]["capability_risk"]), 4)

    return RepoStaticDriftSummary(
        repo_full=repo_full,
        artifact_count=len(artifact_paths),
        profile_count=len(profiles),
        baseline_linked_profile_count=len(baseline_linked),
        avg_semantic_distance=avg_semantic_distance,
        avg_guardrail_shift=avg_guardrail_shift,
        avg_capability_shift=avg_capability_shift,
        avg_autonomy_shift=avg_autonomy_shift,
        highest_capability_artifact_path=highest_capability_artifact_path,
        highest_capability_delta=highest_capability_delta,
    )


def list_top_drifting_artifacts_for_repo(db_path: str, repo_full: str, *, limit: int = 10) -> list[ArtifactDriftLeaderboardEntry]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, artifact_path, artifact_type, semantic_distance, attribute_deltas_json, narrative_json, created_at
            FROM historical_static_profiles
            WHERE normalized_artifact_id LIKE ?
            ORDER BY created_at ASC, id ASC
            """,
            (f"{repo_full.lower()}::%",),
        ).fetchall()

    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(row["artifact_path"], []).append(
            {
                "id": int(row["id"]),
                "artifact_path": row["artifact_path"],
                "artifact_type": row["artifact_type"],
                "semantic_distance": float(row["semantic_distance"]),
                "attribute_deltas": json.loads(row["attribute_deltas_json"]),
                "narrative": json.loads(row["narrative_json"]),
                "created_at": float(row["created_at"]),
            }
        )

    leaderboard: list[ArtifactDriftLeaderboardEntry] = []
    for artifact_path, profiles in grouped.items():
        latest = profiles[-1]
        attribute_deltas = latest["attribute_deltas"]
        semantic_distance = float(latest["semantic_distance"])
        drift_magnitude = round(
            abs(float(attribute_deltas["guardrail_robustness"]))
            + abs(float(attribute_deltas["capability_risk"]))
            + abs(float(attribute_deltas["autonomy_level"]))
            + semantic_distance,
            4,
        )
        leaderboard.append(
            ArtifactDriftLeaderboardEntry(
                artifact_path=artifact_path,
                artifact_type=str(latest["artifact_type"]),
                latest_profile_id=int(latest["id"]),
                sample_count=len(profiles),
                latest_created_at=float(latest["created_at"]),
                semantic_distance=semantic_distance,
                guardrail_shift=round(float(attribute_deltas["guardrail_robustness"]), 4),
                capability_shift=round(float(attribute_deltas["capability_risk"]), 4),
                autonomy_shift=round(float(attribute_deltas["autonomy_level"]), 4),
                drift_magnitude=drift_magnitude,
                narrative=list(latest["narrative"]),
            )
        )

    leaderboard.sort(key=lambda entry: (-entry.drift_magnitude, entry.artifact_path))
    return leaderboard[:limit]


def _row_to_pull_request_audit(row: sqlite3.Row) -> PullRequestAuditRecord:
    fused_confidence = row["fused_confidence"] if "fused_confidence" in row.keys() else None
    pr_feedback_mode = row["pr_feedback_mode"] if "pr_feedback_mode" in row.keys() else PR_FEEDBACK_MODE_COMMENTS
    return PullRequestAuditRecord(
        id=row["id"],
        job_id=row["job_id"],
        repo_full=row["repo_full"],
        pr_number=row["pr_number"],
        pr_title=row["pr_title"] if "pr_title" in row.keys() else None,
        installation_id=row["installation_id"],
        head_sha=row["head_sha"],
        pr_state=row["pr_state"],
        pr_merged=(bool(row["pr_merged"]) if row["pr_merged"] is not None else None),
        pr_closed_at=row["pr_closed_at"],
        pr_merged_at=row["pr_merged_at"],
        pr_merge_commit_sha=row["pr_merge_commit_sha"],
        pr_updated_at=row["pr_updated_at"],
        status=row["status"],
        completion_mode=row["completion_mode"],
        output_mode=row["output_mode"],
        pr_feedback_mode=normalize_pr_feedback_mode(pr_feedback_mode),
        deterministic_score=row["deterministic_score"],
        suggested_risk_level=row["suggested_risk_level"],
        fused_confidence=fused_confidence,
        semantic_review_completed=bool(row["semantic_review_completed"]),
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _derive_audit_recommendation_lane(db_path: str, audit_id: int) -> str | None:
    audit_comment = get_audit_comment_for_audit(db_path, audit_id)
    if audit_comment is None:
        return None
    if audit_comment.comment_mode == "review_request_changes":
        return "escalated"
    if audit_comment.comment_mode in {"review_comment", "full_review"}:
        return "normal"
    return None


def _derive_pr_outcome_kind(
    audit: PullRequestAuditRecord,
    *,
    pr_merged: bool | None,
    recommendation_lane: str | None,
) -> str:
    if recommendation_lane == "escalated" and pr_merged:
        return "recommendation_ignored"
    if recommendation_lane == "escalated" and pr_merged is False:
        return "aligned_reject"
    if recommendation_lane == "normal" and pr_merged:
        return "aligned_merge"
    return "unknown"


def _record_github_reaction_feedback_event(db_path: str, audit_id: int, reaction: object) -> AuditFeedbackEventRecord:
    return record_audit_feedback_event(
        db_path,
        audit_id=audit_id,
        kind="reaction",
        source="github_reaction",
        actor_github_id=getattr(reaction, "user_id", None),
        actor_github_login=getattr(reaction, "user_login", None),
        event_key=_reaction_event_key(audit_id, reaction),
        payload_json=json.dumps(
            {
                "reaction_id": getattr(reaction, "reaction_id", None),
                "content": getattr(reaction, "content", None),
                "target_kind": getattr(reaction, "target_kind", None),
                "target_id": getattr(reaction, "target_id", None),
            }
        ),
        created_at=getattr(reaction, "created_at", None),
    )


def _reaction_event_key(audit_id: int, reaction: object) -> str:
    reaction_id = getattr(reaction, "reaction_id", None)
    if reaction_id:
        return f"reaction:{audit_id}:{reaction_id}"
    return ":".join(
        [
            "reaction",
            str(audit_id),
            str(getattr(reaction, "target_kind", "unknown")),
            str(getattr(reaction, "target_id", "0")),
            str(getattr(reaction, "user_id", "unknown")),
            str(getattr(reaction, "content", "unknown")),
        ]
    )


def _normalize_pr_lifecycle_fields(
    *,
    pr_state: str | None,
    pr_merged: bool | None,
    pr_closed_at: float | None,
    pr_merged_at: float | None,
    pr_merge_commit_sha: str | None,
    pr_updated_at: float | None,
) -> tuple[str | None, int | None, float | None, float | None, str | None, float | None]:
    normalized_pr_merged = int(pr_merged) if pr_merged is not None else None
    normalized_pr_closed_at = pr_closed_at
    normalized_pr_merged_at = pr_merged_at

    if pr_state == "open" and pr_merged is False:
        normalized_pr_closed_at = None
        normalized_pr_merged_at = None

    return (
        pr_state,
        normalized_pr_merged,
        normalized_pr_closed_at,
        normalized_pr_merged_at,
        pr_merge_commit_sha,
        pr_updated_at,
    )


def update_pull_request_audit_state(
    db_path: str,
    *,
    repo_full: str,
    pr_number: int,
    head_sha: str | None,
    pr_title: str | None,
    pr_state: str | None,
    pr_merged: bool | None,
    pr_closed_at: float | None,
    pr_merged_at: float | None,
    pr_merge_commit_sha: str | None,
    pr_updated_at: float | None,
) -> None:
    (
        pr_state,
        pr_merged_value,
        pr_closed_at,
        pr_merged_at,
        pr_merge_commit_sha,
        pr_updated_at,
    ) = _normalize_pr_lifecycle_fields(
        pr_state=pr_state,
        pr_merged=pr_merged,
        pr_closed_at=pr_closed_at,
        pr_merged_at=pr_merged_at,
        pr_merge_commit_sha=pr_merge_commit_sha,
        pr_updated_at=pr_updated_at,
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE pull_request_audits
            SET pr_title = ?,
                pr_state = ?,
                pr_merged = ?,
                pr_closed_at = ?,
                pr_merged_at = ?,
                pr_merge_commit_sha = ?,
                pr_updated_at = ?,
                updated_at = ?
            WHERE repo_full = ? AND pr_number = ?
            """,
            (
                pr_title,
                pr_state,
                pr_merged_value,
                pr_closed_at,
                pr_merged_at,
                pr_merge_commit_sha,
                pr_updated_at,
                time.time(),
                repo_full,
                pr_number,
            ),
        )


def _row_to_changed_artifact(row: sqlite3.Row) -> ChangedArtifactRecord:
    return ChangedArtifactRecord(
        id=row["id"],
        audit_id=row["audit_id"],
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        context_mode=row["context_mode"],
        relevance_reason=row["relevance_reason"],
        changed_hunks=row["changed_hunks"],
        added_count=row["added_count"],
        removed_count=row["removed_count"],
        created_at=row["created_at"],
    )


def _row_to_finding(row: sqlite3.Row) -> FindingRecord:
    return FindingRecord(
        id=row["id"],
        audit_id=row["audit_id"],
        changed_artifact_id=row["changed_artifact_id"],
        source=row["source"],
        rule_id=row["rule_id"],
        title=row["title"],
        severity=row["severity"],
        rationale=row["rationale"],
        evidence=json.loads(row["evidence_json"]),
        created_at=row["created_at"],
    )


def _row_to_audit_comment(row: sqlite3.Row) -> AuditCommentRecord:
    github_review_id = row["github_review_id"] if "github_review_id" in row.keys() else None
    return AuditCommentRecord(
        id=row["id"],
        audit_id=row["audit_id"],
        github_comment_id=row["github_comment_id"],
        github_review_id=github_review_id,
        comment_mode=row["comment_mode"],
        comment_body=row["comment_body"],
        posted_at=row["posted_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_audit_feedback_event(row: sqlite3.Row) -> AuditFeedbackEventRecord:
    return AuditFeedbackEventRecord(
        id=row["id"],
        audit_id=row["audit_id"],
        repo_full=row["repo_full"],
        pr_number=row["pr_number"],
        head_sha=row["head_sha"],
        kind=row["kind"],
        source=row["source"],
        actor_github_id=row["actor_github_id"],
        actor_github_login=row["actor_github_login"],
        event_key=row["event_key"],
        payload_json=row["payload_json"],
        created_at=row["created_at"],
    )


def _row_to_pr_comment_episode(row: sqlite3.Row) -> PrCommentEpisodeRecord:
    return PrCommentEpisodeRecord(
        audit_comment=_row_to_audit_comment(row),
        repo_full=row["repo_full"],
        pr_number=row["pr_number"],
        head_sha=row["head_sha"],
        audit_status=row["audit_status"],
        audit_completion_mode=row["audit_completion_mode"],
        audit_output_mode=row["audit_output_mode"],
        audit_created_at=row["audit_created_at"],
        audit_updated_at=row["audit_updated_at"],
    )


def _row_to_artifact_history(row: sqlite3.Row) -> ArtifactHistoryRecord:
    return ArtifactHistoryRecord(
        audit_id=row["audit_id"],
        job_id=row["job_id"],
        repo_full=row["repo_full"],
        pr_number=row["pr_number"],
        head_sha=row["head_sha"],
        status=row["status"],
        completion_mode=row["completion_mode"],
        output_mode=row["output_mode"],
        deterministic_score=row["deterministic_score"],
        suggested_risk_level=row["suggested_risk_level"],
        fused_confidence=row["fused_confidence"],
        semantic_review_completed=bool(row["semantic_review_completed"]),
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        context_mode=row["context_mode"],
        changed_hunks=row["changed_hunks"],
        added_count=row["added_count"],
        removed_count=row["removed_count"],
        created_at=row["created_at"],
    )


def _row_to_artifact_version(row: sqlite3.Row) -> ArtifactVersionRecord:
    return ArtifactVersionRecord(
        id=row["id"],
        audit_id=row["audit_id"],
        changed_artifact_id=row["changed_artifact_id"],
        normalized_artifact_id=row["normalized_artifact_id"],
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        version_hash=row["version_hash"],
        signal_terms=json.loads(row["signal_terms_json"]),
        line_count=row["line_count"],
        previous_version_id=row["previous_version_id"],
        created_at=row["created_at"],
        content_text=row["content_text"],
    )


def _row_to_static_artifact_profile(row: sqlite3.Row) -> StaticArtifactProfileRecord:
    baseline_provenance = baseline_provenance_from_json(row["baseline_provenance_json"])
    if baseline_provenance is None and row["baseline_profile_id"] is not None:
        baseline_provenance = previous_pr_fallback_provenance(
            row["baseline_profile_id"],
            row["artifact_version_id"],
        )
    if baseline_provenance is None:
        baseline_provenance = no_baseline_provenance()

    return StaticArtifactProfileRecord(
        id=row["id"],
        audit_id=row["audit_id"],
        changed_artifact_id=row["changed_artifact_id"],
        artifact_version_id=row["artifact_version_id"],
        normalized_artifact_id=row["normalized_artifact_id"],
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        profile=_profile_from_json(row["profile_json"]),
        baseline_profile_id=row["baseline_profile_id"],
        baseline_provenance=baseline_provenance,
        semantic_similarity=row["semantic_similarity"],
        semantic_distance=row["semantic_distance"],
        attribute_deltas={key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()},
        narrative=json.loads(row["narrative_json"]),
        signal_terms=json.loads(row["signal_terms_json"]),
        created_at=row["created_at"],
    )


def _profile_from_json(profile_json: str) -> AgentAttributeProfile:
    payload = json.loads(profile_json)
    signal_payload = payload["signals"]
    return AgentAttributeProfile(
        guardrail_robustness=float(payload["guardrail_robustness"]),
        capability_risk=float(payload["capability_risk"]),
        autonomy_level=float(payload["autonomy_level"]),
        stability_vs_creativity=float(payload["stability_vs_creativity"]),
        governance_strength=float(payload["governance_strength"]),
        change_frequency=float(payload["change_frequency"]),
        semantic_density=float(payload["semantic_density"]),
        signals=StaticSignals(
            token_count=int(signal_payload["token_count"]),
            char_count=int(signal_payload["char_count"]),
            section_count=int(signal_payload["section_count"]),
            example_count=int(signal_payload["example_count"]),
            instruction_density=float(signal_payload["instruction_density"]),
            constraint_count=int(signal_payload["constraint_count"]),
            explicit_limit_count=int(signal_payload["explicit_limit_count"]),
            ambiguity_count=int(signal_payload["ambiguity_count"]),
            guardrail_counts={key: int(value) for key, value in signal_payload.get("guardrail_counts", {}).items()},
            write_signal_count=int(signal_payload.get("write_signal_count", 0)),
            read_signal_count=int(signal_payload.get("read_signal_count", 0)),
            sensitive_tool_count=int(signal_payload.get("sensitive_tool_count", 0)),
            prod_signal_count=int(signal_payload.get("prod_signal_count", 0)),
            sandbox_signal_count=int(signal_payload.get("sandbox_signal_count", 0)),
            systems_touched_count=int(signal_payload.get("systems_touched_count", 0)),
            human_review_count=int(signal_payload.get("human_review_count", 0)),
            parallelism_signal_count=int(signal_payload.get("parallelism_signal_count", 0)),
            max_steps=int(signal_payload.get("max_steps", 0)),
            temperature=(float(signal_payload["temperature"]) if signal_payload.get("temperature") is not None else None),
            top_p=(float(signal_payload["top_p"]) if signal_payload.get("top_p") is not None else None),
        ),
    )


def _term_similarity(left: list[str], right: list[str]) -> float:
    left_set = {item.lower() for item in left}
    right_set = {item.lower() for item in right}
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return round(len(left_set & right_set) / len(left_set | right_set), 4)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _drift_magnitude(profile: StaticArtifactProfileRecord) -> float:
    return round(
        abs(profile.attribute_deltas["guardrail_robustness"])
        + abs(profile.attribute_deltas["capability_risk"])
        + abs(profile.attribute_deltas["autonomy_level"])
        + profile.semantic_distance,
        4,
    )


def _build_normalized_artifact_id(repo_full: str, artifact_path: str) -> str:
    return f"{repo_full.lower()}::{artifact_path.lower()}"
