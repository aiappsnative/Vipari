import csv
import io
import json
import sqlite3
import zipfile
from dataclasses import asdict

from engine.drift_profile import AgentAttributeProfile, StaticSignals
from services.baseline_provenance import approved_onboarding_provenance, baseline_provenance_to_json
from services.compliance_export_service import ComplianceExportRequest, build_compliance_export
from services.audit_records import init_audit_record_db
from services.onboarding_records import OnboardingBaselineVersionRecord, init_onboarding_record_db
from services.repo_journey_records import init_repo_journey_db, upsert_repo_posture_snapshot


def _make_profile(
    *,
    guardrail_robustness: float,
    capability_risk: float,
    autonomy_level: float,
    governance_strength: float,
    stability_vs_creativity: float = 0.5,
    semantic_density: float = 0.6,
    change_frequency: float = 0.1,
) -> AgentAttributeProfile:
    return AgentAttributeProfile(
        guardrail_robustness=guardrail_robustness,
        capability_risk=capability_risk,
        autonomy_level=autonomy_level,
        stability_vs_creativity=stability_vs_creativity,
        governance_strength=governance_strength,
        change_frequency=change_frequency,
        semantic_density=semantic_density,
        signals=StaticSignals(
            token_count=10,
            char_count=80,
            section_count=1,
            example_count=0,
            instruction_density=0.5,
            constraint_count=2,
            explicit_limit_count=1,
            ambiguity_count=0,
        ),
    )


def _normalized_artifact_id(repo_full: str, artifact_path: str) -> str:
    return f"{repo_full.lower()}::{artifact_path.lower()}"


def _init_export_db(db_path: str) -> None:
    init_onboarding_record_db(db_path)
    init_audit_record_db(db_path)
    init_repo_journey_db(db_path)


