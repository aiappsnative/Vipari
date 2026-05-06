#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"Environment file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _connector_python(connector_dir: Path) -> Path:
    if os.name == "nt":
        candidate = connector_dir / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = connector_dir / ".venv" / "bin" / "python"
    if not candidate.exists():
        raise FileNotFoundError(
            f"Connector virtualenv Python not found at {candidate}. Install the package dependencies first."
        )
    return candidate


def _request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if payload is not None and "Content-Type" not in (headers or {}):
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _extract_repo_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if isinstance(result, dict):
        repos = result.get("repos")
        if isinstance(repos, list):
            return [item for item in repos if isinstance(item, dict)]
    return []


def _result_keys(payload: dict[str, Any]) -> list[str]:
    result = payload.get("result")
    if isinstance(result, dict):
        return sorted(result.keys())
    return []


def _http_smoke(connector_env: dict[str, str], repo_full: str | None, limit: int) -> dict[str, Any]:
    broker_url = connector_env.get("VIPARI_MCP_BROKER_URL") or connector_env.get("PROMPTDRIFT_MCP_BROKER_URL")
    client_id = connector_env.get("VIPARI_CLIENT_ID") or connector_env.get("PROMPTDRIFT_CLIENT_ID")
    client_secret = connector_env.get("VIPARI_CLIENT_SECRET") or connector_env.get("PROMPTDRIFT_CLIENT_SECRET")
    if not broker_url or not client_id or not client_secret:
        raise ValueError("Connector environment must define broker URL, client ID, and client secret.")

    token_payload = _request_json(
        f"{broker_url.rstrip('/')}/token",
        method="POST",
        body={"client_id": client_id, "client_secret": client_secret},
    )
    token = token_payload.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Token endpoint did not return a usable token.")

    auth_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    tools_payload = _request_json(f"{broker_url.rstrip('/')}/tools", headers=auth_headers)
    tool_names = [tool.get("name") for tool in tools_payload.get("tools", []) if isinstance(tool, dict)]

    repos_payload = _request_json(
        f"{broker_url.rstrip('/')}/invoke",
        method="POST",
        headers=auth_headers,
        body={"tool_name": "vipari.list_repos", "arguments": {"limit": limit}},
    )
    repo_items = _extract_repo_items(repos_payload)
    selected_repo = repo_full or (repo_items[0].get("repo_full") if repo_items else None)

    escalations_payload = _request_json(
        f"{broker_url.rstrip('/')}/invoke",
        method="POST",
        headers=auth_headers,
        body={"tool_name": "vipari.list_escalations", "arguments": {"include_watch": True, "limit": limit}},
    )

    posture_keys: list[str] = []
    casefile_keys: list[str] = []
    if selected_repo:
        posture_payload = _request_json(
            f"{broker_url.rstrip('/')}/invoke",
            method="POST",
            headers=auth_headers,
            body={"tool_name": "vipari.get_repo_posture", "arguments": {"repo_full": selected_repo}},
        )
        casefile_payload = _request_json(
            f"{broker_url.rstrip('/')}/invoke",
            method="POST",
            headers=auth_headers,
            body={"tool_name": "vipari.get_repo_casefile", "arguments": {"repo_full": selected_repo}},
        )
        posture_keys = _result_keys(posture_payload)
        casefile_keys = _result_keys(casefile_payload)

    return {
        "mode": "http",
        "workspace_id": token_payload.get("workspace_id"),
        "tool_names": tool_names,
        "repo_count": len(repo_items),
        "first_repo": repo_items[0].get("repo_full") if repo_items else None,
        "selected_repo": selected_repo,
        "escalation_result_keys": _result_keys(escalations_payload),
        "posture_result_keys": posture_keys,
        "casefile_result_keys": casefile_keys,
    }


def _resolve_template(value: str, env: dict[str, str]) -> str:
    stripped = value.strip()
    if stripped.startswith("{{") and stripped.endswith("}}"):
        return env.get(stripped[2:-2].strip(), value)
    return value


def _load_host_config(config_path: Path, server_name: str, fallback_env: dict[str, str]) -> dict[str, Any]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or server_name not in servers:
        raise KeyError(f"Server '{server_name}' was not found in {config_path}")
    server = servers[server_name]
    if not isinstance(server, dict):
        raise ValueError(f"Server '{server_name}' config must be an object.")
    env_block = server.get("env") or {}
    resolved_env = {
        key: _resolve_template(str(value), fallback_env)
        for key, value in env_block.items()
    }
    return {
        "command": str(server["command"]),
        "args": [str(value) for value in server.get("args", [])],
        "env": resolved_env,
    }


