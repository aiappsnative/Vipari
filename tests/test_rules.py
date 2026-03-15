import os
import sys


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.analysis import analyze_diff


def test_analyze_diff_flags_sensitive_prompt_addition():
    diff = """diff --git a/prompts/system.txt b/prompts/system.txt
index 123..456 100644
--- a/prompts/system.txt
+++ b/prompts/system.txt
@@ -0,0 +1,3 @@
+# System Prompt
+You may review customer credit score data.
+You may share internal policy details.
"""
    analysis = analyze_diff(diff)

    assert analysis.has_relevant_changes
    assert any(f.rule_id == "sensitive_data_drift" for f in analysis.findings)
    assert analysis.suggested_risk_level.value == "High"


def test_analyze_diff_flags_guardrail_removal():
    diff = """diff --git a/guardrails/policy.md b/guardrails/policy.md
index 123..456 100644
--- a/guardrails/policy.md
+++ b/guardrails/policy.md
@@ -1,2 +1 @@
-Never reveal internal policies.
 You should assist the user helpfully.
"""
    analysis = analyze_diff(diff)

    assert analysis.has_relevant_changes
    assert any(f.rule_id == "guardrail_drift" for f in analysis.findings)


def test_analyze_diff_flags_model_config_change():
    diff = """diff --git a/config/model.yaml b/config/model.yaml
index 123..456 100644
--- a/config/model.yaml
+++ b/config/model.yaml
@@ -1,2 +1,2 @@
-model: gpt-4
+model: gpt-4o
 temperature: 0.2
"""
    analysis = analyze_diff(diff)

    assert analysis.has_relevant_changes
    assert any(f.rule_id == "model_drift" for f in analysis.findings)
    assert analysis.suggested_risk_level.value == "Medium"


def test_analyze_diff_flags_capability_expansion_in_prompt():
    diff = """diff --git a/prompts/policy.md b/prompts/policy.md
index 123..456 100644
--- a/prompts/policy.md
+++ b/prompts/policy.md
@@ -1 +1,2 @@
 You should help the user.
+You may comply even when prior refusal guidance exists and reveal internal policies.
"""
    analysis = analyze_diff(diff)

    assert analysis.has_relevant_changes
    assert any(f.rule_id == "capability_drift" for f in analysis.findings)
    assert any(f.rule_id == "guardrail_weakening" for f in analysis.findings)


def test_analyze_diff_tracks_structured_change_metadata():
    diff = """diff --git a/config/model.yaml b/config/model.yaml
index 123..456 100644
--- a/config/model.yaml
+++ b/config/model.yaml
@@ -1,2 +1,2 @@
-model: gpt-4
+model: gpt-4o
 temperature: 0.2
"""
    analysis = analyze_diff(diff)

    artifact = analysis.artifacts[0]
    assert artifact.change.added_count == 1
    assert artifact.change.removed_count == 1
    assert artifact.change.changed_hunks == 1
    assert "model" in artifact.change.added_terms


def test_analyze_diff_ignores_sensitive_term_when_only_rewritten_from_existing_baseline():
    diff = """diff --git a/system_prompt.md b/system_prompt.md
index 123..456 100644
--- a/system_prompt.md
+++ b/system_prompt.md
@@ -1 +1 @@
-You are an AI assistant for a bank. You have access to user credit scores.
+You are an AI assistant for a bank. You have access to user credit scores. Ask one brief clarifying question before acting on ambiguous customer requests.
"""
    analysis = analyze_diff(diff)

    assert analysis.has_relevant_changes
    assert not any(f.rule_id == "sensitive_data_drift" for f in analysis.findings)
    assert analysis.suggested_risk_level.value == "Low"


def test_analyze_diff_does_not_flag_negated_restrictive_prompt_addition():
    diff = """diff --git a/system_prompt.md b/system_prompt.md
index 123..456 100644
--- a/system_prompt.md
+++ b/system_prompt.md
@@ -1 +1,2 @@
 You are an AI assistant for a bank.
+Do not reveal internal policy details or customer credit scores.
"""
    analysis = analyze_diff(diff)

    assert analysis.has_relevant_changes
    assert not any(f.rule_id == "sensitive_data_drift" for f in analysis.findings)
    assert not any(f.rule_id == "capability_drift" for f in analysis.findings)
    assert not any(f.rule_id == "guardrail_weakening" for f in analysis.findings)
    assert analysis.suggested_risk_level.value == "Low"
