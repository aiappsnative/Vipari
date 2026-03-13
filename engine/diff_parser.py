from __future__ import annotations

from typing import List

from .models import ChangedFile, RelevanceResult, StructuredChange


SIGNAL_TERMS = (
    "credit score",
    "customer data",
    "internal policy",
    "internal policies",
    "refuse",
    "never",
    "do not",
    "model",
    "temperature",
    "tool",
    "function calling",
    "retrieval",
    "knowledge base",
    "comply",
    "reveal",
)


def _normalize_git_path(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def extract_changed_files(diff_text: str) -> List[ChangedFile]:
    if not diff_text.strip():
        return []

    changed_files: List[ChangedFile] = []
    current_lines: List[str] = []
    current_old_path = ""
    current_new_path = ""

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current_lines:
                changed_files.append(
                    ChangedFile(
                        old_path=current_old_path,
                        new_path=current_new_path,
                        diff_lines=current_lines,
                    )
                )
            parts = line.split()
            current_old_path = _normalize_git_path(parts[2]) if len(parts) > 2 else ""
            current_new_path = _normalize_git_path(parts[3]) if len(parts) > 3 else ""
            current_lines = [line]
            continue

        if current_lines:
            current_lines.append(line)

    if current_lines:
        changed_files.append(
            ChangedFile(
                old_path=current_old_path,
                new_path=current_new_path,
                diff_lines=current_lines,
            )
        )

    return changed_files


def extract_structured_change(changed_file: ChangedFile, relevance: RelevanceResult) -> StructuredChange:
    added_lines: List[str] = []
    removed_lines: List[str] = []
    changed_hunks = 0

    for line in changed_file.diff_lines:
        if line.startswith("@@"):
            changed_hunks += 1
            continue
        if line.startswith("+++") or line.startswith("---") or line.startswith("diff --git"):
            continue
        if line.startswith("+"):
            added_lines.append(line[1:].strip())
        elif line.startswith("-"):
            removed_lines.append(line[1:].strip())

    added_clean = [line for line in added_lines if line]
    removed_clean = [line for line in removed_lines if line]

    added_terms = [term for term in SIGNAL_TERMS if any(term in line.lower() for line in added_clean)]
    removed_terms = [term for term in SIGNAL_TERMS if any(term in line.lower() for line in removed_clean)]

    return StructuredChange(
        path=changed_file.path,
        artifact_type=relevance.artifact_type,
        context_mode=relevance.context_mode,
        added_lines=added_clean,
        removed_lines=removed_clean,
        changed_hunks=changed_hunks,
        added_count=len(added_clean),
        removed_count=len(removed_clean),
        added_terms=added_terms,
        removed_terms=removed_terms,
    )
