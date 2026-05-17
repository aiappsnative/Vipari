from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import threading
import time
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from services.api_models import BaselineDecisionRequest, RepoArtifactAddRequest, RepoArtifactUpdateRequest, RepoRebaselineRequest, RepositoryBackfillRequest, RepositoryOnboardingRequest
from services.baseline_approval_service import RebaselineExternalError, RebaselineInternalError


_ARTIFACT_OPTIONS_CACHE_TTL_SECONDS = 30.0
_ARTIFACT_OPTIONS_CACHE_MAX_ENTRIES = 128
_REPO_DASHBOARD_TABS = {"audit", "pr-reviews", "drift", "version-control", "baseline", "compliance", "reports"}


def _cache_get(cache: dict[tuple[object, ...], tuple[float, object]], cache_key: tuple[object, ...], *, lock: threading.RLock):
	now = time.monotonic()
	with lock:
		cached = cache.get(cache_key)
		if cached is None:
			return None
		expires_at, value = cached
		if expires_at <= now:
			cache.pop(cache_key, None)
			return None
		cache.pop(cache_key, None)
		cache[cache_key] = cached
		return value


def _cache_set(
	cache: dict[tuple[object, ...], tuple[float, object]],
	cache_key: tuple[object, ...],
	value: object,
	*,
	ttl_seconds: float,
	max_entries: int,
	lock: threading.RLock,
):
	with lock:
		cache[cache_key] = (time.monotonic() + ttl_seconds, value)
		while len(cache) > max_entries:
			oldest_key = next(iter(cache))
			cache.pop(oldest_key, None)
	return value


def _invalidate_cache_entries(cache: dict[tuple[object, ...], tuple[float, object]], *, predicate: Callable[[tuple[object, ...]], bool], lock: threading.RLock) -> None:
	with lock:
		for cache_key in [existing_key for existing_key in cache if predicate(existing_key)]:
			cache.pop(cache_key, None)


def _resolve_repo_dashboard_tab(request: Request) -> str | None:
	raw_tab = (request.query_params.get("tab") or "").strip().lower()
	return raw_tab if raw_tab in _REPO_DASHBOARD_TABS else None


def _repo_dashboard_build_options(active_tab: str | None) -> dict[str, object]:
	if active_tab is None:
		return {}
	options: dict[str, object] = {
		"include_journey": active_tab == "version-control",
		"include_featured_storyline": active_tab == "baseline",
		"include_history_timelines": False,
		"include_history_cues": active_tab == "compliance",
		"include_design_profiles": False,
		"attribute_profile_mode": "ranked",
	}
	if active_tab == "drift":
		options["attribute_profile_mode"] = "all"
	return options


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
	pre_audit_relevance_handler: Callable | None = None,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])
	if pending_proposals_handler is not None:
		router.add_api_route("/api/repos/{repo_full:path}/proposals/pending", pending_proposals_handler, methods=["GET"])
	if pre_audit_relevance_handler is not None:
		router.add_api_route("/api/repos/{repo_full:path}/relevance-decisions", pre_audit_relevance_handler, methods=["GET"])
	return router


