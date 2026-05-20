from __future__ import annotations

import io
import importlib.util
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
    connector_path = Path(__file__).resolve().parent.parent / "customer_mcp_server" / "vipari_mcp_server.py"
    connector_source = connector_path.read_text(encoding="utf-8")
    connector_tool_names = re.findall(r'@server\.tool\(name="([^"]+)"\)', connector_source)
    canonical_manifest = json.loads(render_customer_mcp_tool_manifest())

    assert connector_tool_names == [tool["name"] for tool in canonical_manifest["tools"]]


def test_build_customer_mcp_bundle_uses_self_contained_package_directory():
    bundle_bytes = build_customer_mcp_bundle(app_base_url="https://app.promptdrift.test")

    archive = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    names = set(archive.namelist())

    assert "README.md" in names
    assert "vipari_mcp_server.py" in names
    assert "requirements.txt" in names
    assert "vipari.env.example" in names
    assert "claude-desktop-config.json.example" in names
    assert "tool-manifest.json" in names

    env_example = archive.read("vipari.env.example").decode("utf-8")
    claude_example = archive.read("claude-desktop-config.json.example").decode("utf-8")
    manifest = json.loads(archive.read("tool-manifest.json").decode("utf-8"))

    assert "{{VIPARI_MCP_BROKER_URL}}" not in env_example
    assert "{{VIPARI_MCP_BROKER_URL}}" not in claude_example
    assert "https://app.promptdrift.test/api/agent-integrations/mcp" in env_example
    assert "https://app.promptdrift.test/api/agent-integrations/mcp" in claude_example
    assert [tool["name"] for tool in manifest["tools"]] == [
        "vipari.list_available_tools",
        "vipari.list_repos",
        "vipari.get_repo_posture",
        "vipari.get_repo_casefile",
        "vipari.list_escalations",
        "vipari.get_export_status",
        "vipari.create_compliance_export",
        "vipari.list_baseline_proposals",
        "vipari.create_baseline_proposal",
        "vipari.list_onboarding_proposals",
        "vipari.create_onboarding_proposal",
        "vipari.add_audit_feedback",
        "vipari.triage_audit",
    ]


def test_connector_formats_structured_broker_errors_readably():
    pytest = __import__("pytest")
    pytest.importorskip("mcp.server.fastmcp")

    connector_path = Path(__file__).resolve().parent.parent / "customer_mcp_server" / "vipari_mcp_server.py"
    spec = importlib.util.spec_from_file_location("vipari_mcp_server_for_tests", connector_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    message = module._format_broker_http_error(
        "request",
        403,
        json.dumps(
            {
                "detail": {
                    "error": "insufficient_scope",
                    "message": "Missing required scope: drift.write.low.",
                }
            }
        ),
    )

    assert message == "Vipari MCP broker request failed: HTTP 403 insufficient_scope: Missing required scope: drift.write.low."