from __future__ import annotations

import re

from services.control_plane_records import upsert_ai_system_for_repo
from services.onboarding_records import (
    get_latest_repository_onboarding,
    list_latest_approved_onboarding_baseline_versions_for_onboarding,
    list_onboarded_artifacts_for_onboarding,
)
from services.provenance_labels import artifact_family


def _repo_display_name(repo_full: str) -> str:
    owner, _separator, repo_name = repo_full.partition("/")
    if repo_name:
        return f"{owner}/{repo_name}"
    return repo_full


def _default_purpose_summary(repo_full: str, artifact_families: list[str]) -> str:
    if artifact_families:
        family_copy = ", ".join(artifact_families)
        return f"Repository-backed AI system monitored from GitHub artifacts across: {family_copy}."
    return f"Repository-backed AI system monitored from GitHub evidence in {repo_full}."


def _normalized_words(*parts: str) -> set[str]:
    combined = " ".join(part for part in parts if part)
    return {token for token in re.split(r"[^a-z0-9]+", combined.lower()) if token}


def _infer_domain(repo_full: str, artifact_paths: list[str], evidence_text: str, artifact_families: list[str]) -> str | None:
    words = _normalized_words(repo_full, " ".join(artifact_paths), evidence_text)
    keyword_groups: tuple[tuple[str, set[str]], ...] = (
        ("employment", {"hiring", "hire", "recruit", "recruiting", "candidate", "candidates", "resume", "resumes", "cv", "interview", "interviews", "employment", "worker", "workforce"}),
        ("education", {"student", "students", "education", "classroom", "grading", "grade", "exam", "course", "courses", "admission", "admissions", "tutor", "tutoring"}),
        ("biometric", {"biometric", "face", "facial", "fingerprint", "fingerprints", "iris", "voiceprint", "liveness", "identity", "verification", "kyc"}),
        ("law_enforcement", {"police", "criminal", "investigation", "investigations", "surveillance", "justice", "border", "forensics", "forensic", "law", "enforcement"}),
        ("essential_services", {"credit", "lending", "loan", "loans", "insurance", "claim", "claims", "medical", "healthcare", "health", "diagnosis", "benefits", "eligibility", "housing", "utility", "utilities"}),
        ("internal_productivity", {"ticket", "tickets", "workflow", "knowledge", "documentation", "support", "internal", "ops", "operations", "productivity", "triage"}),
    )
    for domain, keywords in keyword_groups:
        if words.intersection(keywords):
            return domain
    if {"prompt", "tool", "model"}.intersection(artifact_families):
        return "general_purpose"
    return None


def _purpose_summary_for_domain(domain: str | None, artifact_families: list[str], repo_full: str) -> str:
    family_copy = ", ".join(artifact_families) if artifact_families else "repository evidence"
    domain_copy = {
        "employment": "employment and worker-management workflows",
        "education": "education and training workflows",
        "biometric": "biometric or identity-verification workflows",
        "law_enforcement": "law-enforcement or public-authority support workflows",
        "essential_services": "essential private or public service workflows",
        "internal_productivity": "internal productivity workflows",
        "general_purpose": "general-purpose AI workflows",
    }.get(domain)
    if domain_copy:
        return f"Repository-backed AI system supporting {domain_copy}, monitored from GitHub artifacts across: {family_copy}."
    return _default_purpose_summary(repo_full, artifact_families)


def sync_ai_system_for_repo(
    db_path: str,
    *,
    workspace_id: int,
    repo_full: str,
    created_by_user_id: int | None,
):
    onboarding = get_latest_repository_onboarding(db_path, repo_full)
    artifact_families: list[str] = []
    artifact_paths: list[str] = []
    evidence_parts: list[str] = []
    latest_onboarding_status = onboarding.status if onboarding is not None else "connected"
    if onboarding is not None:
        artifacts = list_onboarded_artifacts_for_onboarding(db_path, onboarding.id)
        artifact_families = sorted({artifact_family(item.artifact_type) for item in artifacts if item.artifact_type})
        artifact_paths = [item.artifact_path for item in artifacts if item.artifact_path]
        approved_versions = list_latest_approved_onboarding_baseline_versions_for_onboarding(db_path, onboarding.id)
        evidence_parts = [version.content_text or "" for version in approved_versions if version.content_text]

    inferred_domain = _infer_domain(repo_full, artifact_paths, "\n".join(evidence_parts), artifact_families)
    purpose_summary = _purpose_summary_for_domain(inferred_domain, artifact_families, repo_full)

    return upsert_ai_system_for_repo(
        db_path,
        workspace_id=workspace_id,
        repo_full=repo_full,
        display_name=_repo_display_name(repo_full),
        latest_onboarding_status=latest_onboarding_status,
        artifact_families=artifact_families,
        eu_ai_act_domain=inferred_domain,
        purpose_summary=purpose_summary,
        created_by_user_id=created_by_user_id,
    )