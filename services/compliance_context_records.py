from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from .persistence import connect_sqlite


_ALLOWED_RISK_TIERS = {"unclassified", "minimal", "limited", "high"}
_ALLOWED_CUSTOMER_IMPACT = {"internal", "customer_facing", "critical"}
_ALLOWED_HUMAN_OVERSIGHT = {"required", "optional", "none"}
_ALLOWED_CONTEXT_KEYS = {
    "risk_tier",
    "customer_impact",
    "human_oversight",
    "handles_personal_data",
    "handles_biometric_data",
    "deployment_regions",
    "notes",
}


def default_workspace_compliance_context() -> dict[str, Any]:
    return {
        "risk_tier": "unclassified",
        "customer_impact": "internal",
        "human_oversight": "required",
        "handles_personal_data": False,
        "handles_biometric_data": False,
        "deployment_regions": [],
        "notes": "",
    }


@dataclass(frozen=True)
class WorkspaceComplianceContextRecord:
    id: int
    workspace_id: int
    context_json: str
    context_hash: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class RepoComplianceContextOverrideRecord:
    id: int
    workspace_id: int
    repo_allocation_id: int
    override_json: str
    override_hash: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class EffectiveComplianceContextRecord:
    workspace_id: int
    repo_allocation_id: int | None
    source: str
    context: dict[str, Any]


