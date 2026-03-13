import os
import sys
import hmac
import hashlib
import asyncio
import json
from unittest.mock import patch

# make sure the package root is on sys.path so `import main` works
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def sign_payload(payload: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), payload, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def test_verify_signature_valid():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    body = b"payload"
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    class Dummy:
        def __init__(self):
            self.headers = {"X-Hub-Signature-256": sig}
            self._body = body

        async def body(self):
            return self._body

    req = Dummy()
    assert asyncio.run(main.verify_signature(req))


def test_verify_signature_invalid():
    main.GITHUB_WEBHOOK_SECRET = "secret"

    class Dummy:
        def __init__(self):
            self.headers = {"X-Hub-Signature-256": "sha256=wrong"}
            self._body = b"foo"

        async def body(self):
            return self._body

    req = Dummy()
    assert not asyncio.run(main.verify_signature(req))


def test_needs_audit_false():
    diff = """diff --git a/README.md b/README.md
index 123..456
"""
    assert not main.needs_audit(diff)


def test_needs_audit_true():
    diff = """diff --git a/prompts/test.txt b/prompts/test.txt
index 123..456
"""
    assert main.needs_audit(diff)


def test_webhook_invalid_signature():
    payload = {"action": "opened"}
    response = client.post("/webhook", json=payload, headers={"X-Hub-Signature-256": "bad"})
    assert response.status_code == 400


def test_webhook_queues_relevant_audit_job():
    main.GITHUB_WEBHOOK_SECRET = "secret"
    payload = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"full_name": "doria90/dummyAI"},
        "pull_request": {"number": 7, "head": {"sha": "abc123"}},
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": sign_payload(body, "secret"),
        "X-GitHub-Event": "pull_request",
    }

    with patch("main.generate_jwt", return_value="jwt-token"), patch(
        "main.get_installation_token", return_value="installation-token"
    ), patch("main.fetch_pr_diff", return_value="diff --git a/prompts/test.txt b/prompts/test.txt\nindex 1..2\n"), patch(
        "main.create_audit_job"
    ) as create_job:
        create_job.return_value = type("Job", (), {"id": 42})()
        response = client.post("/webhook", content=body, headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"message": "audit queued", "job_id": 42}
    create_job.assert_called_once()


# additional tests could mock github/openai but for MVP keep simple
