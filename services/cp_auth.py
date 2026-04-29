"""Shared FastAPI dependency helpers for the internal ``/cp/*`` route surface.

All ``/cp/*`` handlers must resolve the machine principal through these
helpers.  Direct JWT decoding inside route handlers is forbidden; use
:func:`require_cp_principal` instead.

Workspace isolation rule
------------------------
Every control-plane route that operates on a workspace-scoped resource must
call :func:`require_cp_workspace_match`.  The check is explicit and
intentional — it must not be buried in a database query.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from config import Settings
from .control_plane_records import MachinePrincipalRecord, get_machine_principal_by_client_id
from .internal_auth import (
    MachinePrincipalClaims,
    TokenValidationError,
    validate_cp_token,
)


def _extract_bearer_token(request: Request) -> str:
    """Pull the raw bearer token from the Authorization header.

    Returns the token string or raises HTTP 401.
    """
    authorization = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")
    token = authorization[len(prefix):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token.")
    return token


def require_cp_principal(
    request: Request,
    settings: Settings,
    db_path: str,
) -> tuple[MachinePrincipalClaims, MachinePrincipalRecord]:
    """Validate the bearer JWT and resolve the machine principal record.

    Raises HTTP 503 when the JWT signing secret is not configured (operator
    misconfiguration), HTTP 401 on any token or principal validation failure.

    Returns a ``(claims, principal)`` pair for the caller to use.  Callers
    should then call :func:`require_cp_scope` and
    :func:`require_cp_workspace_match` as appropriate.
    """
    if not settings.has_internal_jwt_config:
        raise HTTPException(
            status_code=503,
            detail="Internal JWT auth is not configured on this deployment.",
        )

    token = _extract_bearer_token(request)

    try:
        claims = validate_cp_token(
            token,
            secret=settings.internal_jwt_secret,
            issuer=settings.internal_jwt_issuer,
            audience=settings.internal_jwt_audience,
        )
    except TokenValidationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    principal = get_machine_principal_by_client_id(db_path, claims.subject)
    if principal is None:
        raise HTTPException(status_code=401, detail="Machine principal not found.")
    if principal.status != "active":
        raise HTTPException(
            status_code=401,
            detail="Machine principal is not active.",
        )

    return claims, principal


def require_cp_scope(
    claims: MachinePrincipalClaims,
    required_scope: str,
) -> None:
    """Assert the token carries *required_scope*.

    Raises HTTP 403 on scope mismatch.
    """
    if required_scope not in claims.scopes:
        raise HTTPException(
            status_code=403,
            detail=f"Scope '{required_scope}' is required but not granted to this principal.",
        )


def require_cp_workspace_match(
    claims: MachinePrincipalClaims,
    resource_workspace_id: int,
) -> None:
    """Assert the token's workspace matches the resource's workspace.

    Raises HTTP 403 on mismatch.  Cross-workspace access always fails closed.
    """
    if claims.workspace_id != resource_workspace_id:
        raise HTTPException(
            status_code=403,
            detail="Cross-workspace access is not permitted.",
        )


def require_cp_principal_kind(
    principal: MachinePrincipalRecord,
    allowed_kinds: frozenset[str],
) -> None:
    """Assert the resolved principal's kind is in *allowed_kinds*.

    This is the structural human-only gate for high-risk approval routes.
    Service accounts that somehow hold a high-risk scope token are still
    blocked here at the route layer.

    Raises HTTP 403 when the principal kind is not in *allowed_kinds*.
    """
    if principal.principal_kind not in allowed_kinds:
        raise HTTPException(
            status_code=403,
            detail=(
                f"This action requires a principal of kind {sorted(allowed_kinds)}. "
                f"Principal kind '{principal.principal_kind}' is not permitted."
            ),
        )
