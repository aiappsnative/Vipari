from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from services.api_models import BaselineDecisionRequest, RepoRebaselineRequest, RepositoryBackfillRequest, RepositoryOnboardingRequest


def create_dashboard_read_router(
	*,
	list_repos_handler: Callable,
	dashboard_overview_handler: Callable,
	dashboard_escalation_queue_handler: Callable,
	persistence_status_handler: Callable | None = None,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])
	router.add_api_route("/api/repos", list_repos_handler, methods=["GET"])
	router.add_api_route("/api/dashboard/overview", dashboard_overview_handler, methods=["GET"])
	router.add_api_route("/api/dashboard/escalation-queue", dashboard_escalation_queue_handler, methods=["GET"])
	if persistence_status_handler is not None:
		router.add_api_route("/api/persistence", persistence_status_handler, methods=["GET"])
	return router


def create_compliance_api_router(
	*,
	current_workspace_context_fn: Callable,
	build_compliance_workspace_api_context_fn: Callable,
	filter_compliance_evidence_view_fn: Callable,
	export_job_payload_fn: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])

	def compliance_readiness_api(request: Request):
		access_context = current_workspace_context_fn(request)
		view, _export_jobs = build_compliance_workspace_api_context_fn(access_context)
		workspace = access_context.get("workspace") if access_context else None
		payload = {
			"workspace_id": workspace.id if workspace is not None else None,
			"workspace_name": workspace.display_name if workspace is not None else None,
			**asdict(view),
		}
		return JSONResponse(payload)

	def compliance_frameworks_api(request: Request):
		access_context = current_workspace_context_fn(request)
		view, _export_jobs = build_compliance_workspace_api_context_fn(access_context)
		return JSONResponse(
			{
				"metrics": [asdict(metric) for metric in view.metrics],
				"verdict": asdict(view.verdict),
				"framework_cards": [asdict(card) for card in view.framework_cards],
			}
		)

	def compliance_exports_api(request: Request):
		access_context = current_workspace_context_fn(request)
		view, export_jobs = build_compliance_workspace_api_context_fn(access_context)
		return JSONResponse(
			{
				"summary": asdict(view.export_summary),
				"jobs": [export_job_payload_fn(job) for job in export_jobs],
			}
		)

	def compliance_evidence_api(request: Request):
		access_context = current_workspace_context_fn(request)
		view, _export_jobs = build_compliance_workspace_api_context_fn(access_context)
		active_gap, active_repo, evidence_rows, repo_rows = filter_compliance_evidence_view_fn(
			view,
			request.query_params.get("gap"),
			request.query_params.get("repo"),
		)
		return JSONResponse(
			{
				"active_gap": active_gap,
				"active_repo": active_repo,
				"top_gaps": [asdict(item) for item in view.top_gaps],
				"evidence_rows": [asdict(item) for item in evidence_rows],
				"repo_rows": [asdict(item) for item in repo_rows],
			}
		)

	router.add_api_route("/api/compliance/readiness", compliance_readiness_api, methods=["GET"])
	router.add_api_route("/api/compliance/frameworks", compliance_frameworks_api, methods=["GET"])
	router.add_api_route("/api/compliance/exports", compliance_exports_api, methods=["GET"])
	router.add_api_route("/api/compliance/evidence", compliance_evidence_api, methods=["GET"])
	return router


def create_repo_read_router(
	*,
	pending_proposals_handler: Callable | None = None,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])
	if pending_proposals_handler is not None:
		router.add_api_route("/api/repos/{repo_full:path}/proposals/pending", pending_proposals_handler, methods=["GET"])
	return router


