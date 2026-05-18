import json
import logging

from services.observability import JSONFormatter


def test_json_formatter_includes_step_and_exception_details():
    formatter = JSONFormatter("worker")
    logger = logging.getLogger("test-observability")
    record = None

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            12,
            "Failed to reconcile PR lifecycle audit",
            args=(),
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={
                "repo": "doria90/dummyAI",
                "pr_number": 23,
                "head_sha": "sha-pr-23",
                "installation_id": 123,
                "step": "update-pull-request-audit-state",
            },
        )

    assert record is not None
    payload = json.loads(formatter.format(record))

    assert payload["service"] == "worker"
    assert payload["message"] == "Failed to reconcile PR lifecycle audit"
    assert payload["repo"] == "doria90/dummyAI"
    assert payload["pr_number"] == 23
    assert payload["head_sha"] == "sha-pr-23"
    assert payload["installation_id"] == 123
    assert payload["step"] == "update-pull-request-audit-state"
    assert payload["exception_type"] == "RuntimeError"
    assert payload["exception_message"] == "boom"
    assert "RuntimeError: boom" in payload["traceback"]