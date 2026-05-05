from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from .mcp_broker import MCP_BROKER_TOOLS

BASE_DIR = Path(__file__).resolve().parent.parent
CUSTOMER_CONNECTOR_DIR = BASE_DIR / "customer_mcp_server"


def render_customer_mcp_tool_manifest() -> str:
    return json.dumps(
        {
            "tools": [
                {
                    "name": tool["name"],
                    "title": tool["title"],
                    "description": tool["description"],
                    "required_scope": tool["required_scope"],
                }
                for tool in MCP_BROKER_TOOLS
            ]
        },
        indent=2,
    ) + "\n"


def build_customer_mcp_bundle(*, app_base_url: str) -> bytes:
    buffer = io.BytesIO()
    replacements = {
        "{{VIPARI_MCP_BROKER_URL}}": f"{app_base_url.rstrip('/')}/api/agent-integrations/mcp",
    }
    skipped_paths = {"promptdrift_mcp_server.py", "promptdrift.env.example"}
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in CUSTOMER_CONNECTOR_DIR.rglob("*"):
            if path.is_dir():
                continue
            relative_path = path.relative_to(CUSTOMER_CONNECTOR_DIR).as_posix()
            if relative_path in skipped_paths:
                continue
            if relative_path == "tool-manifest.json":
                content = render_customer_mcp_tool_manifest()
            else:
                content = path.read_text(encoding="utf-8")
            for placeholder, value in replacements.items():
                content = content.replace(placeholder, value)
            bundle.writestr(relative_path, content)
    return buffer.getvalue()