def create_repo_dashboard_router(
	*,
	authorize_repo_read_fn: Callable,
	resolve_db_path_fn: Callable[[], str],
	build_repo_dashboard_view_with_timings_fn: Callable,
	list_export_jobs_for_requester_fn: Callable,
	export_job_payload_fn: Callable,
	record_server_timing_metric_fn: Callable,
	attach_server_timing_fn: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])

	def repo_dashboard(request: Request, repo_full: str):
		request_started = time.perf_counter()
		timing_metrics: list[tuple[str, float]] = []
		access_started = time.perf_counter()
		access_context = authorize_repo_read_fn(request, repo_full)
		record_server_timing_metric_fn(timing_metrics, "access", access_started)
		build_started = time.perf_counter()
		repo_view, repo_stage_timings = build_repo_dashboard_view_with_timings_fn(resolve_db_path_fn(), repo_full)
		record_server_timing_metric_fn(timing_metrics, "build", build_started)
		timing_metrics.extend(repo_stage_timings)
		json_started = time.perf_counter()
		payload = asdict(repo_view)
		workspace = access_context.get("workspace")
		session = access_context.get("session")
		if workspace is not None and session is not None:
			payload["export_jobs"] = [
				export_job_payload_fn(job)
				for job in list_export_jobs_for_requester_fn(resolve_db_path_fn(), repo_full, workspace.id, session.user_id)
			]
		else:
			payload["export_jobs"] = []
		response = JSONResponse(payload)
		record_server_timing_metric_fn(timing_metrics, "json", json_started)
		timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
		return attach_server_timing_fn(response, timing_metrics)

	def export_history(request: Request, repo_full: str):
		access_context = authorize_repo_read_fn(request, repo_full)
		workspace = access_context.get("workspace")
		session = access_context.get("session")
		if workspace is None or session is None:
			return JSONResponse({"repo_full": repo_full, "jobs": []})
		jobs = list_export_jobs_for_requester_fn(resolve_db_path_fn(), repo_full, workspace.id, session.user_id)
		return JSONResponse({"repo_full": repo_full, "jobs": [export_job_payload_fn(job) for job in jobs]})

	router.add_api_route("/api/repos/{repo_full:path}/dashboard", repo_dashboard, methods=["GET"])
	router.add_api_route("/api/repos/{repo_full:path}/export/history", export_history, methods=["GET"])
	return router


def create_repo_onboarding_router(
	*,
	authorize_repo_mutation_fn: Callable,
	resolve_installation_id_fn: Callable,
	resolve_db_path_fn: Callable[[], str],
	github_app_id: str,
	github_private_key_path: str,
	generate_jwt_fn: Callable,
	get_installation_token_fn: Callable,
	onboard_repository_fn: Callable,
	plan_repository_history_backfill_fn: Callable,
	execute_repository_history_backfill_fn: Callable,
	build_repo_dashboard_view_fn: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])

	def run_repo_onboarding(request: Request, repo_full: str, payload: RepositoryOnboardingRequest):
		auth_context = authorize_repo_mutation_fn(request, repo_full)
		installation_id = resolve_installation_id_fn(auth_context, payload.installation_id)
		jwt_token = generate_jwt_fn(github_app_id, github_private_key_path)
		token = get_installation_token_fn(jwt_token, installation_id)
		db_path = resolve_db_path_fn()

		onboarding_result = onboard_repository_fn(
			db_path,
			repo_full=repo_full,
			installation_id=installation_id,
			token=token,
		)
		planned_jobs = []
		if payload.plan_backfill:
			planned_jobs = plan_repository_history_backfill_fn(
				db_path,
				repo_full=repo_full,
				token=token,
				commit_limit_per_artifact=payload.commit_limit_per_artifact,
			)
		executed_jobs = []
		if payload.execute_backfill:
			executed_jobs = execute_repository_history_backfill_fn(
				db_path,
				repo_full=repo_full,
				token=token,
			)

		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse(
			{
				"repo_full": repo_full,
				"onboarding_id": onboarding_result.onboarding.id,
				"discovered_artifact_count": len(onboarding_result.artifacts),
				"baseline_version_count": len(onboarding_result.baseline_versions),
				"planned_backfill_job_count": len(planned_jobs),
				"executed_backfill_job_count": len(executed_jobs),
				"dashboard": asdict(dashboard),
			}
		)

	def run_repo_backfill(request: Request, repo_full: str, payload: RepositoryBackfillRequest):
		auth_context = authorize_repo_mutation_fn(request, repo_full)
		installation_id = resolve_installation_id_fn(auth_context, payload.installation_id)
		jwt_token = generate_jwt_fn(github_app_id, github_private_key_path)
		token = get_installation_token_fn(jwt_token, installation_id)
		db_path = resolve_db_path_fn()
		executed_jobs = execute_repository_history_backfill_fn(
			db_path,
			repo_full=repo_full,
			token=token,
		)
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse(
			{
				"repo_full": repo_full,
				"executed_backfill_job_count": len(executed_jobs),
				"completed_backfill_job_count": sum(1 for result in executed_jobs if result.job.status == "completed"),
				"failed_backfill_job_count": sum(1 for result in executed_jobs if result.job.status == "failed"),
				"dashboard": asdict(dashboard),
			}
		)

	router.add_api_route("/api/repos/{repo_full:path}/onboard", run_repo_onboarding, methods=["POST"])
	router.add_api_route("/api/repos/{repo_full:path}/backfill", run_repo_backfill, methods=["POST"])
	return router


