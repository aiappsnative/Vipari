from __future__ import annotations

import io
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CUSTOMER_CONNECTOR_DIR = BASE_DIR / "customer_mcp_server"


def build_customer_mcp_bundle(*, app_base_url: str) -> bytes:
    buffer = io.BytesIO()
    replacements = {
        "{{PROMPTDRIFT_MCP_BROKER_URL}}": f"{app_base_url.rstrip('/')}/api/agent-integrations/mcp",
    }
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in CUSTOMER_CONNECTOR_DIR.rglob("*"):
            if path.is_dir():
                continue
            relative_path = path.relative_to(CUSTOMER_CONNECTOR_DIR).as_posix()
            content = path.read_text(encoding="utf-8")
            for placeholder, value in replacements.items():
                content = content.replace(placeholder, value)
            bundle.writestr(relative_path, content)
    return buffer.getvalue()
