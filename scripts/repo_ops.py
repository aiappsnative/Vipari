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
from services.audit_records import (
    list_audit_feedback_events_for_audit,
    list_audit_feedback_events_for_repo,
    refresh_audit_reaction_feedback_for_audit,
    refresh_audit_reaction_feedback_for_pr,
)
from services.dashboard_views import build_repo_dashboard_view, list_repo_dashboard_index
from services.github_integration import generate_jwt, get_installation_token
from services.onboarding import execute_repository_history_backfill, onboard_repository, plan_repository_history_backfill
from services.oss_eval_harness import (
    compare_eval_package_files,
    compare_oss_eval_package_files,
    list_eval_candidates,
    list_eval_scenarios,
    resolve_eval_reference_package_path,
    list_oss_eval_candidates,
    resolve_eval_target,
    resolve_oss_eval_target,
    run_evaluation,
    run_oss_evaluation,
)
from services.persistence import get_persistence_status, persistence_status_payload, resolve_db_path
from services.schema_migrations import list_applied_migrations, migrate_database


load_dotenv(PROJECT_ROOT / ".env")


def _resolve_db_path(value: str | None) -> str:
    return resolve_db_path(value)


def _require_installation_token(installation_id: int) -> str:
    app_id = os.getenv("GITHUB_APP_ID")
    private_key_path = os.getenv("GITHUB_PRIVATE_KEY_PATH")
    if not app_id or not private_key_path:
        raise RuntimeError("GITHUB_APP_ID and GITHUB_PRIVATE_KEY_PATH are required for this command.")
    jwt_token = generate_jwt(app_id, private_key_path)
    return get_installation_token(jwt_token, installation_id)


def _write_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _default_eval_output_root() -> str:
    return str(PROJECT_ROOT / "artifacts" / "eval-runs")


def _detect_git_branch() -> str:
    try:
        import subprocess

        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown"
    branch_name = result.stdout.strip()
    return branch_name or "unknown"