def bootstrap_compliance_context_tables(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_compliance_contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL UNIQUE,
                context_json TEXT NOT NULL,
                context_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_compliance_context_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                repo_allocation_id INTEGER NOT NULL UNIQUE,
                override_json TEXT NOT NULL,
                override_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                FOREIGN KEY(repo_allocation_id) REFERENCES repo_allocations(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspace_compliance_contexts_workspace_id ON workspace_compliance_contexts(workspace_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_repo_compliance_context_overrides_workspace_id ON repo_compliance_context_overrides(workspace_id)"
        )


def _compute_hash(serialized_json: str) -> str:
    return hashlib.sha256(serialized_json.encode("utf-8")).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalize_enum(value: Any, *, allowed: set[str], default: str) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in allowed:
        return candidate
    return default


def _normalize_regions(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized = []
    seen: set[str] = set()
    for item in value:
        candidate = str(item or "").strip()
        if not candidate:
            continue
        folded = candidate.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(candidate)
    return sorted(normalized, key=str.casefold)


def _normalize_notes(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 2000:
        return text[:2000]
    return text


def normalize_workspace_compliance_context(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    return {
        "risk_tier": _normalize_enum(source.get("risk_tier"), allowed=_ALLOWED_RISK_TIERS, default="unclassified"),
        "customer_impact": _normalize_enum(
            source.get("customer_impact"),
            allowed=_ALLOWED_CUSTOMER_IMPACT,
            default="internal",
        ),
        "human_oversight": _normalize_enum(
            source.get("human_oversight"),
            allowed=_ALLOWED_HUMAN_OVERSIGHT,
            default="required",
        ),
        "handles_personal_data": bool(source.get("handles_personal_data", False)),
        "handles_biometric_data": bool(source.get("handles_biometric_data", False)),
        "deployment_regions": _normalize_regions(source.get("deployment_regions")),
        "notes": _normalize_notes(source.get("notes")),
    }


def normalize_repo_compliance_context_override(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    normalized: dict[str, Any] = {}
    for key in _ALLOWED_CONTEXT_KEYS:
        if key not in source:
            continue
        if key == "risk_tier":
            normalized[key] = _normalize_enum(source.get(key), allowed=_ALLOWED_RISK_TIERS, default="unclassified")
        elif key == "customer_impact":
            normalized[key] = _normalize_enum(source.get(key), allowed=_ALLOWED_CUSTOMER_IMPACT, default="internal")
        elif key == "human_oversight":
            normalized[key] = _normalize_enum(source.get(key), allowed=_ALLOWED_HUMAN_OVERSIGHT, default="required")
        elif key in {"handles_personal_data", "handles_biometric_data"}:
            normalized[key] = bool(source.get(key, False))
        elif key == "deployment_regions":
            normalized[key] = _normalize_regions(source.get(key))
        elif key == "notes":
            normalized[key] = _normalize_notes(source.get(key))
    return normalized


def _row_to_workspace_context(row) -> WorkspaceComplianceContextRecord:
    return WorkspaceComplianceContextRecord(
        id=int(row["id"]),
        workspace_id=int(row["workspace_id"]),
        context_json=str(row["context_json"]),
        context_hash=str(row["context_hash"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _row_to_repo_override(row) -> RepoComplianceContextOverrideRecord:
    return RepoComplianceContextOverrideRecord(
        id=int(row["id"]),
        workspace_id=int(row["workspace_id"]),
        repo_allocation_id=int(row["repo_allocation_id"]),
        override_json=str(row["override_json"]),
        override_hash=str(row["override_hash"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def get_workspace_compliance_context(db_path: str, workspace_id: int) -> WorkspaceComplianceContextRecord | None:
    bootstrap_compliance_context_tables(db_path)
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM workspace_compliance_contexts WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    return _row_to_workspace_context(row) if row is not None else None


def upsert_workspace_compliance_context(
    db_path: str,
    *,
    workspace_id: int,
    context: dict[str, Any],
) -> WorkspaceComplianceContextRecord:
    bootstrap_compliance_context_tables(db_path)
    now = time.time()
    normalized_context = normalize_workspace_compliance_context(context)
    context_json = _canonical_json(normalized_context)
    context_hash = _compute_hash(context_json)
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workspace_compliance_contexts (workspace_id, context_json, context_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                context_json = excluded.context_json,
                context_hash = excluded.context_hash,
                updated_at = excluded.updated_at
            """,
            (workspace_id, context_json, context_hash, now, now),
        )
        row = conn.execute(
            "SELECT * FROM workspace_compliance_contexts WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    return _row_to_workspace_context(row)


def get_repo_compliance_context_override(
    db_path: str,
    repo_allocation_id: int,
    *,
    workspace_id: int | None = None,
) -> RepoComplianceContextOverrideRecord | None:
    bootstrap_compliance_context_tables(db_path)
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM repo_compliance_context_overrides WHERE repo_allocation_id = ?",
            (repo_allocation_id,),
        ).fetchone()
    if row is not None and workspace_id is not None and int(row["workspace_id"]) != int(workspace_id):
        raise ValueError("Repository compliance context override does not belong to workspace.")
    return _row_to_repo_override(row) if row is not None else None


def upsert_repo_compliance_context_override(
    db_path: str,
    *,
    workspace_id: int,
    repo_allocation_id: int,
    context_override: dict[str, Any],
) -> RepoComplianceContextOverrideRecord:
    bootstrap_compliance_context_tables(db_path)
    now = time.time()
    normalized_override = normalize_repo_compliance_context_override(context_override)
    override_json = _canonical_json(normalized_override)
    override_hash = _compute_hash(override_json)
    with connect_sqlite(db_path) as conn:
        allocation_row = conn.execute(
            "SELECT workspace_id FROM repo_allocations WHERE id = ?",
            (repo_allocation_id,),
        ).fetchone()
        if allocation_row is None:
            raise ValueError("Repository allocation not found.")
        if int(allocation_row["workspace_id"]) != int(workspace_id):
            raise ValueError("Repository allocation does not belong to workspace.")
        conn.execute(
            """
            INSERT INTO repo_compliance_context_overrides (
                workspace_id, repo_allocation_id, override_json, override_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo_allocation_id) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                override_json = excluded.override_json,
                override_hash = excluded.override_hash,
                updated_at = excluded.updated_at
            """,
            (workspace_id, repo_allocation_id, override_json, override_hash, now, now),
        )
        row = conn.execute(
            "SELECT * FROM repo_compliance_context_overrides WHERE repo_allocation_id = ?",
            (repo_allocation_id,),
        ).fetchone()
    return _row_to_repo_override(row)


def resolve_effective_compliance_context(
    db_path: str,
    *,
    workspace_id: int,
    repo_allocation_id: int | None = None,
    persist_missing_workspace_context: bool = True,
) -> EffectiveComplianceContextRecord:
    workspace_record = get_workspace_compliance_context(db_path, workspace_id)
    if workspace_record is None:
        workspace_context = default_workspace_compliance_context()
        if persist_missing_workspace_context:
            workspace_record = upsert_workspace_compliance_context(
                db_path,
                workspace_id=workspace_id,
                context=workspace_context,
            )
            workspace_context = json.loads(workspace_record.context_json)
    else:
        workspace_context = normalize_workspace_compliance_context(json.loads(workspace_record.context_json))

    source = "workspace_default"
    effective_context = dict(workspace_context)
    if repo_allocation_id is not None:
        with connect_sqlite(db_path) as conn:
            allocation_row = conn.execute(
                "SELECT workspace_id FROM repo_allocations WHERE id = ?",
                (repo_allocation_id,),
            ).fetchone()
        if allocation_row is None:
            raise ValueError("Repository allocation not found.")
        if int(allocation_row["workspace_id"]) != int(workspace_id):
            raise ValueError("Repository allocation does not belong to workspace.")

        override = get_repo_compliance_context_override(
            db_path,
            repo_allocation_id,
            workspace_id=workspace_id,
        )
        if override is not None:
            source = "repo_override"
            effective_context.update(normalize_repo_compliance_context_override(json.loads(override.override_json)))

    return EffectiveComplianceContextRecord(
        workspace_id=workspace_id,
        repo_allocation_id=repo_allocation_id,
        source=source,
        context=effective_context,
    )
