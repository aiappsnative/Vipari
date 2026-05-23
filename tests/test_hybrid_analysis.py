import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.hybrid_analysis import build_hybrid_analysis_plan, normalize_hybrid_static_analysis_rollout_mode


def test_normalize_hybrid_static_analysis_rollout_mode_defaults_to_off():
    assert normalize_hybrid_static_analysis_rollout_mode(None) == "off"
    assert normalize_hybrid_static_analysis_rollout_mode("shadow") == "shadow"
    assert normalize_hybrid_static_analysis_rollout_mode("enforce") == "off"


def test_build_hybrid_analysis_plan_stays_disabled_by_default():
    analysis = analyze_diff(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n"
    )

    plan = build_hybrid_analysis_plan(
        analysis,
        repo_full="doria90/dummyAI",
        rollout_mode="off",
        max_artifacts_per_review=2,
    )

    assert plan.should_run is False
    assert plan.requests == ()
    assert "disabled" in plan.reason.lower()


def test_build_hybrid_analysis_plan_honors_allowlists_and_maps_analyzers():
    analysis = analyze_diff(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n"
        "diff --git a/config/model.yaml b/config/model.yaml\nindex 1..2\n@@ -0,0 +1 @@\n+temperature: 1.0\n"
    )

    blocked = build_hybrid_analysis_plan(
        analysis,
        repo_full="doria90/dummyAI",
        rollout_mode="shadow",
        max_artifacts_per_review=2,
        allowed_repos="other-org/*",
    )
    allowed = build_hybrid_analysis_plan(
        analysis,
        repo_full="doria90/dummyAI",
        rollout_mode="shadow",
        max_artifacts_per_review=2,
        allowed_repos="doria90/*",
        allowed_artifact_types="prompt,model_config",
    )

    assert blocked.should_run is False
    assert "allowlist" in blocked.reason.lower()
    assert allowed.should_run is True
    assert [request.analyzer_key for request in allowed.requests] == ["prompt_policy_static_scan", "config_contract_scan"]


def test_build_hybrid_analysis_plan_caps_requests_by_priority():
    analysis = analyze_diff(
        "diff --git a/prompts/high.md b/prompts/high.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n"
        "diff --git a/config/medium.yaml b/config/medium.yaml\nindex 1..2\n@@ -0,0 +1 @@\n+api_key: customer-secret\n"
        "diff --git a/prompts/low.md b/prompts/low.md\nindex 1..2\n@@ -0,0 +1 @@\n+Answer politely.\n"
    )

    plan = build_hybrid_analysis_plan(
        analysis,
        repo_full="doria90/dummyAI",
        rollout_mode="shadow",
        max_artifacts_per_review=2,
        allowed_repos="doria90/dummyAI",
    )

    assert plan.should_run is True
    assert plan.request_count == 2
    assert [request.artifact_path for request in plan.requests] == ["prompts/high.md", "prompts/low.md"]