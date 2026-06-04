from __future__ import annotations

import collections
import hmac
import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from config import Settings
from .audit_feedback_records import (
    VALID_FEEDBACK_KINDS,
    VALID_TRIAGE_STATES,
    add_audit_feedback,
    add_audit_triage,
)
from .audit_records import get_pull_request_audit_by_id
from .control_plane_records import (
    create_control_plane_audit_log,
    get_machine_principal_by_client_id,
    get_repo_allocation_for_workspace,
    get_workspace_budget_status,
    get_workspace_entitlement,
    list_repo_allocations_for_workspace,
)
from .dashboard_views import build_repo_dashboard_view, build_workspace_escalation_queue, list_repo_dashboard_index
from .export_jobs import create_export_job, get_export_job
from .internal_auth import (
    ALL_SCOPES,
    SCOPE_DRIFT_WRITE_LOW,
    TokenValidationError,
    issue_mcp_broker_token,
    validate_mcp_broker_token,
    validate_scope_kind_compatibility,
)
from .onboarding_records import get_onboarded_artifact_by_id
from .proposals_records import create_baseline_proposal, create_onboarding_proposal, list_baseline_proposals, list_onboarding_proposals
from .secure_store import decrypt_text


MCP_READ_SCOPE = "drift.read"
MCP_BROKER_AUDIENCE_SUFFIX = "/mcp-broker"
MCP_BROKER_TOKEN_TTL_SECONDS = 900


class _SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._buckets: dict[str, collections.deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = collections.deque()
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
            return True


_mcp_token_endpoint_limiter = _SlidingWindowRateLimiter(limit=20, window_seconds=60.0)
_mcp_invoke_limiter = _SlidingWindowRateLimiter(limit=120, window_seconds=60.0)
_mcp_mutation_limiter = _SlidingWindowRateLimiter(limit=12, window_seconds=60.0)

_MCP_MUTATING_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "vipari.create_compliance_export",
        "vipari.create_baseline_proposal",
        "vipari.create_onboarding_proposal",
        "vipari.add_audit_feedback",
        "vipari.triage_audit",
    }
)


@dataclass(frozen=True)
class McpBrokerPrincipalContext:
    principal_id: int
    client_id: str
    display_name: str
    workspace_id: int
    scopes: frozenset[str]


def issue_mcp_broker_token_via_client_credentials(
    client_id: str,
    client_secret: str,
    *,
    settings: Settings,
    db_path: str,
    client_ip: str,
) -> dict[str, Any]:
    if not settings.has_internal_jwt_config:
        _raise_mcp_server_error(error="service_unavailable", message="Internal JWT auth is not configured.")
    if not _mcp_token_endpoint_limiter.allow(client_ip):
        exc = HTTPException(
            status_code=429,
            detail="Too many requests. Please retry after 60 seconds.",
            headers={"Retry-After": "60"},
        )
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=None,
            subject_id=client_id or client_ip,
            event_type="mcp_broker.token_denied",
            payload={**_mcp_exception_payload(exc), "client_ip": client_ip},
        )
        raise exc

    principal = _authenticate_principal_credentials(
        client_id,
        client_secret,
        settings=settings,
        db_path=db_path,
    )
    try:
        scopes = sorted(_parse_validated_principal_scopes(principal))
    except ValueError:
        exc = HTTPException(status_code=401, detail="Invalid client credentials.")
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=principal.workspace_id,
            subject_id=principal.client_id,
            event_type="mcp_broker.token_denied",
            payload=_mcp_exception_payload(exc),
        )
        raise exc
    token = issue_mcp_broker_token(
        client_id=principal.client_id,
        workspace_id=principal.workspace_id,
        scopes=scopes,
        secret=settings.internal_jwt_secret,
        issuer=settings.internal_jwt_issuer,
        audience=_mcp_broker_audience(settings),
        ttl_seconds=MCP_BROKER_TOKEN_TTL_SECONDS,
    )
    create_control_plane_audit_log(
        db_path,
        workspace_id=principal.workspace_id,
        actor_user_id=None,
        event_type="mcp_broker.token_issued",
        subject_type="machine_principal",
        subject_id=principal.client_id,
    )
    return {
        "token": token,
        "client_id": principal.client_id,
        "workspace_id": principal.workspace_id,
        "ttl_seconds": MCP_BROKER_TOKEN_TTL_SECONDS,
    }