def create_repo_dashboard_router(
	*,
	authorize_repo_read_fn: Callable,
	resolve_db_path_fn: Callable[[], str],
	build_repo_dashboard_view_with_timings_fn: Callable,
	build_pre_audit_relevance_payload_fn: Callable | None,
	build_pr_review_routes_payload_fn: Callable | None,
	list_pre_audit_relevance_decisions_fn: Callable | None,
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
		active_tab = _resolve_repo_dashboard_tab(request)
		build_options = _repo_dashboard_build_options(active_tab)
		build_started = time.perf_counter()
		repo_view, repo_stage_timings = build_repo_dashboard_view_with_timings_fn(resolve_db_path_fn(), repo_full, **build_options)
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
		raw_pr_number = (request.query_params.get("pr") or request.query_params.get("pr_number") or "").strip()
		raw_head_sha = (request.query_params.get("head_sha") or "").strip()
		if (active_tab is None or active_tab == "compliance") and build_pre_audit_relevance_payload_fn is not None and list_pre_audit_relevance_decisions_fn is not None and raw_pr_number.isdigit() and raw_head_sha:
			payload["pre_audit_relevance"] = build_pre_audit_relevance_payload_fn(
				resolve_db_path_fn(),
				repo_full,
				pr_number=int(raw_pr_number),
				head_sha=raw_head_sha,
				list_pre_audit_relevance_decisions_fn=list_pre_audit_relevance_decisions_fn,
			)
		else:
			payload["pre_audit_relevance"] = None
		payload["pr_review_routes"] = build_pr_review_routes_payload_fn(
			resolve_db_path_fn(),
			repo_full,
			pr_number=int(raw_pr_number) if raw_pr_number.isdigit() else None,
			head_sha=raw_head_sha or None,
		) if (active_tab is None or active_tab == "pr-reviews") and build_pr_review_routes_payload_fn is not None else None
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
	authorize_repo_read_fn: Callable,
	authorize_repo_mutation_fn: Callable,
	resolve_installation_id_fn: Callable,
	resolve_db_path_fn: Callable[[], str],
	github_app_id: str,
	github_private_key_path: str,
	generate_jwt_fn: Callable,
	get_installation_token_fn: Callable,
	list_repository_files_fn: Callable,
	onboard_repository_fn: Callable,
	add_repo_artifact_to_onboarding_fn: Callable,
	remove_repo_artifact_from_onboarding_fn: Callable,
	update_repo_artifact_type_fn: Callable,
	infer_artifact_type_from_path_fn: Callable,
	tracked_artifact_type_options_fn: Callable,
	plan_repository_history_backfill_fn: Callable,
	execute_repository_history_backfill_fn: Callable,
	build_repo_dashboard_view_fn: Callable,
	record_server_timing_metric_fn: Callable,
	attach_server_timing_fn: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])
	artifact_options_cache: dict[tuple[object, ...], tuple[float, object]] = {}
	artifact_options_cache_lock = threading.RLock()

	def invalidate_artifact_options_cache(repo_full: str) -> None:
		_invalidate_cache_entries(
			artifact_options_cache,
			predicate=lambda cache_key: len(cache_key) > 0 and cache_key[0] == repo_full,
			lock=artifact_options_cache_lock,
		)

	def list_repo_artifact_options(request: Request, repo_full: str):
		from services.onboarding_records import get_latest_repository_onboarding, list_onboarded_artifacts_for_onboarding

		request_started = time.perf_counter()
		timing_metrics: list[tuple[str, float]] = []
		access_started = time.perf_counter()
		auth_context = authorize_repo_read_fn(request, repo_full)
		record_server_timing_metric_fn(timing_metrics, "access", access_started)
		installation_id = auth_context.get("repo_installation_id")
		if installation_id is None:
			raise HTTPException(status_code=404, detail="Repository installation was not found.")
		db_path = resolve_db_path_fn()
		onboarding_started = time.perf_counter()
		onboarding = get_latest_repository_onboarding(db_path, repo_full)
		tracked_paths = {
			artifact.artifact_path
			for artifact in (list_onboarded_artifacts_for_onboarding(db_path, onboarding.id) if onboarding is not None else [])
		}
		record_server_timing_metric_fn(timing_metrics, "onboarding", onboarding_started)
		ref = onboarding.default_branch if onboarding is not None else None
		tracked_paths_sorted = sorted(tracked_paths)
		cache_key = (repo_full, ref, tuple(tracked_paths_sorted))
		cached_payload = _cache_get(artifact_options_cache, cache_key, lock=artifact_options_cache_lock)
		if cached_payload is not None:
			timing_metrics.append(("options-cache-hit", 0.0))
			json_started = time.perf_counter()
			response = JSONResponse(cached_payload)
			record_server_timing_metric_fn(timing_metrics, "json", json_started)
			timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
			return attach_server_timing_fn(response, timing_metrics)
		file_paths: list[str] = []
		inventory_available = False
		inventory_error = None
		inventory_started = time.perf_counter()
		try:
			jwt_started = time.perf_counter()
			jwt_token = generate_jwt_fn(github_app_id, github_private_key_path)
			record_server_timing_metric_fn(timing_metrics, "jwt", jwt_started)
			installation_token_started = time.perf_counter()
			token = get_installation_token_fn(jwt_token, int(installation_id))
			record_server_timing_metric_fn(timing_metrics, "installation_token", installation_token_started)
			tree_started = time.perf_counter()
			file_paths = list_repository_files_fn(repo_full, token, ref=ref)
			record_server_timing_metric_fn(timing_metrics, "tree", tree_started)
			inventory_available = True
		except (HTTPError, URLError, OSError, ValueError) as exc:
			inventory_error = str(exc)
		record_server_timing_metric_fn(timing_metrics, "inventory", inventory_started)
		json_started = time.perf_counter()
		payload = {
			"repo_full": repo_full,
			"default_branch": ref,
			"inventory_available": inventory_available,
			"inventory_error": inventory_error,
			"tracked_paths": tracked_paths_sorted,
			"artifact_type_options": tracked_artifact_type_options_fn(),
			"files": [
				{
					"path": path,
					"inferred_artifact_type": infer_artifact_type_from_path_fn(path),
				}
				for path in file_paths
				if path not in tracked_paths
			],
		}
		if inventory_available:
			_cache_set(
				artifact_options_cache,
				cache_key,
				payload,
				ttl_seconds=_ARTIFACT_OPTIONS_CACHE_TTL_SECONDS,
				max_entries=_ARTIFACT_OPTIONS_CACHE_MAX_ENTRIES,
				lock=artifact_options_cache_lock,
			)
		response = JSONResponse(payload)
		record_server_timing_metric_fn(timing_metrics, "json", json_started)
		timing_metrics.append(("total", (time.perf_counter() - request_started) * 1000.0))
		return attach_server_timing_fn(response, timing_metrics)

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
		invalidate_artifact_options_cache(repo_full)
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

	def add_repo_artifact(request: Request, repo_full: str, payload: RepoArtifactAddRequest):
		auth_context = authorize_repo_mutation_fn(request, repo_full)
		installation_id = auth_context.get("repo_installation_id")
		if installation_id is None:
			raise HTTPException(status_code=404, detail="Repository installation was not found.")
		jwt_token = generate_jwt_fn(github_app_id, github_private_key_path)
		token = get_installation_token_fn(jwt_token, int(installation_id))
		db_path = resolve_db_path_fn()
		try:
			artifact, baseline = add_repo_artifact_to_onboarding_fn(
				db_path,
				repo_full=repo_full,
				token=token,
				artifact_path=payload.artifact_path,
				artifact_type=payload.artifact_type,
			)
		except ValueError as exc:
			raise HTTPException(status_code=400, detail=str(exc)) from exc
		invalidate_artifact_options_cache(repo_full)
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse({
			"repo_full": repo_full,
			"artifact": asdict(artifact),
			"baseline": asdict(baseline),
			"dashboard": asdict(dashboard),
		})

	def remove_repo_artifact(request: Request, repo_full: str, artifact_path: str):
		authorize_repo_mutation_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			remove_repo_artifact_from_onboarding_fn(db_path, repo_full=repo_full, artifact_path=artifact_path)
		except ValueError as exc:
			raise HTTPException(status_code=404, detail=str(exc)) from exc
		invalidate_artifact_options_cache(repo_full)
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse({"repo_full": repo_full, "artifact_path": artifact_path, "dashboard": asdict(dashboard)})

	def update_repo_artifact(request: Request, repo_full: str, artifact_path: str, payload: RepoArtifactUpdateRequest):
		authorize_repo_mutation_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		try:
			artifact = update_repo_artifact_type_fn(
				db_path,
				repo_full=repo_full,
				artifact_path=artifact_path,
				artifact_type=payload.artifact_type,
			)
		except ValueError as exc:
			raise HTTPException(status_code=400, detail=str(exc)) from exc
		invalidate_artifact_options_cache(repo_full)
		dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
		return JSONResponse({"repo_full": repo_full, "artifact": asdict(artifact), "dashboard": asdict(dashboard)})

	router.add_api_route("/api/repos/{repo_full:path}/artifacts/options", list_repo_artifact_options, methods=["GET"])
	router.add_api_route("/api/repos/{repo_full:path}/onboard", run_repo_onboarding, methods=["POST"])
	router.add_api_route("/api/repos/{repo_full:path}/backfill", run_repo_backfill, methods=["POST"])
	router.add_api_route("/api/repos/{repo_full:path}/artifacts", add_repo_artifact, methods=["POST"])
	router.add_api_route("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}", remove_repo_artifact, methods=["DELETE"])
	router.add_api_route("/api/repos/{repo_full:path}/artifacts/{artifact_path:path}", update_repo_artifact, methods=["PATCH"])
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
	dashboard_repo_audit_handler: Callable,
) -> APIRouter:
	router = APIRouter(tags=["dashboard"])
	router.add_api_route("/dashboard", dashboard_index_handler, methods=["GET"], response_class=HTMLResponse)

	async def dashboard_repo_audit_owner_route(request: Request, repo_owner: str, repo_name: str, artifact: str | None = None, pr: str | None = None, head_sha: str | None = None):
		repo_full = f"{repo_owner}/{repo_name}"
		return await dashboard_repo_audit_handler(request, repo_full, artifact=artifact, pr=pr, head_sha=head_sha)

	router.add_api_route("/dashboard/{repo_owner}/{repo_name}/audit", dashboard_repo_audit_owner_route, methods=["GET"], response_class=HTMLResponse)
	router.add_api_route("/dashboard/{repo_full:path}", dashboard_repo_handler, methods=["GET"], response_class=HTMLResponse)
	return router


