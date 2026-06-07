import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.ai_library_registry import infer_dependency_ecosystem, is_supported_dependency_manifest, match_ai_libraries


def test_registry_matches_python_dependency_manifests():
    file_contents = {
        "requirements.txt": "openai==1.30.0\nrequests==2.32.0\nqdrant-client>=1.9.0\n",
        "pyproject.toml": """
[project]
dependencies = [
  \"anthropic>=0.45.0\",
  \"numpy>=2.0.0\",
]
""",
    }

    matches = match_ai_libraries(file_contents)

    assert [(match.source_file, match.canonical_name) for match in matches] == [
        ("pyproject.toml", "anthropic"),
        ("requirements.txt", "openai"),
        ("requirements.txt", "qdrant"),
    ]
    assert all(match.ecosystem == "python" for match in matches)
    assert all(match.confidence >= 0.9 for match in matches)


def test_registry_matches_package_json_dependencies():
    file_contents = {
        "package.json": """
{
  \"dependencies\": {
    \"openai\": \"^4.0.0\",
    \"@langchain/openai\": \"^0.1.0\",
    \"react\": \"^18.0.0\"
  },
  \"devDependencies\": {
    \"@anthropic-ai/sdk\": \"^0.31.0\"
  }
}
"""
    }

    matches = match_ai_libraries(file_contents)

    assert [match.canonical_name for match in matches] == ["anthropic", "langchain", "openai"]
    assert all(match.source_file == "package.json" for match in matches)
    assert all(match.ecosystem == "npm" for match in matches)


def test_manifest_helpers_identify_supported_files():
    assert infer_dependency_ecosystem("requirements.txt") == "python"
    assert infer_dependency_ecosystem("web/package.json") == "npm"
    assert is_supported_dependency_manifest("services/pyproject.toml") is True
    assert is_supported_dependency_manifest("src/app.py") is False
