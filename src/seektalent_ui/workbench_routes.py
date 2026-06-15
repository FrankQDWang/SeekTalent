from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from seektalent.dev_mode import build_dev_mode_status
from seektalent_ui import workbench_auth_routes, workbench_liepin_recovery as liepin_recovery
from seektalent_ui.auth import (
    get_workbench_store,
    require_csrf_user,
    require_current_user,
)
from seektalent_ui.final_top_candidates import project_final_top_candidates
from seektalent_ui.models import (
    WorkbenchCandidateReviewItemResponse,
    WorkbenchCandidateReviewItemUpdateRequest,
    WorkbenchCandidateReviewQueueResponse,
    WorkbenchDevModeStatusResponse,
    WorkbenchDetailOpenRequestStatus,
    WorkbenchDetailOpenRejectRequest,
    WorkbenchDetailOpenRequestCreateRequest,
    WorkbenchDetailOpenRequestListResponse,
    WorkbenchDetailOpenRequestResponse,
    WorkbenchFinalTopCandidateListResponse,
    WorkbenchGraphCandidateListResponse,
    WorkbenchGraphCandidateResumeSnapshotResponse,
    WorkbenchGraphCandidateSummaryResponse,
    WorkbenchProviderActionResponse,
    WorkbenchRequirementReviewResponse,
    WorkbenchRequirementReviewUpdateRequest,
    WorkbenchRuntimeGraphResponse,
    WorkbenchSecurityAuditEventListResponse,
    WorkbenchSessionCreateRequest,
    WorkbenchSessionListResponse,
    WorkbenchSessionResponse,
    WorkbenchSessionStartBlockedSourceResponse,
    WorkbenchSessionStartResponse,
    WorkbenchSettingsResponse,
    WorkbenchSettingsSourceResponse,
    WorkbenchSourceRunPolicyResponse,
    WorkbenchSourceRunPolicyUpdateRequest,
)
from seektalent_ui.resume_snapshot_projection import build_resume_snapshot_response
from seektalent_ui.runtime_graph import build_runtime_graph
from seektalent_ui.workbench_runtime_source_response import runtime_source_state_response
from seektalent_ui.workbench_response import (
    candidate_review_item_response,
    detail_open_request_response,
    dev_mode_status_response,
    provider_action_response,
    public_runtime_source_reason_code,
    requirement_review_response,
    runtime_final_top_candidate_response,
    runtime_sourcing_job_response,
    security_audit_event_response,
    session_response,
    session_start_blocked_sources,
    source_run_policy_response,
)
from seektalent_ui.workbench_liepin_start_probe import (
    ensure_liepin_browser_session_ready_for_start,
    liepin_dev_mode_setup_reason,
    refresh_liepin_opencli_connection_if_ready,
    workbench_app_settings,
)
from seektalent_ui.workbench_source_connection_routes import router as source_connection_router
from seektalent_ui.workbench_candidate_graph import (
    DEFAULT_GRAPH_CANDIDATE_LIMIT,
    MAX_GRAPH_CANDIDATE_LIMIT,
    list_graph_candidates,
)
from seektalent_ui.workbench_store import (
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
    WorkbenchSourceConnection,
    WorkbenchStore,
    WorkbenchUser,
)


router = APIRouter()
router.include_router(workbench_auth_routes.router)


def _append_waiting_running_note(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    key_suffix: str,
    text: str,
) -> None:
    store.try_append_workbench_note(
        user=user,
        session_id=session_id,
        idempotency_key=f"workbench-running-note:{session_id}:{key_suffix}",
        text=text,
        status_hint="waiting",
        note_kind="waiting",
    )


