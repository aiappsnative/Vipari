from engine.models import ChangedFile
from engine.relevance import classify_changed_file, infer_artifact_type_from_path


def test_infer_artifact_type_from_path_recognizes_agent_paths():
    assert infer_artifact_type_from_path("agents/worker.py") == "ai_code"
    assert infer_artifact_type_from_path("workflows/refund_orchestrator.py") == "ai_code"


def test_infer_artifact_type_from_path_keeps_tool_paths_as_tooling_without_content():
    assert infer_artifact_type_from_path("tools/ai_agent_tool.py") == "tooling"


def test_classify_changed_file_recognizes_agent_workflow_content():
    changed_file = ChangedFile(
        old_path="",
        new_path="src/runtime.py",
        diff_lines=[
            "def run_agent():",
            "    return agent_executor.execute(workflow='refunds')",
        ],
    )

    result = classify_changed_file(changed_file)

    assert result.artifact_type == "ai_code"
    assert result.ai_relevant is True


def test_classify_changed_file_keeps_plain_tool_surface_as_tooling():
    changed_file = ChangedFile(
        old_path="",
        new_path="tools/ai_agent_tool.py",
        diff_lines=[
            "def run_tool():",
            "    return function_call('lookup_customer')",
        ],
    )

    result = classify_changed_file(changed_file)

    assert result.artifact_type == "tooling"
    assert result.ai_relevant is True


def test_classify_changed_file_upgrades_mixed_tool_path_with_orchestration_content():
    changed_file = ChangedFile(
        old_path="",
        new_path="tools/ai_agent_tool.py",
        diff_lines=[
            "def run_tool():",
            "    return agent_executor.plan_and_run('refund workflow')",
        ],
    )

    result = classify_changed_file(changed_file)

    assert result.artifact_type == "ai_code"
    assert result.ai_relevant is True