MCP_BROKER_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "vipari.list_available_tools",
        "title": "List available tools",
        "description": "Return the current principal's granted scopes and the broker-exposed tools available to this token.",
        "required_scope": MCP_READ_SCOPE,
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "vipari.list_repos",
        "title": "List workspace repositories",
        "description": "List repositories allocated to the authenticated workspace with lightweight posture context.",
        "required_scope": MCP_READ_SCOPE,
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "vipari.get_repo_posture",
        "title": "Get repository posture",
        "description": "Return the current review posture, top reasons, and next action for one workspace-visible repository.",
        "required_scope": MCP_READ_SCOPE,
        "input_schema": {
            "type": "object",
            "required": ["repo_full"],
            "properties": {
                "repo_full": {"type": "string"},
            },
        },
    },
    {
        "name": "vipari.get_repo_casefile",
        "title": "Get repository case file",
        "description": "Return a compact repository case file with baseline status, leading findings, and review targets.",
        "required_scope": MCP_READ_SCOPE,
        "input_schema": {
            "type": "object",
            "required": ["repo_full"],
            "properties": {
                "repo_full": {"type": "string"},
            },
        },
    },
    {
        "name": "vipari.list_escalations",
        "title": "List workspace escalations",
        "description": "Return the workspace escalation queue with review-now and optional watch items.",
        "required_scope": MCP_READ_SCOPE,
        "input_schema": {
            "type": "object",
            "properties": {
                "include_watch": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "vipari.get_workspace_budget_status",
        "title": "Get workspace budget status",
        "description": "Return the current advanced analysis budget status and feature breakdown for the authenticated workspace.",
        "required_scope": MCP_READ_SCOPE,
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "vipari.get_export_status",
        "title": "Get export status",
        "description": "Return the current status for one workspace-owned compliance export job.",
        "required_scope": MCP_READ_SCOPE,
        "input_schema": {
            "type": "object",
            "required": ["export_id"],
            "properties": {
                "export_id": {"type": "integer", "minimum": 1},
            },
        },
    },
    {
        "name": "vipari.create_compliance_export",
        "title": "Create compliance export",
        "description": "Create a low-risk compliance export job for one workspace-visible repository.",
        "required_scope": SCOPE_DRIFT_WRITE_LOW,
        "input_schema": {
            "type": "object",
            "required": ["repo_full", "from_date", "to_date", "export_mode"],
            "properties": {
                "repo_full": {"type": "string"},
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
                "export_mode": {"type": "string", "enum": ["compliance", "compliance_plus_drift"]},
                "include_artifact_content": {"type": "boolean"},
            },
        },
    },
    {
        "name": "vipari.list_baseline_proposals",
        "title": "List baseline proposals",
        "description": "List baseline proposals for one workspace-visible artifact.",
        "required_scope": MCP_READ_SCOPE,
        "input_schema": {
            "type": "object",
            "required": ["artifact_id"],
            "properties": {
                "artifact_id": {"type": "integer", "minimum": 1},
            },
        },
    },
    {
        "name": "vipari.create_baseline_proposal",
        "title": "Create baseline proposal",
        "description": "Create a low-risk baseline proposal for one workspace-visible artifact.",
        "required_scope": SCOPE_DRIFT_WRITE_LOW,
        "input_schema": {
            "type": "object",
            "required": ["artifact_id"],
            "properties": {
                "artifact_id": {"type": "integer", "minimum": 1},
                "snapshot_id": {"type": "integer", "minimum": 1},
                "rationale": {"type": "string"},
                "linked_audit_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}},
                "metadata": {"type": "object"},
            },
        },
    },
    {
        "name": "vipari.list_onboarding_proposals",
        "title": "List onboarding proposals",
        "description": "List onboarding proposals for the authenticated workspace.",
        "required_scope": MCP_READ_SCOPE,
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "vipari.create_onboarding_proposal",
        "title": "Create onboarding proposal",
        "description": "Create a low-risk onboarding proposal for a repository in the authenticated workspace.",
        "required_scope": SCOPE_DRIFT_WRITE_LOW,
        "input_schema": {
            "type": "object",
            "required": ["repo_full"],
            "properties": {
                "repo_full": {"type": "string"},
                "installation_id": {"type": "integer", "minimum": 1},
                "proposed_category": {"type": "string"},
                "rationale": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    },
    {
        "name": "vipari.add_audit_feedback",
        "title": "Add audit feedback",
        "description": "Append low-risk structured feedback to an existing repository audit visible to the workspace.",
        "required_scope": SCOPE_DRIFT_WRITE_LOW,
        "input_schema": {
            "type": "object",
            "required": ["audit_id", "kind"],
            "properties": {
                "audit_id": {"type": "integer", "minimum": 1},
                "kind": {"type": "string", "enum": sorted(VALID_FEEDBACK_KINDS)},
                "source": {"type": "string"},
                "comment": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    },
    {
        "name": "vipari.triage_audit",
        "title": "Triage audit",
        "description": "Record a low-risk triage state transition for an existing repository audit visible to the workspace.",
        "required_scope": SCOPE_DRIFT_WRITE_LOW,
        "input_schema": {
            "type": "object",
            "required": ["audit_id", "state"],
            "properties": {
                "audit_id": {"type": "integer", "minimum": 1},
                "state": {"type": "string", "enum": sorted(VALID_TRIAGE_STATES)},
                "reason": {"type": "string"},
            },
        },
    },
)


_MCP_TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    tool["name"]: tool for tool in MCP_BROKER_TOOLS
}


def list_mcp_tools_for_scopes(scopes: frozenset[str]) -> list[dict[str, Any]]:
    return [tool for tool in MCP_BROKER_TOOLS if tool["required_scope"] in scopes]


def authenticate_mcp_broker_request(
    authorization_header: str | None,
    *,
    settings: Settings,
    db_path: str,
) -> McpBrokerPrincipalContext:
    if not settings.has_internal_jwt_config:
        _raise_mcp_server_error(error="service_unavailable", message="Internal JWT auth is not configured.")
    try:
        token = _extract_bearer_token(authorization_header)
    except HTTPException as exc:
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=None,
            subject_id="unknown",
            event_type="mcp_broker.auth_denied",
            payload=_mcp_exception_payload(exc),
        )
        raise
    try:
        claims = validate_mcp_broker_token(
            token,
            secret=settings.internal_jwt_secret,
            issuer=settings.internal_jwt_issuer,
            audience=_mcp_broker_audience(settings),
        )
    except TokenValidationError as exc:
        denial_exc = HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": str(exc),
            },
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=None,
            subject_id="unknown",
            event_type="mcp_broker.auth_denied",
            payload=_mcp_exception_payload(denial_exc),
        )
        _raise_mcp_auth_error(
            error="invalid_token",
            message=str(exc),
            authenticate_value='Bearer error="invalid_token"',
        )

    principal = get_machine_principal_by_client_id(db_path, claims.subject)
    if principal is None:
        denial_exc = HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": "Machine principal not found.",
            },
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=None,
            subject_id=claims.subject,
            event_type="mcp_broker.auth_denied",
            payload=_mcp_exception_payload(denial_exc),
        )
        _raise_mcp_auth_error(
            error="invalid_token",
            message="Machine principal not found.",
            authenticate_value='Bearer error="invalid_token"',
        )
    if principal.status != "active":
        denial_exc = HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": "Machine principal is not active.",
            },
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=principal.workspace_id,
            subject_id=principal.client_id,
            event_type="mcp_broker.auth_denied",
            payload=_mcp_exception_payload(denial_exc),
        )
        _raise_mcp_auth_error(
            error="invalid_token",
            message="Machine principal is not active.",
            authenticate_value='Bearer error="invalid_token"',
        )
    if claims.workspace_id != principal.workspace_id:
        denial_exc = HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": "Token workspace does not match principal.",
            },
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=principal.workspace_id,
            subject_id=principal.client_id,
            event_type="mcp_broker.auth_denied",
            payload=_mcp_exception_payload(denial_exc),
        )
        _raise_mcp_auth_error(
            error="invalid_token",
            message="Token workspace does not match principal.",
            authenticate_value='Bearer error="invalid_token"',
        )
    try:
        current_scopes = _parse_validated_principal_scopes(principal)
    except ValueError as exc:
        denial_exc = HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": str(exc),
            },
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=principal.workspace_id,
            subject_id=principal.client_id,
            event_type="mcp_broker.auth_denied",
            payload=_mcp_exception_payload(denial_exc),
        )
        _raise_mcp_auth_error(
            error="invalid_token",
            message=str(exc),
            authenticate_value='Bearer error="invalid_token"',
        )
    try:
        _require_mcp_workspace_enabled(settings=settings, db_path=db_path, workspace_id=principal.workspace_id)
    except HTTPException as exc:
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=principal.workspace_id,
            subject_id=principal.client_id,
            event_type="mcp_broker.auth_denied",
            payload=_mcp_exception_payload(exc),
        )
        raise

    if not _mcp_invoke_limiter.allow(principal.client_id):
        exc = HTTPException(
            status_code=429,
            detail="Too many MCP broker requests. Please retry after 60 seconds.",
            headers={"Retry-After": "60"},
        )
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=principal.workspace_id,
            subject_id=principal.client_id,
            event_type="mcp_broker.auth_denied",
            payload=_mcp_exception_payload(exc),
        )
        raise exc

    return McpBrokerPrincipalContext(
        principal_id=principal.id,
        client_id=principal.client_id,
        display_name=principal.display_name,
        workspace_id=principal.workspace_id,
        scopes=claims.scopes & current_scopes,
    )


