"""Persistence layer for high-risk change proposals.

Two proposal domains are implemented here:
- **Baseline proposals** — request to promote an artifact snapshot to the
  approved baseline for a repository.  Scoped to an artifact.
- **Repo onboarding proposals** — request to onboard a new repository into a
  workspace.  Scoped to a workspace.  Only the ``onboard`` kind is functional
  in v1.

Proposals follow a strict one-way state machine:
    pending → approved  (side effect executed atomically)
    pending → rejected  (no side effect)
Transitions from any state other than ``pending`` raise a 409 Conflict.

Flood limits (enforced at creation time):
- Baseline: max 5 pending proposals per artifact.
- Repo onboarding: max 20 pending proposals per workspace.

Proposal expiry:
- Proposals expire 30 days after creation.
- Approval of an expired proposal raises a 409 Conflict.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from fastapi import HTTPException

from .persistence import connect_sqlite


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROPOSAL_STATUS_PENDING = "pending"
PROPOSAL_STATUS_APPROVED = "approved"
PROPOSAL_STATUS_REJECTED = "rejected"

PROPOSAL_KIND_BASELINE_PROMOTE = "baseline_promote"

PROPOSAL_KIND_ONBOARD = "onboard"

# Expiry window in seconds (30 days)
PROPOSAL_EXPIRY_SECONDS = 30 * 24 * 60 * 60

# Flood limits
MAX_PENDING_BASELINE_PROPOSALS_PER_ARTIFACT = 5
MAX_PENDING_ONBOARDING_PROPOSALS_PER_WORKSPACE = 20


# ---------------------------------------------------------------------------
# DDL (called by migration 0007)
# ---------------------------------------------------------------------------

BOOTSTRAP_PROPOSAL_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS cp_baseline_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id INTEGER NOT NULL,
    repo_full TEXT NOT NULL,
    workspace_id INTEGER NOT NULL,
    proposal_kind TEXT NOT NULL DEFAULT 'baseline_promote',
    snapshot_id INTEGER,
    rationale TEXT NOT NULL DEFAULT '',
    linked_audit_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    proposer_principal_id INTEGER NOT NULL,
    decision_principal_id INTEGER,
    decision_note TEXT,
    expires_at REAL NOT NULL,
    decided_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cp_baseline_proposals_artifact_id
    ON cp_baseline_proposals(artifact_id);

CREATE INDEX IF NOT EXISTS idx_cp_baseline_proposals_workspace_id
    ON cp_baseline_proposals(workspace_id);

CREATE TABLE IF NOT EXISTS cp_repo_onboarding_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL,
    repo_full TEXT NOT NULL,
    proposal_kind TEXT NOT NULL DEFAULT 'onboard',
    installation_id INTEGER,
    proposed_category TEXT,
    rationale TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    proposer_principal_id INTEGER NOT NULL,
    decision_principal_id INTEGER,
    decision_note TEXT,
    expires_at REAL NOT NULL,
    decided_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cp_repo_onboarding_proposals_workspace_id
    ON cp_repo_onboarding_proposals(workspace_id);
"""


def bootstrap_proposal_tables(db_path: str) -> None:
    with connect_sqlite(db_path) as conn:
        for statement in BOOTSTRAP_PROPOSAL_TABLES_SQL.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                conn.execute(stmt)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BaselineProposalRecord:
    id: int
    artifact_id: int
    repo_full: str
    workspace_id: int
    proposal_kind: str
    snapshot_id: int | None
    rationale: str
    linked_audit_ids: list[int]
    metadata: dict[str, str]
    status: str
    proposer_principal_id: int
    decision_principal_id: int | None
    decision_note: str | None
    expires_at: float
    decided_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class RepoOnboardingProposalRecord:
    id: int
    workspace_id: int
    repo_full: str
    proposal_kind: str
    installation_id: int | None
    proposed_category: str | None
    rationale: str
    metadata: dict[str, str]
    status: str
    proposer_principal_id: int
    decision_principal_id: int | None
    decision_note: str | None
    expires_at: float
    decided_at: float | None
    created_at: float
    updated_at: float


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

