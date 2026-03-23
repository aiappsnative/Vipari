from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from engine.drift_profile import AgentAttributeProfile, StaticSignals, compare_attribute_profiles
from .baseline_provenance import (
    BaselineProvenance,
    approved_onboarding_provenance,
    baseline_provenance_from_json,
    baseline_provenance_to_json,
    historical_fallback_provenance,
    no_baseline_provenance,
)


@dataclass(frozen=True)
class DiscoveredArtifactInput:
    artifact_path: str
    artifact_type: str
    discovery_reason: str
    confidence: float
    baseline_content: str


@dataclass(frozen=True)
class HistoricalBackfillJobInput:
    onboarded_artifact_id: int
    artifact_path: str
    artifact_type: str
    commit_shas: list[str]


@dataclass(frozen=True)
class HistoricalArtifactSnapshotInput:
    commit_sha: str
    content: str


@dataclass(frozen=True)
class RepositoryOnboardingRecord:
    id: int
    repo_full: str
    installation_id: int
    default_branch: str
    status: str
    discovered_artifact_count: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class OnboardedArtifactRecord:
    id: int
    onboarding_id: int
    repo_full: str
    artifact_path: str
    artifact_type: str
    discovery_reason: str
    confidence: float
    created_at: float


@dataclass(frozen=True)
class OnboardingBaselineVersionRecord:
    id: int
    onboarding_id: int
    onboarded_artifact_id: int
    normalized_artifact_id: str
    artifact_path: str
    artifact_type: str
    version_hash: str
    signal_terms: list[str]
    line_count: int
    profile: AgentAttributeProfile
    created_at: float


@dataclass(frozen=True)
class HistoricalBackfillJobRecord:
    id: int
    onboarding_id: int
    onboarded_artifact_id: int
    repo_full: str
    artifact_path: str
    artifact_type: str
    status: str
    commit_count: int
    completed_commit_count: int
    commit_shas: list[str]
    last_error: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class HistoricalArtifactVersionRecord:
    id: int
    backfill_job_id: int
    onboarding_id: int
    onboarded_artifact_id: int
    normalized_artifact_id: str
    artifact_path: str
    artifact_type: str
    commit_sha: str
    version_hash: str
    signal_terms: list[str]
    line_count: int
    previous_version_id: int | None
    created_at: float


@dataclass(frozen=True)
class HistoricalStaticProfileRecord:
    id: int
    backfill_job_id: int
    historical_artifact_version_id: int
    onboarding_id: int
    onboarded_artifact_id: int
    normalized_artifact_id: str
    artifact_path: str
    artifact_type: str
    commit_sha: str
    profile: AgentAttributeProfile
    baseline_profile_id: int | None
    baseline_provenance: BaselineProvenance | None
    semantic_similarity: float
    semantic_distance: float
    attribute_deltas: dict[str, float]
    narrative: list[str]
    signal_terms: list[str]
    created_at: float


