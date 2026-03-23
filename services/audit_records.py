from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from engine.analysis import DiffAnalysis
from engine.diff_parser import extract_signal_terms_from_text
from engine.drift_profile import AgentAttributeProfile, StaticSignals, build_attribute_profile, compare_attribute_profiles
from .baseline_provenance import (
    BASELINE_SOURCE_NONE,
    BaselineProvenance,
    approved_onboarding_provenance,
    baseline_provenance_from_json,
    baseline_provenance_to_json,
    no_baseline_provenance,
    previous_pr_fallback_provenance,
)
from .onboarding_records import get_latest_onboarding_baseline_for_repo_artifact


@dataclass(frozen=True)
class PullRequestAuditRecord:
    id: int
    job_id: int
    repo_full: str
    pr_number: int
    installation_id: int
    head_sha: str
    status: str
    completion_mode: str
    output_mode: str
    deterministic_score: int
    suggested_risk_level: str
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
    comment_mode: str
    comment_body: str
    posted_at: float
    created_at: float
    updated_at: float


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
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_audit_record_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pull_request_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                repo_full TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                installation_id INTEGER NOT NULL,
                head_sha TEXT NOT NULL,
                status TEXT NOT NULL,
                completion_mode TEXT NOT NULL,
                output_mode TEXT NOT NULL,
                deterministic_score INTEGER NOT NULL,
                suggested_risk_level TEXT NOT NULL,
                semantic_review_completed INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(repo_full, pr_number, head_sha)
            )
            """
        )
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
                comment_mode TEXT NOT NULL,
                comment_body TEXT NOT NULL,
                posted_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(audit_id) REFERENCES pull_request_audits(id) ON DELETE CASCADE
            )
            """
        )
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_source_severity ON findings(source, severity)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_rule_id ON findings(rule_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_versions_normalized_id ON artifact_versions(normalized_artifact_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_versions_hash ON artifact_versions(version_hash)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_static_artifact_profiles_normalized_id ON static_artifact_profiles(normalized_artifact_id, created_at)"
        )

        audit_comments_columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_comments)").fetchall()}
        if "github_comment_id" not in audit_comments_columns:
            conn.execute("ALTER TABLE audit_comments ADD COLUMN github_comment_id INTEGER")

        static_profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(static_artifact_profiles)").fetchall()}
        if "baseline_provenance_json" not in static_profile_columns:
            conn.execute("ALTER TABLE static_artifact_profiles ADD COLUMN baseline_provenance_json TEXT")