def create_export_job_router(
	*,
	resolve_db_path_fn: Callable[[], str],
	get_export_job_fn: Callable,
	authorize_export_job_access_fn: Callable,
	export_job_payload_fn: Callable,
	build_export_download_response_fn: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])

	def get_export_status(request: Request, job_id: int):
		try:
			job = get_export_job_fn(resolve_db_path_fn(), job_id)
			if not job:
				raise HTTPException(status_code=404, detail="Export job not found")
			authorize_export_job_access_fn(request, job)
		except ValueError as exc:
			raise HTTPException(status_code=400, detail=str(exc)) from exc
		return JSONResponse(export_job_payload_fn(job))

	def download_export(request: Request, job_id: int, token: str | None = None):
		try:
			job = get_export_job_fn(resolve_db_path_fn(), job_id)
			if not job:
				raise HTTPException(status_code=404, detail="Export job not found")
			authorize_export_job_access_fn(request, job)
			return build_export_download_response_fn(resolve_db_path_fn(), job, token)
		except ValueError as exc:
			raise HTTPException(status_code=400, detail=str(exc)) from exc

	router.add_api_route("/api/export/{job_id}/status", get_export_status, methods=["GET"])
	router.add_api_route("/api/export/{job_id}/download", download_export, methods=["GET"])
	return router


def create_export_create_router(
	*,
	create_export_handler: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])
	router.add_api_route("/api/repos/{repo_full:path}/export/compliance", create_export_handler, methods=["POST"])
	return router


def create_dashboard_page_router(
	*,
	dashboard_index_handler: Callable,
	dashboard_repo_handler: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])
	router.add_api_route("/dashboard", dashboard_index_handler, methods=["GET"], response_class=HTMLResponse)
	router.add_api_route("/dashboard/{repo_full:path}", dashboard_repo_handler, methods=["GET"], response_class=HTMLResponse)
	return router


