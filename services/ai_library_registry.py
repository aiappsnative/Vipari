from __future__ import annotations

from dataclasses import dataclass
import json
import re
import tomllib


@dataclass(frozen=True)
class AiLibraryRegistryEntry:
    canonical_name: str
    ecosystem: str
    package_names: tuple[str, ...]
    vendor: str
    category_primary: str
    capability_tags: tuple[str, ...]
    risk_tags: tuple[str, ...]
    evidence_source_urls: tuple[str, ...]


@dataclass(frozen=True)
class AiLibraryMatch:
    canonical_name: str
    ecosystem: str
    matched_package: str
    source_file: str
    category_primary: str
    capability_tags: tuple[str, ...]
    risk_tags: tuple[str, ...]
    confidence: float


_REGISTRY_ENTRIES: tuple[AiLibraryRegistryEntry, ...] = (
    AiLibraryRegistryEntry(
        canonical_name="openai",
        ecosystem="python",
        package_names=("openai",),
        vendor="OpenAI",
        category_primary="llm_sdk",
        capability_tags=("generative_ai", "model_serving"),
        risk_tags=("external_model_dependency", "external_api_surface"),
        evidence_source_urls=("https://pypi.org/project/openai/",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="anthropic",
        ecosystem="python",
        package_names=("anthropic",),
        vendor="Anthropic",
        category_primary="llm_sdk",
        capability_tags=("generative_ai", "model_serving"),
        risk_tags=("external_model_dependency", "external_api_surface"),
        evidence_source_urls=("https://pypi.org/project/anthropic/",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="langchain",
        ecosystem="python",
        package_names=("langchain", "langchain-openai", "langchain-community"),
        vendor="LangChain",
        category_primary="agent_framework",
        capability_tags=("agentic", "tool_use", "generative_ai"),
        risk_tags=("autonomous_action_hint",),
        evidence_source_urls=("https://pypi.org/project/langchain/",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="llama-index",
        ecosystem="python",
        package_names=("llama-index",),
        vendor="LlamaIndex",
        category_primary="rag_framework",
        capability_tags=("retrieval", "generative_ai"),
        risk_tags=("retrieval_surface_hint",),
        evidence_source_urls=("https://pypi.org/project/llama-index/",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="qdrant",
        ecosystem="python",
        package_names=("qdrant-client",),
        vendor="Qdrant",
        category_primary="vector_database",
        capability_tags=("retrieval", "embeddings"),
        risk_tags=("retrieval_surface_hint",),
        evidence_source_urls=("https://pypi.org/project/qdrant-client/",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="chromadb",
        ecosystem="python",
        package_names=("chromadb",),
        vendor="Chroma",
        category_primary="vector_database",
        capability_tags=("retrieval", "embeddings"),
        risk_tags=("retrieval_surface_hint",),
        evidence_source_urls=("https://pypi.org/project/chromadb/",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="transformers",
        ecosystem="python",
        package_names=("transformers",),
        vendor="Hugging Face",
        category_primary="ml_framework",
        capability_tags=("generative_ai", "classification"),
        risk_tags=("gpai_provider_usage",),
        evidence_source_urls=("https://pypi.org/project/transformers/",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="sentence-transformers",
        ecosystem="python",
        package_names=("sentence-transformers",),
        vendor="Hugging Face",
        category_primary="embedding_sdk",
        capability_tags=("embeddings", "retrieval"),
        risk_tags=("retrieval_surface_hint",),
        evidence_source_urls=("https://pypi.org/project/sentence-transformers/",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="openai",
        ecosystem="npm",
        package_names=("openai",),
        vendor="OpenAI",
        category_primary="llm_sdk",
        capability_tags=("generative_ai", "model_serving"),
        risk_tags=("external_model_dependency", "external_api_surface"),
        evidence_source_urls=("https://www.npmjs.com/package/openai",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="anthropic",
        ecosystem="npm",
        package_names=("@anthropic-ai/sdk",),
        vendor="Anthropic",
        category_primary="llm_sdk",
        capability_tags=("generative_ai", "model_serving"),
        risk_tags=("external_model_dependency", "external_api_surface"),
        evidence_source_urls=("https://www.npmjs.com/package/@anthropic-ai/sdk",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="langchain",
        ecosystem="npm",
        package_names=("langchain", "@langchain/openai", "@langchain/core"),
        vendor="LangChain",
        category_primary="agent_framework",
        capability_tags=("agentic", "tool_use", "generative_ai"),
        risk_tags=("autonomous_action_hint",),
        evidence_source_urls=("https://www.npmjs.com/package/langchain",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="llamaindex",
        ecosystem="npm",
        package_names=("llamaindex",),
        vendor="LlamaIndex",
        category_primary="rag_framework",
        capability_tags=("retrieval", "generative_ai"),
        risk_tags=("retrieval_surface_hint",),
        evidence_source_urls=("https://www.npmjs.com/package/llamaindex",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="pinecone",
        ecosystem="npm",
        package_names=("@pinecone-database/pinecone",),
        vendor="Pinecone",
        category_primary="vector_database",
        capability_tags=("retrieval", "embeddings"),
        risk_tags=("retrieval_surface_hint",),
        evidence_source_urls=("https://www.npmjs.com/package/@pinecone-database/pinecone",),
    ),
    AiLibraryRegistryEntry(
        canonical_name="qdrant",
        ecosystem="npm",
        package_names=("@qdrant/js-client-rest",),
        vendor="Qdrant",
        category_primary="vector_database",
        capability_tags=("retrieval", "embeddings"),
        risk_tags=("retrieval_surface_hint",),
        evidence_source_urls=("https://www.npmjs.com/package/@qdrant/js-client-rest",),
    ),
)

_DEPENDENCY_FILE_NAMES = {
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "poetry.lock": "python",
    "package.json": "npm",
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "npm",
    "yarn.lock": "npm",
    "go.mod": "go",
    "cargo.toml": "rust",
    "cargo.lock": "rust",
}


def list_ai_library_registry() -> list[AiLibraryRegistryEntry]:
    return list(_REGISTRY_ENTRIES)


def match_ai_libraries(file_contents: dict[str, str]) -> list[AiLibraryMatch]:
    alias_index = _build_alias_index()
    deduped_matches: dict[tuple[str, str, str], AiLibraryMatch] = {}

    for path, content in sorted(file_contents.items()):
        ecosystem = infer_dependency_ecosystem(path)
        if ecosystem is None:
            continue

        for dependency in _extract_dependency_names(path, content, ecosystem):
            normalized_dependency = _normalize_package_name(dependency)
            if not normalized_dependency:
                continue
            entry = alias_index.get((ecosystem, normalized_dependency))
            if entry is None:
                continue

            match = AiLibraryMatch(
                canonical_name=entry.canonical_name,
                ecosystem=entry.ecosystem,
                matched_package=dependency,
                source_file=path,
                category_primary=entry.category_primary,
                capability_tags=entry.capability_tags,
                risk_tags=entry.risk_tags,
                confidence=_match_confidence(entry, normalized_dependency),
            )
            dedupe_key = (match.source_file, _normalize_package_name(match.matched_package), match.canonical_name)
            deduped_matches[dedupe_key] = match

    return sorted(
        deduped_matches.values(),
        key=lambda item: (item.source_file.lower(), item.canonical_name.lower(), _normalize_package_name(item.matched_package)),
    )


def infer_dependency_ecosystem(path: str) -> str | None:
    file_name = str(path or "").replace("\\", "/").split("/")[-1].lower()
    return _DEPENDENCY_FILE_NAMES.get(file_name)


def is_supported_dependency_manifest(path: str) -> bool:
    return infer_dependency_ecosystem(path) is not None


def _build_alias_index() -> dict[tuple[str, str], AiLibraryRegistryEntry]:
    index: dict[tuple[str, str], AiLibraryRegistryEntry] = {}
    for entry in _REGISTRY_ENTRIES:
        for package_name in entry.package_names:
            index[(entry.ecosystem, _normalize_package_name(package_name))] = entry
    return index


def _normalize_package_name(value: str) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _match_confidence(entry: AiLibraryRegistryEntry, normalized_dependency: str) -> float:
    primary_name = _normalize_package_name(entry.package_names[0]) if entry.package_names else ""
    return 0.97 if normalized_dependency == primary_name else 0.9


def _extract_dependency_names(path: str, content: str, ecosystem: str) -> list[str]:
    if ecosystem == "python":
        if path.lower().endswith("requirements.txt"):
            return _extract_python_requirements(content)
        if path.lower().endswith("pyproject.toml"):
            return _extract_pyproject_dependencies(content)
        return []

    if ecosystem == "npm":
        if path.lower().endswith("package.json"):
            return _extract_package_json_dependencies(content)
        return []

    return []


def _extract_python_requirements(content: str) -> list[str]:
    dependencies: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r", "--requirement", "-c", "--constraint", "-e", "--editable")):
            continue
        package_name = _extract_python_requirement_name(line)
        if package_name:
            dependencies.append(package_name)
    return dependencies


def _extract_python_requirement_name(line: str) -> str:
    matcher = re.match(r"^([A-Za-z0-9_.\-]+)", line)
    if matcher is None:
        return ""
    return matcher.group(1)


def _extract_pyproject_dependencies(content: str) -> list[str]:
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return []

    dependencies: list[str] = []

    project = parsed.get("project")
    if isinstance(project, dict):
        for item in project.get("dependencies", []):
            if isinstance(item, str):
                package_name = _extract_python_requirement_name(item)
                if package_name:
                    dependencies.append(package_name)

    tool = parsed.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            poetry_dependencies = poetry.get("dependencies", {})
            if isinstance(poetry_dependencies, dict):
                for package_name in poetry_dependencies:
                    if str(package_name).lower() != "python":
                        dependencies.append(str(package_name))

    return dependencies


def _extract_package_json_dependencies(content: str) -> list[str]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return []

    dependencies: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        package_map = parsed.get(key)
        if not isinstance(package_map, dict):
            continue
        for package_name in package_map:
            dependencies.append(str(package_name))
    return dependencies
