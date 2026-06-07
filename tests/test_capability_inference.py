import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.ai_library_registry import AiLibraryMatch
from services.capability_inference import build_capability_delta_signal, compare_capability_profiles, infer_capability_profile
from services.onboarding_records import DiscoveredArtifactInput


def test_infer_capability_profile_combines_dependency_and_artifact_evidence():
    matches = [
        AiLibraryMatch(
            canonical_name="openai",
            ecosystem="python",
            matched_package="openai",
            source_file="requirements.txt",
            category_primary="llm_sdk",
            capability_tags=("generative_ai", "model_serving"),
            risk_tags=("external_model_dependency",),
            confidence=0.97,
        ),
        AiLibraryMatch(
            canonical_name="langchain",
            ecosystem="python",
            matched_package="langchain",
            source_file="requirements.txt",
            category_primary="agent_framework",
            capability_tags=("agentic", "tool_use", "generative_ai"),
            risk_tags=("autonomous_action_hint",),
            confidence=0.97,
        ),
    ]
    artifacts = [
        DiscoveredArtifactInput(
            artifact_path="prompts/system.txt",
            artifact_type="prompt",
            discovery_reason="Path indicates a prompt artifact.",
            confidence=0.9,
            baseline_content="You are a safe assistant.",
        )
    ]

    profile = infer_capability_profile(matches, artifacts)

    assert profile.primary_capability == "generative_ai"
    assert "agentic" in profile.capability_tags
    assert "tool_use" in profile.capability_tags
    assert profile.capability_scores["generative_ai"] == 1.0
    assert profile.confidence > 0.5
    assert len(profile.evidence_items) == 3


def test_infer_capability_profile_empty_when_no_evidence():
    profile = infer_capability_profile([], [])

    assert profile.capability_tags == ()
    assert profile.primary_capability is None
    assert profile.capability_scores == {}
    assert profile.confidence == 0.0
    assert profile.evidence_items == ()


def test_compare_capability_profiles_reports_introduced_and_removed_tags():
    current = infer_capability_profile(
        [
            AiLibraryMatch(
                canonical_name="openai",
                ecosystem="python",
                matched_package="openai",
                source_file="requirements.txt",
                category_primary="llm_sdk",
                capability_tags=("generative_ai", "model_serving"),
                risk_tags=("external_model_dependency",),
                confidence=0.97,
            )
        ],
        [],
    )
    baseline = infer_capability_profile(
        [
            AiLibraryMatch(
                canonical_name="qdrant",
                ecosystem="python",
                matched_package="qdrant-client",
                source_file="requirements.txt",
                category_primary="vector_database",
                capability_tags=("retrieval",),
                risk_tags=("retrieval_surface_hint",),
                confidence=0.97,
            )
        ],
        [],
    )

    delta = compare_capability_profiles(current, baseline)

    assert delta.introduced == ("generative_ai", "model_serving")
    assert delta.removed == ("retrieval",)
    assert delta.retained == ()


def test_build_capability_delta_signal_reports_material_expansion():
    signal = build_capability_delta_signal(0.14, has_measurement=True)

    assert signal.direction == "expanded"
    assert signal.material is True
    assert signal.delta == 0.14
    assert "Material capability delta expanded" in signal.summary


def test_build_capability_delta_signal_reports_unavailable_when_missing_measurement():
    signal = build_capability_delta_signal(None, has_measurement=False)

    assert signal.direction == "stable"
    assert signal.material is False
    assert signal.delta == 0.0
    assert "unavailable" in signal.summary.lower()