def create_repo_baseline_router(
	*,
	authorize_repo_read_fn: Callable,
	authorize_repo_mutation_fn: Callable,
	resolve_db_path_fn: Callable[[], str],
	build_repo_dashboard_view_fn: Callable,
	build_repo_journey_fn: Callable,
	promote_latest_source_to_onboarding_baseline_fn: Callable,
	build_repo_baseline_review_panel_fn: Callable,
	approve_repo_baseline_artifact_fn: Callable,
	reject_repo_baseline_artifact_fn: Callable,
	approve_repo_baseline_fn: Callable,
	reject_repo_baseline_fn: Callable,
	rebaseline_repo_from_snapshot_fn: Callable,
	resolve_actor_login_fn: Callable,
	github_app_id: str,
	github_private_key_path: str,
	generate_jwt_fn: Callable,
	get_installation_token_fn: Callable,
	fetch_file_content_fn: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])

	def promote_artifact_baseline(request: Request, repo_full: str, artifact_path: str):
		authorize_repo_mutation_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		baseline = promote_latest_source_to_onboarding_baseline_fn(db_path, repo_full, artifact_path)
		if baseline is None:
			raise HTTPException(status_code=404, detail="No stored source version is available to promote as baseline.")
		build_repo_journey_fn(db_path, repo_full)
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse(
			{
				"repo_full": repo_full,
				"artifact_path": artifact_path,
				"baseline": asdict(baseline),
				"dashboard": asdict(dashboard),
			}
		)

	def pending_repo_baselines(request: Request, repo_full: str):
		authorize_repo_read_fn(request, repo_full)
		panel = build_repo_baseline_review_panel_fn(resolve_db_path_fn(), repo_full)
		if panel is None:
			raise HTTPException(status_code=404, detail="Repository onboarding was not found.")
		return JSONResponse(asdict(panel))

	def approve_artifact_baseline(request: Request, repo_full: str, artifact_path: str, payload: BaselineDecisionRequest):
		authorize_repo_mutation_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			baseline = approve_repo_baseline_artifact_fn(
				db_path,
				repo_full=repo_full,
				artifact_path=artifact_path,
				actor_login=resolve_actor_login_fn(request, payload),
				approval_note=payload.note,
			)
		except ValueError as exc:
			raise HTTPException(status_code=404, detail=str(exc)) from exc
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse({"repo_full": repo_full, "artifact_path": artifact_path, "baseline": asdict(baseline), "dashboard": asdict(dashboard)})

	def reject_artifact_baseline(request: Request, repo_full: str, artifact_path: str, payload: BaselineDecisionRequest):
		authorize_repo_mutation_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			baseline = reject_repo_baseline_artifact_fn(
				db_path,
				repo_full=repo_full,
				artifact_path=artifact_path,
				actor_login=resolve_actor_login_fn(request, payload),
				approval_note=payload.note,
			)
		except ValueError as exc:
			raise HTTPException(status_code=404, detail=str(exc)) from exc
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse({"repo_full": repo_full, "artifact_path": artifact_path, "baseline": asdict(baseline), "dashboard": asdict(dashboard)})

	def approve_repo_baseline_candidate(request: Request, repo_full: str, payload: BaselineDecisionRequest):
		authorize_repo_mutation_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			baselines = approve_repo_baseline_fn(
				db_path,
				repo_full=repo_full,
				actor_login=resolve_actor_login_fn(request, payload),
				approval_note=payload.note,
			)
		except ValueError as exc:
			raise HTTPException(status_code=404, detail=str(exc)) from exc
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse({"repo_full": repo_full, "approved_baseline_count": len(baselines), "dashboard": asdict(dashboard)})

	def reject_repo_baseline_candidate(request: Request, repo_full: str, payload: BaselineDecisionRequest):
		authorize_repo_mutation_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			baselines = reject_repo_baseline_fn(
				db_path,
				repo_full=repo_full,
				actor_login=resolve_actor_login_fn(request, payload),
				approval_note=payload.note,
			)
		except ValueError as exc:
			raise HTTPException(status_code=404, detail=str(exc)) from exc
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse({"repo_full": repo_full, "rejected_baseline_count": len(baselines), "dashboard": asdict(dashboard)})

	def rebaseline_repo(request: Request, repo_full: str, payload: RepoRebaselineRequest):
		authorize_repo_mutation_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			baselines = rebaseline_repo_from_snapshot_fn(
				db_path,
				repo_full=repo_full,
				snapshot_id=payload.snapshot_id,
				rationale=payload.rationale,
				actor_login=resolve_actor_login_fn(request, payload),
				github_app_id=github_app_id,
				github_private_key_path=github_private_key_path,
				generate_jwt_fn=generate_jwt_fn,
				get_installation_token_fn=get_installation_token_fn,
				fetch_file_content_fn=fetch_file_content_fn,
			)
		except ValueError as exc:
			raise HTTPException(status_code=400, detail=str(exc)) from exc
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse({"repo_full": repo_full, "snapshot_id": payload.snapshot_id, "created_baseline_count": len(baselines), "dashboard": asdict(dashboard)})

	router.add_api_route(
		"/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/baseline",
		promote_artifact_baseline,
		methods=["POST"],
	)
	router.add_api_route("/api/repos/{repo_full:path}/baseline/pending", pending_repo_baselines, methods=["GET"])
	router.add_api_route(
		"/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/baseline/approve",
		approve_artifact_baseline,
		methods=["POST"],
	)
	router.add_api_route(
		"/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/baseline/reject",
		reject_artifact_baseline,
		methods=["POST"],
	)
	router.add_api_route("/api/repos/{repo_full:path}/baseline/approve", approve_repo_baseline_candidate, methods=["POST"])
	router.add_api_route("/api/repos/{repo_full:path}/baseline/reject", reject_repo_baseline_candidate, methods=["POST"])
	router.add_api_route("/api/repos/{repo_full:path}/baseline/rebaseline", rebaseline_repo, methods=["POST"])
	return router