def record_mcp_broker_invocation(
    *,
    db_path: str,
    context: McpBrokerPrincipalContext,
    tool_name: str,
) -> None:
    create_control_plane_audit_log(
        db_path,
        workspace_id=context.workspace_id,
        actor_user_id=None,
        event_type="mcp_broker.tool_invoked",
        subject_type="machine_principal",
        subject_id=context.client_id,
        payload={"tool_name": tool_name},
    )


def record_mcp_broker_denial(
    *,
    db_path: str,
    workspace_id: int | None,
    subject_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    create_control_plane_audit_log(
        db_path,
        workspace_id=workspace_id,
        actor_user_id=None,
        event_type=event_type,
        subject_type="machine_principal",
        subject_id=subject_id,
        payload=payload,
    )


def _authenticate_principal_credentials(
    client_id: str,
    client_secret: str,
    *,
    settings: Settings,
    db_path: str,
):
    generic_401 = "Invalid client credentials."

    principal = get_machine_principal_by_client_id(db_path, client_id)
    if principal is None:
        hmac.compare_digest(secrets.token_urlsafe(32).encode(), client_secret.encode())
        exc = HTTPException(status_code=401, detail=generic_401)
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=None,
            subject_id=client_id or "unknown",
            event_type="mcp_broker.token_denied",
            payload=_mcp_exception_payload(exc),
        )
        raise exc

    if not settings.has_encryption_key:
        exc = HTTPException(status_code=503, detail="APP_ENCRYPTION_KEY must be configured.")
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=principal.workspace_id,
            subject_id=principal.client_id,
            event_type="mcp_broker.token_denied",
            payload=_mcp_exception_payload(exc),
        )
        raise exc

    decrypted_secret = decrypt_text(principal.client_secret_encrypted, settings.app_encryption_key)
    if not hmac.compare_digest(decrypted_secret.encode(), client_secret.encode()):
        exc = HTTPException(status_code=401, detail=generic_401)
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=principal.workspace_id,
            subject_id=principal.client_id,
            event_type="mcp_broker.token_denied",
            payload=_mcp_exception_payload(exc),
        )
        raise exc
    if principal.status != "active":
        exc = HTTPException(status_code=401, detail=generic_401)
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=principal.workspace_id,
            subject_id=principal.client_id,
            event_type="mcp_broker.token_denied",
            payload=_mcp_exception_payload(exc),
        )
        raise exc

    if settings.is_production:
        entitlement = get_workspace_entitlement(db_path, principal.workspace_id)
        flags = json.loads(entitlement.feature_flags_json) if entitlement and entitlement.feature_flags_json else {}
        if flags.get("cp_api_enabled", True) is False:
            exc = HTTPException(status_code=403, detail="Control plane API is not enabled for this workspace.")
            record_mcp_broker_denial(
                db_path=db_path,
                workspace_id=principal.workspace_id,
                subject_id=principal.client_id,
                event_type="mcp_broker.token_denied",
                payload=_mcp_exception_payload(exc),
            )
            raise exc

    return principal