def _connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_onboarding_record_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repository_onboardings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full TEXT NOT NULL,
                installation_id INTEGER NOT NULL,
                default_branch TEXT NOT NULL,
                status TEXT NOT NULL,
                discovered_artifact_count INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS onboarded_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                onboarding_id INTEGER NOT NULL,
                repo_full TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                discovery_reason TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(onboarding_id) REFERENCES repository_onboardings(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS onboarding_baseline_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                onboarding_id INTEGER NOT NULL,
                onboarded_artifact_id INTEGER NOT NULL,
                normalized_artifact_id TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                version_hash TEXT NOT NULL,
                signal_terms_json TEXT NOT NULL,
                line_count INTEGER NOT NULL,
                profile_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(onboarding_id) REFERENCES repository_onboardings(id) ON DELETE CASCADE,
                FOREIGN KEY(onboarded_artifact_id) REFERENCES onboarded_artifacts(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_backfill_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                onboarding_id INTEGER NOT NULL,
                onboarded_artifact_id INTEGER NOT NULL,
                repo_full TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                status TEXT NOT NULL,
                commit_count INTEGER NOT NULL,
                completed_commit_count INTEGER NOT NULL DEFAULT 0,
                commit_shas_json TEXT NOT NULL,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(onboarding_id) REFERENCES repository_onboardings(id) ON DELETE CASCADE,
                FOREIGN KEY(onboarded_artifact_id) REFERENCES onboarded_artifacts(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_artifact_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backfill_job_id INTEGER NOT NULL,
                onboarding_id INTEGER NOT NULL,
                onboarded_artifact_id INTEGER NOT NULL,
                normalized_artifact_id TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                version_hash TEXT NOT NULL,
                signal_terms_json TEXT NOT NULL,
                line_count INTEGER NOT NULL,
                previous_version_id INTEGER,
                created_at REAL NOT NULL,
                FOREIGN KEY(backfill_job_id) REFERENCES historical_backfill_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(onboarding_id) REFERENCES repository_onboardings(id) ON DELETE CASCADE,
                FOREIGN KEY(onboarded_artifact_id) REFERENCES onboarded_artifacts(id) ON DELETE CASCADE,
                FOREIGN KEY(previous_version_id) REFERENCES historical_artifact_versions(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_static_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backfill_job_id INTEGER NOT NULL,
                historical_artifact_version_id INTEGER NOT NULL,
                onboarding_id INTEGER NOT NULL,
                onboarded_artifact_id INTEGER NOT NULL,
                normalized_artifact_id TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                baseline_profile_id INTEGER,
                baseline_provenance_json TEXT,
                semantic_similarity REAL NOT NULL,
                semantic_distance REAL NOT NULL,
                attribute_deltas_json TEXT NOT NULL,
                narrative_json TEXT NOT NULL,
                signal_terms_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(backfill_job_id) REFERENCES historical_backfill_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(historical_artifact_version_id) REFERENCES historical_artifact_versions(id) ON DELETE CASCADE,
                FOREIGN KEY(onboarding_id) REFERENCES repository_onboardings(id) ON DELETE CASCADE,
                FOREIGN KEY(onboarded_artifact_id) REFERENCES onboarded_artifacts(id) ON DELETE CASCADE,
                FOREIGN KEY(baseline_profile_id) REFERENCES historical_static_profiles(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repository_onboardings_repo_created ON repository_onboardings(repo_full, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_onboarded_artifacts_repo_path ON onboarded_artifacts(repo_full, artifact_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_onboarding_baselines_normalized_id ON onboarding_baseline_versions(normalized_artifact_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_historical_backfill_jobs_repo_path ON historical_backfill_jobs(repo_full, artifact_path, created_at)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_historical_artifact_versions_normalized_id ON historical_artifact_versions(normalized_artifact_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_historical_static_profiles_normalized_id ON historical_static_profiles(normalized_artifact_id, created_at)"
        )

        historical_backfill_columns = {row["name"] for row in conn.execute("PRAGMA table_info(historical_backfill_jobs)").fetchall()}
        if "completed_commit_count" not in historical_backfill_columns:
            conn.execute("ALTER TABLE historical_backfill_jobs ADD COLUMN completed_commit_count INTEGER NOT NULL DEFAULT 0")
        if "last_error" not in historical_backfill_columns:
            conn.execute("ALTER TABLE historical_backfill_jobs ADD COLUMN last_error TEXT")

        historical_profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(historical_static_profiles)").fetchall()}
        if "baseline_provenance_json" not in historical_profile_columns:
            conn.execute("ALTER TABLE historical_static_profiles ADD COLUMN baseline_provenance_json TEXT")


def record_repository_onboarding(
    db_path: str,
    *,
    repo_full: str,
    installation_id: int,
    default_branch: str,
    status: str,
    discovered_artifacts: list[DiscoveredArtifactInput],
    extract_signal_terms_fn,
    build_profile_fn,
) -> RepositoryOnboardingRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repository_onboardings (
                repo_full, installation_id, default_branch, status, discovered_artifact_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_full, installation_id, default_branch, status, len(discovered_artifacts), now, now),
        )
        onboarding_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        for artifact in discovered_artifacts:
            cursor = conn.execute(
                """
                INSERT INTO onboarded_artifacts (
                    onboarding_id, repo_full, artifact_path, artifact_type, discovery_reason, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    onboarding_id,
                    repo_full,
                    artifact.artifact_path,
                    artifact.artifact_type,
                    artifact.discovery_reason,
                    artifact.confidence,
                    now,
                ),
            )
            onboarded_artifact_id = int(cursor.lastrowid)
            signal_terms = extract_signal_terms_fn(artifact.baseline_content)
            profile = build_profile_fn(artifact.baseline_content)
            conn.execute(
                """
                INSERT INTO onboarding_baseline_versions (
                    onboarding_id, onboarded_artifact_id, normalized_artifact_id, artifact_path, artifact_type,
                    version_hash, signal_terms_json, line_count, profile_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    onboarding_id,
                    onboarded_artifact_id,
                    _build_normalized_artifact_id(repo_full, artifact.artifact_path),
                    artifact.artifact_path,
                    artifact.artifact_type,
                    hashlib.sha256(artifact.baseline_content.encode("utf-8")).hexdigest(),
                    json.dumps(signal_terms),
                    len([line for line in artifact.baseline_content.splitlines() if line.strip()]),
                    json.dumps(_profile_to_json(profile)),
                    now,
                ),
            )

        row = conn.execute("SELECT * FROM repository_onboardings WHERE id = ?", (onboarding_id,)).fetchone()
    if row is None:
        raise RuntimeError("Failed to store onboarding record.")
    return _row_to_repository_onboarding(row)


def get_latest_repository_onboarding(db_path: str, repo_full: str) -> RepositoryOnboardingRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM repository_onboardings
            WHERE repo_full = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (repo_full,),
        ).fetchone()
    return _row_to_repository_onboarding(row) if row is not None else None


def list_latest_repository_onboardings(db_path: str) -> list[RepositoryOnboardingRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM repository_onboardings ORDER BY repo_full ASC, created_at DESC, id DESC"
        ).fetchall()

    latest_by_repo: dict[str, sqlite3.Row] = {}
    for row in rows:
        repo_full = row["repo_full"]
        if repo_full in latest_by_repo:
            continue
        latest_by_repo[repo_full] = row

    return [_row_to_repository_onboarding(latest_by_repo[repo_full]) for repo_full in sorted(latest_by_repo)]


def list_onboarded_artifacts_for_onboarding(db_path: str, onboarding_id: int) -> list[OnboardedArtifactRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM onboarded_artifacts WHERE onboarding_id = ? ORDER BY artifact_path ASC, id ASC",
            (onboarding_id,),
        ).fetchall()
    return [_row_to_onboarded_artifact(row) for row in rows]


def list_onboarding_baseline_versions_for_onboarding(db_path: str, onboarding_id: int) -> list[OnboardingBaselineVersionRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM onboarding_baseline_versions WHERE onboarding_id = ? ORDER BY artifact_path ASC, id ASC",
            (onboarding_id,),
        ).fetchall()
    return [_row_to_onboarding_baseline_version(row) for row in rows]


def get_latest_onboarding_baseline_for_repo_artifact(
    db_path: str,
    repo_full: str,
    artifact_path: str,
) -> OnboardingBaselineVersionRecord | None:
    normalized_artifact_id = _build_normalized_artifact_id(repo_full, artifact_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM onboarding_baseline_versions
            WHERE normalized_artifact_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (normalized_artifact_id,),
        ).fetchone()
    return _row_to_onboarding_baseline_version(row) if row is not None else None


def create_historical_backfill_jobs(
    db_path: str,
    *,
    onboarding_id: int,
    repo_full: str,
    jobs: list[HistoricalBackfillJobInput],
    status: str = "planned",
) -> list[HistoricalBackfillJobRecord]:
    now = time.time()
    created: list[HistoricalBackfillJobRecord] = []
    with _connect(db_path) as conn:
        for job in jobs:
            conn.execute(
                """
                INSERT INTO historical_backfill_jobs (
                    onboarding_id, onboarded_artifact_id, repo_full, artifact_path, artifact_type,
                    status, commit_count, completed_commit_count, commit_shas_json, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    onboarding_id,
                    job.onboarded_artifact_id,
                    repo_full,
                    job.artifact_path,
                    job.artifact_type,
                    status,
                    len(job.commit_shas),
                    0,
                    json.dumps(job.commit_shas),
                    None,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM historical_backfill_jobs WHERE id = last_insert_rowid()").fetchone()
            if row is not None:
                created.append(_row_to_historical_backfill_job(row))
    return created


def list_historical_backfill_jobs_for_repo(db_path: str, repo_full: str) -> list[HistoricalBackfillJobRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM historical_backfill_jobs WHERE repo_full = ? ORDER BY created_at ASC, id ASC",
            (repo_full,),
        ).fetchall()
    return [_row_to_historical_backfill_job(row) for row in rows]


def get_historical_backfill_job(db_path: str, job_id: int) -> HistoricalBackfillJobRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM historical_backfill_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_historical_backfill_job(row) if row is not None else None


def update_historical_backfill_job_status(
    db_path: str,
    *,
    job_id: int,
    status: str,
    completed_commit_count: int | None = None,
    last_error: str | None = None,
) -> HistoricalBackfillJobRecord:
    with _connect(db_path) as conn:
        if completed_commit_count is None:
            conn.execute(
                """
                UPDATE historical_backfill_jobs
                SET status = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, last_error, time.time(), job_id),
            )
        else:
            conn.execute(
                """
                UPDATE historical_backfill_jobs
                SET status = ?,
                    completed_commit_count = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, completed_commit_count, last_error, time.time(), job_id),
            )
        row = conn.execute("SELECT * FROM historical_backfill_jobs WHERE id = ?", (job_id,)).fetchone()

    if row is None:
        raise RuntimeError("Failed to update historical backfill job.")
    return _row_to_historical_backfill_job(row)


def record_historical_backfill_versions(
    db_path: str,
    *,
    backfill_job_id: int,
    onboarding_id: int,
    onboarded_artifact_id: int,
    repo_full: str,
    artifact_path: str,
    artifact_type: str,
    snapshots: list[HistoricalArtifactSnapshotInput],
    extract_signal_terms_fn,
    build_profile_fn,
) -> tuple[list[HistoricalArtifactVersionRecord], list[HistoricalStaticProfileRecord]]:
    if not snapshots:
        return [], []

    normalized_artifact_id = _build_normalized_artifact_id(repo_full, artifact_path)
    created_versions: list[HistoricalArtifactVersionRecord] = []
    created_profiles: list[HistoricalStaticProfileRecord] = []

    with _connect(db_path) as conn:
        onboarding_baseline_row = conn.execute(
            """
            SELECT *
            FROM onboarding_baseline_versions
            WHERE normalized_artifact_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (normalized_artifact_id,),
        ).fetchone()
        previous_version_row = conn.execute(
            """
            SELECT *
            FROM historical_artifact_versions
            WHERE normalized_artifact_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (normalized_artifact_id,),
        ).fetchone()
        previous_profile_row = conn.execute(
            """
            SELECT *
            FROM historical_static_profiles
            WHERE normalized_artifact_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (normalized_artifact_id,),
        ).fetchone()

        onboarding_baseline = _row_to_onboarding_baseline_version(onboarding_baseline_row) if onboarding_baseline_row is not None else None

        previous_version_id = previous_version_row["id"] if previous_version_row is not None else None
        previous_version_hash = previous_version_row["version_hash"] if previous_version_row is not None else None
        previous_profile_id = previous_profile_row["id"] if previous_profile_row is not None else None
        previous_profile = _profile_from_json(previous_profile_row["profile_json"]) if previous_profile_row is not None else None
        previous_signal_terms = json.loads(previous_profile_row["signal_terms_json"]) if previous_profile_row is not None else []

        base_time = time.time()
        for index, snapshot in enumerate(snapshots):
            version_hash = hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest()
            if version_hash == previous_version_hash:
                continue

            signal_terms = extract_signal_terms_fn(snapshot.content)
            profile = build_profile_fn(snapshot.content)
            created_at = base_time + (index / 1000.0)
            cursor = conn.execute(
                """
                INSERT INTO historical_artifact_versions (
                    backfill_job_id, onboarding_id, onboarded_artifact_id, normalized_artifact_id,
                    artifact_path, artifact_type, commit_sha, version_hash, signal_terms_json,
                    line_count, previous_version_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    backfill_job_id,
                    onboarding_id,
                    onboarded_artifact_id,
                    normalized_artifact_id,
                    artifact_path,
                    artifact_type,
                    snapshot.commit_sha,
                    version_hash,
                    json.dumps(signal_terms),
                    len([line for line in snapshot.content.splitlines() if line.strip()]),
                    previous_version_id,
                    created_at,
                ),
            )
            version_id = int(cursor.lastrowid)

            baseline_profile_id: int | None = None
            baseline_provenance = no_baseline_provenance()
            semantic_similarity = 1.0
            semantic_distance = 0.0
            attribute_deltas = {
                "guardrail_robustness": 0.0,
                "capability_risk": 0.0,
                "autonomy_level": 0.0,
                "stability_vs_creativity": 0.0,
                "governance_strength": 0.0,
                "change_frequency": 0.0,
                "semantic_density": 0.0,
            }
            narrative = ["No approved baseline available; stored snapshot with no explicit comparison baseline."]

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
            elif previous_profile is not None:
                baseline_profile_id = previous_profile_id
                baseline_provenance = historical_fallback_provenance(previous_profile_id, previous_version_id)
                semantic_similarity = _term_similarity(signal_terms, previous_signal_terms)
                drift_delta = compare_attribute_profiles(
                    previous_profile,
                    profile,
                    semantic_similarity=semantic_similarity,
                )
                semantic_distance = drift_delta.semantic_distance
                attribute_deltas = drift_delta.attribute_deltas
                narrative = drift_delta.narrative

            cursor = conn.execute(
                """
                INSERT INTO historical_static_profiles (
                    backfill_job_id, historical_artifact_version_id, onboarding_id, onboarded_artifact_id,
                    normalized_artifact_id, artifact_path, artifact_type, commit_sha, profile_json,
                    baseline_profile_id, baseline_provenance_json, semantic_similarity, semantic_distance, attribute_deltas_json,
                    narrative_json, signal_terms_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    backfill_job_id,
                    version_id,
                    onboarding_id,
                    onboarded_artifact_id,
                    normalized_artifact_id,
                    artifact_path,
                    artifact_type,
                    snapshot.commit_sha,
                    json.dumps(_profile_to_json(profile)),
                    baseline_profile_id,
                    baseline_provenance_to_json(baseline_provenance),
                    semantic_similarity,
                    semantic_distance,
                    json.dumps(attribute_deltas),
                    json.dumps(narrative),
                    json.dumps(signal_terms),
                    created_at,
                ),
            )
            profile_id = int(cursor.lastrowid)

            previous_version_id = version_id
            previous_version_hash = version_hash
            previous_profile_id = profile_id
            previous_profile = profile
            previous_signal_terms = signal_terms

            created_versions.append(
                HistoricalArtifactVersionRecord(
                    id=version_id,
                    backfill_job_id=backfill_job_id,
                    onboarding_id=onboarding_id,
                    onboarded_artifact_id=onboarded_artifact_id,
                    normalized_artifact_id=normalized_artifact_id,
                    artifact_path=artifact_path,
                    artifact_type=artifact_type,
                    commit_sha=snapshot.commit_sha,
                    version_hash=version_hash,
                    signal_terms=signal_terms,
                    line_count=len([line for line in snapshot.content.splitlines() if line.strip()]),
                    previous_version_id=created_versions[-1].id if created_versions else (previous_version_row["id"] if previous_version_row is not None else None),
                    created_at=created_at,
                )
            )
            created_profiles.append(
                HistoricalStaticProfileRecord(
                    id=profile_id,
                    backfill_job_id=backfill_job_id,
                    historical_artifact_version_id=version_id,
                    onboarding_id=onboarding_id,
                    onboarded_artifact_id=onboarded_artifact_id,
                    normalized_artifact_id=normalized_artifact_id,
                    artifact_path=artifact_path,
                    artifact_type=artifact_type,
                    commit_sha=snapshot.commit_sha,
                    profile=profile,
                    baseline_profile_id=baseline_profile_id,
                    baseline_provenance=baseline_provenance,
                    semantic_similarity=semantic_similarity,
                    semantic_distance=semantic_distance,
                    attribute_deltas=attribute_deltas,
                    narrative=narrative,
                    signal_terms=signal_terms,
                    created_at=created_at,
                )
            )

    return created_versions, created_profiles


def list_historical_artifact_versions_for_repo_artifact(
    db_path: str,
    repo_full: str,
    artifact_path: str,
) -> list[HistoricalArtifactVersionRecord]:
    normalized_artifact_id = _build_normalized_artifact_id(repo_full, artifact_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM historical_artifact_versions
            WHERE normalized_artifact_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (normalized_artifact_id,),
        ).fetchall()
    return [_row_to_historical_artifact_version(row) for row in rows]


def list_historical_static_profiles_for_repo_artifact(
    db_path: str,
    repo_full: str,
    artifact_path: str,
) -> list[HistoricalStaticProfileRecord]:
    normalized_artifact_id = _build_normalized_artifact_id(repo_full, artifact_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM historical_static_profiles
            WHERE normalized_artifact_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (normalized_artifact_id,),
        ).fetchall()
    return [_row_to_historical_static_profile(row) for row in rows]


def _row_to_repository_onboarding(row: sqlite3.Row) -> RepositoryOnboardingRecord:
    return RepositoryOnboardingRecord(
        id=row["id"],
        repo_full=row["repo_full"],
        installation_id=row["installation_id"],
        default_branch=row["default_branch"],
        status=row["status"],
        discovered_artifact_count=row["discovered_artifact_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_onboarded_artifact(row: sqlite3.Row) -> OnboardedArtifactRecord:
    return OnboardedArtifactRecord(
        id=row["id"],
        onboarding_id=row["onboarding_id"],
        repo_full=row["repo_full"],
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        discovery_reason=row["discovery_reason"],
        confidence=float(row["confidence"]),
        created_at=row["created_at"],
    )


def _row_to_onboarding_baseline_version(row: sqlite3.Row) -> OnboardingBaselineVersionRecord:
    return OnboardingBaselineVersionRecord(
        id=row["id"],
        onboarding_id=row["onboarding_id"],
        onboarded_artifact_id=row["onboarded_artifact_id"],
        normalized_artifact_id=row["normalized_artifact_id"],
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        version_hash=row["version_hash"],
        signal_terms=json.loads(row["signal_terms_json"]),
        line_count=row["line_count"],
        profile=_profile_from_json(row["profile_json"]),
        created_at=row["created_at"],
    )


def _row_to_historical_backfill_job(row: sqlite3.Row) -> HistoricalBackfillJobRecord:
    return HistoricalBackfillJobRecord(
        id=row["id"],
        onboarding_id=row["onboarding_id"],
        onboarded_artifact_id=row["onboarded_artifact_id"],
        repo_full=row["repo_full"],
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        status=row["status"],
        commit_count=row["commit_count"],
        completed_commit_count=row["completed_commit_count"],
        commit_shas=json.loads(row["commit_shas_json"]),
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_historical_artifact_version(row: sqlite3.Row) -> HistoricalArtifactVersionRecord:
    return HistoricalArtifactVersionRecord(
        id=row["id"],
        backfill_job_id=row["backfill_job_id"],
        onboarding_id=row["onboarding_id"],
        onboarded_artifact_id=row["onboarded_artifact_id"],
        normalized_artifact_id=row["normalized_artifact_id"],
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        commit_sha=row["commit_sha"],
        version_hash=row["version_hash"],
        signal_terms=json.loads(row["signal_terms_json"]),
        line_count=row["line_count"],
        previous_version_id=row["previous_version_id"],
        created_at=row["created_at"],
    )


def _row_to_historical_static_profile(row: sqlite3.Row) -> HistoricalStaticProfileRecord:
    baseline_provenance = baseline_provenance_from_json(row["baseline_provenance_json"])
    if baseline_provenance is None and row["baseline_profile_id"] is not None:
        baseline_provenance = historical_fallback_provenance(row["baseline_profile_id"])
    if baseline_provenance is None:
        baseline_provenance = no_baseline_provenance()

    return HistoricalStaticProfileRecord(
        id=row["id"],
        backfill_job_id=row["backfill_job_id"],
        historical_artifact_version_id=row["historical_artifact_version_id"],
        onboarding_id=row["onboarding_id"],
        onboarded_artifact_id=row["onboarded_artifact_id"],
        normalized_artifact_id=row["normalized_artifact_id"],
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        commit_sha=row["commit_sha"],
        profile=_profile_from_json(row["profile_json"]),
        baseline_profile_id=row["baseline_profile_id"],
        baseline_provenance=baseline_provenance,
        semantic_similarity=float(row["semantic_similarity"]),
        semantic_distance=float(row["semantic_distance"]),
        attribute_deltas={key: float(value) for key, value in json.loads(row["attribute_deltas_json"]).items()},
        narrative=json.loads(row["narrative_json"]),
        signal_terms=json.loads(row["signal_terms_json"]),
        created_at=row["created_at"],
    )


def _build_normalized_artifact_id(repo_full: str, artifact_path: str) -> str:
    return f"{repo_full.lower()}::{artifact_path.lower()}"


def _profile_to_json(profile: AgentAttributeProfile) -> dict:
    return {
        "guardrail_robustness": profile.guardrail_robustness,
        "capability_risk": profile.capability_risk,
        "autonomy_level": profile.autonomy_level,
        "stability_vs_creativity": profile.stability_vs_creativity,
        "governance_strength": profile.governance_strength,
        "change_frequency": profile.change_frequency,
        "semantic_density": profile.semantic_density,
        "signals": {
            "token_count": profile.signals.token_count,
            "char_count": profile.signals.char_count,
            "section_count": profile.signals.section_count,
            "example_count": profile.signals.example_count,
            "instruction_density": profile.signals.instruction_density,
            "constraint_count": profile.signals.constraint_count,
            "explicit_limit_count": profile.signals.explicit_limit_count,
            "ambiguity_count": profile.signals.ambiguity_count,
            "guardrail_counts": profile.signals.guardrail_counts,
            "write_signal_count": profile.signals.write_signal_count,
            "read_signal_count": profile.signals.read_signal_count,
            "sensitive_tool_count": profile.signals.sensitive_tool_count,
            "prod_signal_count": profile.signals.prod_signal_count,
            "sandbox_signal_count": profile.signals.sandbox_signal_count,
            "systems_touched_count": profile.signals.systems_touched_count,
            "human_review_count": profile.signals.human_review_count,
            "parallelism_signal_count": profile.signals.parallelism_signal_count,
            "max_steps": profile.signals.max_steps,
            "temperature": profile.signals.temperature,
            "top_p": profile.signals.top_p,
        },
    }


def _profile_from_json(profile_json: str) -> AgentAttributeProfile:
    payload = json.loads(profile_json)
    signals = payload["signals"]
    return AgentAttributeProfile(
        guardrail_robustness=float(payload["guardrail_robustness"]),
        capability_risk=float(payload["capability_risk"]),
        autonomy_level=float(payload["autonomy_level"]),
        stability_vs_creativity=float(payload["stability_vs_creativity"]),
        governance_strength=float(payload["governance_strength"]),
        change_frequency=float(payload["change_frequency"]),
        semantic_density=float(payload["semantic_density"]),
        signals=StaticSignals(
            token_count=int(signals["token_count"]),
            char_count=int(signals["char_count"]),
            section_count=int(signals["section_count"]),
            example_count=int(signals["example_count"]),
            instruction_density=float(signals["instruction_density"]),
            constraint_count=int(signals["constraint_count"]),
            explicit_limit_count=int(signals["explicit_limit_count"]),
            ambiguity_count=int(signals["ambiguity_count"]),
            guardrail_counts={key: int(value) for key, value in signals.get("guardrail_counts", {}).items()},
            write_signal_count=int(signals.get("write_signal_count", 0)),
            read_signal_count=int(signals.get("read_signal_count", 0)),
            sensitive_tool_count=int(signals.get("sensitive_tool_count", 0)),
            prod_signal_count=int(signals.get("prod_signal_count", 0)),
            sandbox_signal_count=int(signals.get("sandbox_signal_count", 0)),
            systems_touched_count=int(signals.get("systems_touched_count", 0)),
            human_review_count=int(signals.get("human_review_count", 0)),
            parallelism_signal_count=int(signals.get("parallelism_signal_count", 0)),
            max_steps=int(signals.get("max_steps", 0)),
            temperature=(float(signals["temperature"]) if signals.get("temperature") is not None else None),
            top_p=(float(signals["top_p"]) if signals.get("top_p") is not None else None),
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
