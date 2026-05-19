from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


BROKER_URL = _first_env("VIPARI_MCP_BROKER_URL", "PROMPTDRIFT_MCP_BROKER_URL").rstrip("/")
CLIENT_ID = _first_env("VIPARI_CLIENT_ID", "PROMPTDRIFT_CLIENT_ID")
CLIENT_SECRET = _first_env("VIPARI_CLIENT_SECRET", "PROMPTDRIFT_CLIENT_SECRET")
REQUEST_TIMEOUT_SECONDS = float(_first_env("VIPARI_REQUEST_TIMEOUT_SECONDS", "PROMPTDRIFT_REQUEST_TIMEOUT_SECONDS", default="15"))

_BROKER_TOKEN: str | None = None
_BROKER_TOKEN_EXPIRES_AT: float = 0.0

server = FastMCP("Vipari")


def _format_broker_http_error(operation: str, status_code: int, detail_text: str) -> str:
    normalized_detail = (detail_text or "").strip()
    error_code = ""
    message = normalized_detail or f"HTTP {status_code}"
    try:
        payload = json.loads(normalized_detail) if normalized_detail else None
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            error_code = str(detail.get("error") or "").strip()
            message = str(detail.get("message") or message).strip()
        elif detail is not None:
            message = str(detail).strip() or message
    suffix = f" {error_code}: {message}" if error_code else f": {message}"
    return f"Vipari MCP broker {operation} failed: HTTP {status_code}{suffix}"


def _issue_broker_token() -> tuple[str, float]:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("VIPARI_CLIENT_ID and VIPARI_CLIENT_SECRET must be configured.")
    if not BROKER_URL:
        raise RuntimeError("VIPARI_MCP_BROKER_URL must be configured.")
    payload = json.dumps({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}).encode("utf-8")
    request = urllib.request.Request(
        f"{BROKER_URL}/token",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:  # pragma: no cover - customer runtime path
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_format_broker_http_error("token request", exc.code, detail)) from exc
    except OSError as exc:  # pragma: no cover - customer runtime path
        raise RuntimeError(f"Vipari MCP broker token request failed: {exc}") from exc

    ttl_seconds = int(body.get("ttl_seconds", 0))
    if ttl_seconds <= 0 or not body.get("token"):
        raise RuntimeError("Vipari MCP broker token response was malformed.")
    return body["token"], time.time() + max(ttl_seconds - 30, 1)


def _broker_token() -> str:
    global _BROKER_TOKEN, _BROKER_TOKEN_EXPIRES_AT
    if _BROKER_TOKEN and time.time() < _BROKER_TOKEN_EXPIRES_AT:
        return _BROKER_TOKEN
    token, expires_at = _issue_broker_token()
    _BROKER_TOKEN = token
    _BROKER_TOKEN_EXPIRES_AT = expires_at
    return token


def _invalidate_broker_token() -> None:
    global _BROKER_TOKEN, _BROKER_TOKEN_EXPIRES_AT
    _BROKER_TOKEN = None
    _BROKER_TOKEN_EXPIRES_AT = 0.0


