import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.dashboard_views import RepoDashboardArtifactEntry, _artifact_topology_group_key
from services.provenance_labels import artifact_provenance_label


def test_ai_code_artifacts_use_agent_provenance_label():
    provenance = artifact_provenance_label("ai_code")

    assert provenance.family == "agent"
    assert provenance.kind == "ai_agent_surface"
    assert provenance.label == "Agent orchestration surface"


def test_ai_code_artifacts_group_into_agents_topology_bucket():
    entry = RepoDashboardArtifactEntry(
        artifact_path="src/worker.py",
        artifact_type="ai_code",
        discovery_reason="manual test fixture",
        discovery_confidence=1.0,
        baseline_line_count=0,
        historical_version_count=0,
        historical_profile_count=0,
        latest_historical_semantic_distance=0.0,
        latest_historical_drift_magnitude=0.0,
        latest_historical_capability_shift=0.0,
        latest_historical_guardrail_shift=0.0,
        latest_historical_governance_shift=0.0,
        latest_historical_autonomy_shift=0.0,
        pr_profile_count=0,
        latest_pr_semantic_distance=0.0,
        latest_pr_capability_shift=0.0,
        latest_pr_guardrail_shift=0.0,
        provenance_kind="ai_agent_surface",
        provenance_label="Agent orchestration surface",
    )

    assert _artifact_topology_group_key(entry) == "agents"