def invoke_mcp_broker_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    context: McpBrokerPrincipalContext,
    db_path: str,
) -> dict[str, Any]:
    try:
        tool = _resolve_mcp_tool_definition(tool_name)
        _require_scope(context, str(tool["required_scope"]))
        _require_tool_rate_limit(context, str(tool["name"]))
        handler = _resolve_mcp_tool_handler(tool_name)
        if handler is None:
            _raise_mcp_client_error(status_code=404, error="tool_not_found", message="MCP tool not found.")
        return handler(arguments or {}, context=context, db_path=db_path)
    except HTTPException as exc:
        record_mcp_broker_denial(
            db_path=db_path,
            workspace_id=context.workspace_id,
            subject_id=context.client_id,
            event_type="mcp_broker.tool_denied",
            payload={
                **_mcp_exception_payload(exc),
                "tool_name": tool_name,
                "argument_keys": sorted(str(key) for key in (arguments or {}).keys()),
            },
        )
        raise


def _extract_bearer_token(header_value: str | None) -> str:
    prefix = "Bearer "
    if not header_value or not header_value.startswith(prefix):
        _raise_mcp_auth_error(
            error="invalid_request",
            message="Missing or malformed Authorization header.",
            authenticate_value="Bearer",
        )
    token = header_value[len(prefix):].strip()
    if not token:
        _raise_mcp_auth_error(
            error="invalid_request",
            message="Empty bearer token.",
            authenticate_value="Bearer",
        )
    return token


def _mcp_broker_audience(settings: Settings) -> str:
    return f"{settings.internal_jwt_audience}{MCP_BROKER_AUDIENCE_SUFFIX}"


def _resolve_mcp_tool_definition(tool_name: str) -> dict[str, Any]:
    canonical_name = tool_name.replace("promptdrift.", "vipari.", 1)
    tool = _MCP_TOOL_DEFINITIONS.get(canonical_name)
    if tool is None:
        _raise_mcp_client_error(status_code=404, error="tool_not_found", message="MCP tool not found.")
    return tool


def _resolve_mcp_tool_handler(tool_name: str):
    handlers = {
        "vipari.list_available_tools": _tool_list_available_tools,
        "vipari.list_repos": _tool_list_repos,
        "vipari.get_repo_posture": _tool_get_repo_posture,
        "vipari.get_repo_casefile": _tool_get_repo_casefile,
        "vipari.list_escalations": _tool_list_escalations,
        "vipari.get_workspace_budget_status": _tool_get_workspace_budget_status,
        "vipari.get_export_status": _tool_get_export_status,
        "vipari.create_compliance_export": _tool_create_compliance_export,
        "vipari.list_baseline_proposals": _tool_list_baseline_proposals,
        "vipari.create_baseline_proposal": _tool_create_baseline_proposal,
        "vipari.list_onboarding_proposals": _tool_list_onboarding_proposals,
        "vipari.create_onboarding_proposal": _tool_create_onboarding_proposal,
        "vipari.add_audit_feedback": _tool_add_audit_feedback,
        "vipari.triage_audit": _tool_triage_audit,
        "promptdrift.list_repos": _tool_list_repos,
        "promptdrift.get_repo_posture": _tool_get_repo_posture,
        "promptdrift.get_repo_casefile": _tool_get_repo_casefile,
        "promptdrift.list_escalations": _tool_list_escalations,
    }
    return handlers.get(tool_name)


def _require_scope(context: McpBrokerPrincipalContext, scope: str) -> None:
    if scope not in context.scopes:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "insufficient_scope",
                "message": f"Missing required scope: {scope}.",
                "required_scope": scope,
                "granted_scopes": sorted(context.scopes),
            },
            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'},
        )


def _require_tool_rate_limit(context: McpBrokerPrincipalContext, tool_name: str) -> None:
    if tool_name not in _MCP_MUTATING_TOOL_NAMES:
        return
    if _mcp_mutation_limiter.allow(f"{context.client_id}:{tool_name}"):
        return
    raise HTTPException(
        status_code=429,
        detail={
            "error": "rate_limited",
            "message": "Too many MCP mutation requests for this tool. Please retry after 60 seconds.",
            "tool_name": tool_name,
            "retry_after_seconds": 60,
        },
        headers={"Retry-After": "60"},
    )


def _parse_validated_principal_scopes(principal) -> frozenset[str]:
    try:
        scopes_raw = json.loads(principal.scopes_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Current principal scope configuration is invalid.") from exc
    if not isinstance(scopes_raw, list) or any(not isinstance(scope, str) for scope in scopes_raw):
        raise ValueError("Current principal scope configuration is invalid.")
    scopes = frozenset(scope.strip() for scope in scopes_raw if scope.strip())
    if not scopes.issubset(ALL_SCOPES):
        raise ValueError("Current principal scope configuration is invalid.")
    try:
        validate_scope_kind_compatibility(principal.principal_kind, scopes)
    except ValueError as exc:
        raise ValueError("Current principal scope configuration is invalid.") from exc
    return scopes


def _require_mcp_workspace_enabled(*, settings: Settings, db_path: str, workspace_id: int) -> None:
    if not settings.is_production:
        return
    entitlement = get_workspace_entitlement(db_path, workspace_id)
    flags = json.loads(entitlement.feature_flags_json) if entitlement and entitlement.feature_flags_json else {}
    if flags.get("cp_api_enabled", True) is False:
        _raise_mcp_client_error(
            status_code=403,
            error="feature_disabled",
            message="Control plane API is not enabled for this workspace.",
        )


def _raise_mcp_auth_error(*, error: str, message: str, authenticate_value: str) -> None:
    raise HTTPException(
        status_code=401,
        detail={
            "error": error,
            "message": message,
        },
        headers={"WWW-Authenticate": authenticate_value},
    )


def _raise_mcp_client_error(*, status_code: int, error: str, message: str) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={
            "error": error,
            "message": message,
        },
    )


