#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from services.github_integration import post_check_run, post_commit_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call a Vipari governance-decision endpoint and exit with the recommended code."
    )
    parser.add_argument("endpoint_url", help="Governance decision endpoint URL without query parameters.")
    parser.add_argument("--pr-number", required=True, type=int, help="Pull request number to evaluate.")
    parser.add_argument("--head-sha", required=True, help="Head SHA to evaluate.")
    parser.add_argument("--rollout-mode", default="dry_run", help="Governance rollout mode to request.")
    parser.add_argument(
        "--admin-token",
        help="Optional API admin token; sent as X-Admin-Token for admin-token endpoints.",
    )
    parser.add_argument(
        "--bearer-token",
        help="Optional bearer token; sent as Authorization: Bearer ... for control-plane endpoints.",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="Additional raw header in 'Name: Value' format. May be repeated.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full response JSON instead of the compact summary line.",
    )
    parser.add_argument(
        "--github-status-token",
        help="Optional GitHub token used to post a commit status after reading the governance decision.",
    )
    parser.add_argument(
        "--github-status-context",
        default="vipari/governance-gate",
        help="GitHub commit status context to use when --github-status-token is provided.",
    )
    parser.add_argument(
        "--github-check-run-token",
        help="Optional GitHub token used to post a completed check run after reading the governance decision.",
    )
    parser.add_argument(
        "--github-check-run-name",
        default="Vipari Governance",
        help="GitHub check-run name to use when --github-check-run-token is provided.",
    )
    return parser


def build_request_url(endpoint_url: str, *, pr_number: int, head_sha: str, rollout_mode: str) -> str:
    query = urllib.parse.urlencode(
        {
            "pr_number": int(pr_number),
            "head_sha": head_sha,
            "rollout_mode": rollout_mode,
        }
    )
    separator = "&" if "?" in endpoint_url else "?"
    return f"{endpoint_url}{separator}{query}"


def build_headers(
    *,
    admin_token: str | None,
    bearer_token: str | None,
    raw_headers: list[str],
) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    if admin_token:
        headers["X-Admin-Token"] = admin_token
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    for raw_header in raw_headers:
        if ":" not in raw_header:
            raise ValueError(f"Invalid --header value: {raw_header!r}. Expected 'Name: Value'.")
        key, value = raw_header.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def request_json(url: str, *, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def summarize_payload(payload: dict[str, Any]) -> str:
    conclusion = str(payload.get("conclusion") or "unknown")
    gate = str(payload.get("recommended_gate") or "unknown")
    decision = payload.get("governance_decision") if isinstance(payload.get("governance_decision"), dict) else {}
    lane = str(decision.get("decision_lane") or "unknown")
    repo_full = str(payload.get("repo_full") or "unknown")
    pr_number = payload.get("pr_number")
    head_sha = str(payload.get("head_sha") or "")
    return f"{repo_full} PR #{pr_number} {head_sha}: lane={lane} conclusion={conclusion} gate={gate}"


def github_status_state(payload: dict[str, Any]) -> str:
    conclusion = str(payload.get("conclusion") or "").strip().lower()
    if conclusion == "failure":
        return "failure"
    if conclusion in {"success", "neutral"}:
        return "success"
    return "error"


def github_check_run_conclusion(payload: dict[str, Any]) -> str:
    conclusion = str(payload.get("conclusion") or "").strip().lower()
    if conclusion in {"success", "neutral", "failure"}:
        return conclusion
    return "failure"


def github_check_run_title(payload: dict[str, Any]) -> str:
    gate = str(payload.get("recommended_gate") or "").strip().lower()
    if gate == "block":
        return "Governance blocked merge"
    if gate == "warn":
        return "Governance recommends escalation"
    return "Governance passed"


def github_check_run_evidence_lines(reason: dict[str, Any], *, limit: int = 2) -> list[str]:
    evidence = reason.get("evidence") if isinstance(reason.get("evidence"), list) else []
    lines: list[str] = []
    for item in [str(value).strip() for value in evidence if str(value).strip()][:limit]:
        lines.append(f"  Evidence: {item}")
    return lines


def github_check_run_text(payload: dict[str, Any]) -> str | None:
    decision = payload.get("governance_decision") if isinstance(payload.get("governance_decision"), dict) else {}
    rationale = decision.get("rationale") if isinstance(decision.get("rationale"), list) else []
    lane = str(decision.get("decision_lane") or "inactive").strip()
    rollout_mode = str(decision.get("rollout_mode") or "off").strip()

    lines = [
        f"Decision lane: {lane}",
        f"Rollout mode: {rollout_mode}",
    ]
    normalized_reasons = [reason for reason in rationale if isinstance(reason, dict) and str(reason.get("summary") or "").strip()]
    if normalized_reasons:
        lines.append("")
        lines.append("Rationale:")
        for reason in normalized_reasons:
            lines.append(f"- {str(reason.get('summary') or '').strip()}")
            lines.extend(github_check_run_evidence_lines(reason))
    return "\n".join(lines)


def maybe_post_commit_status(
    payload: dict[str, Any],
    *,
    github_status_token: str | None,
    github_status_context: str,
) -> None:
    if not github_status_token:
        return
    repo_full = str(payload.get("repo_full") or "").strip()
    head_sha = str(payload.get("head_sha") or "").strip()
    if not repo_full or not head_sha:
        raise RuntimeError("Governance response must include repo_full and head_sha to post a commit status.")
    post_commit_status(
        repo_full,
        head_sha,
        github_status_token,
        state=github_status_state(payload),
        description=summarize_payload(payload)[:140],
        context=github_status_context,
        target_url=(str(payload.get("review_url") or "").strip() or None),
    )


def maybe_post_check_run(
    payload: dict[str, Any],
    *,
    github_check_run_token: str | None,
    github_check_run_name: str,
) -> None:
    if not github_check_run_token:
        return
    repo_full = str(payload.get("repo_full") or "").strip()
    head_sha = str(payload.get("head_sha") or "").strip()
    if not repo_full or not head_sha:
        raise RuntimeError("Governance response must include repo_full and head_sha to post a check run.")
    post_check_run(
        repo_full,
        head_sha,
        github_check_run_token,
        name=github_check_run_name,
        conclusion=github_check_run_conclusion(payload),
        title=github_check_run_title(payload),
        summary=summarize_payload(payload),
        text=github_check_run_text(payload),
        details_url=(str(payload.get("review_url") or "").strip() or None),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        url = build_request_url(
            args.endpoint_url,
            pr_number=args.pr_number,
            head_sha=args.head_sha,
            rollout_mode=args.rollout_mode,
        )
        headers = build_headers(
            admin_token=args.admin_token,
            bearer_token=args.bearer_token,
            raw_headers=list(args.header),
        )
        payload = request_json(url, headers=headers)
        maybe_post_commit_status(
            payload,
            github_status_token=args.github_status_token,
            github_status_context=args.github_status_context,
        )
        maybe_post_check_run(
            payload,
            github_check_run_token=args.github_check_run_token,
            github_check_run_name=args.github_check_run_name,
        )
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(summarize_payload(payload))
        exit_code = payload.get("recommended_exit_code")
        if not isinstance(exit_code, int):
            raise RuntimeError("Governance response did not include an integer recommended_exit_code.")
        return exit_code
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())