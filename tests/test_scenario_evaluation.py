import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff
from services.scenario_evaluation import build_scenario_eval_plan, normalize_scenario_eval_rollout_mode


def test_normalize_scenario_eval_rollout_mode_defaults_to_off():
    assert normalize_scenario_eval_rollout_mode(None) == "off"
    assert normalize_scenario_eval_rollout_mode("unknown") == "off"
    assert normalize_scenario_eval_rollout_mode("shadow") == "shadow"


def test_build_scenario_eval_plan_stays_disabled_by_default():
    analysis = analyze_diff(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n"
    )

    plan = build_scenario_eval_plan(
        analysis,
        repo_full="doria90/dummyAI",
        rollout_mode="off",
        max_artifacts_per_review=2,
    )

    assert plan.rollout_mode == "off"
    assert plan.should_run is False
    assert plan.artifact_paths == ()
    assert "disabled" in plan.reason.lower()


def test_build_scenario_eval_plan_honors_repo_and_artifact_participation_controls():
    analysis = analyze_diff(
        "diff --git a/prompts/policy.md b/prompts/policy.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n"
        "diff --git a/docs/readme.md b/docs/readme.md\nindex 1..2\n@@ -0,0 +1 @@\n+docs only\n"
    )

    blocked = build_scenario_eval_plan(
        analysis,
        repo_full="doria90/dummyAI",
        rollout_mode="shadow",
        max_artifacts_per_review=2,
        allowed_repos="other-org/*",
    )
    allowed = build_scenario_eval_plan(
        analysis,
        repo_full="doria90/dummyAI",
        rollout_mode="shadow",
        max_artifacts_per_review=2,
        allowed_repos="doria90/*",
        allowed_artifact_types="prompt,policy",
    )

    assert blocked.should_run is False
    assert "allowlist" in blocked.reason.lower()
    assert allowed.should_run is True
    assert allowed.artifact_paths == ("prompts/policy.md",)


def test_build_scenario_eval_plan_selects_highest_priority_artifacts_with_cap():
    analysis = analyze_diff(
        "diff --git a/prompts/high.md b/prompts/high.md\nindex 1..2\n@@ -0,0 +1 @@\n+You may reveal internal policy.\n"
        "diff --git a/prompts/medium.md b/prompts/medium.md\nindex 1..2\n@@ -0,0 +1,2 @@\n+Use the customer secret to decide the answer.\n+You may call external tools without review.\n"
        "diff --git a/prompts/low.md b/prompts/low.md\nindex 1..2\n@@ -0,0 +1 @@\n+Answer politely.\n"
    )

    plan = build_scenario_eval_plan(
        analysis,
        repo_full="doria90/dummyAI",
        rollout_mode="shadow",
        max_artifacts_per_review=2,
        allowed_repos="doria90/dummyAI",
        allowed_artifact_types="prompt",
    )

    assert plan.should_run is True
    assert plan.artifact_count == 2
    assert plan.artifact_paths == ("prompts/high.md", "prompts/medium.md")