def _seed_export_fixture(db_path: str) -> dict[str, float]:
    repo_full = "test/repo"
    artifact_path = "prompts/system.txt"
    normalized_artifact_id = _normalized_artifact_id(repo_full, artifact_path)
    baseline_profile = _make_profile(
        guardrail_robustness=0.8,
        capability_risk=0.2,
        autonomy_level=0.1,
        governance_strength=0.9,
    )
    current_profile = _make_profile(
        guardrail_robustness=0.5,
        capability_risk=0.6,
        autonomy_level=0.2,
        governance_strength=0.7,
    )
    baseline_created_at = 1_700_000_000.0
    audit_created_at = 1_700_000_100.0

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO repository_onboardings (id, repo_full, installation_id, default_branch, status, discovered_artifact_count, approved_by, approved_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, repo_full, 123, "main", "completed", 1, "owner", baseline_created_at, baseline_created_at, baseline_created_at),
        )
        conn.execute(
            "INSERT INTO onboarded_artifacts (id, onboarding_id, repo_full, artifact_path, artifact_type, discovery_reason, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (10, 1, repo_full, artifact_path, "prompt", "discovered", 0.99, baseline_created_at),
        )
        conn.execute(
            "INSERT INTO onboarding_baseline_versions (id, onboarding_id, onboarded_artifact_id, normalized_artifact_id, artifact_path, artifact_type, version_hash, signal_terms_json, line_count, content_text, profile_json, approval_status, approved_by, approved_at, approval_note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                100,
                1,
                10,
                normalized_artifact_id,
                artifact_path,
                "prompt",
                "baseline-hash",
                json.dumps(["policy", "safety"]),
                12,
                "baseline system prompt",
                json.dumps(asdict(baseline_profile)),
                "approved",
                "reviewer",
                baseline_created_at,
                "Looks good",
                baseline_created_at,
            ),
        )
        conn.execute(
            "INSERT INTO baseline_audit_log (id, repo_full, onboarding_id, artifact_path, action, actor_login, note, baseline_version_id, snapshot_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (200, repo_full, 1, artifact_path, "approved", "reviewer", "Accepted for monitoring", 100, None, baseline_created_at),
        )
        conn.execute(
            "INSERT INTO pull_request_audits (id, job_id, repo_full, pr_number, installation_id, head_sha, pr_state, pr_merged, pr_closed_at, pr_merged_at, pr_merge_commit_sha, pr_updated_at, status, completion_mode, output_mode, deterministic_score, suggested_risk_level, semantic_review_completed, error_message, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                300,
                9000,
                repo_full,
                7,
                123,
                "head123",
                "open",
                0,
                None,
                None,
                None,
                audit_created_at,
                "completed",
                "full",
                "json",
                88,
                "high",
                1,
                None,
                audit_created_at,
                audit_created_at,
            ),
        )
        conn.execute(
            "INSERT INTO changed_artifacts (id, audit_id, artifact_path, artifact_type, context_mode, relevance_reason, changed_hunks, added_count, removed_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (301, 300, artifact_path, "prompt", "full", "primary artifact", 1, 10, 2, audit_created_at),
        )
        conn.execute(
            "INSERT INTO findings (id, audit_id, changed_artifact_id, source, rule_id, title, severity, rationale, evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (302, 300, 301, "static", "RULE-1", "Missing safeguard", "high", "Guardrails weakened", json.dumps(["example"]), audit_created_at),
        )
        conn.execute(
            "INSERT INTO artifact_versions (id, audit_id, changed_artifact_id, normalized_artifact_id, artifact_path, artifact_type, version_hash, signal_terms_json, line_count, content_text, previous_version_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                303,
                300,
                301,
                normalized_artifact_id,
                artifact_path,
                "prompt",
                "scan-hash",
                json.dumps(["tool"]),
                14,
                "current system prompt",
                None,
                audit_created_at,
            ),
        )
        conn.execute(
            "INSERT INTO static_artifact_profiles (id, audit_id, changed_artifact_id, artifact_version_id, normalized_artifact_id, artifact_path, artifact_type, profile_json, baseline_profile_id, baseline_provenance_json, semantic_similarity, semantic_distance, attribute_deltas_json, narrative_json, signal_terms_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                304,
                300,
                301,
                303,
                normalized_artifact_id,
                artifact_path,
                "prompt",
                json.dumps(asdict(current_profile)),
                None,
                baseline_provenance_to_json(approved_onboarding_provenance(100)),
                0.75,
                0.25,
                json.dumps(
                    {
                        "guardrail_robustness": -0.3,
                        "capability_risk": 0.4,
                        "autonomy_level": 0.1,
                        "governance_strength": -0.2,
                        "stability_vs_creativity": 0.05,
                        "semantic_density": 0.12,
                        "change_frequency": 0.0,
                    }
                ),
                json.dumps(["Guardrails weakened", "Capabilities expanded"]),
                json.dumps(["tool"]),
                audit_created_at,
            ),
        )

    upsert_repo_posture_snapshot(
        db_path,
        snapshot_key="snap-1",
        repo_full=repo_full,
        commit_sha="head123",
        pr_number=7,
        author="alice",
        created_at=audit_created_at,
        snapshot_type="pull_request",
        baseline_reference="baseline-hash",
        default_branch="main",
        source_ref="refs/pull/7/head",
        source_url="https://github.com/test/repo/pull/7",
        attribute_vector={"capability": 0.6, "guardrails": 0.5, "autonomy": 0.2, "governance": 0.7},
        artifact_coverage={"artifact_count": 1, "tracked_paths": [artifact_path]},
        artifact_state={artifact_path: {"artifact_type": "prompt", "profile": {"guardrails": 0.5}}},
        change_summary={"changed_artifact_count": 1, "added_artifact_count": 0, "removed_artifact_count": 0, "critical_surfaces_changed": 1},
        change_breakdown={
            "changed_artifact_count": 1,
            "added_artifact_count": 0,
            "removed_artifact_count": 0,
            "changed_artifact_paths": [artifact_path],
            "added_artifact_paths": [],
            "removed_artifact_paths": [],
            "by_family": {"prompt": 1, "config": 0, "tool": 0, "governance": 0, "model": 0, "other": 0},
            "critical_surfaces_changed": 1,
        },
        drift_summary={"semantic_distance": 0.25},
        risk_summary={"risk_level": "high", "score": 1.6, "critical_surfaces_changed": 1},
        change_labels=["guardrails_weakened", "capability_expanded"],
        baseline_authority={"approved": True},
        input_summary={"baseline": "approved"},
        distance_from_baseline=0.25,
        distance_from_previous=0.1,
        materializer_version=1,
    )

    return {"baseline_created_at": baseline_created_at, "audit_created_at": audit_created_at}


