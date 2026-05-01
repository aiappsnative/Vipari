from __future__ import annotations

import base64
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from config import Settings
from .control_plane_records import (
    get_machine_principal_by_client_id,
    get_repo_allocation_for_workspace,
    get_workspace_entitlement,
    list_repo_allocations_for_workspace,
)
from .dashboard_views import build_repo_dashboard_view, build_workspace_escalation_queue, list_repo_dashboard_index
from .secure_store import decrypt_text


MCP_READ_SCOPE = "drift.read"


@dataclass(frozen=True)
class McpBrokerPrincipalContext:
    client_id: str
    display_name: str
    workspace_id: int
    scopes: frozenset[str]


MCP_BROKER_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "promptdrift.list_repos",
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
        "name": "promptdrift.get_repo_posture",
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
        "name": "promptdrift.get_repo_casefile",
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
        "name": "promptdrift.list_escalations",
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
)


def list_mcp_tools_for_scopes(scopes: frozenset[str]) -> list[dict[str, Any]]:
    return [tool for tool in MCP_BROKER_TOOLS if tool["required_scope"] in scopes]


def authenticate_mcp_broker_request(
    authorization_header: str | None,
    *,
    settings: Settings,
    db_path: str,
) -> McpBrokerPrincipalContext:
    client_id, client_secret = _parse_basic_auth(authorization_header)
    generic_401 = "Invalid client credentials."

    principal = get_machine_principal_by_client_id(db_path, client_id)
    if principal is None:
        hmac.compare_digest(secrets.token_urlsafe(32).encode(), client_secret.encode())
        raise HTTPException(status_code=401, detail=generic_401)

    if not settings.has_encryption_key:
        raise HTTPException(status_code=503, detail="APP_ENCRYPTION_KEY must be configured.")

    decrypted_secret = decrypt_text(principal.client_secret_encrypted, settings.app_encryption_key)
    if not hmac.compare_digest(decrypted_secret.encode(), client_secret.encode()):
        raise HTTPException(status_code=401, detail=generic_401)
    if principal.status != "active":
        raise HTTPException(status_code=401, detail=generic_401)

    if settings.is_production:
        entitlement = get_workspace_entitlement(db_path, principal.workspace_id)
        flags = json.loads(entitlement.feature_flags_json) if entitlement and entitlement.feature_flags_json else {}
        if flags.get("cp_api_enabled", True) is False:
            raise HTTPException(status_code=403, detail="Control plane API is not enabled for this workspace.")

    scopes = frozenset(json.loads(principal.scopes_json))
    return McpBrokerPrincipalContext(
        client_id=principal.client_id,
        display_name=principal.display_name,
        workspace_id=principal.workspace_id,
        scopes=scopes,
    )


def invoke_mcp_broker_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    context: McpBrokerPrincipalContext,
    db_path: str,
) -> dict[str, Any]:
    _require_scope(context, MCP_READ_SCOPE)
    handlers = {
        "promptdrift.list_repos": _tool_list_repos,
        "promptdrift.get_repo_posture": _tool_get_repo_posture,
        "promptdrift.get_repo_casefile": _tool_get_repo_casefile,
        "promptdrift.list_escalations": _tool_list_escalations,
    }
    handler = handlers.get(tool_name)
    if handler is None:
        raise HTTPException(status_code=404, detail="MCP tool not found.")
    return handler(arguments or {}, context=context, db_path=db_path)


def _parse_basic_auth(header_value: str | None) -> tuple[str, str]:
    if not header_value or not header_value.startswith("Basic "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header.",
            headers={"WWW-Authenticate": "Basic"},
        )
    token = header_value[6:].strip()
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=401, detail="Malformed basic auth credentials.") from exc
    client_id, separator, client_secret = decoded.partition(":")
    if not separator or not client_id or not client_secret:
        raise HTTPException(status_code=401, detail="Malformed basic auth credentials.")
    return client_id, client_secret


def _require_scope(context: McpBrokerPrincipalContext, scope: str) -> None:
    if scope not in context.scopes:
        raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}.")


def _allowed_repo_fulls(db_path: str, workspace_id: int) -> set[str]:
    return {allocation.repo_full for allocation in list_repo_allocations_for_workspace(db_path, workspace_id)}


def _require_allocated_repo(db_path: str, workspace_id: int, repo_full: str):
    allocation = get_repo_allocation_for_workspace(db_path, workspace_id, repo_full)
    if allocation is None:
        raise HTTPException(status_code=404, detail="Repository is not allocated to this workspace.")
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


def _tool_get_repo_posture(arguments: dict[str, Any], *, context: McpBrokerPrincipalContext, db_path: str) -> dict[str, Any]:
    repo_full = str(arguments.get("repo_full") or "").strip()
    if not repo_full:
        raise HTTPException(status_code=400, detail="repo_full is required.")
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
        raise HTTPException(status_code=400, detail="repo_full is required.")
    _require_allocated_repo(db_path, context.workspace_id, repo_full)
    view = build_repo_dashboard_view(db_path, repo_full, include_journey=True, include_detail_sections=True)
    posture, _, leading = _derive_repo_posture(view)
    return {
        "repo_full": repo_full,
        "posture": posture,
        "summary": _repo_casefile_summary(view, posture, leading),
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


def _fallback_repo_action(view, posture: str, leading=None) -> str:
    if leading is not None and leading.recommended_action:
        return leading.recommended_action
    if posture == "baseline_review":
        return "Review the pending baseline candidate before treating this repo as authoritative."
    if posture == "not_onboarded":
        return "Finish onboarding so PromptDrift can build a review-ready baseline and posture model."
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
        return "This repository is not onboarded yet, so PromptDrift cannot assemble a full case file."
    return "PromptDrift has current onboarding state for this repository and no urgent repo-level escalation is open."