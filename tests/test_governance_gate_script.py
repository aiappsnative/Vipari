from __future__ import annotations

import io
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import scripts.governance_gate as governance_gate


class _FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self._buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self._buffer

    def __exit__(self, exc_type, exc, tb):
        self._buffer.close()
        return False


def test_governance_gate_returns_zero_for_warn_lane(capsys):
    payload = {
        "repo_full": "doria90/dummyAI",
        "pr_number": 84,
        "head_sha": "sha-governance-84",
        "conclusion": "neutral",
        "recommended_gate": "warn",
        "recommended_exit_code": 0,
        "governance_decision": {"decision_lane": "escalate"},
    }
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        exit_code = governance_gate.main(
            [
                "https://example.test/api/repos/doria90/dummyAI/governance-decision",
                "--pr-number",
                "84",
                "--head-sha",
                "sha-governance-84",
            ]
        )

    assert exit_code == 0
    assert "lane=escalate" in capsys.readouterr().out


def test_governance_gate_returns_one_for_block_lane(capsys):
    payload = {
        "repo_full": "doria90/dummyAI",
        "pr_number": 84,
        "head_sha": "sha-governance-84",
        "conclusion": "failure",
        "recommended_gate": "block",
        "recommended_exit_code": 1,
        "governance_decision": {"decision_lane": "block_merge"},
    }
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        exit_code = governance_gate.main(
            [
                "https://example.test/api/repos/doria90/dummyAI/governance-decision",
                "--pr-number",
                "84",
                "--head-sha",
                "sha-governance-84",
                "--rollout-mode",
                "enforce",
            ]
        )

    assert exit_code == 1
    assert "conclusion=failure" in capsys.readouterr().out


def test_governance_gate_supports_admin_token_header():
    captured_request = {}

    def _fake_urlopen(request, timeout=30):
        captured_request["url"] = request.full_url
        captured_request["admin_token"] = request.get_header("X-admin-token")
        return _FakeResponse(
            {
                "repo_full": "doria90/dummyAI",
                "pr_number": 84,
                "head_sha": "sha-governance-84",
                "conclusion": "success",
                "recommended_gate": "pass",
                "recommended_exit_code": 0,
                "governance_decision": {"decision_lane": "normal"},
            }
        )

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        exit_code = governance_gate.main(
            [
                "https://example.test/api/repos/doria90/dummyAI/governance-decision",
                "--pr-number",
                "84",
                "--head-sha",
                "sha-governance-84",
                "--admin-token",
                "secret-token",
            ]
        )

    assert exit_code == 0
    assert "pr_number=84" in captured_request["url"]
    assert captured_request["admin_token"] == "secret-token"


def test_governance_gate_returns_two_for_http_errors(capsys):
    with patch(
        "urllib.request.urlopen",
        side_effect=RuntimeError("network exploded"),
    ):
        exit_code = governance_gate.main(
            [
                "https://example.test/api/repos/doria90/dummyAI/governance-decision",
                "--pr-number",
                "84",
                "--head-sha",
                "sha-governance-84",
            ]
        )

    assert exit_code == 2
    assert "network exploded" in capsys.readouterr().err


def test_governance_gate_posts_commit_status_when_token_provided():
    payload = {
        "repo_full": "doria90/dummyAI",
        "pr_number": 84,
        "head_sha": "sha-governance-84",
        "review_url": "https://app.example.test/dashboard/doria90%2FdummyAI?tab=pr-reviews",
        "conclusion": "failure",
        "recommended_gate": "block",
        "recommended_exit_code": 1,
        "governance_decision": {"decision_lane": "block_merge"},
    }
    posted = []

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        with patch(
            "scripts.governance_gate.post_commit_status",
            lambda repo_full, sha, token, **kwargs: posted.append((repo_full, sha, token, kwargs)),
        ):
            exit_code = governance_gate.main(
                [
                    "https://example.test/api/repos/doria90/dummyAI/governance-decision",
                    "--pr-number",
                    "84",
                    "--head-sha",
                    "sha-governance-84",
                    "--github-status-token",
                    "ghs_test_token",
                ]
            )

    assert exit_code == 1
    assert posted == [
        (
            "doria90/dummyAI",
            "sha-governance-84",
            "ghs_test_token",
            {
                "state": "failure",
                "description": "doria90/dummyAI PR #84 sha-governance-84: lane=block_merge conclusion=failure gate=block",
                "context": "vipari/governance-gate",
                "target_url": "https://app.example.test/dashboard/doria90%2FdummyAI?tab=pr-reviews",
            },
        )
    ]


def test_governance_gate_posts_neutral_check_run_when_token_provided():
    payload = {
        "repo_full": "doria90/dummyAI",
        "pr_number": 84,
        "head_sha": "sha-governance-84",
        "review_url": "https://app.example.test/dashboard/doria90%2FdummyAI?tab=pr-reviews",
        "conclusion": "neutral",
        "recommended_gate": "warn",
        "recommended_exit_code": 0,
        "governance_decision": {
            "rollout_mode": "dry_run",
            "decision_lane": "escalate",
            "rationale": [
                {
                    "summary": "The completed PR audit reached a high-risk outcome and should enter the escalation lane.",
                    "evidence": ["fused_risk=High", "fused_confidence=High"],
                }
            ],
        },
    }
    posted = []

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        with patch(
            "scripts.governance_gate.post_check_run",
            lambda repo_full, sha, token, **kwargs: posted.append((repo_full, sha, token, kwargs)),
        ):
            exit_code = governance_gate.main(
                [
                    "https://example.test/api/repos/doria90/dummyAI/governance-decision",
                    "--pr-number",
                    "84",
                    "--head-sha",
                    "sha-governance-84",
                    "--github-check-run-token",
                    "ghs_test_token",
                ]
            )

    assert exit_code == 0
    assert posted == [
        (
            "doria90/dummyAI",
            "sha-governance-84",
            "ghs_test_token",
            {
                "name": "Vipari Governance",
                "conclusion": "neutral",
                "title": "Governance recommends escalation",
                "summary": "doria90/dummyAI PR #84 sha-governance-84: lane=escalate conclusion=neutral gate=warn",
                "text": "Decision lane: escalate\nRollout mode: dry_run\n\nRationale:\n- The completed PR audit reached a high-risk outcome and should enter the escalation lane.\n  Evidence: fused_risk=High\n  Evidence: fused_confidence=High",
                "details_url": "https://app.example.test/dashboard/doria90%2FdummyAI?tab=pr-reviews",
            },
        )
    ]


def test_governance_gate_returns_two_when_check_run_payload_lacks_repo_or_sha(capsys):
    payload = {
        "repo_full": "",
        "pr_number": 84,
        "head_sha": "",
        "conclusion": "neutral",
        "recommended_gate": "warn",
        "recommended_exit_code": 0,
        "governance_decision": {"decision_lane": "escalate"},
    }

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        exit_code = governance_gate.main(
            [
                "https://example.test/api/repos/doria90/dummyAI/governance-decision",
                "--pr-number",
                "84",
                "--head-sha",
                "sha-governance-84",
                "--github-check-run-token",
                "ghs_test_token",
            ]
        )

    assert exit_code == 2
    assert "repo_full and head_sha to post a check run" in capsys.readouterr().err