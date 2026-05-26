from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .operational_policy import (
    OperationalPolicy,
    PolicyScope,
    RepoPolicyOverride,
    canonical_policy_dict,
    canonical_policy_json,
    canonical_repo_policy_override_json,
    compute_policy_hash,
    compute_repo_policy_override_hash,
    default_policy_for_preset,
    normalize_operational_policy,
    normalize_repo_policy_override,
    resolve_effective_policy,
)
from .persistence import connect_sqlite


BOOTSTRAP_OPERATIONAL_POLICY_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS workspace_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL UNIQUE,
    active_version_id INTEGER,
    preset_key TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspace_policies_workspace_id
    ON workspace_policies(workspace_id);

CREATE TABLE IF NOT EXISTS repo_policy_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL,
    repo_allocation_id INTEGER NOT NULL UNIQUE,
    active_version_id INTEGER,
    override_json TEXT NOT NULL,
    override_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY(repo_allocation_id) REFERENCES repo_allocations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_repo_policy_overrides_workspace_id
    ON repo_policy_overrides(workspace_id);

CREATE TABLE IF NOT EXISTS policy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    scope_ref_id INTEGER NOT NULL,
    workspace_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    preset_key TEXT,
    policy_json TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    diff_summary_json TEXT NOT NULL DEFAULT '{}',
    created_by_user_id INTEGER,
    created_at REAL NOT NULL,
    UNIQUE(scope_type, scope_ref_id, version_number),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_policy_versions_workspace_id
    ON policy_versions(workspace_id);

CREATE INDEX IF NOT EXISTS idx_policy_versions_scope
    ON policy_versions(scope_type, scope_ref_id, version_number);
