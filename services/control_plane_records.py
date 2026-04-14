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
    "workspace_invites",
    "billing_customers",
    "subscriptions",
    "entitlements",
    "github_installations",
    "repo_connections",
    "repo_allocations",
    "billing_handoff_claims",
    "control_plane_audit_logs",
    "webhook_event_receipts",
)


@dataclass(frozen=True)
class UserRecord:
    id: int
    display_name: str
    profile_name_override: str | None
    theme_preference: str
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
    profile_url: str | None
    company: str | None
    blog: str | None
    location: str | None
    bio: str | None
    twitter_username: str | None
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
    pr_comments_setting_enabled: bool
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
class WorkspaceInviteRecord:
    id: int
    workspace_id: int
    invited_github_login: str
    role: str
    invitation_state: str
    invited_by_user_id: int | None
    accepted_user_id: int | None
    accepted_at: float | None
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
    next_payment_at: float | None
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
    pr_comments_enabled: bool
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


@dataclass(frozen=True)
class BillingHandoffClaimRecord:
    id: int
    claim_token: str
    provider: str
    external_purchase_id: str
    plan_code: str
    billing_status: str
    billing_email: str | None
    source: str | None
    claimed_workspace_id: int | None
    claimed_user_id: int | None
    next_payment_at: float | None
    expires_at: float
    consumed_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class AdminWorkspaceUserRecord:
    workspace_id: int | None
    workspace_slug: str | None
    workspace_display_name: str | None
    workspace_status: str | None
    workspace_billing_owner_user_id: int | None
    setup_state: str | None
    user_id: int
    user_display_name: str
    user_active: bool
    github_login: str | None
    github_user_id: str | None
    primary_email: str | None
    avatar_url: str | None
    github_profile_url: str | None
    github_company: str | None
    github_blog: str | None
    github_location: str | None
    github_bio: str | None
    github_twitter_username: str | None
    membership_role: str | None
    membership_state: str | None
    plan_code: str | None
    subscription_status: str | None
    dashboard_enabled: bool
    pr_comments_enabled: bool
    next_payment_at: float | None
    installation_id: int | None
    installation_account_login: str | None
    installation_count: int
    connected_repo_count: int
    allocated_repo_count: int
    onboarded_repo_count: int
    last_login_at: float | None


@dataclass(frozen=True)
class AdminInstallationRecord:
    installation_id: int
    workspace_id: int | None
    account_login: str
    account_type: str
    target_type: str
    status: str
    repo_count: int
    last_synced_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class AdminBillingClaimRecord:
    claim_token: str
    provider: str
    external_purchase_id: str
    plan_code: str
    billing_status: str
    billing_email: str | None
    source: str | None
    claimed_workspace_id: int | None
    claimed_user_id: int | None
    next_payment_at: float | None
    expires_at: float
    consumed_at: float | None
    updated_at: float


@dataclass(frozen=True)
class ControlPlaneAuditLogRecord:
    id: int
    workspace_id: int | None
    actor_user_id: int | None
    event_type: str
    subject_type: str
    subject_id: str
    payload_json: str
    created_at: float


