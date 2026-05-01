from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP


BROKER_URL = os.getenv("PROMPTDRIFT_MCP_BROKER_URL", "").rstrip("/")
CLIENT_ID = os.getenv("PROMPTDRIFT_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("PROMPTDRIFT_CLIENT_SECRET", "")

server = FastMCP("PromptDrift")


def _authorization_header() -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("PROMPTDRIFT_CLIENT_ID and PROMPTDRIFT_CLIENT_SECRET must be configured.")
    token = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _invoke(tool_name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
    if not BROKER_URL:
        raise RuntimeError("PROMPTDRIFT_MCP_BROKER_URL must be configured.")
    payload = json.dumps({"tool_name": tool_name, "arguments": arguments or {}}).encode("utf-8")
    request = urllib.request.Request(
        f"{BROKER_URL}/invoke",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": _authorization_header(),
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:  # pragma: no cover - customer runtime path
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PromptDrift MCP broker request failed: HTTP {exc.code}: {detail}") from exc


@server.tool(name="promptdrift.list_repos")
def list_repos(limit: int = 50) -> dict[str, object]:
    """List repositories available to the bound PromptDrift workspace."""
    return _invoke("promptdrift.list_repos", {"limit": limit})


@server.tool(name="promptdrift.get_repo_posture")
def get_repo_posture(repo_full: str) -> dict[str, object]:
    """Get the current PromptDrift posture for one repository."""
    return _invoke("promptdrift.get_repo_posture", {"repo_full": repo_full})


@server.tool(name="promptdrift.get_repo_casefile")
def get_repo_casefile(repo_full: str) -> dict[str, object]:
    """Get a compact case file for one PromptDrift-tracked repository."""
    return _invoke("promptdrift.get_repo_casefile", {"repo_full": repo_full})


@server.tool(name="promptdrift.list_escalations")
def list_escalations(include_watch: bool = False, limit: int = 20) -> dict[str, object]:
    """List the current workspace escalation queue from PromptDrift."""
    return _invoke(
        "promptdrift.list_escalations",
        {"include_watch": include_watch, "limit": limit},
    )


if __name__ == "__main__":  # pragma: no cover - customer runtime entrypoint
    server.run()