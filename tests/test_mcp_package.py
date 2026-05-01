from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.mcp_package import build_customer_mcp_bundle, render_customer_mcp_tool_manifest


def test_checked_in_tool_manifest_matches_broker_contract():
    manifest_path = Path(__file__).resolve().parent.parent / "customer_mcp_server" / "tool-manifest.json"
    checked_in_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    canonical_manifest = json.loads(render_customer_mcp_tool_manifest())

    assert checked_in_manifest == canonical_manifest


def test_connector_tool_registrations_match_broker_contract():
    connector_path = Path(__file__).resolve().parent.parent / "customer_mcp_server" / "promptdrift_mcp_server.py"
    connector_source = connector_path.read_text(encoding="utf-8")
    connector_tool_names = re.findall(r'@server\.tool\(name="([^"]+)"\)', connector_source)
    canonical_manifest = json.loads(render_customer_mcp_tool_manifest())

    assert connector_tool_names == [tool["name"] for tool in canonical_manifest["tools"]]


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