from dataclasses import dataclass, field
from enum import Enum
from typing import List


class SemanticContextMode(str, Enum):
    DIFF_ONLY = "diff_only"
    SECTION_CONTEXT = "section_context"
    FULL_ARTIFACT_COMPARE = "full_artifact_compare"


@dataclass(frozen=True)
class ChangedFile:
    old_path: str
    new_path: str
    diff_lines: List[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        if self.new_path and self.new_path != "/dev/null":
            return self.new_path
        return self.old_path

    @property
    def raw_diff(self) -> str:
        return "\n".join(self.diff_lines)


@dataclass(frozen=True)
class RelevanceResult:
    path: str
    ai_relevant: bool
    artifact_type: str
    reason: str
    context_mode: SemanticContextMode
