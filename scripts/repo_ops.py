from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.audit_jobs import init_db
from services.dashboard_views import build_repo_dashboard_view, list_repo_dashboard_index
from services.github_integration import generate_jwt, get_installation_token
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill


load_dotenv(PROJECT_ROOT / ".env")


def _resolve_db_path(value: str | None) -> str:
    if value:
        return value
    return os.getenv("AUDIT_DB_PATH", str(PROJECT_ROOT / "promptdrift.db"))


def _require_installation_token(installation_id: int) -> str:
    app_id = os.getenv("GITHUB_APP_ID")
    private_key_path = os.getenv("GITHUB_PRIVATE_KEY_PATH")
    if not app_id or not private_key_path:
        raise RuntimeError("GITHUB_APP_ID and GITHUB_PRIVATE_KEY_PATH are required for this command.")
    jwt_token = generate_jwt(app_id, private_key_path)
    return get_installation_token(jwt_token, installation_id)


def _write_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_list_repos(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    init_db(db_path)
    _write_json({"repos": [asdict(item) for item in list_repo_dashboard_index(db_path)]})
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    init_db(db_path)
    _write_json(asdict(build_repo_dashboard_view(db_path, args.repo_full)))
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    init_db(db_path)
    token = _require_installation_token(args.installation_id)

    onboarding_result = onboard_repository(
        db_path,
        repo_full=args.repo_full,
        installation_id=args.installation_id,
        token=token,
    )
    planned_jobs = []
    if args.plan_backfill:
        planned_jobs = plan_repository_history_backfill(
            db_path,
            repo_full=args.repo_full,
            token=token,
            commit_limit_per_artifact=args.commit_limit,
        )
    executed_jobs = []
    if args.execute_backfill:
        executed_jobs = execute_repository_history_backfill(
            db_path,
            repo_full=args.repo_full,
            token=token,
        )

    _write_json(
        {
            "repo_full": args.repo_full,
            "onboarding": asdict(onboarding_result.onboarding),
            "discovered_artifact_count": len(onboarding_result.artifacts),
            "baseline_version_count": len(onboarding_result.baseline_versions),
            "planned_backfill_job_count": len(planned_jobs),
            "executed_backfill_job_count": len(executed_jobs),
            "dashboard": asdict(build_repo_dashboard_view(db_path, args.repo_full)),
        }
    )
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    init_db(db_path)
    token = _require_installation_token(args.installation_id)
    execution_results = execute_repository_history_backfill(
        db_path,
        repo_full=args.repo_full,
        token=token,
    )
    _write_json(
        {
            "repo_full": args.repo_full,
            "executed_backfill_job_count": len(execution_results),
            "completed_backfill_job_count": sum(1 for result in execution_results if result.job.status == "completed"),
            "failed_backfill_job_count": sum(1 for result in execution_results if result.job.status == "failed"),
            "dashboard": asdict(build_repo_dashboard_view(db_path, args.repo_full)),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PromptDrift repo operator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-repos", help="List repos with onboarding data")
    list_parser.add_argument("--db", help="Path to the PromptDrift SQLite database")
    list_parser.set_defaults(func=cmd_list_repos)

    dashboard_parser = subparsers.add_parser("dashboard", help="Print the unified dashboard payload for a repo")
    dashboard_parser.add_argument("repo_full", help="Repository full name, for example owner/repo")
    dashboard_parser.add_argument("--db", help="Path to the PromptDrift SQLite database")
    dashboard_parser.set_defaults(func=cmd_dashboard)

    onboard_parser = subparsers.add_parser("onboard", help="Run onboarding and optional backfill planning/execution")
    onboard_parser.add_argument("repo_full", help="Repository full name, for example owner/repo")
    onboard_parser.add_argument("installation_id", type=int, help="GitHub App installation id")
    onboard_parser.add_argument("--db", help="Path to the PromptDrift SQLite database")
    onboard_parser.add_argument("--commit-limit", type=int, default=10, help="Max historical commits per artifact when planning backfill")
    onboard_parser.add_argument("--plan-backfill", action="store_true", help="Plan selective historical backfill jobs after onboarding")
    onboard_parser.add_argument("--execute-backfill", action="store_true", help="Execute planned historical backfill jobs after onboarding")
    onboard_parser.set_defaults(func=cmd_onboard)

    backfill_parser = subparsers.add_parser("backfill", help="Execute planned historical backfill jobs for a repo")
    backfill_parser.add_argument("repo_full", help="Repository full name, for example owner/repo")
    backfill_parser.add_argument("installation_id", type=int, help="GitHub App installation id")
    backfill_parser.add_argument("--db", help="Path to the PromptDrift SQLite database")
    backfill_parser.set_defaults(func=cmd_backfill)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
