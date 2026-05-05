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


def _issue_broker_token() -> tuple[str, float]:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "VIPARI_CLIENT_ID and VIPARI_CLIENT_SECRET must be configured. Legacy PROMPTDRIFT_* variables are also accepted."
        )
    if not BROKER_URL:
        raise RuntimeError(
            "VIPARI_MCP_BROKER_URL must be configured. Legacy PROMPTDRIFT_MCP_BROKER_URL is also accepted."
        )
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
        raise RuntimeError(f"Vipari MCP broker token request failed: HTTP {exc.code}: {detail}") from exc
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
        raise RuntimeError(
            "VIPARI_MCP_BROKER_URL must be configured. Legacy PROMPTDRIFT_MCP_BROKER_URL is also accepted."
        )
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
            raise RuntimeError(f"Vipari MCP broker request failed: HTTP {exc.code}: {detail}") from exc
        except OSError as exc:  # pragma: no cover - customer runtime path
            raise RuntimeError(f"Vipari MCP broker request failed: {exc}") from exc
    raise RuntimeError("Vipari MCP broker request failed after token refresh.")


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


if __name__ == "__main__":  # pragma: no cover - customer runtime entrypoint
    server.run()