def cmd_list_repos(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    migrate_database(db_path)
    _write_json({"repos": [asdict(item) for item in list_repo_dashboard_index(db_path)]})
    return 0


def cmd_persistence_status(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    migrate_database(db_path)
    payload = persistence_status_payload(get_persistence_status(db_path))
    payload["applied_migrations"] = [asdict(item) for item in list_applied_migrations(db_path)]
    _write_json(payload)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    migrate_database(db_path)
    _write_json(asdict(build_repo_dashboard_view(db_path, args.repo_full)))
    return 0


def cmd_feedback_events(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    migrate_database(db_path)
    if args.audit_id is not None:
        events = list_audit_feedback_events_for_audit(db_path, int(args.audit_id))
    else:
        events = list_audit_feedback_events_for_repo(db_path, args.repo_full, limit=args.limit)
    _write_json({"feedback_events": [asdict(event) for event in events]})
    return 0


def cmd_refresh_feedback_reactions(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    migrate_database(db_path)

    if args.audit_id is None and args.pr_number is None:
        raise RuntimeError("Either --audit-id or --pr-number is required.")
    if args.audit_id is not None and args.pr_number is not None:
        raise RuntimeError("Pass either --audit-id or --pr-number, not both.")

    token = _require_installation_token(args.installation_id)
    if args.audit_id is not None:
        events = refresh_audit_reaction_feedback_for_audit(
            db_path,
            audit_id=int(args.audit_id),
            token=token,
        )
    else:
        events = refresh_audit_reaction_feedback_for_pr(
            db_path,
            repo_full=args.repo_full,
            pr_number=int(args.pr_number),
            head_sha=args.head_sha,
            token=token,
        )

    _write_json(
        {
            "repo_full": args.repo_full,
            "audit_id": args.audit_id,
            "pr_number": args.pr_number,
            "head_sha": args.head_sha,
            "recorded_count": len(events),
            "feedback_events": [asdict(event) for event in events],
        }
    )
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    migrate_database(db_path)
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
    migrate_database(db_path)
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


def cmd_list_eval_candidates(_: argparse.Namespace) -> int:
    _write_json({"candidates": [asdict(candidate) for candidate in list_eval_candidates()]})
    return 0


def cmd_list_eval_scenarios(_: argparse.Namespace) -> int:
    _write_json({"scenarios": [asdict(scenario) for scenario in list_eval_scenarios()]})
    return 0


def cmd_eval_run(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    migrate_database(db_path)
    target = resolve_eval_target(args.target)
    token = _require_installation_token(args.installation_id)
    output_root = args.output_dir or _default_eval_output_root()
    branch_name = args.branch or _detect_git_branch()
    mode = args.mode or target.recommended_mode
    commit_limit = args.commit_limit if args.commit_limit is not None else target.commit_limit_per_artifact
    expected_control_surfaces = list(target.expected_control_surfaces)
    if args.expect_control_surface:
        expected_control_surfaces.extend(args.expect_control_surface)
    compare_to_package_path = args.compare_to
    if compare_to_package_path is None and args.compare_to_scenario:
        compare_to_package_path = resolve_eval_reference_package_path(args.compare_to_scenario)

    result = run_oss_evaluation(
        db_path,
        repo_full=target.repo_full,
        installation_id=args.installation_id,
        token=token,
        mode=mode,
        commit_limit_per_artifact=commit_limit,
        output_root=output_root,
        branch_name=branch_name,
        candidate_key=target.key,
        expected_control_surfaces=expected_control_surfaces,
        manual_notes=args.notes or target.notes,
        run_label=args.run_label,
        compare_to_package_path=compare_to_package_path,
        scenario_key=args.scenario,
    )
    payload = {
        "repo_full": target.repo_full,
        "candidate_key": target.key,
        "package_path": result.package_path,
        "repo_dashboard_path": result.repo_dashboard_path,
        "overview_dashboard_path": result.overview_dashboard_path,
        "comparison_path": result.comparison_path,
        "run": result.package,
    }
    _write_json(payload)
    return 0


def cmd_eval_compare(args: argparse.Namespace) -> int:
    summary = compare_oss_eval_package_files(args.current_package, args.baseline_package)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    _write_json(summary)
    return 0


def cmd_list_oss_eval_candidates(args: argparse.Namespace) -> int:
    return cmd_list_eval_candidates(args)


def cmd_oss_eval_run(args: argparse.Namespace) -> int:
    return cmd_eval_run(args)


def cmd_oss_eval_compare(args: argparse.Namespace) -> int:
    return cmd_eval_compare(args)


def cmd_migrate_db(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db)
    _write_json(asdict(migrate_database(db_path)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DriftGuard repo operator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-repos", help="List repos with onboarding data")
    list_parser.add_argument("--db", help="Path to the DriftGuard SQLite database")
    list_parser.set_defaults(func=cmd_list_repos)

    persistence_parser = subparsers.add_parser("persistence-status", help="Print the current persistence backend and logical table layout")
    persistence_parser.add_argument("--db", help="Path to the DriftGuard SQLite database")
    persistence_parser.set_defaults(func=cmd_persistence_status)

    migrate_parser = subparsers.add_parser("migrate-db", help="Apply schema migrations/bootstrap for the configured database")
    migrate_parser.add_argument("--db", help="Path or DATABASE_URL override for the target database")
    migrate_parser.set_defaults(func=cmd_migrate_db)
    dashboard_parser = subparsers.add_parser("dashboard", help="Print the unified dashboard payload for a repo")
    dashboard_parser.add_argument("repo_full", help="Repository full name, for example owner/repo")
    dashboard_parser.add_argument("--db", help="Path to the DriftGuard SQLite database")
    dashboard_parser.set_defaults(func=cmd_dashboard)

    feedback_parser = subparsers.add_parser("feedback-events", help="List persisted feedback events for a repo or audit id")
    feedback_parser.add_argument("repo_full", help="Repository full name, for example owner/repo")
    feedback_parser.add_argument("--db", help="Path to the DriftGuard SQLite database")
    feedback_parser.add_argument("--audit-id", type=int, help="Optional audit id filter")
    feedback_parser.add_argument("--limit", type=int, default=100, help="Max events to return when listing by repo")
    feedback_parser.set_defaults(func=cmd_feedback_events)

    refresh_feedback_parser = subparsers.add_parser(
        "refresh-feedback-reactions",
        help="Force-refresh GitHub reactions for a persisted audit or PR",
    )
    refresh_feedback_parser.add_argument("repo_full", help="Repository full name, for example owner/repo")
    refresh_feedback_parser.add_argument("installation_id", type=int, help="GitHub App installation id")
    refresh_feedback_parser.add_argument("--db", help="Path to the DriftGuard SQLite database")
    refresh_feedback_parser.add_argument("--audit-id", type=int, help="Refresh reactions for a specific audit id")
    refresh_feedback_parser.add_argument("--pr-number", type=int, help="Refresh reactions for a specific PR number")
    refresh_feedback_parser.add_argument("--head-sha", help="Optional PR head SHA filter when refreshing by PR")
    refresh_feedback_parser.set_defaults(func=cmd_refresh_feedback_reactions)

    onboard_parser = subparsers.add_parser("onboard", help="Run onboarding and optional backfill planning/execution")
    onboard_parser.add_argument("repo_full", help="Repository full name, for example owner/repo")
    onboard_parser.add_argument("installation_id", type=int, help="GitHub App installation id")
    onboard_parser.add_argument("--db", help="Path to the DriftGuard SQLite database")
    onboard_parser.add_argument("--commit-limit", type=int, default=10, help="Max historical commits per artifact when planning backfill")
    onboard_parser.add_argument("--plan-backfill", action="store_true", help="Plan selective historical backfill jobs after onboarding")
    onboard_parser.add_argument("--execute-backfill", action="store_true", help="Execute planned historical backfill jobs after onboarding")
    onboard_parser.set_defaults(func=cmd_onboard)

    backfill_parser = subparsers.add_parser("backfill", help="Execute planned historical backfill jobs for a repo")
    backfill_parser.add_argument("repo_full", help="Repository full name, for example owner/repo")
    backfill_parser.add_argument("installation_id", type=int, help="GitHub App installation id")
    backfill_parser.add_argument("--db", help="Path to the DriftGuard SQLite database")
    backfill_parser.set_defaults(func=cmd_backfill)

    eval_candidates_parser = subparsers.add_parser("list-eval-candidates", help="List curated evaluation candidates")
    eval_candidates_parser.set_defaults(func=cmd_list_eval_candidates)

    eval_scenarios_parser = subparsers.add_parser("list-eval-scenarios", help="List curated seeded evaluation scenarios")
    eval_scenarios_parser.set_defaults(func=cmd_list_eval_scenarios)

    eval_run_parser = subparsers.add_parser("eval-run", help="Run the repeatable evaluation harness for a curated candidate or owner/repo")
    eval_run_parser.add_argument("target", help="Candidate key or owner/repo name to evaluate")
    eval_run_parser.add_argument("installation_id", type=int, help="GitHub App installation id")
    eval_run_parser.add_argument("--db", help="Path to the DriftGuard SQLite database")
    eval_run_parser.add_argument("--output-dir", help="Directory where evaluation artifacts should be written")
    eval_run_parser.add_argument("--commit-limit", type=int, help="Max historical commits per artifact when planning backfill")
    eval_run_parser.add_argument(
        "--mode",
        choices=["baseline_only", "baseline_plus_backfill"],
        help="Whether to only baseline the repo or also plan and execute backfill",
    )
    eval_run_parser.add_argument("--branch", help="Branch label to store in the saved evaluation package")
    eval_run_parser.add_argument("--run-label", help="Stable label for the saved evaluation package")
    eval_run_parser.add_argument("--notes", help="Manual reviewer notes to store alongside the run package")
    eval_run_parser.add_argument("--scenario", help="Optional seeded scenario key that adds explicit assertions to the run package")
    eval_run_parser.add_argument("--compare-to-scenario", help="Optional seeded scenario key whose checked-in reference package should be used for comparison")
    eval_run_parser.add_argument(
        "--expect-control-surface",
        action="append",
        help="Expected AI control surface to record in the evaluator rubric (may be passed multiple times)",
    )
    eval_run_parser.add_argument("--compare-to", help="Path to a previously saved run-package.json to compare against")
    eval_run_parser.set_defaults(func=cmd_eval_run)

    eval_compare_parser = subparsers.add_parser("eval-compare", help="Compare two saved evaluation packages")
    eval_compare_parser.add_argument("current_package", help="Path to the newer run-package.json")
    eval_compare_parser.add_argument("baseline_package", help="Path to the baseline run-package.json")
    eval_compare_parser.add_argument("--output", help="Optional path for saving the comparison summary JSON")
    eval_compare_parser.set_defaults(func=cmd_eval_compare)

    oss_eval_candidates_parser = subparsers.add_parser("list-oss-eval-candidates", help="Compatibility alias for listing curated OSS evaluation candidates")
    oss_eval_candidates_parser.set_defaults(func=cmd_list_oss_eval_candidates)

    oss_eval_run_parser = subparsers.add_parser("oss-eval-run", help="Compatibility alias for running the OSS-focused evaluation harness")
    for action in eval_run_parser._actions[1:]:
        oss_eval_run_parser._add_action(action)
    oss_eval_run_parser.set_defaults(func=cmd_oss_eval_run)

    oss_eval_compare_parser = subparsers.add_parser("oss-eval-compare", help="Compatibility alias for comparing saved OSS evaluation packages")
    for action in eval_compare_parser._actions[1:]:
        oss_eval_compare_parser._add_action(action)
    oss_eval_compare_parser.set_defaults(func=cmd_oss_eval_compare)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
