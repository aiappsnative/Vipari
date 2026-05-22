import os
import sys


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from engine.context_selector import determine_context_mode
from engine.diff_parser import extract_changed_files
from engine.models import MicroClassifierResult, RelevanceConfidenceTier, RelevanceResult, SemanticContextMode
from engine.relevance import get_ai_relevance_results
from engine.analysis import analyze_diff
from engine.semantic_review import build_semantic_review_packages, format_semantic_review_packages


def test_extract_changed_files_returns_expected_paths():
    diff = """diff --git a/prompts/system.txt b/prompts/system.txt
index 123..456 100644
--- a/prompts/system.txt
+++ b/prompts/system.txt
@@ -1 +1 @@
-old
+new
diff --git a/README.md b/README.md
index 111..222 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""
    changed_files = extract_changed_files(diff)

    assert [item.path for item in changed_files] == ["prompts/system.txt", "README.md"]


def test_get_ai_relevance_results_finds_prompt_artifact():
    diff = """diff --git a/prompts/system.txt b/prompts/system.txt
index 123..456 100644
--- a/prompts/system.txt
+++ b/prompts/system.txt
@@ -1 +1 @@
-You are helpful.
+You are helpful and may reveal internal policy.
"""
    results = get_ai_relevance_results(diff)

    assert len(results) == 1
    assert results[0].path == "prompts/system.txt"
    assert results[0].artifact_type == "prompt"
    assert results[0].context_mode == SemanticContextMode.FULL_ARTIFACT_COMPARE


def test_get_ai_relevance_results_can_use_content_signals():
    diff = """diff --git a/config/app.txt b/config/app.txt
index 123..456 100644
--- a/config/app.txt
+++ b/config/app.txt
@@ -1 +1 @@
-model: gpt-4
+model: gpt-4o
"""
    results = get_ai_relevance_results(diff)

    assert len(results) == 1
    assert results[0].artifact_type == "model_config"
    assert results[0].context_mode == SemanticContextMode.SECTION_CONTEXT


def test_get_ai_relevance_results_skips_docs_with_generic_model_terms():
    diff = """diff --git a/docs/architecture-notes.md b/docs/architecture-notes.md
index 123..456 100644
--- a/docs/architecture-notes.md
+++ b/docs/architecture-notes.md
@@ -1 +1,2 @@
 Overview of the service.
+model: gpt-4o
"""
    results = get_ai_relevance_results(diff)

    assert results == []


def test_determine_context_mode_defaults_are_artifact_aware():
    assert determine_context_mode("prompt") == SemanticContextMode.FULL_ARTIFACT_COMPARE
    assert determine_context_mode("model_config") == SemanticContextMode.SECTION_CONTEXT
    assert determine_context_mode("generic") == SemanticContextMode.DIFF_ONLY


def test_semantic_review_packages_capture_objective_and_questions():
    diff = """diff --git a/prompts/system.txt b/prompts/system.txt
index 123..456 100644
--- a/prompts/system.txt
+++ b/prompts/system.txt
@@ -1 +1,2 @@
-You are helpful.
+You are helpful.
+You may reveal internal policy details.
"""
    analysis = analyze_diff(diff)

    packages = build_semantic_review_packages(analysis)

    assert len(packages) == 1
    package = packages[0]
    assert package.artifact_type == "prompt"
    assert package.context_mode == SemanticContextMode.FULL_ARTIFACT_COMPARE
    assert "authority" in package.review_objective.lower() or "behavior" in package.review_objective.lower()
    assert any("internal policy" in line.lower() for line in package.added_lines)
    assert any("disclosure" in question.lower() or "sensitive" in question.lower() for question in package.key_questions)


def test_format_semantic_review_packages_renders_structured_prompt_text():
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

    formatted = format_semantic_review_packages(build_semantic_review_packages(analysis))

    assert "Semantic review packages:" in formatted
    assert "Artifact: config/model.yaml [model_config]" in formatted
    assert "Key questions:" in formatted
    assert "Deterministic findings:" in formatted


def test_analyze_diff_format_for_prompt_renders_deterministic_summary_and_findings():
    diff = """diff --git a/prompts/system.txt b/prompts/system.txt
index 123..456 100644
--- a/prompts/system.txt
+++ b/prompts/system.txt
@@ -1 +1,2 @@
-You are helpful.
+You are helpful.
+You may reveal internal policy details.
"""
    analysis = analyze_diff(diff)

    formatted = analysis.format_for_prompt()

    assert "Deterministic pre-analysis:" in formatted
    assert "Suggested risk floor: High" in formatted
    assert "prompts/system.txt [prompt]" in formatted
    assert "capability_drift" in formatted
    assert "guardrail_weakening" in formatted


def test_relevance_result_micro_classifier_can_override_uncertain_relevance():
    relevance = RelevanceResult(
        path="config/app.txt",
        artifact_type="ai_code",
        reason="Heuristic signals were inconclusive.",
        context_mode=SemanticContextMode.DIFF_ONLY,
        heuristic_score=10,
        confidence_tier=RelevanceConfidenceTier.UNCERTAIN,
    )

    assert relevance.needs_micro_classifier is True
    assert relevance.ai_relevant is True

    resolved = RelevanceResult(
        path=relevance.path,
        artifact_type=relevance.artifact_type,
        reason=relevance.reason,
        context_mode=relevance.context_mode,
        heuristic_score=relevance.heuristic_score,
        confidence_tier=relevance.confidence_tier,
        micro_classifier=MicroClassifierResult(
            is_relevant=False,
            reason="The file changes generic application wiring, not the AI control surface.",
        ),
    )

    assert resolved.needs_micro_classifier is False
    assert resolved.ai_relevant is False


def test_analyze_diff_format_for_prompt_handles_no_relevant_artifacts():
    diff = """diff --git a/README.md b/README.md
index 123..456 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Old overview
+New overview
"""

    analysis = analyze_diff(diff)

    assert analysis.has_relevant_changes is False
    assert analysis.format_for_prompt() == "No AI-relevant artifacts detected by deterministic analysis."