def create_repo_history_router(
	*,
	authorize_repo_read_fn: Callable,
	resolve_db_path_fn: Callable[[], str],
	build_artifact_storyline_payload_fn: Callable,
	build_repo_journey_payload_fn: Callable,
	build_repo_snapshot_detail_payload_fn: Callable,
	build_repo_snapshot_compare_payload_fn: Callable,
	build_repo_artifact_storyline_fn: Callable,
	build_repo_journey_fn: Callable,
	get_repo_snapshot_detail_fn: Callable,
	snapshot_to_public_payload_fn: Callable,
	compare_repo_snapshots_fn: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])

	def artifact_storyline(request: Request, repo_full: str, artifact_path: str):
		authorize_repo_read_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			payload = build_artifact_storyline_payload_fn(
				db_path,
				repo_full,
				artifact_path,
				build_repo_artifact_storyline_fn=build_repo_artifact_storyline_fn,
			)
		except ValueError as exc:
			raise HTTPException(status_code=404, detail=str(exc)) from exc
		return JSONResponse(payload)

	def repo_journey(request: Request, repo_full: str):
		authorize_repo_read_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		return JSONResponse(
			build_repo_journey_payload_fn(
				db_path,
				repo_full,
				build_repo_journey_fn=build_repo_journey_fn,
				snapshot_to_public_payload_fn=snapshot_to_public_payload_fn,
			)
		)

	def repo_snapshot_detail(request: Request, repo_full: str, snapshot_id: int):
		authorize_repo_read_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			payload = build_repo_snapshot_detail_payload_fn(
				db_path,
				repo_full,
				snapshot_id,
				get_repo_snapshot_detail_fn=get_repo_snapshot_detail_fn,
				snapshot_to_public_payload_fn=snapshot_to_public_payload_fn,
			)
		except ValueError as exc:
			raise HTTPException(status_code=404, detail=str(exc)) from exc
		return JSONResponse(payload)

	def repo_snapshot_compare(request: Request, repo_full: str, left: int, right: int):
		authorize_repo_read_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			payload = build_repo_snapshot_compare_payload_fn(
				db_path,
				repo_full,
				left,
				right,
				compare_repo_snapshots_fn=compare_repo_snapshots_fn,
			)
		except ValueError as exc:
			raise HTTPException(status_code=404, detail=str(exc)) from exc
		return JSONResponse(payload)

	router.add_api_route("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/episodes", artifact_storyline, methods=["GET"])
	router.add_api_route("/api/repos/{repo_full:path}/journey", repo_journey, methods=["GET"])
	router.add_api_route("/api/repos/{repo_full:path}/snapshots/{snapshot_id}", repo_snapshot_detail, methods=["GET"])
	router.add_api_route("/api/repos/{repo_full:path}/compare", repo_snapshot_compare, methods=["GET"])
	return router