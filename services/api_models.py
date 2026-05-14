from __future__ import annotations

from pydantic import BaseModel


class RepositoryOnboardingRequest(BaseModel):
	installation_id: int
	commit_limit_per_artifact: int = 5
	plan_backfill: bool = True
	execute_backfill: bool = True


class RepositoryBackfillRequest(BaseModel):
	installation_id: int


class RepoArtifactAddRequest(BaseModel):
	artifact_path: str
	artifact_type: str | None = None


class RepoArtifactUpdateRequest(BaseModel):
	artifact_type: str


class BaselineDecisionRequest(BaseModel):
	note: str | None = None
	actor_login: str | None = None


class RepoRebaselineRequest(BaseModel):
	snapshot_id: int
	rationale: str | None = None
	actor_login: str | None = None