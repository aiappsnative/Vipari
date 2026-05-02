"""Internal control-plane JWT auth primitives.

This module is intentionally separate from ``services/github_integration.py``
even though both use PyJWT.  GitHub App JWTs are RS256-signed, short-lived,
and contain GitHub-specific claims.  Internal control-plane tokens are
HS256-signed, workspace-scoped, and carry explicit application scopes that
the ``/cp/*`` route layer enforces.  Mixing the two would conflate their
very different trust semantics.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import jwt


# ---------------------------------------------------------------------------
# Scope vocabulary
# ---------------------------------------------------------------------------

SCOPE_DRIFT_READ = "drift.read"
SCOPE_DRIFT_WRITE_LOW = "drift.write.low"
SCOPE_DRIFT_WRITE_HIGH = "drift.write.high"
SCOPE_ADMIN_READ = "admin.read"
SCOPE_ADMIN_WRITE = "admin.write"

ALL_SCOPES: frozenset[str] = frozenset(
    {
        SCOPE_DRIFT_READ,
        SCOPE_DRIFT_WRITE_LOW,
        SCOPE_DRIFT_WRITE_HIGH,
        SCOPE_ADMIN_READ,
        SCOPE_ADMIN_WRITE,
    }
)


# ---------------------------------------------------------------------------
# Principal kind vocabulary
# ---------------------------------------------------------------------------

PRINCIPAL_KIND_SERVICE_ACCOUNT = "service_account"
PRINCIPAL_KIND_HUMAN_OPERATOR = "human_operator"

ALL_PRINCIPAL_KINDS: frozenset[str] = frozenset(
    {PRINCIPAL_KIND_SERVICE_ACCOUNT, PRINCIPAL_KIND_HUMAN_OPERATOR}
)

# Scopes that require a human_operator principal.  Service accounts must
# never be granted these — enforcement happens at principal creation and
# again at every route that requires human oversight.
SCOPES_REQUIRING_HUMAN_OPERATOR: frozenset[str] = frozenset(
    {SCOPE_DRIFT_WRITE_HIGH, SCOPE_ADMIN_WRITE}
)


def validate_scope_kind_compatibility(principal_kind: str, scopes: list[str] | frozenset[str]) -> None:
    """Raise ValueError if *principal_kind* is incompatible with any scope in *scopes*.

    Service accounts must never hold human-only scopes.  Callers should turn
    ValueError into an appropriate HTTP 400 response.
    """
    if principal_kind == PRINCIPAL_KIND_SERVICE_ACCOUNT:
        human_only = frozenset(scopes) & SCOPES_REQUIRING_HUMAN_OPERATOR
        if human_only:
            raise ValueError(
                f"Scopes {sorted(human_only)} require principal_kind 'human_operator' and "
                "cannot be granted to a 'service_account'."
            )


# ---------------------------------------------------------------------------
# Claims model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MachinePrincipalClaims:
    """Validated claims extracted from a verified control-plane token."""

    subject: str           # machine_principals.client_id
    workspace_id: int
    scopes: frozenset[str]
    issued_at: float
    expires_at: float
    issuer: str
    audience: str


@dataclass(frozen=True)
class McpBrokerClaims:
    """Validated claims extracted from a verified MCP broker token."""

    subject: str
    workspace_id: int
    scopes: frozenset[str]
    issued_at: float
    expires_at: float
    issuer: str
    audience: str


# ---------------------------------------------------------------------------
# Issuance
# ---------------------------------------------------------------------------


def issue_cp_token(
    *,
    client_id: str,
    workspace_id: int,
    scopes: list[str],
    secret: str,
    issuer: str,
    audience: str,
    ttl_seconds: int,
) -> str:
    """Issue a signed HS256 JWT for a workspace-bound machine principal.

    The caller is responsible for validating that *scopes* is a subset of
    :data:`ALL_SCOPES` and that *workspace_id* matches the principal's stored
    record before calling this function.
    """
    now = int(time.time())
    payload = {
        "sub": client_id,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
        "workspace_id": workspace_id,
        "scopes": list(scopes),
        "typ": "cp",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def issue_mcp_broker_token(
    *,
    client_id: str,
    workspace_id: int,
    scopes: list[str],
    secret: str,
    issuer: str,
    audience: str,
    ttl_seconds: int,
) -> str:
    """Issue a signed HS256 JWT for MCP broker access only."""
    now = int(time.time())
    payload = {
        "sub": client_id,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
        "workspace_id": workspace_id,
        "scopes": list(scopes),
        "typ": "mcp_broker",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TokenValidationError(Exception):
    """Raised when a control-plane token cannot be validated."""


def validate_cp_token(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str,
) -> MachinePrincipalClaims:
    """Decode and validate a control-plane JWT.

    Raises :class:`TokenValidationError` for any validation failure so callers
    can map the exception to an HTTP 401 without leaking internals.
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["sub", "iss", "aud", "iat", "exp", "workspace_id", "scopes"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenValidationError("Token has expired.") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenValidationError("Token issuer is invalid.") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenValidationError("Token audience is invalid.") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise TokenValidationError(f"Token is missing required claim: {exc}") from exc
    except jwt.DecodeError as exc:
        raise TokenValidationError("Token signature is invalid or malformed.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenValidationError(f"Token is invalid: {exc}") from exc

    typ = payload.get("typ")
    if typ != "cp":
        raise TokenValidationError("Token type claim is not 'cp'.")

    subject = payload.get("sub")
    if not subject:
        raise TokenValidationError("Token subject is missing.")

    workspace_id_raw = payload.get("workspace_id")
    if not isinstance(workspace_id_raw, int):
        raise TokenValidationError("Token workspace_id claim is missing or not an integer.")

    scopes_raw = payload.get("scopes")
    if not isinstance(scopes_raw, list):
        raise TokenValidationError("Token scopes claim is missing or not a list.")

    aud_raw = payload.get("aud", "")
    audience_str = aud_raw if isinstance(aud_raw, str) else (aud_raw[0] if aud_raw else "")

    return MachinePrincipalClaims(
        subject=subject,
        workspace_id=int(workspace_id_raw),
        scopes=frozenset(scopes_raw),
        issued_at=float(payload["iat"]),
        expires_at=float(payload["exp"]),
        issuer=payload["iss"],
        audience=audience_str,
    )


def validate_mcp_broker_token(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str,
) -> McpBrokerClaims:
    """Decode and validate an MCP broker JWT."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["sub", "iss", "aud", "iat", "exp", "workspace_id", "scopes"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenValidationError("Token has expired.") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenValidationError("Token issuer is invalid.") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenValidationError("Token audience is invalid.") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise TokenValidationError(f"Token is missing required claim: {exc}") from exc
    except jwt.DecodeError as exc:
        raise TokenValidationError("Token signature is invalid or malformed.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenValidationError(f"Token is invalid: {exc}") from exc

    typ = payload.get("typ")
    if typ != "mcp_broker":
        raise TokenValidationError("Token type claim is not 'mcp_broker'.")

    subject = payload.get("sub")
    if not subject:
        raise TokenValidationError("Token subject is missing.")

    workspace_id_raw = payload.get("workspace_id")
    if not isinstance(workspace_id_raw, int):
        raise TokenValidationError("Token workspace_id claim is missing or not an integer.")

    scopes_raw = payload.get("scopes")
    if not isinstance(scopes_raw, list):
        raise TokenValidationError("Token scopes claim is missing or not a list.")

    aud_raw = payload.get("aud", "")
    audience_str = aud_raw if isinstance(aud_raw, str) else (aud_raw[0] if aud_raw else "")

    return McpBrokerClaims(
        subject=subject,
        workspace_id=int(workspace_id_raw),
        scopes=frozenset(scopes_raw),
        issued_at=float(payload["iat"]),
        expires_at=float(payload["exp"]),
        issuer=payload["iss"],
        audience=audience_str,
    )
