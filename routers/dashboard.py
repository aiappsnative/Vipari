from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


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
	repo_dashboard_handler: Callable,
	artifact_storyline_handler: Callable,
	repo_journey_handler: Callable,
	repo_snapshot_detail_handler: Callable,
	repo_snapshot_compare_handler: Callable,
	export_history_handler: Callable | None = None,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])
	router.add_api_route("/api/repos/{repo_full:path}/dashboard", repo_dashboard_handler, methods=["GET"])
	router.add_api_route(
		"/api/repos/{repo_full:path}/artifacts/{artifact_path:path}/episodes",
		artifact_storyline_handler,
		methods=["GET"],
	)
	router.add_api_route("/api/repos/{repo_full:path}/journey", repo_journey_handler, methods=["GET"])
	router.add_api_route("/api/repos/{repo_full:path}/snapshots/{snapshot_id}", repo_snapshot_detail_handler, methods=["GET"])
	router.add_api_route("/api/repos/{repo_full:path}/compare", repo_snapshot_compare_handler, methods=["GET"])
	if export_history_handler is not None:
		router.add_api_route("/api/repos/{repo_full:path}/export/history", export_history_handler, methods=["GET"])
	return router