def _read_csv_from_zip(zf: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(zf.read(filename).decode())))


class TestComplianceExportService:
    def test_build_baseline_registry_csv_uses_supported_baseline_fields(self):
        from services.compliance_export_service import _build_baseline_registry_csv

        baseline = OnboardingBaselineVersionRecord(
            id=1,
            onboarding_id=1,
            onboarded_artifact_id=1,
            normalized_artifact_id="artifact-1",
            artifact_path="prompts/system.txt",
            artifact_type="prompt",
            version_hash="abc123",
            signal_terms=["policy"],
            line_count=12,
            profile=_make_profile(
                guardrail_robustness=0.4,
                capability_risk=0.3,
                autonomy_level=0.2,
                governance_strength=0.8,
            ),
            approval_status="approved",
            approved_by="reviewer",
            approved_at=1_700_000_000,
            approval_note="Looks good",
            created_at=1_700_000_000,
            content_text=None,
        )

        csv_text = _build_baseline_registry_csv([baseline])

        assert "approval_source" in csv_text
        assert "repo_baseline_review" in csv_text
        assert "approved" in csv_text

    def test_build_compliance_export_uses_actual_values_and_includes_artifact_content(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _init_export_db(db_path)
        timestamps = _seed_export_fixture(db_path)

        request = ComplianceExportRequest(
            repo_full="test/repo",
            from_ts=timestamps["baseline_created_at"] - 1,
            to_ts=timestamps["audit_created_at"] + 1,
            export_mode="compliance_plus_drift",
            include_artifact_content=True,
        )

        result = build_compliance_export(db_path, request)

        with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as zf:
            assert "09-artifact-content.json" in zf.namelist()

            baseline_audit_rows = _read_csv_from_zip(zf, "02-baseline-audit-log.csv")
            assert baseline_audit_rows == [
                {
                    "actor": "reviewer",
                    "action": "approved",
                    "artifact_path": "prompts/system.txt",
                    "artifact_type": "prompt",
                    "timestamp": "2023-11-14T22:13:20Z",
                    "rationale": "Accepted for monitoring",
                }
            ]

            version_rows = _read_csv_from_zip(zf, "03-version-history.csv")
            assert version_rows[0]["high-level_risk_status"] == "high"

            findings_rows = _read_csv_from_zip(zf, "05-findings.csv")
            assert findings_rows == [
                {
                    "audit_id": "300",
                    "pr_number": "7",
                    "artifact_path": "prompts/system.txt",
                    "source": "static",
                    "rule_id": "RULE-1",
                    "title": "Missing safeguard",
                    "severity": "high",
                    "rationale": "Guardrails weakened",
                    "created_at": "2023-11-14T22:15:00Z",
                }
            ]

            risk_rows = _read_csv_from_zip(zf, "06-risk-events.csv")
            assert risk_rows == [
                {
                    "snapshot_key": "snap-1",
                    "commit_sha": "head123",
                    "pr_number": "7",
                    "artifact": "prompts/system.txt",
                    "created_at": "2023-11-14T22:15:00Z",
                    "risk_level": "high",
                    "reason": "1 critical surfaces changed; labels: guardrails_weakened, capability_expanded; baseline distance 0.2500",
                    "baseline_reference": "baseline-hash",
                    "source_url": "https://github.com/test/repo/pull/7",
                }
            ]

            drift_history_rows = _read_csv_from_zip(zf, "07-drift/artifact-drift-history.csv")
            assert drift_history_rows == [
                {
                    "artifact_path": "prompts/system.txt",
                    "artifact_type": "prompt",
                    "audit_id": "300",
                    "pr_number": "7",
                    "head_sha": "head123",
                    "created_at": "2023-11-14T22:15:00Z",
                    "version_hash": "scan-hash",
                    "semantic_distance": "0.25",
                    "guardrail_robustness_delta": "-0.3",
                    "capability_risk_delta": "0.4",
                    "autonomy_level_delta": "0.1",
                    "governance_strength_delta": "-0.2",
                    "stability_vs_creativity_delta": "0.05",
                    "semantic_density_delta": "0.12",
                    "narrative": "Guardrails weakened | Capabilities expanded",
                }
            ]

            leaderboard_rows = _read_csv_from_zip(zf, "07-drift/drift-leaderboard.csv")
            assert leaderboard_rows == [
                {
                    "artifact_path": "prompts/system.txt",
                    "artifact_type": "prompt",
                    "sample_count": "1",
                    "latest_created_at": "2023-11-14T22:15:00Z",
                    "semantic_distance": "0.25",
                    "guardrail_shift": "-0.3",
                    "capability_shift": "0.4",
                    "autonomy_shift": "0.1",
                    "drift_magnitude": "1.05",
                    "narrative": "Guardrails weakened | Capabilities expanded",
                }
            ]

            posture_summary = json.loads(zf.read("07-drift/posture-summary.json"))
            assert posture_summary == {
                "artifact_count": 1,
                "profile_count": 1,
                "baseline_linked_profile_count": 1,
                "avg_semantic_distance": 0.25,
                "avg_guardrail_shift": 0.3,
                "avg_capability_shift": 0.4,
                "highest_capability_artifact_path": "prompts/system.txt",
                "highest_capability_delta": 0.4,
            }

            artifact_content = json.loads(zf.read("09-artifact-content.json"))
            assert artifact_content == [
                {
                    "source_kind": "approved_baseline",
                    "artifact_path": "prompts/system.txt",
                    "artifact_type": "prompt",
                    "version_hash": "baseline-hash",
                    "approved_by": "reviewer",
                    "approved_at": "2023-11-14T22:13:20Z",
                    "created_at": "2023-11-14T22:13:20Z",
                    "content_text": "baseline system prompt",
                },
                {
                    "source_kind": "pr_scan",
                    "artifact_path": "prompts/system.txt",
                    "artifact_type": "prompt",
                    "version_hash": "scan-hash",
                    "audit_id": 300,
                    "pr_number": 7,
                    "head_sha": "head123",
                    "created_at": "2023-11-14T22:15:00Z",
                    "content_text": "current system prompt",
                },
            ]

    def test_build_compliance_export_omits_artifact_content_when_not_requested(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _init_export_db(db_path)
        timestamps = _seed_export_fixture(db_path)

        request = ComplianceExportRequest(
            repo_full="test/repo",
            from_ts=timestamps["baseline_created_at"] - 1,
            to_ts=timestamps["audit_created_at"] + 1,
            export_mode="compliance",
            include_artifact_content=False,
        )

        result = build_compliance_export(db_path, request)

        with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as zf:
            assert "09-artifact-content.json" not in zf.namelist()
            assert "07-drift/posture-summary.json" not in zf.namelist()

    def test_empty_date_range_keeps_headers_without_rows(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _init_export_db(db_path)
        _seed_export_fixture(db_path)

        request = ComplianceExportRequest(
            repo_full="test/repo",
            from_ts=1_800_000_000,
            to_ts=1_800_000_100,
            export_mode="compliance",
            include_artifact_content=False,
        )

        result = build_compliance_export(db_path, request)

        with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as zf:
            assert _read_csv_from_zip(zf, "04-pr-scan-history.csv") == []
            assert _read_csv_from_zip(zf, "05-findings.csv") == []
            assert _read_csv_from_zip(zf, "06-risk-events.csv") == []