def _extract_structured_result(call_result: Any) -> dict[str, Any]:
    structured = getattr(call_result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(call_result, "content", None)
    if isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
    return {}


async def _host_smoke_async(
    *,
    connector_dir: Path,
    connector_env: dict[str, str],
    repo_full: str | None,
    limit: int,
    config_path: Path | None,
    server_name: str,
) -> dict[str, Any]:
    try:
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as exc:
        raise RuntimeError(
            "Host mode requires the MCP SDK. Run this script with the connector virtualenv Python or install 'mcp'."
        ) from exc

    if config_path is not None:
        host_spec = _load_host_config(config_path, server_name, connector_env)
        command = host_spec["command"]
        args = host_spec["args"]
        host_env = {**os.environ, **host_spec["env"]}
        cwd = connector_dir
    else:
        command = str(_connector_python(connector_dir))
        args = ["vipari_mcp_server.py"]
        host_env = {**os.environ, **connector_env}
        cwd = connector_dir

    server = StdioServerParameters(command=command, args=args, env=host_env, cwd=cwd)
    async with stdio_client(server) as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            tool_list = await session.list_tools()
            tool_names = [tool.name for tool in tool_list.tools]

            repos_call = await session.call_tool("vipari.list_repos", {"limit": limit})
            repos_payload = _extract_structured_result(repos_call)
            repo_items = _extract_repo_items(repos_payload)
            selected_repo = repo_full or (repo_items[0].get("repo_full") if repo_items else None)

            escalations_call = await session.call_tool("vipari.list_escalations", {"include_watch": True, "limit": limit})
            escalations_payload = _extract_structured_result(escalations_call)

            posture_keys: list[str] = []
            casefile_keys: list[str] = []
            if selected_repo:
                posture_call = await session.call_tool("vipari.get_repo_posture", {"repo_full": selected_repo})
                casefile_call = await session.call_tool("vipari.get_repo_casefile", {"repo_full": selected_repo})
                posture_keys = _result_keys(_extract_structured_result(posture_call))
                casefile_keys = _result_keys(_extract_structured_result(casefile_call))

            return {
                "mode": "host",
                "server_name": getattr(initialize_result.serverInfo, "name", None),
                "tool_names": tool_names,
                "repo_count": len(repo_items),
                "first_repo": repo_items[0].get("repo_full") if repo_items else None,
                "selected_repo": selected_repo,
                "escalation_result_keys": _result_keys(escalations_payload),
                "posture_result_keys": posture_keys,
                "casefile_result_keys": casefile_keys,
            }


def _host_smoke(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(_host_smoke_async(**kwargs))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repeatable Vipari MCP live smoke checks.")
    parser.add_argument("--connector-dir", required=True, help="Path to the unpacked connector directory.")
    parser.add_argument("--env-file", help="Optional path to the connector .env file. Defaults to <connector-dir>/.env.")
    parser.add_argument("--mode", choices=["http", "host", "both"], default="both", help="Which smoke path to execute.")
    parser.add_argument("--repo-full", help="Optional repo_full to force for posture and casefile checks.")
    parser.add_argument("--limit", type=int, default=10, help="List limit for repos and escalations.")
    parser.add_argument("--mcp-config", help="Optional Claude-style MCP host config JSON to use for host mode.")
    parser.add_argument("--server-name", default="vipari", help="Server name inside the MCP host config.")
    args = parser.parse_args(argv)

    connector_dir = Path(args.connector_dir).resolve()
    env_file = Path(args.env_file).resolve() if args.env_file else connector_dir / ".env"
    connector_env = _load_env_file(env_file)

    results: list[dict[str, Any]] = []
    if args.mode in {"http", "both"}:
        results.append(_http_smoke(connector_env, args.repo_full, args.limit))
    if args.mode in {"host", "both"}:
        config_path = Path(args.mcp_config).resolve() if args.mcp_config else None
        results.append(
            _host_smoke(
                connector_dir=connector_dir,
                connector_env=connector_env,
                repo_full=args.repo_full,
                limit=args.limit,
                config_path=config_path,
                server_name=args.server_name,
            )
        )

    print(json.dumps({"results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())