def _raise_mcp_server_error(*, error: str, message: str) -> None:
    raise HTTPException(
        status_code=503,
        detail={
            "error": error,
            "message": message,
        },
    )


def _mcp_exception_payload(exc: HTTPException) -> dict[str, Any]:
    payload: dict[str, Any] = {"status_code": exc.status_code}
    if isinstance(exc.detail, dict):
        payload.update(exc.detail)
    elif exc.detail is not None:
        payload["message"] = str(exc.detail)
    return payload


def _allowed_repo_fulls(db_path: str, workspace_id: int) -> set[str]:
    return {
        allocation.repo_full
        for allocation in list_repo_allocations_for_workspace(db_path, workspace_id)
        if allocation.allocation_status in {"active", "onboarded"}
    }


def _require_allocated_repo(db_path: str, workspace_id: int, repo_full: str):
    allocation = get_repo_allocation_for_workspace(db_path, workspace_id, repo_full)
    if allocation is None or allocation.allocation_status not in {"active", "onboarded"}:
        _raise_mcp_client_error(
            status_code=404,
            error="repo_not_allocated",
            message="Repository is not allocated to this workspace.",
        )
    return allocation


def _derive_repo_posture(view) -> tuple[str, list[Any], Any | None]:
    insights = list(view.insights or [])
    review_now = [item for item in insights if item.priority == "review_now"]
    watch = [item for item in insights if item.priority == "watch"]
    leading = review_now[0] if review_now else watch[0] if watch else None
    if review_now:
        return ("risk", review_now, leading)
    if watch:
        return ("watch", watch, leading)
    if view.baseline_review is not None and view.baseline_review.pending_count > 0:
        return ("baseline_review", [], None)
    if view.onboarding is None:
        return ("not_onboarded", [], None)
    return ("healthy", [], None)