@router.get("/api/workbench/sessions", response_model=WorkbenchSessionListResponse)
async def list_sessions(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSessionListResponse:
    store = get_workbench_store(request)
    await refresh_liepin_opencli_connection_if_ready(request=request, store=store, user=user)
    connections: dict[str, WorkbenchSourceConnection] = {
        connection.source_kind: connection for connection in store.list_source_connections(user=user)
    }
    liepin_setup_reason = liepin_dev_mode_setup_reason(request)
    return WorkbenchSessionListResponse(
        sessions=[
            session_response(
                session,
                connections,
                runtime_source_state=runtime_source_state_response(store=store, user=user, session=session),
                runtime_source_count_projection=store.latest_runtime_source_count_projection(
                    user=user,
                    session_id=session.session_id,
                ),
                liepin_setup_reason=liepin_setup_reason,
            )
            for session in store.list_workbench_sessions(user=user)
        ]
    )


@router.post("/api/workbench/sessions", response_model=WorkbenchSessionResponse, status_code=201)
async def create_session(
    request: WorkbenchSessionCreateRequest,
    http_request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchSessionResponse:
    job_title = request.jobTitle.strip()
    jd_text = request.jdText.strip()
    notes = request.notes.strip()
    if not job_title:
        raise HTTPException(status_code=400, detail="jobTitle must not be empty.")
    if not jd_text:
        raise HTTPException(status_code=400, detail="jdText must not be empty.")
    if len(jd_text) > 20_000:
        raise HTTPException(status_code=400, detail="jdText must be at most 20000 characters.")
    source_kinds = request.sourceKinds
    if source_kinds is not None and len(set(source_kinds)) != len(source_kinds):
        raise HTTPException(status_code=400, detail="sourceKinds must not contain duplicates.")
    store = get_workbench_store(http_request)
    requested_source_kinds = source_kinds if source_kinds is not None else ["cts", "liepin"]
    if "liepin" in requested_source_kinds:
        await refresh_liepin_opencli_connection_if_ready(
            request=http_request,
            store=store,
            user=user,
            bind_unbound_account=True,
        )
    session = store.create_workbench_session(
        user=user,
        job_title=job_title,
        jd_text=jd_text,
        notes=notes,
        source_kinds=source_kinds,
    )
    connections: dict[str, WorkbenchSourceConnection] = {
        connection.source_kind: connection for connection in store.list_source_connections(user=user)
    }
    return session_response(
        session,
        connections,
        runtime_source_state=runtime_source_state_response(store=store, user=user, session=session),
        runtime_source_count_projection=store.latest_runtime_source_count_projection(
            user=user,
            session_id=session.session_id,
        ),
        liepin_setup_reason=liepin_dev_mode_setup_reason(http_request),
    )


@router.get("/api/workbench/sessions/{session_id}", response_model=WorkbenchSessionResponse)
async def get_session(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSessionResponse:
    store = get_workbench_store(request)
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found.")
    session = await liepin_recovery.recover_liepin_session(request=request, store=store, user=user, session=session)
    connections: dict[str, WorkbenchSourceConnection] = {
        connection.source_kind: connection for connection in store.list_source_connections(user=user)
    }
    return session_response(
        session,
        connections,
        runtime_source_state=runtime_source_state_response(store=store, user=user, session=session),
        runtime_source_count_projection=store.latest_runtime_source_count_projection(
            user=user,
            session_id=session.session_id,
        ),
        liepin_setup_reason=liepin_dev_mode_setup_reason(request),
    )


@router.get(
    "/api/workbench/sessions/{session_id}/candidates",
    response_model=WorkbenchCandidateReviewQueueResponse,
)
def list_candidate_review_items(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchCandidateReviewQueueResponse:
    store = get_workbench_store(request)
    items = store.list_candidate_review_items(user=user, session_id=session_id)
    if items is None:
        raise HTTPException(status_code=404, detail="Not found.")
    graph_candidates = _final_graph_candidate_index(request=request, store=store, user=user, session_id=session_id)
    return WorkbenchCandidateReviewQueueResponse(
        items=[candidate_review_item_response(item, graph_candidates.get(item.review_item_id)) for item in items]
    )


@router.get(
    "/api/workbench/sessions/{session_id}/final-top10",
    response_model=WorkbenchFinalTopCandidateListResponse,
)
def list_final_top_candidates(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchFinalTopCandidateListResponse:
    store = get_workbench_store(request)
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found.")
    runtime_source_state = runtime_source_state_response(store=store, user=user, session=session)
    runtime_final = store.list_runtime_final_top_review_items(user=user, session_id=session_id)
    if runtime_final is not None:
        revision, runtime_items = runtime_final
        return WorkbenchFinalTopCandidateListResponse(
            items=[
                runtime_final_top_candidate_response(item, rank=index + 1)
                for index, item in enumerate(runtime_items[:10])
            ],
            coverageStatus=runtime_source_state.coverageStatus,
            finalizationRevision=revision,
        )
    if not store.has_runtime_sourcing_job(user=user, session_id=session_id):
        review_items = store.list_candidate_review_items(user=user, session_id=session_id) or []
        projected_items = project_final_top_candidates(review_items, limit=10)
        return WorkbenchFinalTopCandidateListResponse(
            items=projected_items,
            coverageStatus=runtime_source_state.coverageStatus,
            finalizationRevision=runtime_source_state.finalizationRevision,
        )
    return WorkbenchFinalTopCandidateListResponse(
        items=[],
        coverageStatus=runtime_source_state.coverageStatus,
        finalizationRevision=runtime_source_state.finalizationRevision,
    )


@router.get(
    "/api/workbench/sessions/{session_id}/runtime-graph",
    response_model=WorkbenchRuntimeGraphResponse,
)
def get_session_runtime_graph(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchRuntimeGraphResponse:
    store = get_workbench_store(request)
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found.")
    events = store.list_all_session_workbench_events(user=user, session_id=session_id)
    detail_open_requests = store.list_liepin_detail_open_requests(user=user, session_id=session_id)
    final_top = _final_top_candidate_list_for_runtime_graph(
        request=request,
        store=store,
        user=user,
        session_id=session_id,
    )
    return build_runtime_graph(
        session=session,
        events=events,
        runtime_source_state=runtime_source_state_response(store=store, user=user, session=session),
        detail_open_requests=detail_open_requests,
        final_top=final_top,
    )


def _final_top_candidate_list_for_runtime_graph(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
) -> WorkbenchFinalTopCandidateListResponse | None:
    del store
    try:
        final_top = list_final_top_candidates(session_id=session_id, request=request, user=user)
    except HTTPException:
        return None
    return final_top if final_top.items else None


@router.get("/api/workbench/dev-mode/status", response_model=WorkbenchDevModeStatusResponse)
def get_dev_mode_status(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchDevModeStatusResponse:
    del user
    payload = getattr(request.app.state, "dev_mode_env_diagnostics", None)
    if payload is None:
        payload = build_dev_mode_status(workbench_app_settings(request))
    return dev_mode_status_response(payload)


@router.get(
    "/api/workbench/sessions/{session_id}/graph-candidates",
    response_model=WorkbenchGraphCandidateListResponse,
)
def list_session_graph_candidates(
    session_id: str,
    node_id: str,
    request: Request,
    limit: int = DEFAULT_GRAPH_CANDIDATE_LIMIT,
    cursor: str | None = None,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchGraphCandidateListResponse:
    store = get_workbench_store(request)
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Workbench settings are not available.")
    response = list_graph_candidates(
        settings=settings,
        graph_secret=_workbench_graph_secret(request),
        store=store,
        user=user,
        session_id=session_id,
        node_id=node_id,
        limit=limit,
        cursor=cursor,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return response


@router.get(
    "/api/workbench/sessions/{session_id}/graph-candidates/{graph_candidate_id}/resume-snapshot",
    response_model=WorkbenchGraphCandidateResumeSnapshotResponse,
)
def get_graph_candidate_resume_snapshot(
    session_id: str,
    graph_candidate_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchGraphCandidateResumeSnapshotResponse:
    store = get_workbench_store(request)
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Workbench settings are not available.")
    response = build_resume_snapshot_response(
        settings=settings,
        graph_secret=_workbench_graph_secret(request),
        store=store,
        user=user,
        session_id=session_id,
        graph_candidate_id=graph_candidate_id,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return response


def _workbench_graph_secret(request: Request) -> str:
    secret = getattr(request.app.state, "workbench_graph_secret", None)
    if not isinstance(secret, str) or not secret:
        raise HTTPException(status_code=500, detail="Workbench graph secret is not configured.")
    return secret


router.include_router(source_connection_router)


@router.put(
    "/api/workbench/sessions/{session_id}/candidates/{review_item_id}",
    response_model=WorkbenchCandidateReviewItemResponse,
)
def update_candidate_review_item(
    session_id: str,
    review_item_id: str,
    update: WorkbenchCandidateReviewItemUpdateRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchCandidateReviewItemResponse:
    if update.status is None and update.note is None:
        raise HTTPException(status_code=400, detail="Candidate update must include status or note.")
    store = get_workbench_store(request)
    item = store.update_candidate_review_item(
        user=user,
        session_id=session_id,
        review_item_id=review_item_id,
        review_status=update.status,
        note=update.note,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return candidate_review_item_response(item)


@router.post(
    "/api/workbench/sessions/{session_id}/candidates/{review_item_id}/provider-actions/open",
    response_model=WorkbenchProviderActionResponse,
)
def open_candidate_provider_action(
    session_id: str,
    review_item_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchProviderActionResponse:
    store = get_workbench_store(request)
    try:
        action = store.build_liepin_provider_open_action(
            user=user,
            session_id=session_id,
            review_item_id=review_item_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if action is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return provider_action_response(action)


@router.post(
    "/api/workbench/sessions/{session_id}/candidates/{review_item_id}/detail-open-requests",
    response_model=WorkbenchDetailOpenRequestResponse,
    status_code=202,
)
def create_detail_open_request(
    session_id: str,
    review_item_id: str,
    create_request: WorkbenchDetailOpenRequestCreateRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchDetailOpenRequestResponse:
    store = get_workbench_store(request)
    try:
        detail_request = store.create_liepin_detail_open_request(
            user=user,
            session_id=session_id,
            review_item_id=review_item_id,
            idempotency_key=create_request.idempotencyKey,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if detail_request is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if detail_request.ledger is not None and detail_request.ledger.status == "leased":
        runner = getattr(request.app.state, "workbench_job_runner", None)
        if runner is not None:
            runner.wake()
    return detail_open_request_response(detail_request)


@router.get(
    "/api/workbench/detail-open-requests",
    response_model=WorkbenchDetailOpenRequestListResponse,
)
def list_detail_open_requests(
    request: Request,
    session_id: str | None = None,
    status: WorkbenchDetailOpenRequestStatus | None = None,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchDetailOpenRequestListResponse:
    store = get_workbench_store(request)
    return WorkbenchDetailOpenRequestListResponse(
        requests=[
            detail_open_request_response(detail_request)
            for detail_request in store.list_liepin_detail_open_requests(
                user=user,
                session_id=session_id,
                status=status,
            )
        ]
    )


@router.post(
    "/api/workbench/detail-open-requests/{request_id}/approve",
    response_model=WorkbenchDetailOpenRequestResponse,
)
def approve_detail_open_request(
    request_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchDetailOpenRequestResponse:
    store = get_workbench_store(request)
    try:
        detail_request = store.approve_liepin_detail_open_request(user=user, request_id=request_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if detail_request is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if detail_request.ledger is not None and detail_request.ledger.status == "leased":
        runner = getattr(request.app.state, "workbench_job_runner", None)
        if runner is not None:
            runner.wake()
    return detail_open_request_response(detail_request)


@router.post(
    "/api/workbench/detail-open-requests/{request_id}/reject",
    response_model=WorkbenchDetailOpenRequestResponse,
)
def reject_detail_open_request(
    request_id: str,
    reject_request: WorkbenchDetailOpenRejectRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchDetailOpenRequestResponse:
    store = get_workbench_store(request)
    try:
        detail_request = store.reject_liepin_detail_open_request(
            user=user,
            request_id=request_id,
            reason=reject_request.reason,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if detail_request is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return detail_open_request_response(detail_request)


@router.get(
    "/api/workbench/sessions/{session_id}/requirements",
    response_model=WorkbenchRequirementReviewResponse,
)
def get_requirement_review(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchRequirementReviewResponse:
    store = get_workbench_store(request)
    review = store.get_requirement_review(user=user, session_id=session_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return requirement_review_response(review)


@router.put(
    "/api/workbench/sessions/{session_id}/requirements",
    response_model=WorkbenchRequirementReviewResponse,
)
def update_requirement_review(
    session_id: str,
    review_update: WorkbenchRequirementReviewUpdateRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchRequirementReviewResponse:
    store = get_workbench_store(request)
    try:
        review = store.update_requirement_review(
            user=user,
            session_id=session_id,
            requirement_sheet=review_update.requirement_sheet,
        )
    except ValueError as exc:
        if str(exc) == "requirement_sheet_job_title_mismatch":
            raise HTTPException(status_code=409, detail="requirement_sheet_job_title_mismatch") from exc
        raise
    if review is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return requirement_review_response(review)


@router.post(
    "/api/workbench/sessions/{session_id}/requirements/prepare",
    response_model=WorkbenchRequirementReviewResponse,
)
def prepare_requirement_review(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchRequirementReviewResponse:
    store = get_workbench_store(request)
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found.")
    runner = getattr(request.app.state, "workbench_job_runner", None)
    if runner is None:
        raise HTTPException(status_code=500, detail="Workbench runtime is not available.")
    runner.start_requirement_review(user=user, session_id=session_id)
    review = store.get_requirement_review(user=user, session_id=session_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return requirement_review_response(review)


@router.post(
    "/api/workbench/sessions/{session_id}/requirements/approve",
    response_model=WorkbenchRequirementReviewResponse,
)
def approve_requirement_review(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchRequirementReviewResponse:
    store = get_workbench_store(request)
    try:
        review = store.approve_requirement_review(user=user, session_id=session_id)
    except PermissionError as exc:
        if str(exc) == "requirement_review_empty":
            raise HTTPException(status_code=409, detail="requirement_review_empty") from exc
        raise
    if review is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return requirement_review_response(review)


@router.post(
    "/api/workbench/sessions/{session_id}/start",
    response_model=WorkbenchSessionStartResponse,
    status_code=202,
)
async def start_session_source_runs(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchSessionStartResponse:
    store = get_workbench_store(request)
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if session.requirement_review.status != "approved":
        raise HTTPException(status_code=409, detail="requirement_review_not_approved")
    if session.requirement_review.requirement_sheet is None:
        raise HTTPException(status_code=409, detail="requirement_review_empty")
    blocked: list[WorkbenchSessionStartBlockedSourceResponse] = []
    should_wake_runner = False
    for source_run in session.source_runs:
        if source_run.status in {"completed", "failed"}:
            continue
        if (
            source_run.source_kind == "liepin"
            and source_run.status != "running"
            and (
                source_run.status in {"blocked", "queued"}
                or source_run.auth_state == "login_required"
            )
        ):
            probe = await ensure_liepin_browser_session_ready_for_start(
                request=request,
                store=store,
                user=user,
                session_id=session_id,
                source_run_id=source_run.source_run_id,
            )
            if not probe.ready:
                reason = probe.reason_code or LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE
                blocked_run = store.block_source_run_for_start_probe(
                    user=user,
                    session_id=session_id,
                    source_run_id=source_run.source_run_id,
                    warning_code=reason,
                    warning_message=probe.warning_message or LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
                )
                if blocked_run is None or blocked_run.status != "blocked" or blocked_run.warning_code != reason:
                    continue
                blocked.append(
                    WorkbenchSessionStartBlockedSourceResponse(
                        sourceRunId=source_run.source_run_id,
                        sourceKind=source_run.source_kind,
                        reason=public_runtime_source_reason_code(reason) or "source_provider_failed",
                    )
                )
                continue
    try:
        runtime_result = store.start_runtime_sourcing_job(
            user=user,
            session_id=session_id,
            idempotency_key="workbench-primary-runtime-sourcing",
        )
    except PermissionError as exc:
        if str(exc) == "selected_source_blocked":
            refreshed = store.get_workbench_session(user=user, session_id=session_id)
            if refreshed is None:
                raise HTTPException(status_code=404, detail="Not found.") from exc
            return WorkbenchSessionStartResponse(
                sessionId=session_id,
                runtimeJob=None,
                blockedSources=session_start_blocked_sources(session_response(refreshed)),
            )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "runtime_sourcing_already_terminal":
            runtime_result = None
        else:
            raise HTTPException(status_code=500, detail="runtime_sourcing_start_failed") from exc
    runtime_job_response = None
    if runtime_result is not None:
        runtime_job, _was_created = runtime_result
        should_wake_runner = runtime_job.status in {"queued", "running"}
        runtime_job_response = runtime_sourcing_job_response(runtime_job)
    runner = getattr(request.app.state, "workbench_job_runner", None)
    if runner is not None and should_wake_runner:
        _append_waiting_running_note(
            store=store,
            user=user,
            session_id=session_id,
            key_suffix="source-started",
            text="检索已启动，正在根据已确认标准推进所选渠道。",
        )
        runner.wake()
    return WorkbenchSessionStartResponse(
        sessionId=session_id,
        runtimeJob=runtime_job_response,
        blockedSources=blocked,
    )


@router.put(
    "/api/workbench/sessions/{session_id}/source-runs/liepin/policy",
    response_model=WorkbenchSourceRunPolicyResponse,
)
def update_liepin_source_run_policy(
    session_id: str,
    policy_update: WorkbenchSourceRunPolicyUpdateRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchSourceRunPolicyResponse:
    store = get_workbench_store(request)
    policy = store.update_liepin_source_run_policy(
        user=user,
        session_id=session_id,
        detail_open_mode=policy_update.detailOpenMode,
    )
    if policy is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return source_run_policy_response(policy)


@router.get(
    "/api/workbench/sessions/{session_id}/source-runs/liepin/policy",
    response_model=WorkbenchSourceRunPolicyResponse,
)
def get_liepin_source_run_policy(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSourceRunPolicyResponse:
    store = get_workbench_store(request)
    policy = store.get_liepin_source_run_policy(user=user, session_id=session_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return source_run_policy_response(policy)


@router.get(
    "/api/workbench/security-audit-events",
    response_model=WorkbenchSecurityAuditEventListResponse,
    response_model_exclude_none=True,
)
def list_security_audit_events(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSecurityAuditEventListResponse:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    store = get_workbench_store(request)
    return WorkbenchSecurityAuditEventListResponse(
        events=[security_audit_event_response(event) for event in store.list_security_audit_events_for_user(user=user)]
    )


@router.get("/api/workbench/settings", response_model=WorkbenchSettingsResponse)
def settings(user: WorkbenchUser = Depends(require_current_user)) -> WorkbenchSettingsResponse:
    return WorkbenchSettingsResponse(
        workspaceId=user.workspace_id,
        sources=[
            WorkbenchSettingsSourceResponse(
                sourceKind="cts",
                label="CTS",
                enabled=True,
                authRequired=False,
            ),
            WorkbenchSettingsSourceResponse(
                sourceKind="liepin",
                label="Liepin",
                enabled=True,
                authRequired=True,
            ),
        ],
    )


def _final_graph_candidate_index(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
) -> dict[str, WorkbenchGraphCandidateSummaryResponse]:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return {}
    response = list_graph_candidates(
        settings=settings,
        graph_secret=_workbench_graph_secret(request),
        store=store,
        user=user,
        session_id=session_id,
        node_id="final-shortlist",
        limit=MAX_GRAPH_CANDIDATE_LIMIT,
        cursor=None,
    )
    if response is None:
        return {}
    return {
        item.reviewItemId: item
        for item in response.items
        if item.reviewItemId
    }
