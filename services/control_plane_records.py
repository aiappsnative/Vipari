from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from .persistence import connect_sqlite


CONTROL_PLANE_TABLES = (
    "users",
    "github_identities",
    "user_sessions",
    "workspaces",
    "workspace_memberships",
    "billing_customers",
    "subscriptions",
    "entitlements",
    "github_installations",
    "repo_connections",
    "repo_allocations",
    "control_plane_audit_logs",
    "webhook_event_receipts",
)


@dataclass(frozen=True)
class UserRecord:
    id: int
    display_name: str
    primary_email: str | None
    active: bool
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class GithubIdentityRecord:
    id: int
    user_id: int
    github_user_id: str
    github_login: str
    avatar_url: str | None
    granted_scopes: list[str]
    access_token_encrypted: str | None
    refresh_token_encrypted: str | None
    token_expires_at: float | None
    last_login_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class WorkspaceRecord:
    id: int
    slug: str
    display_name: str
    status: str
    billing_owner_user_id: int | None
    setup_state: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class WorkspaceMembershipRecord:
    id: int
    workspace_id: int
    user_id: int
    role: str
    invitation_state: str
    invited_by_user_id: int | None
    joined_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class UserSessionRecord:
    id: int
    session_id: str
    user_id: int
    workspace_id: int | None
    csrf_secret: str
    expires_at: float
    revoked_at: float | None
    last_seen_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class BillingCustomerRecord:
    id: int
    workspace_id: int
    stripe_customer_id: str
    billing_email: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class SubscriptionRecord:
    id: int
    workspace_id: int
    stripe_subscription_id: str
    stripe_price_id: str
    plan_code: str
    status: str
    cancel_at_period_end: bool
    current_period_start_at: float | None
    current_period_end_at: float | None
    trial_ends_at: float | None
    last_webhook_event_id: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class EntitlementRecord:
    id: int
    workspace_id: int
    plan_code: str
    subscription_status: str
    dashboard_enabled: bool
    repo_limit: int
    org_limit: int
    seat_limit: int
    retention_policy: str
    support_tier: str
    feature_flags_json: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class GithubInstallationRecord:
    id: int
    workspace_id: int | None
    installation_id: int
    account_id: str
    account_login: str
    account_type: str
    target_type: str
    status: str
    last_synced_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class RepoConnectionRecord:
    id: int
    installation_id: int
    workspace_id: int | None
    repo_github_id: str
    repo_full: str
    default_branch: str | None
    is_private: bool
    status: str
    last_synced_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class RepoAllocationRecord:
    id: int
    workspace_id: int
    installation_id: int
    repo_github_id: str
    repo_full: str
    allocation_status: str
    baseline_mode: str
    activated_by_user_id: int | None
    activated_at: float | None
    deactivated_at: float | None
    created_at: float
    updated_at: float