def _row_to_baseline_proposal(row: sqlite3.Row) -> BaselineProposalRecord:
    return BaselineProposalRecord(
        id=row["id"],
        artifact_id=row["artifact_id"],
        repo_full=row["repo_full"],
        workspace_id=row["workspace_id"],
        proposal_kind=row["proposal_kind"],
        snapshot_id=row["snapshot_id"],
        rationale=row["rationale"] or "",
        linked_audit_ids=json.loads(row["linked_audit_ids_json"] or "[]"),
        metadata=json.loads(row["metadata_json"] or "{}"),
        status=row["status"],
        proposer_principal_id=row["proposer_principal_id"],
        decision_principal_id=row["decision_principal_id"],
        decision_note=row["decision_note"],
        expires_at=row["expires_at"],
        decided_at=row["decided_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_onboarding_proposal(row: sqlite3.Row) -> RepoOnboardingProposalRecord:
    return RepoOnboardingProposalRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        repo_full=row["repo_full"],
        proposal_kind=row["proposal_kind"],
        installation_id=row["installation_id"],
        proposed_category=row["proposed_category"],
        rationale=row["rationale"] or "",
        metadata=json.loads(row["metadata_json"] or "{}"),
        status=row["status"],
        proposer_principal_id=row["proposer_principal_id"],
        decision_principal_id=row["decision_principal_id"],
        decision_note=row["decision_note"],
        expires_at=row["expires_at"],
        decided_at=row["decided_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_rationale(value: str) -> str:
    if len(value) > 2000:
        raise HTTPException(status_code=422, detail="rationale must not exceed 2000 characters.")
    return value


def _validate_and_serialise_metadata(value: dict[str, str]) -> str:
    if len(value) > 20:
        raise HTTPException(status_code=422, detail="metadata must not exceed 20 keys.")
    for k, v in value.items():
        if len(k) > 80:
            raise HTTPException(status_code=422, detail=f"metadata key '{k[:80]}…' exceeds 80 characters.")
        if len(v) > 500:
            raise HTTPException(status_code=422, detail="metadata values must not exceed 500 characters.")
    return json.dumps(value, sort_keys=True)


def _validate_linked_audit_ids(values: list[int]) -> str:
    if len(values) > 50:
        raise HTTPException(status_code=422, detail="linked_audit_ids must not exceed 50 items.")
    for v in values:
        if not isinstance(v, int) or v <= 0:
            raise HTTPException(status_code=422, detail="linked_audit_ids must contain positive integers only.")
    return json.dumps(values)


def _validate_decision_note(value: str | None) -> str | None:
    if value is not None and len(value) > 2000:
        raise HTTPException(status_code=422, detail="decision_note must not exceed 2000 characters.")
    return value


# ---------------------------------------------------------------------------
# Baseline proposal CRUD
# ---------------------------------------------------------------------------

def create_baseline_proposal(
    db_path: str,
    *,
    artifact_id: int,
    repo_full: str,
    workspace_id: int,
    snapshot_id: int | None,
    rationale: str,
    linked_audit_ids: list[int],
    metadata: dict[str, str],
    proposer_principal_id: int,
) -> BaselineProposalRecord:
    rationale = _validate_rationale(rationale)
    metadata_json = _validate_and_serialise_metadata(metadata)
    linked_json = _validate_linked_audit_ids(linked_audit_ids)
    now = time.time()
    expires_at = now + PROPOSAL_EXPIRY_SECONDS

    with connect_sqlite(db_path) as conn:
        # Acquire an immediate write lock before the COUNT so the check and
        # the INSERT are atomic — prevents concurrent requests from both
        # passing the flood limit and racing to insert.
        conn.execute("BEGIN IMMEDIATE")
        # Flood limit check
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM cp_baseline_proposals WHERE artifact_id = ? AND status = ?",
            (artifact_id, PROPOSAL_STATUS_PENDING),
        ).fetchone()[0]
        if pending_count >= MAX_PENDING_BASELINE_PROPOSALS_PER_ARTIFACT:
            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=409,
                detail=(
                    f"There are already {MAX_PENDING_BASELINE_PROPOSALS_PER_ARTIFACT} pending baseline proposals "
                    "for this artifact. Resolve existing proposals before creating new ones."
                ),
            )

        conn.execute(
            """
            INSERT INTO cp_baseline_proposals (
                artifact_id, repo_full, workspace_id, proposal_kind, snapshot_id,
                rationale, linked_audit_ids_json, metadata_json, status,
                proposer_principal_id, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id, repo_full, workspace_id, PROPOSAL_KIND_BASELINE_PROMOTE, snapshot_id,
                rationale, linked_json, metadata_json, PROPOSAL_STATUS_PENDING,
                proposer_principal_id, expires_at, now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM cp_baseline_proposals WHERE id = last_insert_rowid()"
        ).fetchone()
    return _row_to_baseline_proposal(row)


def list_baseline_proposals(
    db_path: str,
    *,
    artifact_id: int,
    workspace_id: int,
) -> list[BaselineProposalRecord]:
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM cp_baseline_proposals WHERE artifact_id = ? AND workspace_id = ? ORDER BY created_at DESC",
            (artifact_id, workspace_id),
        ).fetchall()
    return [_row_to_baseline_proposal(r) for r in rows]


def list_pending_baseline_proposals_for_repo(
    db_path: str,
    repo_full: str,
) -> list[BaselineProposalRecord]:
    """List all pending baseline proposals for a repo regardless of workspace.
    Intended for operator-level views where workspace context is not available.
    """
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM cp_baseline_proposals WHERE repo_full = ? AND status = ? ORDER BY created_at DESC",
            (repo_full, PROPOSAL_STATUS_PENDING),
        ).fetchall()
    return [_row_to_baseline_proposal(r) for r in rows]


def get_baseline_proposal(
    db_path: str,
    *,
    proposal_id: int,
    workspace_id: int,
) -> BaselineProposalRecord | None:
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM cp_baseline_proposals WHERE id = ? AND workspace_id = ?",
            (proposal_id, workspace_id),
        ).fetchone()
    return _row_to_baseline_proposal(row) if row else None


def approve_baseline_proposal(
    db_path: str,
    *,
    proposal_id: int,
    artifact_id: int,
    workspace_id: int,
    decision_principal_id: int,
    decision_note: str | None,
) -> BaselineProposalRecord:
    _validate_decision_note(decision_note)
    now = time.time()

    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM cp_baseline_proposals WHERE id = ? AND workspace_id = ? AND artifact_id = ?",
            (proposal_id, workspace_id, artifact_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Proposal not found.")
        proposal = _row_to_baseline_proposal(row)

        if proposal.status != PROPOSAL_STATUS_PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already '{proposal.status}' and cannot be approved.",
            )
        if proposal.expires_at < now:
            raise HTTPException(status_code=409, detail="Proposal has expired and can no longer be approved.")
        # Four-eyes: proposer and approver must be different principals.
        if proposal.proposer_principal_id == decision_principal_id:
            raise HTTPException(
                status_code=409,
                detail="Proposer and approver must be different principals (four-eyes rule).",
            )

        # Use a conditional UPDATE to guard against a TOCTOU race: if another
        # request concurrently approved or rejected this proposal, rowcount=0.
        result = conn.execute(
            """
            UPDATE cp_baseline_proposals
            SET status = ?, decision_principal_id = ?, decision_note = ?,
                decided_at = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (PROPOSAL_STATUS_APPROVED, decision_principal_id, decision_note, now, now,
             proposal_id, PROPOSAL_STATUS_PENDING),
        )
        if result.rowcount == 0:
            # Race lost — re-read and surface the current status.
            current_row = conn.execute(
                "SELECT status FROM cp_baseline_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            current_status = current_row["status"] if current_row else "unknown"
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already '{current_status}' and cannot be approved.",
            )
        updated_row = conn.execute(
            "SELECT * FROM cp_baseline_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
    return _row_to_baseline_proposal(updated_row)


def reject_baseline_proposal(
    db_path: str,
    *,
    proposal_id: int,
    artifact_id: int,
    workspace_id: int,
    decision_principal_id: int,
    decision_note: str | None,
) -> BaselineProposalRecord:
    _validate_decision_note(decision_note)
    now = time.time()

    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM cp_baseline_proposals WHERE id = ? AND workspace_id = ? AND artifact_id = ?",
            (proposal_id, workspace_id, artifact_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Proposal not found.")
        proposal = _row_to_baseline_proposal(row)

        if proposal.status != PROPOSAL_STATUS_PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already '{proposal.status}' and cannot be rejected.",
            )
        # Note: rejection of an expired proposal is intentionally permitted so
        # that operators can clean up stale proposals without being blocked.

        # Conditional UPDATE guards against concurrent approve/reject races.
        result = conn.execute(
            """
            UPDATE cp_baseline_proposals
            SET status = ?, decision_principal_id = ?, decision_note = ?,
                decided_at = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (PROPOSAL_STATUS_REJECTED, decision_principal_id, decision_note, now, now,
             proposal_id, PROPOSAL_STATUS_PENDING),
        )
        if result.rowcount == 0:
            current_row = conn.execute(
                "SELECT status FROM cp_baseline_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            current_status = current_row["status"] if current_row else "unknown"
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already '{current_status}' and cannot be rejected.",
            )
        updated_row = conn.execute(
            "SELECT * FROM cp_baseline_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
    return _row_to_baseline_proposal(updated_row)


# ---------------------------------------------------------------------------
# Repo onboarding proposal CRUD
# ---------------------------------------------------------------------------

def create_onboarding_proposal(
    db_path: str,
    *,
    workspace_id: int,
    repo_full: str,
    installation_id: int | None,
    proposed_category: str | None,
    rationale: str,
    metadata: dict[str, str],
    proposer_principal_id: int,
) -> RepoOnboardingProposalRecord:
    rationale = _validate_rationale(rationale)
    metadata_json = _validate_and_serialise_metadata(metadata)
    now = time.time()
    expires_at = now + PROPOSAL_EXPIRY_SECONDS

    with connect_sqlite(db_path) as conn:
        # Acquire an immediate write lock so COUNT + INSERT are atomic.
        conn.execute("BEGIN IMMEDIATE")
        # Duplicate-pending guard: reject a second proposal for the same repo
        # while one is already pending, rather than silently accumulating duplicates.
        existing = conn.execute(
            "SELECT id FROM cp_repo_onboarding_proposals WHERE workspace_id = ? AND repo_full = ? AND status = ?",
            (workspace_id, repo_full, PROPOSAL_STATUS_PENDING),
        ).fetchone()
        if existing is not None:
            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=409,
                detail="A pending onboarding proposal for this repository already exists.",
            )
        # Flood limit check
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM cp_repo_onboarding_proposals WHERE workspace_id = ? AND status = ?",
            (workspace_id, PROPOSAL_STATUS_PENDING),
        ).fetchone()[0]
        if pending_count >= MAX_PENDING_ONBOARDING_PROPOSALS_PER_WORKSPACE:
            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=409,
                detail=(
                    f"There are already {MAX_PENDING_ONBOARDING_PROPOSALS_PER_WORKSPACE} pending onboarding proposals "
                    "for this workspace. Resolve existing proposals before creating new ones."
                ),
            )

        conn.execute(
            """
            INSERT INTO cp_repo_onboarding_proposals (
                workspace_id, repo_full, proposal_kind, installation_id, proposed_category,
                rationale, metadata_json, status, proposer_principal_id,
                expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id, repo_full, PROPOSAL_KIND_ONBOARD, installation_id, proposed_category,
                rationale, metadata_json, PROPOSAL_STATUS_PENDING, proposer_principal_id,
                expires_at, now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM cp_repo_onboarding_proposals WHERE id = last_insert_rowid()"
        ).fetchone()
    return _row_to_onboarding_proposal(row)


def list_onboarding_proposals(
    db_path: str,
    *,
    workspace_id: int,
) -> list[RepoOnboardingProposalRecord]:
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM cp_repo_onboarding_proposals WHERE workspace_id = ? ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
    return [_row_to_onboarding_proposal(r) for r in rows]


def get_onboarding_proposal(
    db_path: str,
    *,
    proposal_id: int,
    workspace_id: int,
) -> RepoOnboardingProposalRecord | None:
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM cp_repo_onboarding_proposals WHERE id = ? AND workspace_id = ?",
            (proposal_id, workspace_id),
        ).fetchone()
    return _row_to_onboarding_proposal(row) if row else None


def approve_onboarding_proposal(
    db_path: str,
    *,
    proposal_id: int,
    workspace_id: int,
    decision_principal_id: int,
    decision_note: str | None,
) -> RepoOnboardingProposalRecord:
    _validate_decision_note(decision_note)
    now = time.time()

    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM cp_repo_onboarding_proposals WHERE id = ? AND workspace_id = ?",
            (proposal_id, workspace_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Proposal not found.")
        proposal = _row_to_onboarding_proposal(row)

        if proposal.status != PROPOSAL_STATUS_PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already '{proposal.status}' and cannot be approved.",
            )
        if proposal.expires_at < now:
            raise HTTPException(status_code=409, detail="Proposal has expired and can no longer be approved.")
        # Four-eyes: proposer and approver must be different principals.
        if proposal.proposer_principal_id == decision_principal_id:
            raise HTTPException(
                status_code=409,
                detail="Proposer and approver must be different principals (four-eyes rule).",
            )

        # Conditional UPDATE guards against concurrent approve/reject races.
        result = conn.execute(
            """
            UPDATE cp_repo_onboarding_proposals
            SET status = ?, decision_principal_id = ?, decision_note = ?,
                decided_at = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (PROPOSAL_STATUS_APPROVED, decision_principal_id, decision_note, now, now,
             proposal_id, PROPOSAL_STATUS_PENDING),
        )
        if result.rowcount == 0:
            current_row = conn.execute(
                "SELECT status FROM cp_repo_onboarding_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            current_status = current_row["status"] if current_row else "unknown"
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already '{current_status}' and cannot be approved.",
            )
        updated_row = conn.execute(
            "SELECT * FROM cp_repo_onboarding_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
    return _row_to_onboarding_proposal(updated_row)


def reject_onboarding_proposal(
    db_path: str,
    *,
    proposal_id: int,
    workspace_id: int,
    decision_principal_id: int,
    decision_note: str | None,
) -> RepoOnboardingProposalRecord:
    _validate_decision_note(decision_note)
    now = time.time()

    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM cp_repo_onboarding_proposals WHERE id = ? AND workspace_id = ?",
            (proposal_id, workspace_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Proposal not found.")
        proposal = _row_to_onboarding_proposal(row)

        if proposal.status != PROPOSAL_STATUS_PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already '{proposal.status}' and cannot be rejected.",
            )
        # Note: rejection of an expired proposal is intentionally permitted so
        # that operators can clean up stale proposals without being blocked.

        # Conditional UPDATE guards against concurrent approve/reject races.
        result = conn.execute(
            """
            UPDATE cp_repo_onboarding_proposals
            SET status = ?, decision_principal_id = ?, decision_note = ?,
                decided_at = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (PROPOSAL_STATUS_REJECTED, decision_principal_id, decision_note, now, now,
             proposal_id, PROPOSAL_STATUS_PENDING),
        )
        if result.rowcount == 0:
            current_row = conn.execute(
                "SELECT status FROM cp_repo_onboarding_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            current_status = current_row["status"] if current_row else "unknown"
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already '{current_status}' and cannot be rejected.",
            )
        updated_row = conn.execute(
            "SELECT * FROM cp_repo_onboarding_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
    return _row_to_onboarding_proposal(updated_row)
