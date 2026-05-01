from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from .mcp_broker import MCP_BROKER_TOOLS


BASE_DIR = Path(__file__).resolve().parent.parent
CUSTOMER_CONNECTOR_DIR = BASE_DIR / "customer_mcp_server"


def build_customer_mcp_bundle(*, app_base_url: str) -> bytes:
    buffer = io.BytesIO()
    broker_url = f"{app_base_url.rstrip('/')}/api/agent-integrations/mcp"
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in CUSTOMER_CONNECTOR_DIR.rglob("*"):
            if path.is_dir():
                continue
            relative_path = path.relative_to(CUSTOMER_CONNECTOR_DIR).as_posix()
            bundle.writestr(relative_path, path.read_text(encoding="utf-8"))
        bundle.writestr("promptdrift.env.example", _env_example_contents(broker_url))
        bundle.writestr("claude-desktop-config.json.example", _claude_desktop_example(broker_url))
        bundle.writestr("tool-manifest.json", json.dumps({"tools": list(MCP_BROKER_TOOLS)}, indent=2))
    return buffer.getvalue()


def _env_example_contents(broker_url: str) -> str:
    return (
        "PROMPTDRIFT_MCP_BROKER_URL=" + broker_url + "\n"
        "PROMPTDRIFT_CLIENT_ID=replace-with-your-client-id\n"
        "PROMPTDRIFT_CLIENT_SECRET=replace-with-your-client-secret\n"
    )


def _claude_desktop_example(broker_url: str) -> str:
    payload = {
        "mcpServers": {
            "promptdrift": {
                "command": "python",
                "args": ["promptdrift_mcp_server.py"],
                "env": {
                    "PROMPTDRIFT_MCP_BROKER_URL": broker_url,
                    "PROMPTDRIFT_CLIENT_ID": "replace-with-your-client-id",
                    "PROMPTDRIFT_CLIENT_SECRET": "replace-with-your-client-secret",
                },
            }
        }
    }
    return json.dumps(payload, indent=2)