"""


@dataclass(frozen=True)
class WorkspacePolicyRecord:
    id: int
    workspace_id: int
    active_version_id: int | None
    preset_key: str
    policy_json: str
    policy_hash: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class RepoPolicyOverrideRecord:
    id: int
    workspace_id: int
    repo_allocation_id: int
    active_version_id: int | None
    override_json: str
    override_hash: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class PolicyVersionRecord:
    id: int
    scope_type: str
    scope_ref_id: int
    workspace_id: int
    version_number: int
    preset_key: str | None
    policy_json: str
    policy_hash: str
    diff_summary_json: str
    created_by_user_id: int | None
    created_at: float


@dataclass(frozen=True)
class EffectivePolicyResolutionRecord:
    workspace_policy_version_id: int | None
    repo_policy_version_id: int | None
    effective_policy_hash: str
    effective_policy_source: str
    policy_decision_json: str
    policy: OperationalPolicy


def _row_to_workspace_policy(row) -> WorkspacePolicyRecord:
    return WorkspacePolicyRecord(
        id=int(row["id"]),
        workspace_id=int(row["workspace_id"]),
        active_version_id=int(row["active_version_id"]) if row["active_version_id"] is not None else None,
        preset_key=str(row["preset_key"]),
        policy_json=str(row["policy_json"]),
        policy_hash=str(row["policy_hash"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _row_to_repo_policy_override(row) -> RepoPolicyOverrideRecord:
    return RepoPolicyOverrideRecord(
        id=int(row["id"]),
        workspace_id=int(row["workspace_id"]),
        repo_allocation_id=int(row["repo_allocation_id"]),
        active_version_id=int(row["active_version_id"]) if row["active_version_id"] is not None else None,
        override_json=str(row["override_json"]),
        override_hash=str(row["override_hash"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _row_to_policy_version(row) -> PolicyVersionRecord:
    return PolicyVersionRecord(
        id=int(row["id"]),
        scope_type=str(row["scope_type"]),
        scope_ref_id=int(row["scope_ref_id"]),
        workspace_id=int(row["workspace_id"]),
        version_number=int(row["version_number"]),
        preset_key=str(row["preset_key"]) if row["preset_key"] is not None else None,
        policy_json=str(row["policy_json"]),
        policy_hash=str(row["policy_hash"]),
        diff_summary_json=str(row["diff_summary_json"]),
        created_by_user_id=int(row["created_by_user_id"]) if row["created_by_user_id"] is not None else None,
        created_at=float(row["created_at"]),
    )


def bootstrap_operational_policy_tables(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        for statement in BOOTSTRAP_OPERATIONAL_POLICY_TABLES_SQL.strip().split(";"):
            sql = statement.strip()
            if sql:
                conn.execute(sql)


def ensure_pull_request_audit_policy_provenance(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(pull_request_audits)").fetchall()}
        if not columns:
            return
        if "workspace_policy_version_id" not in columns:
            conn.execute("ALTER TABLE pull_request_audits ADD COLUMN workspace_policy_version_id INTEGER")
        if "repo_policy_version_id" not in columns:
            conn.execute("ALTER TABLE pull_request_audits ADD COLUMN repo_policy_version_id INTEGER")
        if "effective_policy_hash" not in columns:
            conn.execute("ALTER TABLE pull_request_audits ADD COLUMN effective_policy_hash TEXT")
        if "effective_policy_source" not in columns:
            conn.execute("ALTER TABLE pull_request_audits ADD COLUMN effective_policy_source TEXT")
        if "policy_decision_json" not in columns:
            conn.execute("ALTER TABLE pull_request_audits ADD COLUMN policy_decision_json TEXT NOT NULL DEFAULT '{}' ")


def _diff_summary(previous_json: str | None, current_json: str) -> str:
    if not previous_json:
        return json.dumps({"changed_sections": ["initial_create"]}, sort_keys=True, separators=(",", ":"))
    previous = json.loads(previous_json)
    current = json.loads(current_json)
    changed_sections = []
    for section in ("preset_key", "categories", "attribute_templates", "llm_strategy", "gating"):
        if previous.get(section) != current.get(section):
            changed_sections.append(section)
    return json.dumps({"changed_sections": changed_sections}, sort_keys=True, separators=(",", ":"))


def get_workspace_policy(db_path: str, workspace_id: int) -> WorkspacePolicyRecord | None:
    with connect_sqlite(db_path) as conn:
        row = conn.execute("SELECT * FROM workspace_policies WHERE workspace_id = ?", (workspace_id,)).fetchone()
    return _row_to_workspace_policy(row) if row is not None else None


def list_policy_versions_for_scope(db_path: str, *, scope_type: str, scope_ref_id: int) -> list[PolicyVersionRecord]:
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM policy_versions WHERE scope_type = ? AND scope_ref_id = ? ORDER BY version_number ASC, id ASC",
            (scope_type, scope_ref_id),
        ).fetchall()
    return [_row_to_policy_version(row) for row in rows]


def upsert_workspace_policy(
    db_path: str,
    *,
    workspace_id: int,
    policy: OperationalPolicy,
    created_by_user_id: int | None,
) -> WorkspacePolicyRecord:
    bootstrap_operational_policy_tables(db_path)
    now = time.time()
    policy_json = canonical_policy_json(policy)
    policy_hash = compute_policy_hash(policy)
    with connect_sqlite(db_path) as conn:
        row = conn.execute("SELECT * FROM workspace_policies WHERE workspace_id = ?", (workspace_id,)).fetchone()
        previous_json = None
        if row is None:
            conn.execute(
                "INSERT INTO workspace_policies (workspace_id, active_version_id, preset_key, policy_json, policy_hash, created_at, updated_at) VALUES (?, NULL, ?, ?, ?, ?, ?)",
                (workspace_id, policy.preset_key.value, policy_json, policy_hash, now, now),
            )
            row = conn.execute("SELECT * FROM workspace_policies WHERE workspace_id = ?", (workspace_id,)).fetchone()
        current = _row_to_workspace_policy(row)
        if current.active_version_id is not None and current.policy_hash == policy_hash:
            return current
        if current.active_version_id is not None:
            previous_json = current.policy_json

        version_number = int(
            conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM policy_versions WHERE scope_type = ? AND scope_ref_id = ?",
                (PolicyScope.WORKSPACE.value, current.id),
            ).fetchone()[0]
        ) + 1
        diff_summary_json = _diff_summary(previous_json, policy_json)
        conn.execute(
            "INSERT INTO policy_versions (scope_type, scope_ref_id, workspace_id, version_number, preset_key, policy_json, policy_hash, diff_summary_json, created_by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                PolicyScope.WORKSPACE.value,
                current.id,
                workspace_id,
                version_number,
                policy.preset_key.value,
                policy_json,
                policy_hash,
                diff_summary_json,
                created_by_user_id,
                now,
            ),
        )
        version_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "UPDATE workspace_policies SET active_version_id = ?, preset_key = ?, policy_json = ?, policy_hash = ?, updated_at = ? WHERE id = ?",
            (version_id, policy.preset_key.value, policy_json, policy_hash, now, current.id),
        )
        updated = conn.execute("SELECT * FROM workspace_policies WHERE id = ?", (current.id,)).fetchone()
    return _row_to_workspace_policy(updated)


def get_repo_policy_override(db_path: str, repo_allocation_id: int) -> RepoPolicyOverrideRecord | None:
    with connect_sqlite(db_path) as conn:
        row = conn.execute("SELECT * FROM repo_policy_overrides WHERE repo_allocation_id = ?", (repo_allocation_id,)).fetchone()
    return _row_to_repo_policy_override(row) if row is not None else None


def resolve_effective_policy_record(
    db_path: str,
    *,
    workspace_id: int,
    repo_allocation_id: int | None = None,
) -> EffectivePolicyResolutionRecord:
    workspace_record = get_workspace_policy(db_path, workspace_id)
    if workspace_record is None:
        workspace_record = upsert_workspace_policy(
            db_path,
            workspace_id=workspace_id,
            policy=default_policy_for_preset(),
            created_by_user_id=None,
        )

    workspace_policy = normalize_operational_policy(json.loads(workspace_record.policy_json))
    repo_record = get_repo_policy_override(db_path, repo_allocation_id) if repo_allocation_id is not None else None
    repo_override = normalize_repo_policy_override(json.loads(repo_record.override_json)) if repo_record is not None else None
    resolved = resolve_effective_policy(workspace_policy, repo_override)
    policy_decision_json = json.dumps(
        {
            "effective_policy": canonical_policy_dict(resolved.policy),
            "effective_policy_source": resolved.source,
            "repo_policy_version_id": repo_record.active_version_id if repo_record is not None else None,
            "workspace_policy_version_id": workspace_record.active_version_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return EffectivePolicyResolutionRecord(
        workspace_policy_version_id=workspace_record.active_version_id,
        repo_policy_version_id=repo_record.active_version_id if repo_record is not None else None,
        effective_policy_hash=compute_policy_hash(resolved.policy),
        effective_policy_source=resolved.source,
        policy_decision_json=policy_decision_json,
        policy=resolved.policy,
    )


def upsert_repo_policy_override(
    db_path: str,
    *,
    workspace_id: int,
    repo_allocation_id: int,
    policy_override: RepoPolicyOverride,
    created_by_user_id: int | None,
) -> RepoPolicyOverrideRecord:
    bootstrap_operational_policy_tables(db_path)
    now = time.time()
    override_json = canonical_repo_policy_override_json(policy_override)
    override_hash = compute_repo_policy_override_hash(policy_override)
    with connect_sqlite(db_path) as conn:
        row = conn.execute("SELECT * FROM repo_policy_overrides WHERE repo_allocation_id = ?", (repo_allocation_id,)).fetchone()
        previous_json = None
        if row is None:
            conn.execute(
                "INSERT INTO repo_policy_overrides (workspace_id, repo_allocation_id, active_version_id, override_json, override_hash, created_at, updated_at) VALUES (?, ?, NULL, ?, ?, ?, ?)",
                (workspace_id, repo_allocation_id, override_json, override_hash, now, now),
            )
            row = conn.execute("SELECT * FROM repo_policy_overrides WHERE repo_allocation_id = ?", (repo_allocation_id,)).fetchone()
        current = _row_to_repo_policy_override(row)
        if current.active_version_id is not None and current.override_hash == override_hash:
            return current

        if current.active_version_id is not None:
            previous_json = current.override_json

        version_number = int(
            conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM policy_versions WHERE scope_type = ? AND scope_ref_id = ?",
                (PolicyScope.REPO.value, current.id),
            ).fetchone()[0]
        ) + 1
        diff_summary_json = _diff_summary(previous_json, override_json)
        conn.execute(
            "INSERT INTO policy_versions (scope_type, scope_ref_id, workspace_id, version_number, preset_key, policy_json, policy_hash, diff_summary_json, created_by_user_id, created_at) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
            (
                PolicyScope.REPO.value,
                current.id,
                workspace_id,
                version_number,
                override_json,
                override_hash,
                diff_summary_json,
                created_by_user_id,
                now,
            ),
        )
        version_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "UPDATE repo_policy_overrides SET active_version_id = ?, override_json = ?, override_hash = ?, updated_at = ? WHERE id = ?",
            (version_id, override_json, override_hash, now, current.id),
        )
        updated = conn.execute("SELECT * FROM repo_policy_overrides WHERE id = ?", (current.id,)).fetchone()
    return _row_to_repo_policy_override(updated)