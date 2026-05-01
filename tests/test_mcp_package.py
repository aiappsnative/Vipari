from __future__ import annotations

import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.mcp_package import build_customer_mcp_bundle


def test_build_customer_mcp_bundle_uses_self_contained_package_directory():
    bundle_bytes = build_customer_mcp_bundle(app_base_url="https://app.promptdrift.test")

    archive = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    names = set(archive.namelist())

    assert "README.md" in names
    assert "promptdrift_mcp_server.py" in names
    assert "requirements.txt" in names
    assert "promptdrift.env.example" in names
    assert "claude-desktop-config.json.example" in names
    assert "tool-manifest.json" in names

    env_example = archive.read("promptdrift.env.example").decode("utf-8")
    claude_example = archive.read("claude-desktop-config.json.example").decode("utf-8")
    manifest = json.loads(archive.read("tool-manifest.json").decode("utf-8"))

    assert "{{PROMPTDRIFT_MCP_BROKER_URL}}" not in env_example
    assert "{{PROMPTDRIFT_MCP_BROKER_URL}}" not in claude_example
    assert "https://app.promptdrift.test/api/agent-integrations/mcp" in env_example
    assert "https://app.promptdrift.test/api/agent-integrations/mcp" in claude_example
    assert [tool["name"] for tool in manifest["tools"]] == [
        "promptdrift.list_repos",
        "promptdrift.get_repo_posture",
        "promptdrift.get_repo_casefile",
        "promptdrift.list_escalations",
    ]