def record_audit_result(
    db_path: str,
    *,
    job_id: int,
    repo_full: str,
    pr_number: int,
    installation_id: int,
    head_sha: str,
    deterministic_analysis: DiffAnalysis,
    status: str,
    completion_mode: str,
    output_mode: str,
    comment_body: str | None,
    comment_mode: str | None,
    semantic_review_completed: bool,
    error_message: str | None = None,
    artifact_snapshots: dict[str, str] | None = None,
    github_comment_id: int | None = None,
) -> PullRequestAuditRecord:
    now = time.time()
    artifact_snapshots = artifact_snapshots or {}
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM pull_request_audits WHERE job_id = ?",
            (job_id,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO pull_request_audits (
                    job_id, repo_full, pr_number, installation_id, head_sha,
                    status, completion_mode, output_mode,
                    deterministic_score, suggested_risk_level, semantic_review_completed,
                    error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    repo_full,
                    pr_number,
                    installation_id,
                    head_sha,
                    status,
                    completion_mode,
                    output_mode,
                    deterministic_analysis.deterministic_score,
                    deterministic_analysis.suggested_risk_level.value,
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
                    installation_id = ?,
                    head_sha = ?,
                    status = ?,
                    completion_mode = ?,
                    output_mode = ?,
                    deterministic_score = ?,
                    suggested_risk_level = ?,
                    semantic_review_completed = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    repo_full,
                    pr_number,
                    installation_id,
                    head_sha,
                    status,
                    completion_mode,
                    output_mode,
                    deterministic_analysis.deterministic_score,
                    deterministic_analysis.suggested_risk_level.value,
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
                        version_hash, signal_terms_json, line_count, previous_version_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                onboarding_baseline = get_latest_onboarding_baseline_for_repo_artifact(db_path, repo_full, artifact.relevance.path)

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
                        audit_id, github_comment_id, comment_mode, comment_body, posted_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (audit_id, github_comment_id, comment_mode, comment_body, now, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE audit_comments
                    SET github_comment_id = ?,
                        comment_mode = ?,
                        comment_body = ?,
                        posted_at = ?,
                        updated_at = ?
                    WHERE audit_id = ?
                    """,
                    (github_comment_id, comment_mode, comment_body, now, now, audit_id),
                )
        else:
            conn.execute("DELETE FROM audit_comments WHERE audit_id = ?", (audit_id,))

        row = conn.execute("SELECT * FROM pull_request_audits WHERE id = ?", (audit_id,)).fetchone()

    if row is None:
        raise RuntimeError("Failed to store or reload pull request audit record.")
    return _row_to_pull_request_audit(row)


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
        onboarding_baseline = get_latest_onboarding_baseline_for_repo_artifact(db_path, repo_full, artifact_path)
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
            SELECT sap.*
            FROM static_artifact_profiles sap
            INNER JOIN pull_request_audits pra ON pra.id = sap.audit_id
            WHERE pra.repo_full = ?
            ORDER BY sap.created_at ASC, sap.id ASC
            """,
            (repo_full,),
        ).fetchall()

    profiles = [_row_to_static_artifact_profile(row) for row in rows]
    artifact_paths = {profile.artifact_path for profile in profiles}
    baseline_linked = [
        profile
        for profile in profiles
        if profile.baseline_provenance is not None and profile.baseline_provenance.source_type != BASELINE_SOURCE_NONE
    ]
    avg_semantic_distance = _average([profile.semantic_distance for profile in baseline_linked])
    avg_guardrail_shift = _average([abs(profile.attribute_deltas["guardrail_robustness"]) for profile in baseline_linked])
    avg_capability_shift = _average([abs(profile.attribute_deltas["capability_risk"]) for profile in baseline_linked])
    avg_autonomy_shift = _average([abs(profile.attribute_deltas["autonomy_level"]) for profile in baseline_linked])

    highest_capability_artifact_path: str | None = None
    highest_capability_delta = 0.0
    if baseline_linked:
        highest_capability = max(baseline_linked, key=lambda profile: profile.attribute_deltas["capability_risk"])
        highest_capability_artifact_path = highest_capability.artifact_path
        highest_capability_delta = round(highest_capability.attribute_deltas["capability_risk"], 4)

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
            SELECT sap.*
            FROM static_artifact_profiles sap
            INNER JOIN pull_request_audits pra ON pra.id = sap.audit_id
            WHERE pra.repo_full = ?
            ORDER BY sap.created_at ASC, sap.id ASC
            """,
            (repo_full,),
        ).fetchall()

    grouped: dict[str, list[StaticArtifactProfileRecord]] = {}
    for profile in (_row_to_static_artifact_profile(row) for row in rows):
        grouped.setdefault(profile.artifact_path, []).append(profile)

    leaderboard: list[ArtifactDriftLeaderboardEntry] = []
    for artifact_path, profiles in grouped.items():
        latest = profiles[-1]
        leaderboard.append(
            ArtifactDriftLeaderboardEntry(
                artifact_path=artifact_path,
                artifact_type=latest.artifact_type,
                latest_profile_id=latest.id,
                sample_count=len(profiles),
                latest_created_at=latest.created_at,
                semantic_distance=latest.semantic_distance,
                guardrail_shift=round(latest.attribute_deltas["guardrail_robustness"], 4),
                capability_shift=round(latest.attribute_deltas["capability_risk"], 4),
                autonomy_shift=round(latest.attribute_deltas["autonomy_level"], 4),
                drift_magnitude=_drift_magnitude(latest),
                narrative=latest.narrative,
            )
        )

    leaderboard.sort(key=lambda entry: (-entry.drift_magnitude, entry.artifact_path))
    return leaderboard[:limit]


def _row_to_pull_request_audit(row: sqlite3.Row) -> PullRequestAuditRecord:
    return PullRequestAuditRecord(
        id=row["id"],
        job_id=row["job_id"],
        repo_full=row["repo_full"],
        pr_number=row["pr_number"],
        installation_id=row["installation_id"],
        head_sha=row["head_sha"],
        status=row["status"],
        completion_mode=row["completion_mode"],
        output_mode=row["output_mode"],
        deterministic_score=row["deterministic_score"],
        suggested_risk_level=row["suggested_risk_level"],
        semantic_review_completed=bool(row["semantic_review_completed"]),
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
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
    return AuditCommentRecord(
        id=row["id"],
        audit_id=row["audit_id"],
        github_comment_id=row["github_comment_id"],
        comment_mode=row["comment_mode"],
        comment_body=row["comment_body"],
        posted_at=row["posted_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
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