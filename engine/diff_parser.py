from __future__ import annotations

from typing import List

from .models import ChangedFile


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