def _row_to_user(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        display_name=row["profile_name_override"] or row["display_name"],
        profile_name_override=row["profile_name_override"],
        theme_preference=row["theme_preference"] or "dark",
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
        profile_url=row["profile_url"],
        company=row["company"],
        blog=row["blog"],
        location=row["location"],
        bio=row["bio"],
        twitter_username=row["twitter_username"],
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
        pr_comments_setting_enabled=bool(row["pr_comments_setting_enabled"]),
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


def _row_to_workspace_invite(row: sqlite3.Row) -> WorkspaceInviteRecord:
    return WorkspaceInviteRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        invited_github_login=row["invited_github_login"],
        role=row["role"],
        invitation_state=row["invitation_state"],
        invited_by_user_id=row["invited_by_user_id"],
        accepted_user_id=row["accepted_user_id"],
        accepted_at=row["accepted_at"],
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
        next_payment_at=row["next_payment_at"],
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
        pr_comments_enabled=bool(row["pr_comments_enabled"]),
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


def _row_to_billing_handoff_claim(row: sqlite3.Row) -> BillingHandoffClaimRecord:
    return BillingHandoffClaimRecord(
        id=row["id"],
        claim_token=row["claim_token"],
        provider=row["provider"],
        external_purchase_id=row["external_purchase_id"],
        plan_code=row["plan_code"],
        billing_status=row["billing_status"],
        billing_email=row["billing_email"],
        source=row["source"],
        claimed_workspace_id=row["claimed_workspace_id"],
        claimed_user_id=row["claimed_user_id"],
        next_payment_at=row["next_payment_at"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_control_plane_audit_log(row: sqlite3.Row) -> ControlPlaneAuditLogRecord:
    return ControlPlaneAuditLogRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        actor_user_id=row["actor_user_id"],
        event_type=row["event_type"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        payload_json=row["payload_json"],
        created_at=row["created_at"],
    )


def _connect(db_path: str) -> sqlite3.Connection:
    return connect_sqlite(db_path, foreign_keys=True)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    if column_name in _table_columns(conn, table_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

def _repo_connections_needs_rebuild(conn: sqlite3.Connection) -> bool:
    foreign_keys = conn.execute("PRAGMA foreign_key_list(repo_connections)").fetchall()
    for foreign_key in foreign_keys:
        # PRAGMA foreign_key_list columns: id, seq, table, from, to, on_update, on_delete, match
        if foreign_key[2] == "github_installations" and foreign_key[3] == "installation_id":
            return foreign_key[4] != "installation_id"
    return False


def _rebuild_repo_connections_table(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT * FROM repo_connections").fetchall()
    normalized_rows: list[tuple[object, ...]] = []
    for row in rows:
        row_dict = dict(row)
        normalized_rows.append(
            (
                row_dict.get("id"),
                row_dict["installation_id"],
                row_dict.get("workspace_id"),
                row_dict["repo_github_id"],
                row_dict["repo_full"],
                row_dict.get("default_branch"),
                int(bool(row_dict.get("is_private", 1))),
                row_dict.get("status", "available"),
                row_dict.get("last_synced_at"),
                row_dict["created_at"],
                row_dict["updated_at"],
            )
        )

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE repo_connections RENAME TO repo_connections_legacy")
    conn.execute(
        """
        CREATE TABLE repo_connections (
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
    if normalized_rows:
        conn.executemany(
            """
            INSERT INTO repo_connections (
                id, installation_id, workspace_id, repo_github_id, repo_full, default_branch,
                is_private, status, last_synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            normalized_rows,
        )
    conn.execute("DROP TABLE repo_connections_legacy")
    conn.execute("PRAGMA foreign_keys=ON")


def _repo_allocations_needs_rebuild(conn: sqlite3.Connection) -> bool:
    foreign_keys = conn.execute("PRAGMA foreign_key_list(repo_allocations)").fetchall()
    for foreign_key in foreign_keys:
        if foreign_key[2] == "github_installations" and foreign_key[3] == "installation_id":
            return foreign_key[4] != "installation_id"
    return False


def _rebuild_repo_allocations_table(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT * FROM repo_allocations").fetchall()
    normalized_rows: list[tuple[object, ...]] = []
    for row in rows:
        row_dict = dict(row)
        normalized_rows.append(
            (
                row_dict.get("id"),
                row_dict["workspace_id"],
                row_dict["installation_id"],
                row_dict["repo_github_id"],
                row_dict["repo_full"],
                row_dict["allocation_status"],
                row_dict.get("baseline_mode", "default_branch"),
                row_dict.get("activated_by_user_id"),
                row_dict.get("activated_at"),
                row_dict.get("deactivated_at"),
                row_dict["created_at"],
                row_dict["updated_at"],
            )
        )

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE repo_allocations RENAME TO repo_allocations_legacy")
    conn.execute(
        """
        CREATE TABLE repo_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL,
            installation_id INTEGER NOT NULL,
            repo_github_id TEXT NOT NULL,
            repo_full TEXT NOT NULL,
            allocation_status TEXT NOT NULL,
            baseline_mode TEXT NOT NULL,
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
    if normalized_rows:
        conn.executemany(
            """
            INSERT INTO repo_allocations (
                id, workspace_id, installation_id, repo_github_id, repo_full, allocation_status,
                baseline_mode, activated_by_user_id, activated_at, deactivated_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            normalized_rows,
        )
    conn.execute("DROP TABLE repo_allocations_legacy")
    conn.execute("PRAGMA foreign_keys=ON")


def init_control_plane_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                profile_name_override TEXT,
                theme_preference TEXT NOT NULL DEFAULT 'dark',
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
                profile_url TEXT,
                company TEXT,
                blog TEXT,
                location TEXT,
                bio TEXT,
                twitter_username TEXT,
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
                pr_comments_setting_enabled INTEGER NOT NULL DEFAULT 1,
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
            CREATE TABLE IF NOT EXISTS workspace_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                invited_github_login TEXT NOT NULL,
                role TEXT NOT NULL,
                invitation_state TEXT NOT NULL DEFAULT 'pending',
                invited_by_user_id INTEGER,
                accepted_user_id INTEGER,
                accepted_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(workspace_id, invited_github_login),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                FOREIGN KEY(invited_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(accepted_user_id) REFERENCES users(id) ON DELETE SET NULL
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
                next_payment_at REAL,
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
                pr_comments_enabled INTEGER NOT NULL DEFAULT 0,
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
            CREATE TABLE IF NOT EXISTS billing_handoff_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_token TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                external_purchase_id TEXT NOT NULL UNIQUE,
                plan_code TEXT NOT NULL,
                billing_status TEXT NOT NULL,
                billing_email TEXT,
                source TEXT,
                claimed_workspace_id INTEGER,
                claimed_user_id INTEGER,
                next_payment_at REAL,
                expires_at REAL NOT NULL,
                consumed_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(claimed_workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL,
                FOREIGN KEY(claimed_user_id) REFERENCES users(id) ON DELETE SET NULL
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

        # Additive migrations for databases created before the latest control-plane schema.
        _ensure_column(conn, "users", "profile_name_override", "TEXT")
        _ensure_column(conn, "users", "theme_preference", "TEXT NOT NULL DEFAULT 'dark'")
        _ensure_column(conn, "workspaces", "setup_state", "TEXT NOT NULL DEFAULT 'workspace_no_subscription'")
        _ensure_column(conn, "workspaces", "pr_comments_setting_enabled", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "user_sessions", "workspace_id", "INTEGER")
        _ensure_column(conn, "user_sessions", "last_seen_at", "REAL")
        _ensure_column(conn, "billing_customers", "billing_email", "TEXT")
        _ensure_column(conn, "subscriptions", "cancel_at_period_end", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "subscriptions", "current_period_start_at", "REAL")
        _ensure_column(conn, "subscriptions", "current_period_end_at", "REAL")
        _ensure_column(conn, "subscriptions", "next_payment_at", "REAL")
        _ensure_column(conn, "subscriptions", "trial_ends_at", "REAL")
        _ensure_column(conn, "subscriptions", "last_webhook_event_id", "TEXT")
        _ensure_column(conn, "entitlements", "feature_flags_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "entitlements", "pr_comments_enabled", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "billing_handoff_claims", "next_payment_at", "REAL")
        _ensure_column(conn, "github_identities", "profile_url", "TEXT")
        _ensure_column(conn, "github_identities", "company", "TEXT")
        _ensure_column(conn, "github_identities", "blog", "TEXT")
        _ensure_column(conn, "github_identities", "location", "TEXT")
        _ensure_column(conn, "github_identities", "bio", "TEXT")
        _ensure_column(conn, "github_identities", "twitter_username", "TEXT")
        _ensure_column(conn, "github_installations", "workspace_id", "INTEGER")
        _ensure_column(conn, "github_installations", "target_type", "TEXT NOT NULL DEFAULT 'Organization'")
        _ensure_column(conn, "github_installations", "status", "TEXT NOT NULL DEFAULT 'active'")
        _ensure_column(conn, "github_installations", "last_synced_at", "REAL")
        _ensure_column(conn, "repo_connections", "workspace_id", "INTEGER")
        _ensure_column(conn, "repo_connections", "status", "TEXT NOT NULL DEFAULT 'available'")
        _ensure_column(conn, "repo_connections", "last_synced_at", "REAL")
        if _repo_connections_needs_rebuild(conn):
            _rebuild_repo_connections_table(conn)
        _ensure_column(conn, "repo_allocations", "baseline_mode", "TEXT NOT NULL DEFAULT 'default_branch'")
        _ensure_column(conn, "repo_allocations", "activated_by_user_id", "INTEGER")
        _ensure_column(conn, "repo_allocations", "activated_at", "REAL")
        _ensure_column(conn, "repo_allocations", "deactivated_at", "REAL")
        if _repo_allocations_needs_rebuild(conn):
            _rebuild_repo_allocations_table(conn)
        _ensure_column(conn, "webhook_event_receipts", "processed_at", "REAL")
        _ensure_column(conn, "webhook_event_receipts", "error_summary", "TEXT")


def upsert_github_identity(
    db_path: str,
    *,
    github_user_id: str,
    github_login: str,
    display_name: str,
    primary_email: str | None,
    avatar_url: str | None,
    profile_url: str | None = None,
    company: str | None = None,
    blog: str | None = None,
    location: str | None = None,
    bio: str | None = None,
    twitter_username: str | None = None,
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
                    user_id, github_user_id, github_login, avatar_url, profile_url, company, blog, location, bio, twitter_username, granted_scopes_json,
                    access_token_encrypted, refresh_token_encrypted, token_expires_at, last_login_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    user_id,
                    github_user_id,
                    github_login,
                    avatar_url,
                    profile_url,
                    company,
                    blog,
                    location,
                    bio,
                    twitter_username,
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
                SET github_login = ?, avatar_url = ?, profile_url = ?, company = ?, blog = ?, location = ?, bio = ?, twitter_username = ?, granted_scopes_json = ?, access_token_encrypted = ?, last_login_at = ?, updated_at = ?
                WHERE github_user_id = ?
                """,
                (
                    github_login,
                    avatar_url,
                    profile_url,
                    company,
                    blog,
                    location,
                    bio,
                    twitter_username,
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


def create_user(db_path: str, *, display_name: str, primary_email: str | None, active: bool = True) -> UserRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users (display_name, profile_name_override, primary_email, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (display_name, display_name, primary_email, int(bool(active)), now, now),
        )
        row = conn.execute("SELECT * FROM users WHERE id = last_insert_rowid()").fetchone()
    return _row_to_user(row)


def update_user_admin_fields(
    db_path: str,
    user_id: int,
    *,
    display_name: str,
    primary_email: str | None,
    active: bool,
) -> UserRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET display_name = ?, profile_name_override = ?, primary_email = ?, active = ?, updated_at = ? WHERE id = ?",
            (display_name, display_name, primary_email, int(bool(active)), now, user_id),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise ValueError("User not found.")
    return _row_to_user(row)


def delete_user(db_path: str, user_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


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


def update_user_profile_display_name(db_path: str, user_id: int, display_name: str) -> UserRecord:
    existing_user = get_user_by_id(db_path, user_id)
    return update_user_profile_preferences(
        db_path,
        user_id,
        display_name=display_name,
        theme_preference=existing_user.theme_preference if existing_user else "dark",
    )


def update_user_profile_preferences(
    db_path: str,
    user_id: int,
    *,
    display_name: str,
    theme_preference: str,
) -> UserRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET profile_name_override = ?, theme_preference = ?, updated_at = ? WHERE id = ?",
            (display_name, theme_preference, now, user_id),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row)


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
            "INSERT INTO workspaces (slug, display_name, status, billing_owner_user_id, setup_state, pr_comments_setting_enabled, created_at, updated_at) VALUES (?, ?, 'active', ?, 'workspace_no_subscription', 1, ?, ?)",
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


def update_workspace_pr_comments_setting(db_path: str, workspace_id: int, *, enabled: bool) -> WorkspaceRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE workspaces SET pr_comments_setting_enabled = ?, updated_at = ? WHERE id = ?",
            (int(bool(enabled)), now, workspace_id),
        )
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return _row_to_workspace(row)


def update_workspace_display_name(db_path: str, workspace_id: int, *, display_name: str) -> WorkspaceRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE workspaces SET display_name = ?, updated_at = ? WHERE id = ?",
            (display_name, now, workspace_id),
        )
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return _row_to_workspace(row)


def update_workspace_admin_fields(db_path: str, workspace_id: int, *, slug: str, display_name: str) -> WorkspaceRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE workspaces SET slug = ?, display_name = ?, updated_at = ? WHERE id = ?",
            (slug, display_name, now, workspace_id),
        )
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if row is None:
        raise ValueError("Workspace not found.")
    return _row_to_workspace(row)


def delete_workspace(db_path: str, workspace_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))


def get_workspace_membership(db_path: str, workspace_id: int, user_id: int) -> WorkspaceMembershipRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM workspace_memberships WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        ).fetchone()
    return _row_to_membership(row) if row else None


def upsert_workspace_membership(
    db_path: str,
    *,
    workspace_id: int,
    user_id: int,
    role: str,
    invitation_state: str = "accepted",
    invited_by_user_id: int | None = None,
) -> WorkspaceMembershipRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workspace_memberships (
                workspace_id, user_id, role, invitation_state, invited_by_user_id, joined_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, user_id) DO UPDATE SET
                role = excluded.role,
                invitation_state = excluded.invitation_state,
                invited_by_user_id = excluded.invited_by_user_id,
                joined_at = excluded.joined_at,
                updated_at = excluded.updated_at
            """,
            (workspace_id, user_id, role, invitation_state, invited_by_user_id, now, now, now),
        )
        row = conn.execute(
            "SELECT * FROM workspace_memberships WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        ).fetchone()
        _refresh_workspace_setup_state(conn, workspace_id)
    if row is None:
        raise ValueError("Workspace membership was not saved.")
    return _row_to_membership(row)


def delete_workspace_membership(db_path: str, *, workspace_id: int, user_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM workspace_memberships WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        )
        _refresh_workspace_setup_state(conn, workspace_id)


def list_workspace_invites_for_workspace(db_path: str, workspace_id: int) -> list[WorkspaceInviteRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM workspace_invites WHERE workspace_id = ? AND invitation_state = 'pending' ORDER BY invited_github_login COLLATE NOCASE, id",
            (workspace_id,),
        ).fetchall()
    return [_row_to_workspace_invite(row) for row in rows]


def upsert_workspace_invite(
    db_path: str,
    *,
    workspace_id: int,
    invited_github_login: str,
    role: str,
    invited_by_user_id: int | None,
) -> WorkspaceInviteRecord:
    now = time.time()
    normalized_login = invited_github_login.strip().lower()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workspace_invites (
                workspace_id, invited_github_login, role, invitation_state, invited_by_user_id, accepted_user_id, accepted_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, NULL, NULL, ?, ?)
            ON CONFLICT(workspace_id, invited_github_login) DO UPDATE SET
                role = excluded.role,
                invitation_state = 'pending',
                invited_by_user_id = excluded.invited_by_user_id,
                accepted_user_id = NULL,
                accepted_at = NULL,
                updated_at = excluded.updated_at
            """,
            (workspace_id, normalized_login, role, invited_by_user_id, now, now),
        )
        row = conn.execute(
            "SELECT * FROM workspace_invites WHERE workspace_id = ? AND invited_github_login = ?",
            (workspace_id, normalized_login),
        ).fetchone()
    return _row_to_workspace_invite(row)


def accept_workspace_invites_for_github_login(db_path: str, *, user_id: int, github_login: str) -> list[WorkspaceMembershipRecord]:
    now = time.time()
    normalized_login = github_login.strip().lower()
    accepted_memberships: list[WorkspaceMembershipRecord] = []
    with _connect(db_path) as conn:
        invite_rows = conn.execute(
            "SELECT * FROM workspace_invites WHERE invited_github_login = ? AND invitation_state = 'pending'",
            (normalized_login,),
        ).fetchall()
        for invite_row in invite_rows:
            invite = _row_to_workspace_invite(invite_row)
            membership_row = conn.execute(
                "SELECT * FROM workspace_memberships WHERE workspace_id = ? AND user_id = ?",
                (invite.workspace_id, user_id),
            ).fetchone()
            if membership_row is None:
                conn.execute(
                    """
                    INSERT INTO workspace_memberships (
                        workspace_id, user_id, role, invitation_state, invited_by_user_id, joined_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'accepted', ?, ?, ?, ?)
                    """,
                    (invite.workspace_id, user_id, invite.role, invite.invited_by_user_id, now, now, now),
                )
                membership_row = conn.execute(
                    "SELECT * FROM workspace_memberships WHERE workspace_id = ? AND user_id = ?",
                    (invite.workspace_id, user_id),
                ).fetchone()
            conn.execute(
                "UPDATE workspace_invites SET invitation_state = 'accepted', accepted_user_id = ?, accepted_at = ?, updated_at = ? WHERE id = ?",
                (user_id, now, now, invite.id),
            )
            _refresh_workspace_setup_state(conn, invite.workspace_id)
            if membership_row is not None:
                accepted_memberships.append(_row_to_membership(membership_row))
    return accepted_memberships


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


def get_github_installation_by_installation_id(db_path: str, installation_id: int) -> GithubInstallationRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM github_installations WHERE installation_id = ? ORDER BY updated_at DESC LIMIT 1",
            (installation_id,),
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


def get_billing_customer_by_stripe_customer_id(db_path: str, stripe_customer_id: str) -> BillingCustomerRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM billing_customers WHERE stripe_customer_id = ?", (stripe_customer_id,)).fetchone()
    return _row_to_billing_customer(row) if row else None


def get_billing_handoff_claim_by_token(db_path: str, claim_token: str) -> BillingHandoffClaimRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM billing_handoff_claims WHERE claim_token = ?", (claim_token,)).fetchone()
    return _row_to_billing_handoff_claim(row) if row else None


def _refresh_workspace_setup_state(conn: sqlite3.Connection, workspace_id: int) -> None:
    now = time.time()
    entitlement = conn.execute("SELECT * FROM entitlements WHERE workspace_id = ?", (workspace_id,)).fetchone()
    if entitlement is None:
        setup_state = "workspace_no_subscription"
    else:
        subscription_status = str(entitlement["subscription_status"] or "").lower()
        if subscription_status in {"incomplete", "pending", "trialing_pending"}:
            setup_state = "billing_pending_confirmation"
        elif subscription_status in {"past_due", "unpaid", "payment_failed", "incomplete_expired", "expired"}:
            setup_state = "payment_failed"
        elif not bool(entitlement["pr_comments_enabled"]):
            setup_state = "payment_failed"
        else:
            installation = conn.execute(
                "SELECT 1 FROM github_installations WHERE workspace_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
            allocated = conn.execute(
                "SELECT COUNT(*) FROM repo_allocations WHERE workspace_id = ? AND allocation_status IN ('active', 'onboarded')",
                (workspace_id,),
            ).fetchone()[0]
            onboarded = conn.execute(
                "SELECT COUNT(*) FROM repo_allocations WHERE workspace_id = ? AND allocation_status = 'onboarded'",
                (workspace_id,),
            ).fetchone()[0]
            if installation is None:
                setup_state = "awaiting_github_install"
            elif int(allocated) <= 0 or int(onboarded) <= 0:
                setup_state = "awaiting_repo_onboarding"
            elif bool(entitlement["dashboard_enabled"]):
                setup_state = "active"
            else:
                setup_state = "active_comments_only"
    conn.execute("UPDATE workspaces SET setup_state = ?, updated_at = ? WHERE id = ?", (setup_state, now, workspace_id))


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


def create_billing_handoff_claim(
    db_path: str,
    *,
    claim_token: str,
    provider: str,
    external_purchase_id: str,
    plan_code: str,
    billing_status: str,
    billing_email: str | None,
    source: str | None,
    next_payment_at: float | None,
    expires_at: float,
) -> BillingHandoffClaimRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO billing_handoff_claims (
                claim_token, provider, external_purchase_id, plan_code, billing_status, billing_email, source,
                claimed_workspace_id, claimed_user_id, next_payment_at, expires_at, consumed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, ?, ?)
            ON CONFLICT(external_purchase_id) DO UPDATE SET
                claim_token = excluded.claim_token,
                provider = excluded.provider,
                plan_code = excluded.plan_code,
                billing_status = excluded.billing_status,
                billing_email = excluded.billing_email,
                source = excluded.source,
                next_payment_at = excluded.next_payment_at,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (claim_token, provider, external_purchase_id, plan_code, billing_status, billing_email, source, next_payment_at, expires_at, now, now),
        )
        row = conn.execute("SELECT * FROM billing_handoff_claims WHERE external_purchase_id = ?", (external_purchase_id,)).fetchone()
    return _row_to_billing_handoff_claim(row)


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
    next_payment_at: float | None,
    trial_ends_at: float | None,
    last_webhook_event_id: str | None,
) -> SubscriptionRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (
                workspace_id, stripe_subscription_id, stripe_price_id, plan_code, status,
                cancel_at_period_end, current_period_start_at, current_period_end_at, next_payment_at, trial_ends_at,
                last_webhook_event_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stripe_subscription_id) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                stripe_price_id = excluded.stripe_price_id,
                plan_code = excluded.plan_code,
                status = excluded.status,
                cancel_at_period_end = excluded.cancel_at_period_end,
                current_period_start_at = excluded.current_period_start_at,
                current_period_end_at = excluded.current_period_end_at,
                next_payment_at = excluded.next_payment_at,
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
                next_payment_at if next_payment_at is not None else current_period_end_at,
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
        _refresh_workspace_setup_state(conn, workspace_id)
    return _row_to_subscription(row)


def get_subscription_by_stripe_subscription_id(db_path: str, stripe_subscription_id: str) -> SubscriptionRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM subscriptions WHERE stripe_subscription_id = ?", (stripe_subscription_id,)).fetchone()
    return _row_to_subscription(row) if row else None


def upsert_entitlement(db_path: str, *, workspace_id: int, payload: dict[str, object]) -> EntitlementRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO entitlements (
                workspace_id, plan_code, subscription_status, dashboard_enabled, pr_comments_enabled, repo_limit, org_limit, seat_limit,
                retention_policy, support_tier, feature_flags_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                plan_code = excluded.plan_code,
                subscription_status = excluded.subscription_status,
                dashboard_enabled = excluded.dashboard_enabled,
                pr_comments_enabled = excluded.pr_comments_enabled,
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
                int(bool(payload.get("pr_comments_enabled", False))),
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
        _refresh_workspace_setup_state(conn, workspace_id)
    return _row_to_entitlement(row)


def activate_billing_handoff_claim(
    db_path: str,
    *,
    claim_token: str,
    workspace_id: int,
    user_id: int,
) -> BillingHandoffClaimRecord:
    from .entitlements import derive_entitlement_payload

    now = time.time()
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM billing_handoff_claims WHERE claim_token = ?", (claim_token,)).fetchone()
        if row is None:
            raise ValueError("Unknown billing handoff claim.")
        claim = _row_to_billing_handoff_claim(row)
        if claim.consumed_at is not None and (claim.claimed_workspace_id != workspace_id or claim.claimed_user_id != user_id):
            raise ValueError("Billing handoff claim is already consumed.")
        if claim.expires_at < now:
            raise ValueError("Billing handoff claim has expired.")
        if not claim.billing_email:
            raise ValueError("Billing handoff claim is missing a billing email and cannot be securely activated.")

        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user_row is None:
            raise ValueError("Authenticated user not found.")
        user = _row_to_user(user_row)
        normalized_user_email = (user.primary_email or "").strip().lower()
        normalized_claim_email = claim.billing_email.strip().lower()
        if not normalized_user_email or normalized_user_email != normalized_claim_email:
            raise ValueError("Billing handoff claim does not belong to this user.")

        synthetic_customer_id = f"{claim.provider}:customer:{claim.external_purchase_id}"
        synthetic_subscription_id = f"{claim.provider}:subscription:{claim.external_purchase_id}"
        synthetic_price_id = f"{claim.provider}:plan:{claim.plan_code}"

        conn.execute(
            """
            INSERT INTO billing_customers (workspace_id, stripe_customer_id, billing_email, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                stripe_customer_id = excluded.stripe_customer_id,
                billing_email = excluded.billing_email,
                updated_at = excluded.updated_at
            """,
            (workspace_id, synthetic_customer_id, claim.billing_email, now, now),
        )
        conn.execute(
            """
            INSERT INTO subscriptions (
                workspace_id, stripe_subscription_id, stripe_price_id, plan_code, status,
                cancel_at_period_end, current_period_start_at, current_period_end_at, next_payment_at, trial_ends_at,
                last_webhook_event_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(stripe_subscription_id) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                stripe_price_id = excluded.stripe_price_id,
                plan_code = excluded.plan_code,
                status = excluded.status,
                current_period_end_at = excluded.current_period_end_at,
                next_payment_at = excluded.next_payment_at,
                last_webhook_event_id = excluded.last_webhook_event_id,
                updated_at = excluded.updated_at
            """,
            (
                workspace_id,
                synthetic_subscription_id,
                synthetic_price_id,
                claim.plan_code,
                claim.billing_status,
                claim.next_payment_at,
                claim.next_payment_at,
                claim.external_purchase_id,
                now,
                now,
            ),
        )

        payload = derive_entitlement_payload(claim.plan_code, claim.billing_status)
        conn.execute(
            """
            INSERT INTO entitlements (
                workspace_id, plan_code, subscription_status, dashboard_enabled, pr_comments_enabled, repo_limit, org_limit, seat_limit,
                retention_policy, support_tier, feature_flags_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                plan_code = excluded.plan_code,
                subscription_status = excluded.subscription_status,
                dashboard_enabled = excluded.dashboard_enabled,
                pr_comments_enabled = excluded.pr_comments_enabled,
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
                int(bool(payload.get("pr_comments_enabled", False))),
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
        conn.execute(
            "UPDATE billing_handoff_claims SET claimed_workspace_id = ?, claimed_user_id = ?, consumed_at = ?, updated_at = ? WHERE id = ?",
            (workspace_id, user_id, now, now, claim.id),
        )
        _refresh_workspace_setup_state(conn, workspace_id)
        updated = conn.execute("SELECT * FROM billing_handoff_claims WHERE id = ?", (claim.id,)).fetchone()
    return _row_to_billing_handoff_claim(updated)


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


def create_control_plane_audit_log(
    db_path: str,
    *,
    workspace_id: int | None,
    actor_user_id: int | None,
    event_type: str,
    subject_type: str,
    subject_id: str,
    payload: dict[str, object] | None = None,
) -> ControlPlaneAuditLogRecord:
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO control_plane_audit_logs (
                workspace_id, actor_user_id, event_type, subject_type, subject_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (workspace_id, actor_user_id, event_type, subject_type, subject_id, json.dumps(payload or {}, sort_keys=True), now),
        )
        row = conn.execute("SELECT * FROM control_plane_audit_logs WHERE id = last_insert_rowid()").fetchone()
    return _row_to_control_plane_audit_log(row)


def upsert_github_installation(
    db_path: str,
    *,
    workspace_id: int | None,
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
        if workspace_id is not None:
            _refresh_workspace_setup_state(conn, workspace_id)
    return _row_to_installation(row)


def replace_repo_connections(db_path: str, *, workspace_id: int | None, installation_id: int, repositories: list[dict[str, object]]) -> None:
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


def list_admin_workspace_users(db_path: str) -> list[AdminWorkspaceUserRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                w.id AS workspace_id,
                w.slug AS workspace_slug,
                w.display_name AS workspace_display_name,
                w.status AS workspace_status,
                w.billing_owner_user_id AS workspace_billing_owner_user_id,
                w.setup_state AS setup_state,
                u.id AS user_id,
                COALESCE(u.profile_name_override, u.display_name) AS user_display_name,
                COALESCE(u.active, 1) AS user_active,
                gi.github_login AS github_login,
                gi.github_user_id AS github_user_id,
                u.primary_email AS primary_email,
                gi.avatar_url AS avatar_url,
                gi.profile_url AS github_profile_url,
                gi.company AS github_company,
                gi.blog AS github_blog,
                gi.location AS github_location,
                gi.bio AS github_bio,
                gi.twitter_username AS github_twitter_username,
                wm.role AS membership_role,
                wm.invitation_state AS membership_state,
                e.plan_code AS plan_code,
                e.subscription_status AS subscription_status,
                COALESCE(e.dashboard_enabled, 0) AS dashboard_enabled,
                COALESCE(e.pr_comments_enabled, 0) AS pr_comments_enabled,
                s.next_payment_at AS next_payment_at,
                inst.installation_id AS installation_id,
                inst.account_login AS installation_account_login,
                COALESCE(inst.installation_count, 0) AS installation_count,
                COALESCE(conns.connected_repo_count, 0) AS connected_repo_count,
                COALESCE(alloc.allocated_repo_count, 0) AS allocated_repo_count,
                COALESCE(alloc.onboarded_repo_count, 0) AS onboarded_repo_count,
                gi.last_login_at AS last_login_at
            FROM users u
            LEFT JOIN github_identities gi ON gi.user_id = u.id
            LEFT JOIN workspace_memberships wm ON wm.user_id = u.id
            LEFT JOIN workspaces w ON w.id = wm.workspace_id
            LEFT JOIN entitlements e ON e.workspace_id = w.id
            LEFT JOIN subscriptions s ON s.workspace_id = w.id
            LEFT JOIN (
                SELECT
                    gi.workspace_id AS workspace_id,
                    MAX(CASE WHEN rc.installation_id IS NOT NULL THEN gi.installation_id ELSE NULL END) AS installation_id,
                    MIN(CASE WHEN rc.installation_id IS NOT NULL THEN gi.account_login ELSE NULL END) AS account_login,
                    COUNT(DISTINCT rc.installation_id) AS installation_count
                FROM github_installations gi
                LEFT JOIN repo_connections rc
                    ON rc.workspace_id = gi.workspace_id
                    AND rc.installation_id = gi.installation_id
                WHERE gi.workspace_id IS NOT NULL AND gi.status = 'active'
                GROUP BY gi.workspace_id
            ) inst ON inst.workspace_id = w.id
            LEFT JOIN (
                SELECT
                    workspace_id,
                    COUNT(DISTINCT repo_full) AS connected_repo_count
                FROM repo_connections
                WHERE workspace_id IS NOT NULL
                GROUP BY workspace_id
            ) conns ON conns.workspace_id = w.id
            LEFT JOIN (
                SELECT
                    workspace_id,
                    SUM(CASE WHEN allocation_status IN ('active', 'onboarded') THEN 1 ELSE 0 END) AS allocated_repo_count,
                    SUM(CASE WHEN allocation_status = 'onboarded' THEN 1 ELSE 0 END) AS onboarded_repo_count
                FROM repo_allocations
                GROUP BY workspace_id
            ) alloc ON alloc.workspace_id = w.id
            ORDER BY COALESCE(w.updated_at, u.updated_at) DESC, u.id ASC
            """
        ).fetchall()
    return [
        AdminWorkspaceUserRecord(
            workspace_id=row["workspace_id"],
            workspace_slug=row["workspace_slug"],
            workspace_display_name=row["workspace_display_name"],
            workspace_status=row["workspace_status"],
            workspace_billing_owner_user_id=row["workspace_billing_owner_user_id"],
            setup_state=row["setup_state"],
            user_id=row["user_id"],
            user_display_name=row["user_display_name"],
            user_active=bool(row["user_active"]),
            github_login=row["github_login"],
            github_user_id=row["github_user_id"],
            primary_email=row["primary_email"],
            avatar_url=row["avatar_url"],
            github_profile_url=row["github_profile_url"],
            github_company=row["github_company"],
            github_blog=row["github_blog"],
            github_location=row["github_location"],
            github_bio=row["github_bio"],
            github_twitter_username=row["github_twitter_username"],
            membership_role=row["membership_role"],
            membership_state=row["membership_state"],
            plan_code=row["plan_code"],
            subscription_status=row["subscription_status"],
            dashboard_enabled=bool(row["dashboard_enabled"]),
            pr_comments_enabled=bool(row["pr_comments_enabled"]),
            next_payment_at=row["next_payment_at"],
            installation_id=row["installation_id"],
            installation_account_login=row["installation_account_login"],
            installation_count=int(row["installation_count"] or 0),
            connected_repo_count=int(row["connected_repo_count"] or 0),
            allocated_repo_count=int(row["allocated_repo_count"] or 0),
            onboarded_repo_count=int(row["onboarded_repo_count"] or 0),
            last_login_at=row["last_login_at"],
        )
        for row in rows
    ]


def list_recent_control_plane_audit_logs(db_path: str, *, limit: int = 20) -> list[ControlPlaneAuditLogRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM control_plane_audit_logs ORDER BY created_at DESC, id DESC LIMIT ?",
            (max(int(limit), 1),),
        ).fetchall()
    return [_row_to_control_plane_audit_log(row) for row in rows]


def list_unclaimed_installations(db_path: str) -> list[AdminInstallationRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                inst.installation_id,
                inst.workspace_id,
                inst.account_login,
                inst.account_type,
                inst.target_type,
                inst.status,
                COALESCE(repo_counts.repo_count, 0) AS repo_count,
                inst.last_synced_at,
                inst.created_at,
                inst.updated_at
            FROM github_installations inst
            LEFT JOIN (
                SELECT installation_id, COUNT(*) AS repo_count
                FROM repo_connections
                GROUP BY installation_id
            ) repo_counts ON repo_counts.installation_id = inst.installation_id
            WHERE inst.workspace_id IS NULL
            ORDER BY inst.updated_at DESC
            """
        ).fetchall()
    return [
        AdminInstallationRecord(
            installation_id=row["installation_id"],
            workspace_id=row["workspace_id"],
            account_login=row["account_login"],
            account_type=row["account_type"],
            target_type=row["target_type"],
            status=row["status"],
            repo_count=int(row["repo_count"] or 0),
            last_synced_at=row["last_synced_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def list_billing_handoff_claims(db_path: str) -> list[AdminBillingClaimRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM billing_handoff_claims ORDER BY updated_at DESC
            """
        ).fetchall()
    return [
        AdminBillingClaimRecord(
            claim_token=row["claim_token"],
            provider=row["provider"],
            external_purchase_id=row["external_purchase_id"],
            plan_code=row["plan_code"],
            billing_status=row["billing_status"],
            billing_email=row["billing_email"],
            source=row["source"],
            claimed_workspace_id=row["claimed_workspace_id"],
            claimed_user_id=row["claimed_user_id"],
            next_payment_at=row["next_payment_at"],
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


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


def get_repo_allocation_for_installation(db_path: str, installation_id: int, repo_full: str) -> RepoAllocationRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM repo_allocations WHERE installation_id = ? AND repo_full = ? AND allocation_status IN ('active', 'onboarded') ORDER BY updated_at DESC LIMIT 1",
            (installation_id, repo_full),
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
        if row is not None:
            _refresh_workspace_setup_state(conn, row["workspace_id"])
    return _row_to_repo_allocation(row)