def _tool_list_available_tools(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    tools = list_mcp_tools_for_scopes(context.scopes)
    return {
        "workspace_id": context.workspace_id,
        "client_id": context.client_id,
        "granted_scopes": sorted(context.scopes),
        "tool_count": len(tools),
        "tools": [
            {
                "name": tool["name"],
                "title": tool["title"],
                "required_scope": tool["required_scope"],
            }
            for tool in tools
        ],
    }


def _tool_list_repos(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    limit = max(1, min(int(arguments.get("limit", 50)), 100))
    allocation_status_by_full = {
        allocation.repo_full: allocation.allocation_status
        for allocation in list_repo_allocations_for_workspace(db_path, context.workspace_id)
    }
    repos = list_repo_dashboard_index(
        db_path,
        allowed_repo_fulls=_allowed_repo_fulls(db_path, context.workspace_id),
        allocation_status_by_full=allocation_status_by_full,
    )
    payload = []
    for repo in repos[:limit]:
        view = build_repo_dashboard_view(db_path, repo.repo_full, include_journey=False, include_detail_sections=True)
        posture, posture_items, leading = _derive_repo_posture(view)
        payload.append(
            {
                "repo_full": repo.repo_full,
                "default_branch": repo.default_branch,
                "allocation_status": repo.allocation_status,
                "onboarding_status": repo.onboarding_status,
                "discovered_artifact_count": repo.discovered_artifact_count,
                "posture": posture,
                "open_escalation_count": len(posture_items),
                "top_reason": (leading.risk_reasons[0] if leading and leading.risk_reasons else None),
                "recommended_next_action": (leading.recommended_action if leading else _fallback_repo_action(view, posture)),
            }
        )
    return {
        "workspace_id": context.workspace_id,
        "repo_count": len(payload),
        "repos": payload,
    }


def _tool_get_workspace_budget_status(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    del arguments
    summary = get_workspace_budget_status(db_path, context.workspace_id)
    if summary is None:
        _raise_mcp_client_error(status_code=404, error="workspace_not_found", message="Workspace not found.")
    return {
        "workspace_id": summary.workspace_id,
        "workspace_display_name": summary.workspace_display_name,
        "unit_limit": summary.unit_limit,
        "used_units": summary.used_units,
        "remaining_units": summary.remaining_units,
        "utilization_percent": summary.utilization_percent,
        "estimated_cost_usd": summary.estimated_cost_usd,
        "alert_state": summary.alert_state,
        "alerts": list(summary.alerts),
        "feature_breakdown": list(summary.feature_breakdown),
    }


def _tool_get_repo_posture(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    repo_full = str(arguments.get("repo_full") or "").strip()
    if not repo_full:
        _raise_mcp_client_error(status_code=400, error="invalid_argument", message="repo_full is required.")
    _require_allocated_repo(db_path, context.workspace_id, repo_full)
    view = build_repo_dashboard_view(db_path, repo_full, include_journey=False, include_detail_sections=True)
    posture, posture_items, leading = _derive_repo_posture(view)
    return {
        "repo_full": repo_full,
        "posture": posture,
        "top_reasons": (leading.risk_reasons[:3] if leading and leading.risk_reasons else []),
        "recommended_next_action": _fallback_repo_action(view, posture, leading),
        "open_escalation_count": len(posture_items),
        "top_insight": (
            {
                "title": leading.title,
                "artifact_path": leading.artifact_path,
                "priority": leading.priority,
                "review_target": leading.review_target,
                "review_url": leading.review_url,
                "flag_summary": leading.flag_summary,
            }
            if leading is not None
            else None
        ),
        "baseline_review": (
            {
                "pending_count": view.baseline_review.pending_count,
                "approved_count": view.baseline_review.approved_count,
                "authoritative_artifact_count": view.baseline_review.authoritative_artifact_count,
            }
            if view.baseline_review is not None
            else None
        ),
    }


def _tool_get_repo_casefile(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    repo_full = str(arguments.get("repo_full") or "").strip()
    if not repo_full:
        _raise_mcp_client_error(status_code=400, error="invalid_argument", message="repo_full is required.")
    _require_allocated_repo(db_path, context.workspace_id, repo_full)
    view = build_repo_dashboard_view(db_path, repo_full, include_journey=True, include_detail_sections=True)
    posture, _, leading = _derive_repo_posture(view)
    return {
        "repo_full": repo_full,
        "posture": posture,
        "summary": _repo_casefile_summary(view, posture, leading),
        "audit_brief": asdict(view.audit_brief) if view.audit_brief is not None else None,
        "baseline_review": (
            {
                "is_pending_review": view.baseline_review.is_pending_review,
                "approved_count": view.baseline_review.approved_count,
                "pending_count": view.baseline_review.pending_count,
                "rejected_count": view.baseline_review.rejected_count,
            }
            if view.baseline_review is not None
            else None
        ),
        "coverage_summary": {
            "discovered_artifact_count": (view.onboarding.discovered_artifact_count if view.onboarding else 0),
            "baseline_version_count": view.baseline_version_count,
            "pull_request_audit_count": view.pull_request_audit_count,
        },
        "featured_artifacts": [
            {
                "artifact_path": artifact.artifact_path,
                "artifact_type": artifact.artifact_type,
                "provenance_label": artifact.provenance_label,
                "historical_version_count": artifact.historical_version_count,
                "drift_magnitude": max(artifact.leaderboard_drift_magnitude, artifact.latest_historical_drift_magnitude),
            }
            for artifact in (view.artifacts or [])[:5]
        ],
        "open_insights": [
            {
                "title": insight.title,
                "artifact_path": insight.artifact_path,
                "priority": insight.priority,
                "rationale": insight.rationale,
                "recommended_action": insight.recommended_action,
                "review_target": insight.review_target,
                "review_url": insight.review_url,
            }
            for insight in (view.insights or [])[:5]
        ],
        "recent_review_targets": [
            {
                "label": snapshot.get("label") or snapshot.get("source_label") or "snapshot",
                "source": snapshot.get("source_type") or snapshot.get("source"),
                "source_ref": snapshot.get("source_ref"),
                "source_url": snapshot.get("source_url"),
            }
            for snapshot in (view.journey_snapshots or [])[:5]
        ],
    }


def _tool_list_escalations(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    limit = max(1, min(int(arguments.get("limit", 20)), 100))
    include_watch = bool(arguments.get("include_watch", False))
    queue = build_workspace_escalation_queue(
        db_path,
        allowed_repo_fulls=_allowed_repo_fulls(db_path, context.workspace_id),
        include_watch=include_watch,
    )
    return {
        "workspace_id": context.workspace_id,
        "workspace_posture": queue["workspace_posture"],
        "workspace_posture_reasons": queue["workspace_posture_reasons"],
        "escalation_count": queue["escalation_count"],
        "watch_count": queue["watch_count"],
        "items": [
            {
                "repo_full": item["repo_full"],
                "artifact_path": item["artifact_path"],
                "priority": item["priority"],
                "title": item["title"],
                "rationale": item["rationale"],
                "recommended_action": item["recommended_action"],
                "review_target": item["review_target"],
                "review_url": item["review_url"],
            }
            for item in queue["items"][:limit]
        ],
        "truncated": len(queue["items"]) > limit,
    }


def _tool_get_export_status(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    export_id = _require_positive_int(arguments.get("export_id"), field_name="export_id")
    job = get_export_job(db_path, export_id)
    if job is None or job.workspace_id != context.workspace_id:
        _raise_mcp_client_error(status_code=404, error="export_not_found", message="Export not found.")
    return {
        "id": job.id,
        "repo_full": job.repo_full,
        "workspace_id": context.workspace_id,
        "status": job.status,
        "export_mode": job.export_mode,
        "from_ts": job.from_ts,
        "to_ts": job.to_ts,
        "attempt_count": job.attempt_count,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
    }


def _tool_create_compliance_export(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    repo_full = str(arguments.get("repo_full") or "").strip()
    if not repo_full:
        _raise_mcp_client_error(status_code=400, error="invalid_argument", message="repo_full is required.")
    _require_allocated_repo(db_path, context.workspace_id, repo_full)
    from_date = str(arguments.get("from_date") or "").strip()
    to_date = str(arguments.get("to_date") or "").strip()
    if not from_date or not to_date:
        _raise_mcp_client_error(status_code=400, error="invalid_argument", message="from_date and to_date are required.")
    try:
        from_ts = datetime.fromisoformat(from_date).timestamp()
        to_ts = datetime.fromisoformat(to_date).timestamp()
    except ValueError as exc:
        _raise_mcp_client_error(status_code=400, error="invalid_argument", message="from_date and to_date must be valid ISO dates.")
    export_mode = str(arguments.get("export_mode") or "").strip()
    if export_mode not in {"compliance", "compliance_plus_drift"}:
        _raise_mcp_client_error(
            status_code=400,
            error="invalid_argument",
            message="export_mode must be compliance or compliance_plus_drift.",
        )
    include_artifact_content = bool(arguments.get("include_artifact_content", False))
    job = create_export_job(
        db_path,
        repo_full=repo_full,
        from_ts=from_ts,
        to_ts=to_ts,
        export_mode=export_mode,
        include_artifact_content=include_artifact_content,
        workspace_id=context.workspace_id,
    )
    create_control_plane_audit_log(
        db_path,
        workspace_id=context.workspace_id,
        actor_user_id=None,
        event_type="export.created",
        subject_type="export_job",
        subject_id=str(job.id),
        payload={"repo_full": repo_full, "tool_name": "vipari.create_compliance_export"},
    )
    return {
        "job_id": job.id,
        "workspace_id": context.workspace_id,
        "repo_full": repo_full,
        "status": job.status,
    }


def _tool_list_baseline_proposals(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    artifact_id = _require_positive_int(arguments.get("artifact_id"), field_name="artifact_id")
    artifact = get_onboarded_artifact_by_id(db_path, artifact_id)
    if artifact is None:
        _raise_mcp_client_error(status_code=404, error="artifact_not_found", message="Artifact not found.")
    _require_allocated_repo(db_path, context.workspace_id, artifact.repo_full)
    proposals = list_baseline_proposals(db_path, artifact_id=artifact_id, workspace_id=context.workspace_id)
    return {"artifact_id": artifact_id, "proposals": [_serialize_baseline_proposal(proposal) for proposal in proposals]}


def _tool_create_baseline_proposal(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    artifact_id = _require_positive_int(arguments.get("artifact_id"), field_name="artifact_id")
    artifact = get_onboarded_artifact_by_id(db_path, artifact_id)
    if artifact is None:
        _raise_mcp_client_error(status_code=404, error="artifact_not_found", message="Artifact not found.")
    _require_allocated_repo(db_path, context.workspace_id, artifact.repo_full)
    snapshot_id = _optional_positive_int(arguments.get("snapshot_id"), field_name="snapshot_id")
    rationale = _optional_text(arguments.get("rationale"), field_name="rationale") or ""
    linked_audit_ids = _int_list(arguments.get("linked_audit_ids"), field_name="linked_audit_ids")
    metadata = _string_dict(arguments.get("metadata"), field_name="metadata")
    proposal = create_baseline_proposal(
        db_path,
        artifact_id=artifact_id,
        repo_full=artifact.repo_full,
        workspace_id=context.workspace_id,
        snapshot_id=snapshot_id,
        rationale=rationale,
        linked_audit_ids=linked_audit_ids,
        metadata=metadata,
        proposer_principal_id=context.principal_id,
    )
    create_control_plane_audit_log(
        db_path,
        workspace_id=context.workspace_id,
        actor_user_id=None,
        event_type="proposal.created",
        subject_type="baseline_proposal",
        subject_id=str(proposal.id),
        payload={"artifact_id": artifact_id, "proposer_principal_id": context.principal_id, "tool_name": "vipari.create_baseline_proposal"},
    )
    return _serialize_baseline_proposal(proposal)


def _tool_list_onboarding_proposals(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    proposals = list_onboarding_proposals(db_path, workspace_id=context.workspace_id)
    return {
        "workspace_id": context.workspace_id,
        "proposals": [_serialize_onboarding_proposal(proposal) for proposal in proposals],
    }


def _tool_create_onboarding_proposal(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    repo_full = str(arguments.get("repo_full") or "").strip()
    if not repo_full:
        _raise_mcp_client_error(status_code=400, error="invalid_argument", message="repo_full is required.")
    _require_allocated_repo(db_path, context.workspace_id, repo_full)
    installation_id = _optional_positive_int(arguments.get("installation_id"), field_name="installation_id")
    proposed_category = _optional_text(arguments.get("proposed_category"), field_name="proposed_category")
    rationale = _optional_text(arguments.get("rationale"), field_name="rationale") or ""
    metadata = _string_dict(arguments.get("metadata"), field_name="metadata")
    proposal = create_onboarding_proposal(
        db_path,
        workspace_id=context.workspace_id,
        repo_full=repo_full,
        installation_id=installation_id,
        proposed_category=proposed_category,
        rationale=rationale,
        metadata=metadata,
        proposer_principal_id=context.principal_id,
    )
    create_control_plane_audit_log(
        db_path,
        workspace_id=context.workspace_id,
        actor_user_id=None,
        event_type="proposal.created",
        subject_type="onboarding_proposal",
        subject_id=str(proposal.id),
        payload={"repo_full": repo_full, "proposer_principal_id": context.principal_id, "tool_name": "vipari.create_onboarding_proposal"},
    )
    return _serialize_onboarding_proposal(proposal)


def _tool_add_audit_feedback(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    audit_id = _require_positive_int(arguments.get("audit_id"), field_name="audit_id")
    kind = str(arguments.get("kind") or "").strip()
    if kind not in VALID_FEEDBACK_KINDS:
        _raise_mcp_client_error(
            status_code=400,
            error="invalid_argument",
            message=f"kind must be one of {sorted(VALID_FEEDBACK_KINDS)}.",
        )
    audit = get_pull_request_audit_by_id(db_path, audit_id)
    if audit is None:
        _raise_mcp_client_error(status_code=404, error="audit_not_found", message="Audit not found.")
    _require_allocated_repo(db_path, context.workspace_id, audit.repo_full)
    source = f"mcp:{context.client_id}"
    comment = _optional_text(arguments.get("comment"), field_name="comment")
    metadata = _string_dict(arguments.get("metadata"), field_name="metadata")
    event = add_audit_feedback(
        db_path,
        audit_id=audit_id,
        workspace_id=context.workspace_id,
        source=source,
        kind=kind,
        comment=comment,
        metadata=metadata,
    )
    create_control_plane_audit_log(
        db_path,
        workspace_id=context.workspace_id,
        actor_user_id=None,
        event_type="audit.feedback_added",
        subject_type="audit",
        subject_id=str(audit_id),
        payload={"source": source, "tool_name": "vipari.add_audit_feedback"},
    )
    return {
        "id": event.id,
        "audit_id": event.audit_id,
        "kind": event.kind,
        "source": event.source,
        "client_id": context.client_id,
        "comment": event.comment,
        "metadata": event.metadata,
        "created_at": event.created_at,
    }


def _tool_triage_audit(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    audit_id = _require_positive_int(arguments.get("audit_id"), field_name="audit_id")
    state = str(arguments.get("state") or "").strip()
    if state not in VALID_TRIAGE_STATES:
        _raise_mcp_client_error(
            status_code=400,
            error="invalid_argument",
            message=f"state must be one of {sorted(VALID_TRIAGE_STATES)}.",
        )
    audit = get_pull_request_audit_by_id(db_path, audit_id)
    if audit is None:
        _raise_mcp_client_error(status_code=404, error="audit_not_found", message="Audit not found.")
    _require_allocated_repo(db_path, context.workspace_id, audit.repo_full)
    reason = _optional_text(arguments.get("reason"), field_name="reason")
    event = add_audit_triage(
        db_path,
        audit_id=audit_id,
        workspace_id=context.workspace_id,
        state=state,
        reason=reason,
    )
    create_control_plane_audit_log(
        db_path,
        workspace_id=context.workspace_id,
        actor_user_id=None,
        event_type="audit.triage_state_changed",
        subject_type="audit",
        subject_id=str(audit_id),
        payload={"state": state, "tool_name": "vipari.triage_audit"},
    )
    return {
        "id": event.id,
        "audit_id": event.audit_id,
        "state": event.state,
        "reason": event.reason,
        "created_at": event.created_at,
    }


def _fallback_repo_action(view, posture: str, leading=None) -> str:
    if leading is not None and leading.recommended_action:
        return leading.recommended_action
    if posture == "baseline_review":
        return "Review the pending baseline candidate before treating this repo as authoritative."
    if posture == "not_onboarded":
        return "Finish onboarding so Vipari can build a review-ready baseline and posture model."
    if view.onboarding is None:
        return "Allocate and onboard the repository before asking agents for posture advice."
    return "Continue normal review; no urgent repo-level escalation is open right now."


def _repo_casefile_summary(view, posture: str, leading) -> str:
    if leading is not None and leading.flag_summary:
        return leading.flag_summary
    if posture == "baseline_review" and view.baseline_review is not None:
        return (
            f"{view.baseline_review.pending_count} baseline candidate(s) are waiting for approval before this repo has a fully authoritative reference point."
        )
    if view.onboarding is None:
        return "This repository is not onboarded yet, so Vipari cannot assemble a full case file."
    return "Vipari has current onboarding state for this repository and no urgent repo-level escalation is open."


def _require_positive_int(value: Any, *, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        _raise_mcp_client_error(
            status_code=400,
            error="invalid_argument",
            message=f"{field_name} must be a positive integer.",
        )
    if number <= 0:
        _raise_mcp_client_error(
            status_code=400,
            error="invalid_argument",
            message=f"{field_name} must be a positive integer.",
        )
    return number


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 2000:
        _raise_mcp_client_error(
            status_code=400,
            error="invalid_argument",
            message=f"{field_name} is too long.",
        )
    return text


def _string_dict(value: Any, *, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        _raise_mcp_client_error(
            status_code=400,
            error="invalid_argument",
            message=f"{field_name} must be an object.",
        )
    normalized: dict[str, str] = {}
    for raw_key, raw_item in value.items():
        key = str(raw_key).strip()
        if not key:
            _raise_mcp_client_error(
                status_code=400,
                error="invalid_argument",
                message=f"{field_name} keys must be non-empty.",
            )
        item = str(raw_item).strip()
        if len(key) > 120 or len(item) > 500:
            _raise_mcp_client_error(
                status_code=400,
                error="invalid_argument",
                message=f"{field_name} entries are too long.",
            )
        normalized[key] = item
    return normalized


def _optional_positive_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _require_positive_int(value, field_name=field_name)


def _int_list(value: Any, *, field_name: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        _raise_mcp_client_error(
            status_code=400,
            error="invalid_argument",
            message=f"{field_name} must be an array.",
        )
    return [_require_positive_int(item, field_name=field_name) for item in value]


def _serialize_baseline_proposal(proposal) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "artifact_id": proposal.artifact_id,
        "repo_full": proposal.repo_full,
        "workspace_id": proposal.workspace_id,
        "proposal_kind": proposal.proposal_kind,
        "snapshot_id": proposal.snapshot_id,
        "rationale": proposal.rationale,
        "linked_audit_ids": proposal.linked_audit_ids,
        "metadata": proposal.metadata,
        "status": proposal.status,
        "proposer_principal_id": proposal.proposer_principal_id,
        "decision_principal_id": proposal.decision_principal_id,
        "decision_note": proposal.decision_note,
        "expires_at": proposal.expires_at,
        "decided_at": proposal.decided_at,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
    }


def _serialize_onboarding_proposal(proposal) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "workspace_id": proposal.workspace_id,
        "repo_full": proposal.repo_full,
        "proposal_kind": proposal.proposal_kind,
        "installation_id": proposal.installation_id,
        "proposed_category": proposal.proposed_category,
        "rationale": proposal.rationale,
        "metadata": proposal.metadata,
        "status": proposal.status,
        "proposer_principal_id": proposal.proposer_principal_id,
        "decision_principal_id": proposal.decision_principal_id,
        "decision_note": proposal.decision_note,
        "expires_at": proposal.expires_at,
        "decided_at": proposal.decided_at,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
    }