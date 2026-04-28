import os
import sys

import pytest


sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import main


@pytest.fixture(autouse=True)
def restore_main_runtime_state():
    settings_snapshot = main.settings.model_dump()
    global_snapshot = {
        "GITHUB_APP_ID": main.GITHUB_APP_ID,
        "GITHUB_PRIVATE_KEY_PATH": main.GITHUB_PRIVATE_KEY_PATH,
        "GITHUB_WEBHOOK_SECRET": main.GITHUB_WEBHOOK_SECRET,
        "OPENAI_API_KEY": main.OPENAI_API_KEY,
        "FOUNDRY_API_KEY": main.FOUNDRY_API_KEY,
        "AZURE_OPENAI_ENDPOINT": main.AZURE_OPENAI_ENDPOINT,
        "AI_MODEL": main.AI_MODEL,
        "AI_API_KEY": main.AI_API_KEY,
        "AUDIT_DB_PATH": main.AUDIT_DB_PATH,
        "AUDIT_WORKER_ENABLED": main.AUDIT_WORKER_ENABLED,
        "LLM_TIMEOUT_SECONDS": main.LLM_TIMEOUT_SECONDS,
        "AUDIT_MAX_ATTEMPTS": main.AUDIT_MAX_ATTEMPTS,
        "AUDIT_MAX_RETRY_WINDOW_SECONDS": main.AUDIT_MAX_RETRY_WINDOW_SECONDS,
        "AUDIT_WORKER_POLL_SECONDS": main.AUDIT_WORKER_POLL_SECONDS,
        "PR_DIFF_FETCH_ATTEMPTS": main.PR_DIFF_FETCH_ATTEMPTS,
        "PR_DIFF_FETCH_RETRY_SECONDS": main.PR_DIFF_FETCH_RETRY_SECONDS,
    }

    yield

    for field_name, value in settings_snapshot.items():
        setattr(main.settings, field_name, value)

    for global_name, value in global_snapshot.items():
        setattr(main, global_name, value)