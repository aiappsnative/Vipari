from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from engine.drift_profile import AgentAttributeProfile, StaticSignals


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
    commit_shas: list[str]
    created_at: float
    updated_at: float


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
                commit_shas_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(onboarding_id) REFERENCES repository_onboardings(id) ON DELETE CASCADE,
                FOREIGN KEY(onboarded_artifact_id) REFERENCES onboarded_artifacts(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repository_onboardings_repo_created ON repository_onboardings(repo_full, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_onboarded_artifacts_repo_path ON onboarded_artifacts(repo_full, artifact_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_onboarding_baselines_normalized_id ON onboarding_baseline_versions(normalized_artifact_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_historical_backfill_jobs_repo_path ON historical_backfill_jobs(repo_full, artifact_path, created_at)")


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
                    status, commit_count, commit_shas_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    onboarding_id,
                    job.onboarded_artifact_id,
                    repo_full,
                    job.artifact_path,
                    job.artifact_type,
                    status,
                    len(job.commit_shas),
                    json.dumps(job.commit_shas),
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
        commit_shas=json.loads(row["commit_shas_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
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