def _invoke(tool_name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
    if not BROKER_URL:
        raise RuntimeError("VIPARI_MCP_BROKER_URL must be configured.")
    payload = json.dumps({"tool_name": tool_name, "arguments": arguments or {}}).encode("utf-8")
    for attempt in range(2):
        request = urllib.request.Request(
            f"{BROKER_URL}/invoke",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_broker_token()}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:  # pragma: no cover - customer runtime path
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401 and attempt == 0:
                _invalidate_broker_token()
                continue
            raise RuntimeError(_format_broker_http_error("request", exc.code, detail)) from exc
        except OSError as exc:  # pragma: no cover - customer runtime path
            raise RuntimeError(f"Vipari MCP broker request failed: {exc}") from exc
    raise RuntimeError("Vipari MCP broker request failed after token refresh.")


@server.tool(name="vipari.list_available_tools")
def list_available_tools() -> dict[str, object]:
    """List the effective Vipari tools and scopes available to the current connector token."""
    return _invoke("vipari.list_available_tools", {})


@server.tool(name="vipari.list_repos")
def list_repos(limit: int = 50) -> dict[str, object]:
    """List repositories available to the bound Vipari workspace."""
    return _invoke("vipari.list_repos", {"limit": limit})


@server.tool(name="vipari.get_repo_posture")
def get_repo_posture(repo_full: str) -> dict[str, object]:
    """Get the current Vipari posture for one repository."""
    return _invoke("vipari.get_repo_posture", {"repo_full": repo_full})


@server.tool(name="vipari.get_repo_casefile")
def get_repo_casefile(repo_full: str) -> dict[str, object]:
    """Get a compact case file for one Vipari-tracked repository."""
    return _invoke("vipari.get_repo_casefile", {"repo_full": repo_full})


@server.tool(name="vipari.list_escalations")
def list_escalations(include_watch: bool = False, limit: int = 20) -> dict[str, object]:
    """List the current workspace escalation queue from Vipari."""
    return _invoke(
        "vipari.list_escalations",
        {"include_watch": include_watch, "limit": limit},
    )


@server.tool(name="vipari.get_export_status")
def get_export_status(export_id: int) -> dict[str, object]:
    """Get the current status for one workspace-owned Vipari export job."""
    return _invoke("vipari.get_export_status", {"export_id": export_id})


@server.tool(name="vipari.create_compliance_export")
def create_compliance_export(
    repo_full: str,
    from_date: str,
    to_date: str,
    export_mode: str,
    include_artifact_content: bool = False,
) -> dict[str, object]:
    """Create a low-risk compliance export job for one Vipari-visible repository."""
    return _invoke(
        "vipari.create_compliance_export",
        {
            "repo_full": repo_full,
            "from_date": from_date,
            "to_date": to_date,
            "export_mode": export_mode,
            "include_artifact_content": include_artifact_content,
        },
    )


@server.tool(name="vipari.list_baseline_proposals")
def list_baseline_proposals(artifact_id: int) -> dict[str, object]:
    """List baseline proposals for one workspace-visible Vipari artifact."""
    return _invoke("vipari.list_baseline_proposals", {"artifact_id": artifact_id})


@server.tool(name="vipari.create_baseline_proposal")
def create_baseline_proposal(
    artifact_id: int,
    snapshot_id: int | None = None,
    rationale: str = "",
    linked_audit_ids: list[int] | None = None,
    metadata: dict[str, str] | None = None,
) -> dict[str, object]:
    """Create a low-risk baseline proposal for one workspace-visible Vipari artifact."""
    arguments: dict[str, object] = {"artifact_id": artifact_id}
    if snapshot_id is not None:
        arguments["snapshot_id"] = snapshot_id
    if rationale:
        arguments["rationale"] = rationale
    if linked_audit_ids:
        arguments["linked_audit_ids"] = linked_audit_ids
    if metadata:
        arguments["metadata"] = metadata
    return _invoke("vipari.create_baseline_proposal", arguments)


@server.tool(name="vipari.list_onboarding_proposals")
def list_onboarding_proposals() -> dict[str, object]:
    """List onboarding proposals for the authenticated Vipari workspace."""
    return _invoke("vipari.list_onboarding_proposals", {})


@server.tool(name="vipari.create_onboarding_proposal")
def create_onboarding_proposal(
    repo_full: str,
    installation_id: int | None = None,
    proposed_category: str = "",
    rationale: str = "",
    metadata: dict[str, str] | None = None,
) -> dict[str, object]:
    """Create a low-risk onboarding proposal for a repository in the authenticated Vipari workspace."""
    arguments: dict[str, object] = {"repo_full": repo_full}
    if installation_id is not None:
        arguments["installation_id"] = installation_id
    if proposed_category:
        arguments["proposed_category"] = proposed_category
    if rationale:
        arguments["rationale"] = rationale
    if metadata:
        arguments["metadata"] = metadata
    return _invoke("vipari.create_onboarding_proposal", arguments)


@server.tool(name="vipari.add_audit_feedback")
def add_audit_feedback(
    audit_id: int,
    kind: str,
    source: str = "",
    comment: str = "",
    metadata: dict[str, str] | None = None,
) -> dict[str, object]:
    """Append low-risk structured feedback to an existing Vipari audit."""
    arguments: dict[str, object] = {"audit_id": audit_id, "kind": kind}
    if source:
        arguments["source"] = source
    if comment:
        arguments["comment"] = comment
    if metadata:
        arguments["metadata"] = metadata
    return _invoke("vipari.add_audit_feedback", arguments)


@server.tool(name="vipari.triage_audit")
def triage_audit(audit_id: int, state: str, reason: str = "") -> dict[str, object]:
    """Record a low-risk triage state transition for an existing Vipari audit."""
    arguments: dict[str, object] = {"audit_id": audit_id, "state": state}
    if reason:
        arguments["reason"] = reason
    return _invoke("vipari.triage_audit", arguments)


if __name__ == "__main__":  # pragma: no cover - customer runtime entrypoint
    server.run()