def create_repo_baseline_router(
	*,
	authorize_repo_read_fn: Callable,
	authorize_repo_mutation_fn: Callable,
	resolve_baseline_approval_mode_fn: Callable,
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
		auth_context = authorize_repo_mutation_fn(request, repo_full)
		db_path = resolve_db_path_fn()
		baseline_approval_mode = resolve_baseline_approval_mode_fn(auth_context, db_path, repo_full)
		actor_login = resolve_actor_login_fn(request, payload)
		membership = auth_context.get("membership") if isinstance(auth_context, dict) else None
		if baseline_approval_mode == "auto" and (
			membership is None or getattr(membership, "role", None) not in {"owner", "admin"}
		):
			raise HTTPException(
				status_code=403,
				detail="Auto-approved baseline creation requires a workspace owner or admin role.",
			)
		try:
			baselines = rebaseline_repo_from_snapshot_fn(
				db_path,
				repo_full=repo_full,
				snapshot_id=payload.snapshot_id,
				rationale=payload.rationale,
				actor_login=actor_login,
				github_app_id=github_app_id,
				github_private_key_path=github_private_key_path,
				generate_jwt_fn=generate_jwt_fn,
				get_installation_token_fn=get_installation_token_fn,
				fetch_file_content_fn=fetch_file_content_fn,
			)
			auto_approved_baselines = []
			if baseline_approval_mode == "auto":
				auto_approved_baselines = approve_repo_baseline_fn(
					db_path,
					repo_full=repo_full,
					actor_login=actor_login,
					approval_note=payload.rationale,
				)
		except ValueError as exc:
			raise HTTPException(status_code=400, detail=str(exc)) from exc
		except RebaselineExternalError as exc:
			raise HTTPException(status_code=502, detail=str(exc)) from exc
		except RebaselineInternalError as exc:
			raise HTTPException(status_code=500, detail=str(exc)) from exc
		except Exception as exc:
			raise HTTPException(status_code=500, detail=f"Unexpected rebaseline failure: {type(exc).__name__}") from exc
		response_payload = {
			"repo_full": repo_full,
			"snapshot_id": payload.snapshot_id,
			"created_baseline_count": len(baselines),
			"baseline_approval_mode": baseline_approval_mode,
			"auto_approved": baseline_approval_mode == "auto",
			"approved_baseline_count": len(auto_approved_baselines),
			"baseline_candidate_status": "approved" if baseline_approval_mode == "auto" else "pending",
			"dashboard": None,
		}
		try:
			dashboard = build_repo_dashboard_view_fn(db_path, repo_full)
			response_payload["dashboard"] = asdict(dashboard)
		except Exception:
			pass
		return JSONResponse(response_payload)

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