def _row_to_user(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        display_name=row["display_name"],
        primary_email=row["primary_email"],
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_github_identity(row: sqlite3.Row) -> GithubIdentityRecord:
    return GithubIdentityRecord(
        id=row["id"],
        user_id=row["user_id"],
        github_user_id=row["github_user_id"],
        github_login=row["github_login"],
        avatar_url=row["avatar_url"],
        granted_scopes=json.loads(row["granted_scopes_json"] or "[]"),
        access_token_encrypted=row["access_token_encrypted"],
        refresh_token_encrypted=row["refresh_token_encrypted"],
        token_expires_at=row["token_expires_at"],
        last_login_at=row["last_login_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_workspace(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=row["id"],
        slug=row["slug"],
        display_name=row["display_name"],
        status=row["status"],
        billing_owner_user_id=row["billing_owner_user_id"],
        setup_state=row["setup_state"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_membership(row: sqlite3.Row) -> WorkspaceMembershipRecord:
    return WorkspaceMembershipRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        user_id=row["user_id"],
        role=row["role"],
        invitation_state=row["invitation_state"],
        invited_by_user_id=row["invited_by_user_id"],
        joined_at=row["joined_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_session(row: sqlite3.Row) -> UserSessionRecord:
    return UserSessionRecord(
        id=row["id"],
        session_id=row["session_id"],
        user_id=row["user_id"],
        workspace_id=row["workspace_id"],
        csrf_secret=row["csrf_secret"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        last_seen_at=row["last_seen_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_subscription(row: sqlite3.Row) -> SubscriptionRecord:
    return SubscriptionRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        stripe_subscription_id=row["stripe_subscription_id"],
        stripe_price_id=row["stripe_price_id"],
        plan_code=row["plan_code"],
        status=row["status"],
        cancel_at_period_end=bool(row["cancel_at_period_end"]),
        current_period_start_at=row["current_period_start_at"],
        current_period_end_at=row["current_period_end_at"],
        trial_ends_at=row["trial_ends_at"],
        last_webhook_event_id=row["last_webhook_event_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_entitlement(row: sqlite3.Row) -> EntitlementRecord:
    return EntitlementRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        plan_code=row["plan_code"],
        subscription_status=row["subscription_status"],
        dashboard_enabled=bool(row["dashboard_enabled"]),
        repo_limit=row["repo_limit"],
        org_limit=row["org_limit"],
        seat_limit=row["seat_limit"],
        retention_policy=row["retention_policy"],
        support_tier=row["support_tier"],
        feature_flags_json=row["feature_flags_json"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_installation(row: sqlite3.Row) -> GithubInstallationRecord:
    return GithubInstallationRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        installation_id=row["installation_id"],
        account_id=row["account_id"],
        account_login=row["account_login"],
        account_type=row["account_type"],
        target_type=row["target_type"],
        status=row["status"],
        last_synced_at=row["last_synced_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_billing_customer(row: sqlite3.Row) -> BillingCustomerRecord:
    return BillingCustomerRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        stripe_customer_id=row["stripe_customer_id"],
        billing_email=row["billing_email"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_repo_connection(row: sqlite3.Row) -> RepoConnectionRecord:
    return RepoConnectionRecord(
        id=row["id"],
        installation_id=row["installation_id"],
        workspace_id=row["workspace_id"],
        repo_github_id=row["repo_github_id"],
        repo_full=row["repo_full"],
        default_branch=row["default_branch"],
        is_private=bool(row["is_private"]),
        status=row["status"],
        last_synced_at=row["last_synced_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_repo_allocation(row: sqlite3.Row) -> RepoAllocationRecord:
    return RepoAllocationRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        installation_id=row["installation_id"],
        repo_github_id=row["repo_github_id"],
        repo_full=row["repo_full"],
        allocation_status=row["allocation_status"],
        baseline_mode=row["baseline_mode"],
        activated_by_user_id=row["activated_by_user_id"],
        activated_at=row["activated_at"],
        deactivated_at=row["deactivated_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _connect(db_path: str) -> sqlite3.Connection:
    return connect_sqlite(db_path, foreign_keys=True)


def init_control_plane_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                primary_email TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS github_identities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                github_user_id TEXT NOT NULL UNIQUE,
                github_login TEXT NOT NULL,
                avatar_url TEXT,
                granted_scopes_json TEXT NOT NULL DEFAULT '[]',
                access_token_encrypted TEXT,
                refresh_token_encrypted TEXT,
                token_expires_at REAL,
                last_login_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                billing_owner_user_id INTEGER,
                setup_state TEXT NOT NULL DEFAULT 'workspace_no_subscription',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(billing_owner_user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                invitation_state TEXT NOT NULL DEFAULT 'accepted',
                invited_by_user_id INTEGER,
                joined_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(workspace_id, user_id),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(invited_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                workspace_id INTEGER,
                csrf_secret TEXT NOT NULL,
                expires_at REAL NOT NULL,
                revoked_at REAL,
                last_seen_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL UNIQUE,
                stripe_customer_id TEXT NOT NULL UNIQUE,
                billing_email TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                stripe_subscription_id TEXT NOT NULL UNIQUE,
                stripe_price_id TEXT NOT NULL,
                plan_code TEXT NOT NULL,
                status TEXT NOT NULL,
                cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                current_period_start_at REAL,
                current_period_end_at REAL,
                trial_ends_at REAL,
                last_webhook_event_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entitlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL UNIQUE,
                plan_code TEXT NOT NULL,
                subscription_status TEXT NOT NULL,
                dashboard_enabled INTEGER NOT NULL DEFAULT 0,
                repo_limit INTEGER NOT NULL DEFAULT 0,
                org_limit INTEGER NOT NULL DEFAULT 0,
                seat_limit INTEGER NOT NULL DEFAULT 0,
                retention_policy TEXT NOT NULL DEFAULT 'standard',
                support_tier TEXT NOT NULL DEFAULT 'community',
                feature_flags_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS github_installations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER,
                installation_id INTEGER NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                account_login TEXT NOT NULL,
                account_type TEXT NOT NULL,
                target_type TEXT NOT NULL,
                status TEXT NOT NULL,
                last_synced_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                installation_id INTEGER NOT NULL,
                workspace_id INTEGER,
                repo_github_id TEXT NOT NULL,
                repo_full TEXT NOT NULL,
                default_branch TEXT,
                is_private INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'available',
                last_synced_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(installation_id, repo_github_id),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL,
                FOREIGN KEY(installation_id) REFERENCES github_installations(installation_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                installation_id INTEGER NOT NULL,
                repo_github_id TEXT NOT NULL,
                repo_full TEXT NOT NULL,
                allocation_status TEXT NOT NULL,
                baseline_mode TEXT NOT NULL DEFAULT 'default_branch',
                activated_by_user_id INTEGER,
                activated_at REAL,
                deactivated_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(workspace_id, repo_github_id),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                FOREIGN KEY(installation_id) REFERENCES github_installations(installation_id) ON DELETE CASCADE,
                FOREIGN KEY(activated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS control_plane_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER,
                actor_user_id INTEGER,
                event_type TEXT NOT NULL,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL,
                FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_event_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                processed_at REAL,
                error_summary TEXT,
                created_at REAL NOT NULL,
                UNIQUE(provider, event_id)
            )
            """
        )


def upsert_github_identity(
    db_path: str,
    *,
    github_user_id: str,
    github_login: str,
    display_name: str,
    primary_email: str | None,
    avatar_url: str | None,
    granted_scopes: list[str],
    access_token_encrypted: str | None,
) -> tuple[UserRecord, GithubIdentityRecord]:
    now = time.time()
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM github_identities WHERE github_user_id = ?",
            (github_user_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO users (display_name, primary_email, active, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
                (display_name, primary_email, now, now),
            )
            user_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                INSERT INTO github_identities (
                    user_id, github_user_id, github_login, avatar_url, granted_scopes_json,
                    access_token_encrypted, refresh_token_encrypted, token_expires_at, last_login_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    user_id,
                    github_user_id,
                    github_login,
                    avatar_url,
                    json.dumps(granted_scopes),
                    access_token_encrypted,
                    now,
                    now,
                    now,
                ),
            )
        else:
            user_id = existing["user_id"]
            conn.execute(
                "UPDATE users SET display_name = ?, primary_email = ?, updated_at = ? WHERE id = ?",
                (display_name, primary_email, now, user_id),
            )
            conn.execute(
                """
                UPDATE github_identities
                SET github_login = ?, avatar_url = ?, granted_scopes_json = ?, access_token_encrypted = ?, last_login_at = ?, updated_at = ?
                WHERE github_user_id = ?
                """,
                (
                    github_login,
                    avatar_url,
                    json.dumps(granted_scopes),
                    access_token_encrypted,
                    now,
                    now,
                    github_user_id,
                ),
            )

        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        identity = _row_to_github_identity(
            conn.execute("SELECT * FROM github_identities WHERE github_user_id = ?", (github_user_id,)).fetchone()
        )
    return user, identity


def create_user_session(
    db_path: str,
    *,
    session_id: str,
    user_id: int,
    workspace_id: int | None,
    csrf_secret: str,
    expires_at: float,
) -> UserSessionRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO user_sessions (session_id, user_id, workspace_id, csrf_secret, expires_at, revoked_at, last_seen_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (session_id, user_id, workspace_id, csrf_secret, expires_at, now, now, now),
        )
        row = conn.execute("SELECT * FROM user_sessions WHERE session_id = ?", (session_id,)).fetchone()
    return _row_to_session(row)


def get_user_session(db_path: str, session_id: str) -> UserSessionRecord | None:
    now = time.time()
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM user_sessions WHERE session_id = ? AND revoked_at IS NULL AND expires_at > ?",
            (session_id, now),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE user_sessions SET last_seen_at = ?, updated_at = ? WHERE id = ?", (now, now, row["id"]))
        refreshed = conn.execute("SELECT * FROM user_sessions WHERE id = ?", (row["id"],)).fetchone()
    return _row_to_session(refreshed)


def get_user_by_id(db_path: str, user_id: int) -> UserRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def get_github_identity_for_user(db_path: str, user_id: int) -> GithubIdentityRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM github_identities WHERE user_id = ?", (user_id,)).fetchone()
    return _row_to_github_identity(row) if row else None


def revoke_user_session(db_path: str, session_id: str) -> None:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute("UPDATE user_sessions SET revoked_at = ?, updated_at = ? WHERE session_id = ?", (now, now, session_id))


def update_session_workspace(db_path: str, session_id: str, workspace_id: int) -> None:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE user_sessions SET workspace_id = ?, updated_at = ? WHERE session_id = ?",
            (workspace_id, now, session_id),
        )


def create_workspace(db_path: str, *, slug: str, display_name: str, billing_owner_user_id: int) -> WorkspaceRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO workspaces (slug, display_name, status, billing_owner_user_id, setup_state, created_at, updated_at) VALUES (?, ?, 'active', ?, 'workspace_no_subscription', ?, ?)",
            (slug, display_name, billing_owner_user_id, now, now),
        )
        workspace_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "INSERT INTO workspace_memberships (workspace_id, user_id, role, invitation_state, invited_by_user_id, joined_at, created_at, updated_at) VALUES (?, ?, 'owner', 'accepted', ?, ?, ?, ?)",
            (workspace_id, billing_owner_user_id, billing_owner_user_id, now, now, now),
        )
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return _row_to_workspace(row)


def list_workspace_memberships_for_user(db_path: str, user_id: int) -> list[WorkspaceMembershipRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM workspace_memberships WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    return [_row_to_membership(row) for row in rows]


def get_workspace_by_id(db_path: str, workspace_id: int) -> WorkspaceRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return _row_to_workspace(row) if row else None


def get_workspace_membership(db_path: str, workspace_id: int, user_id: int) -> WorkspaceMembershipRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM workspace_memberships WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        ).fetchone()
    return _row_to_membership(row) if row else None


def get_workspace_subscription(db_path: str, workspace_id: int) -> SubscriptionRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE workspace_id = ? ORDER BY updated_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
    return _row_to_subscription(row) if row else None


def get_workspace_entitlement(db_path: str, workspace_id: int) -> EntitlementRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM entitlements WHERE workspace_id = ?", (workspace_id,)).fetchone()
    return _row_to_entitlement(row) if row else None


def get_workspace_installation(db_path: str, workspace_id: int) -> GithubInstallationRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM github_installations WHERE workspace_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
    return _row_to_installation(row) if row else None


def count_workspace_repo_allocations(db_path: str, workspace_id: int) -> tuple[int, int]:
    with _connect(db_path) as conn:
        allocated = conn.execute(
            "SELECT COUNT(*) FROM repo_allocations WHERE workspace_id = ? AND allocation_status IN ('active', 'onboarded')",
            (workspace_id,),
        ).fetchone()[0]
        onboarded = conn.execute(
            "SELECT COUNT(*) FROM repo_allocations WHERE workspace_id = ? AND allocation_status = 'onboarded'",
            (workspace_id,),
        ).fetchone()[0]
    return int(allocated), int(onboarded)


def count_workspaces(db_path: str) -> int:
    with _connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
    return int(count)


def get_billing_customer_for_workspace(db_path: str, workspace_id: int) -> BillingCustomerRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM billing_customers WHERE workspace_id = ?", (workspace_id,)).fetchone()
    return _row_to_billing_customer(row) if row else None


def upsert_billing_customer(
    db_path: str,
    *,
    workspace_id: int,
    stripe_customer_id: str,
    billing_email: str | None,
) -> BillingCustomerRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO billing_customers (workspace_id, stripe_customer_id, billing_email, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                stripe_customer_id = excluded.stripe_customer_id,
                billing_email = excluded.billing_email,
                updated_at = excluded.updated_at
            """,
            (workspace_id, stripe_customer_id, billing_email, now, now),
        )
        row = conn.execute("SELECT * FROM billing_customers WHERE workspace_id = ?", (workspace_id,)).fetchone()
    return _row_to_billing_customer(row)


def upsert_subscription(
    db_path: str,
    *,
    workspace_id: int,
    stripe_subscription_id: str,
    stripe_price_id: str,
    plan_code: str,
    status: str,
    cancel_at_period_end: bool,
    current_period_start_at: float | None,
    current_period_end_at: float | None,
    trial_ends_at: float | None,
    last_webhook_event_id: str | None,
) -> SubscriptionRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (
                workspace_id, stripe_subscription_id, stripe_price_id, plan_code, status,
                cancel_at_period_end, current_period_start_at, current_period_end_at, trial_ends_at,
                last_webhook_event_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stripe_subscription_id) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                stripe_price_id = excluded.stripe_price_id,
                plan_code = excluded.plan_code,
                status = excluded.status,
                cancel_at_period_end = excluded.cancel_at_period_end,
                current_period_start_at = excluded.current_period_start_at,
                current_period_end_at = excluded.current_period_end_at,
                trial_ends_at = excluded.trial_ends_at,
                last_webhook_event_id = excluded.last_webhook_event_id,
                updated_at = excluded.updated_at
            """,
            (
                workspace_id,
                stripe_subscription_id,
                stripe_price_id,
                plan_code,
                status,
                int(cancel_at_period_end),
                current_period_start_at,
                current_period_end_at,
                trial_ends_at,
                last_webhook_event_id,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE stripe_subscription_id = ?",
            (stripe_subscription_id,),
        ).fetchone()
        conn.execute("UPDATE workspaces SET setup_state = ?, updated_at = ? WHERE id = ?", ("billing_pending_confirmation" if status in {"incomplete", "pending"} else "awaiting_github_install", now, workspace_id))
    return _row_to_subscription(row)


def upsert_entitlement(db_path: str, *, workspace_id: int, payload: dict[str, object]) -> EntitlementRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO entitlements (
                workspace_id, plan_code, subscription_status, dashboard_enabled, repo_limit, org_limit, seat_limit,
                retention_policy, support_tier, feature_flags_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                plan_code = excluded.plan_code,
                subscription_status = excluded.subscription_status,
                dashboard_enabled = excluded.dashboard_enabled,
                repo_limit = excluded.repo_limit,
                org_limit = excluded.org_limit,
                seat_limit = excluded.seat_limit,
                retention_policy = excluded.retention_policy,
                support_tier = excluded.support_tier,
                feature_flags_json = excluded.feature_flags_json,
                updated_at = excluded.updated_at
            """,
            (
                workspace_id,
                payload["plan_code"],
                payload["subscription_status"],
                int(bool(payload["dashboard_enabled"])),
                payload["repo_limit"],
                payload["org_limit"],
                payload["seat_limit"],
                payload["retention_policy"],
                payload["support_tier"],
                payload.get("feature_flags_json", "{}"),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM entitlements WHERE workspace_id = ?", (workspace_id,)).fetchone()
        setup_state = "awaiting_github_install" if bool(payload["dashboard_enabled"]) else "payment_failed"
        conn.execute("UPDATE workspaces SET setup_state = ?, updated_at = ? WHERE id = ?", (setup_state, now, workspace_id))
    return _row_to_entitlement(row)


def has_processed_webhook_event(db_path: str, provider: str, event_id: str) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM webhook_event_receipts WHERE provider = ? AND event_id = ? AND status = 'processed'",
            (provider, event_id),
        ).fetchone()
    return row is not None


def record_webhook_event(
    db_path: str,
    *,
    provider: str,
    event_id: str,
    event_type: str,
    status: str,
    error_summary: str | None = None,
) -> None:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO webhook_event_receipts (provider, event_id, event_type, status, processed_at, error_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, event_id) DO UPDATE SET
                event_type = excluded.event_type,
                status = excluded.status,
                processed_at = excluded.processed_at,
                error_summary = excluded.error_summary
            """,
            (provider, event_id, event_type, status, now if status == "processed" else None, error_summary, now),
        )


def upsert_github_installation(
    db_path: str,
    *,
    workspace_id: int,
    installation_id: int,
    account_id: str,
    account_login: str,
    account_type: str,
    target_type: str,
    status: str = "active",
) -> GithubInstallationRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO github_installations (
                workspace_id, installation_id, account_id, account_login, account_type, target_type, status, last_synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(installation_id) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                account_id = excluded.account_id,
                account_login = excluded.account_login,
                account_type = excluded.account_type,
                target_type = excluded.target_type,
                status = excluded.status,
                last_synced_at = excluded.last_synced_at,
                updated_at = excluded.updated_at
            """,
            (workspace_id, installation_id, account_id, account_login, account_type, target_type, status, now, now, now),
        )
        row = conn.execute("SELECT * FROM github_installations WHERE installation_id = ?", (installation_id,)).fetchone()
        conn.execute("UPDATE workspaces SET setup_state = 'awaiting_repo_onboarding', updated_at = ? WHERE id = ?", (now, workspace_id))
    return _row_to_installation(row)


def replace_repo_connections(db_path: str, *, workspace_id: int, installation_id: int, repositories: list[dict[str, object]]) -> None:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM repo_connections WHERE installation_id = ?", (installation_id,))
        for repo in repositories:
            conn.execute(
                """
                INSERT INTO repo_connections (
                    installation_id, workspace_id, repo_github_id, repo_full, default_branch, is_private, status, last_synced_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    installation_id,
                    workspace_id,
                    repo["repo_github_id"],
                    repo["repo_full"],
                    repo.get("default_branch"),
                    int(bool(repo.get("is_private", True))),
                    repo.get("status", "available"),
                    now,
                    now,
                    now,
                ),
            )


def list_repo_connections_for_workspace(db_path: str, workspace_id: int) -> list[RepoConnectionRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM repo_connections WHERE workspace_id = ? ORDER BY repo_full", (workspace_id,)).fetchall()
    return [_row_to_repo_connection(row) for row in rows]


def get_repo_connection_for_workspace(db_path: str, workspace_id: int, repo_full: str) -> RepoConnectionRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM repo_connections WHERE workspace_id = ? AND repo_full = ?",
            (workspace_id, repo_full),
        ).fetchone()
    return _row_to_repo_connection(row) if row else None


def list_repo_allocations_for_workspace(db_path: str, workspace_id: int) -> list[RepoAllocationRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM repo_allocations WHERE workspace_id = ? ORDER BY repo_full", (workspace_id,)).fetchall()
    return [_row_to_repo_allocation(row) for row in rows]


def get_repo_allocation_for_workspace(db_path: str, workspace_id: int, repo_full: str) -> RepoAllocationRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM repo_allocations WHERE workspace_id = ? AND repo_full = ?",
            (workspace_id, repo_full),
        ).fetchone()
    return _row_to_repo_allocation(row) if row else None


def allocate_repo_to_workspace(
    db_path: str,
    *,
    workspace_id: int,
    installation_id: int,
    repo_github_id: str,
    repo_full: str,
    baseline_mode: str,
    activated_by_user_id: int,
) -> RepoAllocationRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repo_allocations (
                workspace_id, installation_id, repo_github_id, repo_full, allocation_status, baseline_mode,
                activated_by_user_id, activated_at, deactivated_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(workspace_id, repo_github_id) DO UPDATE SET
                installation_id = excluded.installation_id,
                repo_full = excluded.repo_full,
                allocation_status = 'active',
                baseline_mode = excluded.baseline_mode,
                activated_by_user_id = excluded.activated_by_user_id,
                activated_at = excluded.activated_at,
                deactivated_at = NULL,
                updated_at = excluded.updated_at
            """,
            (workspace_id, installation_id, repo_github_id, repo_full, baseline_mode, activated_by_user_id, now, now, now),
        )
        row = conn.execute(
            "SELECT * FROM repo_allocations WHERE workspace_id = ? AND repo_github_id = ?",
            (workspace_id, repo_github_id),
        ).fetchone()
    return _row_to_repo_allocation(row)


def update_repo_allocation_status(db_path: str, allocation_id: int, allocation_status: str) -> RepoAllocationRecord:
    now = time.time()
    deactivated_at = now if allocation_status == "inactive" else None
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE repo_allocations SET allocation_status = ?, deactivated_at = ?, updated_at = ? WHERE id = ?",
            (allocation_status, deactivated_at, now, allocation_id),
        )
        row = conn.execute("SELECT * FROM repo_allocations WHERE id = ?", (allocation_id,)).fetchone()
    return _row_to_repo_allocation(row)