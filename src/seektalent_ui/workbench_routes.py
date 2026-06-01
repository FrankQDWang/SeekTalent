from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from seektalent.config import AppSettings
from seektalent.dev_mode import build_dev_mode_status
from seektalent.providers.liepin.client import build_liepin_worker_client
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent.providers.liepin.worker_contracts import SessionStatus
from seektalent_ui import workbench_auth_routes
from seektalent_ui.liepin_account_binding import (
    bind_observed_liepin_account,
    ensure_workbench_liepin_provider_connection,
)
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
    WorkbenchLiepinLoginHandoffResponse,
    WorkbenchLiepinLoginRelayInputRequest,
    WorkbenchProviderActionResponse,
    WorkbenchRequirementReviewResponse,
    WorkbenchRequirementReviewUpdateRequest,
    WorkbenchRuntimeGraphResponse,
    WorkbenchRuntimeSourceLaneStateResponse,
    WorkbenchRuntimeSourceStateResponse,
    WorkbenchRuntimeSourceWorkflowStepResponse,
    RuntimeSourceCoverageStatus,
    RuntimeSourceDetailState,
    RuntimeSourceDisplayStatus,
    WorkbenchSecurityAuditEventListResponse,
    WorkbenchSessionCreateRequest,
    WorkbenchSessionListResponse,
    WorkbenchSessionResponse,
    WorkbenchSessionStartBlockedSourceResponse,
    WorkbenchSessionStartResponse,
    WorkbenchSettingsResponse,
    WorkbenchSettingsSourceResponse,
    WorkbenchSourceConnectionListResponse,
    WorkbenchSourceConnectionResponse,
    WorkbenchSourceRunPolicyResponse,
    WorkbenchSourceRunPolicyUpdateRequest,
)
from seektalent_ui.resume_snapshot_projection import build_resume_snapshot_response
from seektalent_ui.runtime_graph import build_runtime_graph
from seektalent_ui.workbench_response import (
    candidate_review_item_response,
    detail_open_request_response,
    dev_mode_status_response,
    liepin_start_probe_warning_message,
    provider_action_response,
    public_runtime_source_reason_code,
    requirement_review_response,
    runtime_final_top_candidate_response,
    runtime_sourcing_job_response,
    security_audit_event_response,
    session_response,
    session_start_blocked_sources,
    source_connection_response,
    source_run_policy_response,
)
from seektalent_ui.workbench_candidate_graph import (
    DEFAULT_GRAPH_CANDIDATE_LIMIT,
    MAX_GRAPH_CANDIDATE_LIMIT,
    list_graph_candidates,
)
from seektalent_ui.workbench_store import (
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
    RuntimeSourceCountProjection,
    WorkbenchRuntimeSourceLaneLatestState,
    WorkbenchSession,
    WorkbenchSourceConnection,
    WorkbenchSourceRun,
    WorkbenchStore,
    WorkbenchUser,
    DEFAULT_TENANT_ID,
)


router = APIRouter()
router.include_router(workbench_auth_routes.router)

RUNTIME_SOURCE_REASON_CODES = {
    "blocked_backend_unavailable",
    "failed_provider_error",
    "login_required",
    "partial_timeout",
    "cancelled_by_user",
    "liepin_connection_not_connected",
    "liepin_browser_login_required",
    "liepin_browser_probe_unavailable",
    "liepin_browser_account_mismatch",
    "liepin_opencli_backend_disabled",
    "liepin_opencli_command_missing",
    "liepin_opencli_extension_disconnected",
    "liepin_opencli_daemon_not_running",
    "liepin_opencli_daemon_stale",
    "liepin_opencli_status_unavailable",
    "liepin_opencli_forbidden_command",
    "liepin_opencli_forbidden_text",
    "liepin_opencli_host_blocked",
    "liepin_opencli_start_url_blocked",
    "liepin_opencli_window_policy_blocked",
    "liepin_opencli_budget_exhausted",
    "liepin_opencli_timeout",
    "liepin_opencli_login_required",
    "liepin_opencli_identity_intercept",
    "liepin_opencli_risk_page",
    "liepin_opencli_unknown_modal",
    "liepin_opencli_source_policy_missing",
    "liepin_opencli_malformed_state",
    "liepin_opencli_detail_not_opened",
    "liepin_opencli_filter_unapplied",
    "liepin_opencli_stale_ref",
    "liepin_opencli_selector_not_found",
    "liepin_opencli_selector_ambiguous",
    "liepin_opencli_target_not_found",
    "runtime_failed",
}

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
    await _refresh_liepin_opencli_connection_if_ready(request=request, store=store, user=user)
    connections: dict[str, WorkbenchSourceConnection] = {
        connection.source_kind: connection for connection in store.list_source_connections(user=user)
    }
    liepin_setup_reason = _liepin_dev_mode_setup_reason(request)
    return WorkbenchSessionListResponse(
        sessions=[
            session_response(
                session,
                connections,
                runtime_source_state=_runtime_source_state_response(store=store, user=user, session=session),
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
        await _refresh_liepin_opencli_connection_if_ready(request=http_request, store=store, user=user)
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
        runtime_source_state=_runtime_source_state_response(store=store, user=user, session=session),
        runtime_source_count_projection=store.latest_runtime_source_count_projection(
            user=user,
            session_id=session.session_id,
        ),
        liepin_setup_reason=_liepin_dev_mode_setup_reason(http_request),
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
    await _refresh_liepin_opencli_connection_if_ready(request=request, store=store, user=user)
    connections: dict[str, WorkbenchSourceConnection] = {
        connection.source_kind: connection for connection in store.list_source_connections(user=user)
    }
    return session_response(
        session,
        connections,
        runtime_source_state=_runtime_source_state_response(store=store, user=user, session=session),
        runtime_source_count_projection=store.latest_runtime_source_count_projection(
            user=user,
            session_id=session.session_id,
        ),
        liepin_setup_reason=_liepin_dev_mode_setup_reason(request),
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
    runtime_source_state = _runtime_source_state_response(store=store, user=user, session=session)
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
        runtime_source_state=_runtime_source_state_response(store=store, user=user, session=session),
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
        payload = build_dev_mode_status(_workbench_app_settings(request))
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


@router.get("/api/workbench/source-connections", response_model=WorkbenchSourceConnectionListResponse)
async def list_source_connections(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSourceConnectionListResponse:
    store = get_workbench_store(request)
    await _refresh_liepin_opencli_connection_if_ready(request=request, store=store, user=user)
    return WorkbenchSourceConnectionListResponse(
        connections=[source_connection_response(connection) for connection in store.list_source_connections(user=user)]
    )


@router.post(
    "/api/workbench/source-connections/liepin",
    response_model=WorkbenchSourceConnectionResponse,
    status_code=201,
)
def create_liepin_source_connection(
    request: Request,
    response: Response,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchSourceConnectionResponse:
    store = get_workbench_store(request)
    connection, created = store.get_or_create_liepin_source_connection(user=user)
    if not created:
        response.status_code = 200
    return source_connection_response(connection)


@router.get(
    "/api/workbench/source-connections/{connection_id}",
    response_model=WorkbenchSourceConnectionResponse,
)
def get_source_connection(
    connection_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSourceConnectionResponse:
    store = get_workbench_store(request)
    connection = store.get_source_connection(user=user, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return source_connection_response(connection)


@router.post(
    "/api/workbench/source-connections/{connection_id}/login",
    response_model=WorkbenchLiepinLoginHandoffResponse,
)
async def start_liepin_connection_login(
    connection_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchLiepinLoginHandoffResponse:
    _require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    existing = store.get_source_connection(user=user, connection_id=connection_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Not found.")
    compliance_gate_ref = ensure_workbench_liepin_provider_connection(
        settings=_workbench_app_settings(request),
        user=user,
        connection=existing,
    )
    safe_frame_url = f"/api/workbench/source-connections/{connection_id}/login/frame"
    warning_code: str | None = None
    warning_message: str | None = None
    handoff_state = "safe_frame_available"
    try:
        worker_client = _liepin_worker_client(request)
        await worker_client.login_handoff(
            connection_id=connection_id,
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=user.workspace_id,
        )
    except LiepinWorkerModeError:
        safe_frame_url = None
        warning_code = "relay_pending_worker"
        warning_message = "Managed browser login relay is not configured or not ready."
        handoff_state = "relay_pending_worker"
    connection = store.start_liepin_login_handoff(
        user=user,
        connection_id=connection_id,
        provider_account_hash=None,
        compliance_gate_ref=compliance_gate_ref,
        warning_code=warning_code,
        warning_message=warning_message,
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return WorkbenchLiepinLoginHandoffResponse(
        connectionId=connection.connection_id,
        sourceKind="liepin",
        status=connection.status,
        handoffMode="server_managed_browser",
        handoffState=handoff_state,
        safeFrameUrl=safe_frame_url,
        warningCode=connection.warning_code,
        warningMessage=connection.warning_message,
    )


@router.get("/api/workbench/source-connections/{connection_id}/login/frame", response_class=HTMLResponse)
def liepin_connection_login_frame(
    connection_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> HTMLResponse:
    _require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    connection = store.get_source_connection(user=user, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return HTMLResponse(_login_frame_html(connection_id))


@router.get("/api/workbench/source-connections/{connection_id}/login/snapshot")
async def liepin_connection_login_snapshot(
    connection_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> dict[str, object]:
    _require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    connection = store.get_source_connection(user=user, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        snapshot = await _liepin_worker_client(request).login_relay_snapshot(connection_id=connection_id)
    except LiepinWorkerModeError as exc:
        raise HTTPException(status_code=409, detail="Liepin login relay is not available.") from exc
    return {
        "connectionId": snapshot.connection_id,
        "status": snapshot.status,
        "pageTitle": snapshot.page_title,
        "pageOrigin": snapshot.page_origin,
        "imageMimeType": snapshot.image_mime_type,
        "imageBase64": snapshot.image_base64,
        "updatedAt": snapshot.updated_at,
    }


@router.post("/api/workbench/source-connections/{connection_id}/login/input")
async def liepin_connection_login_input(
    connection_id: str,
    input_request: WorkbenchLiepinLoginRelayInputRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    connection = store.get_source_connection(user=user, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        result = await _liepin_worker_client(request).submit_login_relay_input(
            connection_id=connection_id,
            action=input_request.action,
            x=input_request.x,
            y=input_request.y,
            text=input_request.text,
            key=input_request.key,
        )
    except LiepinWorkerModeError as exc:
        raise HTTPException(status_code=409, detail="Liepin login relay is not available.") from exc
    return {"connectionId": result.connection_id, "accepted": result.accepted, "updatedAt": result.updated_at}


@router.post(
    "/api/workbench/source-connections/{connection_id}/login/complete",
    response_model=WorkbenchSourceConnectionResponse,
)
async def complete_liepin_connection_login(
    connection_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchSourceConnectionResponse:
    _require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    connection = store.get_source_connection(user=user, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        result = await _liepin_worker_client(request).complete_login_relay(connection_id=connection_id)
    except LiepinWorkerModeError as exc:
        if exc.setup_status == "login_not_verified":
            raise HTTPException(status_code=409, detail="Liepin login has not been verified.") from exc
        raise HTTPException(status_code=409, detail="Liepin login relay is not available.") from exc
    observed_subject = result.provider_account_hash
    if observed_subject is None or connection.compliance_gate_ref is None:
        raise HTTPException(status_code=409, detail="Liepin account identity could not be verified.")
    provider_account_hash = bind_observed_liepin_account(
        settings=_workbench_app_settings(request),
        user=user,
        connection_id=connection_id,
        compliance_gate_ref=connection.compliance_gate_ref,
        observed_provider_account_subject=observed_subject,
    )
    if provider_account_hash is None:
        raise HTTPException(status_code=409, detail="Liepin account identity could not be bound.")
    updated = store.mark_liepin_connection_connected_without_source_runs(
        user=user,
        connection_id=connection_id,
        provider_account_hash=provider_account_hash,
        compliance_gate_ref=connection.compliance_gate_ref,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return source_connection_response(updated)


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
            probe = await _ensure_liepin_browser_session_ready_for_start(
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


@router.get("/api/workbench/security-audit-events", response_model=WorkbenchSecurityAuditEventListResponse)
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


def _liepin_worker_client(request: Request):
    client = getattr(request.app.state, "liepin_worker_client", None)
    if client is not None:
        return client
    runner = getattr(request.app.state, "workbench_job_runner", None)
    runner_client = getattr(runner, "liepin_worker_client", None)
    if runner_client is not None:
        return runner_client
    app_settings = getattr(request.app.state, "settings", None)
    if app_settings is None:
        raise HTTPException(status_code=500, detail="Liepin worker settings are not available.")
    return build_liepin_worker_client(app_settings)


@dataclass(frozen=True)
class _LiepinStartProbeResult:
    ready: bool
    reason_code: str | None = None
    warning_message: str | None = None


def _liepin_probe_unavailable_result() -> _LiepinStartProbeResult:
    return _LiepinStartProbeResult(
        ready=False,
        reason_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
        warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
    )


def _liepin_probe_login_required_result() -> _LiepinStartProbeResult:
    return _LiepinStartProbeResult(
        ready=False,
        reason_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
        warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    )


def _liepin_probe_account_mismatch_result() -> _LiepinStartProbeResult:
    return _LiepinStartProbeResult(
        ready=False,
        reason_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
        warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
    )


def _workbench_app_settings(request: Request) -> AppSettings:
    app_settings = getattr(request.app.state, "settings", None)
    if app_settings is None:
        raise HTTPException(status_code=500, detail="Workbench settings are not available.")
    return app_settings


def _require_legacy_liepin_login_relay_enabled(request: Request) -> None:
    if not _workbench_app_settings(request).workbench_legacy_liepin_login_relay_enabled:
        raise HTTPException(status_code=410, detail="liepin_legacy_login_relay_disabled")


async def _refresh_liepin_opencli_connection_if_ready(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
) -> WorkbenchSourceConnection | None:
    settings = _workbench_app_settings(request)
    if settings.liepin_browser_action_backend != "opencli":
        return None
    connection = next(
        (candidate for candidate in store.list_source_connections(user=user) if candidate.source_kind == "liepin"),
        None,
    )
    if connection is None or connection.status == "connected":
        return connection
    try:
        await _liepin_worker_client(request).ensure_ready()
        if not connection.provider_account_hash:
            return connection
        return store.mark_liepin_connection_connected(
            user=user,
            connection_id=connection.connection_id,
            provider_account_hash=connection.provider_account_hash,
            compliance_gate_ref=connection.compliance_gate_ref,
        )
    except (LiepinWorkerModeError, OSError, RuntimeError, ValueError):
        return connection


async def _ensure_liepin_browser_session_ready_for_start(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    source_run_id: str,
) -> _LiepinStartProbeResult:
    connection, _created = store.get_or_create_liepin_source_connection(user=user)
    settings = _workbench_app_settings(request)
    try:
        worker_client = _liepin_worker_client(request)
        await worker_client.ensure_ready()
        if settings.liepin_browser_action_backend == "opencli":
            if not connection.provider_account_hash or not connection.compliance_gate_ref:
                if store.mark_liepin_connection_login_required(
                    user=user,
                    connection_id=connection.connection_id,
                    warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
                    warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
                    session_id=session_id,
                    source_run_id=source_run_id,
                ) is None:
                    return _LiepinStartProbeResult(ready=True)
                return _liepin_probe_unavailable_result()
            status = await worker_client.session_status(
                connection_id=connection.connection_id,
                tenant=DEFAULT_TENANT_ID,
                workspace=user.workspace_id,
                provider_account_hash=connection.provider_account_hash,
            )
            if status.status != "ready":
                if store.mark_liepin_connection_login_required(
                    user=user,
                    connection_id=connection.connection_id,
                    warning_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
                    warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
                    session_id=session_id,
                    source_run_id=source_run_id,
                ) is None:
                    return _LiepinStartProbeResult(ready=True)
                return _liepin_probe_login_required_result()
            if status.provider_account_hash != connection.provider_account_hash:
                if store.mark_liepin_connection_login_required(
                    user=user,
                    connection_id=connection.connection_id,
                    warning_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
                    warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
                    session_id=session_id,
                    source_run_id=source_run_id,
                ) is None:
                    return _LiepinStartProbeResult(ready=True)
                return _liepin_probe_account_mismatch_result()
            updated_connection = store.mark_liepin_connection_connected_for_source_run(
                user=user,
                connection_id=connection.connection_id,
                session_id=session_id,
                source_run_id=source_run_id,
                provider_account_hash=connection.provider_account_hash,
                compliance_gate_ref=connection.compliance_gate_ref,
            )
            if updated_connection is None:
                store.block_source_run_for_start_probe(
                    user=user,
                    session_id=session_id,
                    source_run_id=source_run_id,
                    warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
                    warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
                )
                return _liepin_probe_unavailable_result()
            return _LiepinStartProbeResult(ready=True)
        status: SessionStatus = await worker_client.session_status(
            connection_id=connection.connection_id,
            tenant=DEFAULT_TENANT_ID,
            workspace=user.workspace_id,
            provider_account_hash=connection.provider_account_hash,
        )
    except LiepinWorkerModeError as exc:
        reason = _liepin_start_probe_error_reason(exc)
        if reason == LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE:
            reason = _liepin_dev_mode_setup_reason(request) or reason
        warning_message = liepin_start_probe_warning_message(reason)
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=reason,
            warning_message=warning_message,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return _LiepinStartProbeResult(ready=True)
        return _LiepinStartProbeResult(ready=False, reason_code=reason, warning_message=warning_message)
    except (OSError, RuntimeError, ValueError):
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return _LiepinStartProbeResult(ready=True)
        return _liepin_probe_unavailable_result()

    if status.status != "ready":
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
            warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return _LiepinStartProbeResult(ready=True)
        return _liepin_probe_login_required_result()
    if not status.provider_account_hash:
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return _LiepinStartProbeResult(ready=True)
        return _liepin_probe_unavailable_result()
    if connection.provider_account_hash and connection.provider_account_hash != status.provider_account_hash:
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
            warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return _LiepinStartProbeResult(ready=True)
        return _liepin_probe_account_mismatch_result()

    compliance_gate_ref = ensure_workbench_liepin_provider_connection(
        settings=settings,
        user=user,
        connection=connection,
    )
    provider_account_hash = bind_observed_liepin_account(
        settings=settings,
        user=user,
        connection_id=connection.connection_id,
        compliance_gate_ref=compliance_gate_ref,
        observed_provider_account_subject=status.provider_account_hash,
    )
    if provider_account_hash is None:
        return _liepin_probe_account_mismatch_result()
    updated_connection = store.mark_liepin_connection_connected_for_source_run(
        user=user,
        connection_id=connection.connection_id,
        session_id=session_id,
        source_run_id=source_run_id,
        provider_account_hash=provider_account_hash,
        compliance_gate_ref=compliance_gate_ref,
    )
    if updated_connection is None:
        store.block_source_run_for_start_probe(
            user=user,
            session_id=session_id,
            source_run_id=source_run_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
        )
        return _liepin_probe_unavailable_result()
    return _LiepinStartProbeResult(ready=True)


def _liepin_start_probe_error_reason(exc: LiepinWorkerModeError) -> str:
    code = str(exc.code or "").strip()
    if code in RUNTIME_SOURCE_REASON_CODES and (
        code.startswith("liepin_browser_") or code.startswith("liepin_opencli_")
    ):
        return code
    return LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE


def _liepin_dev_mode_setup_reason(request: Request) -> str | None:
    diagnostics = getattr(request.app.state, "dev_mode_env_diagnostics", None)
    if diagnostics is None:
        try:
            diagnostics = build_dev_mode_status(_workbench_app_settings(request))
        except (AttributeError, TypeError, ValueError):
            return None
    components = getattr(diagnostics, "components", ())
    for component in components:
        code = getattr(component, "reasonCode", None)
        if (
            isinstance(code, str)
            and code in RUNTIME_SOURCE_REASON_CODES
            and code.startswith("liepin_opencli_")
            and code != "liepin_opencli_backend_disabled"
        ):
            return code
    return None


def _login_frame_html(connection_id: str) -> str:
    snapshot_url = f"/api/workbench/source-connections/{connection_id}/login/snapshot"
    input_url = f"/api/workbench/source-connections/{connection_id}/login/input"
    complete_url = f"/api/workbench/source-connections/{connection_id}/login/complete"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>猎聘登录</title>
  <style>
    :root {{
      color-scheme: light;
      --paper: #f4efe6;
      --ink: #25211b;
      --muted: #777067;
      --line: #d8d0c3;
      --accent: #2f6b4f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--paper);
      color: var(--ink);
      font: 13px/1.4 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-height: 100vh;
      border: 1px solid var(--line);
    }}
    header, footer {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      background: rgba(255, 255, 255, 0.36);
      border-bottom: 1px solid var(--line);
    }}
    footer {{
      border-top: 1px solid var(--line);
      border-bottom: 0;
      flex-wrap: wrap;
    }}
    strong {{ font-size: 14px; }}
    #state {{ color: var(--muted); }}
    #viewport {{
      min-height: 0;
      display: grid;
      place-items: center;
      padding: 12px;
      background: #e9e2d8;
    }}
    #view {{
      max-width: 100%;
      max-height: calc(100vh - 116px);
      border: 1px solid #cfc6b8;
      background: #fff;
      cursor: crosshair;
    }}
    input {{
      min-width: 220px;
      flex: 1 1 280px;
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 8px 9px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--ink);
    }}
    button {{
      border: 1px solid #bfb5a6;
      border-radius: 4px;
      padding: 8px 10px;
      background: #fffaf1;
      color: var(--ink);
      cursor: pointer;
    }}
    button.primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <strong>猎聘登录</strong>
      <span id="state">初始化</span>
    </header>
    <section id="viewport">
      <img id="view" alt="猎聘登录画面" />
    </section>
    <footer>
      <input id="text" autocomplete="off" placeholder="输入文字后发送到登录页" />
      <button id="type" type="button">输入</button>
      <button id="enter" type="button">Enter</button>
      <button id="refresh" type="button">刷新画面</button>
      <button id="done" class="primary" type="button">我已完成登录</button>
    </footer>
  </main>
  <script>
    const snapshotUrl = {json.dumps(snapshot_url)};
    const inputUrl = {json.dumps(input_url)};
    const completeUrl = {json.dumps(complete_url)};
    const image = document.getElementById("view");
    const state = document.getElementById("state");
    const text = document.getElementById("text");

    function csrfHeader() {{
      const row = document.cookie.split("; ").find((item) => item.startsWith("seektalent_workbench_csrf="));
      return row ? decodeURIComponent(row.split("=")[1]) : "";
    }}

    async function postJson(url, body) {{
      const response = await fetch(url, {{
        method: "POST",
        headers: {{"Content-Type": "application/json", "X-CSRF-Token": csrfHeader()}},
        body: JSON.stringify(body),
      }});
      if (!response.ok) throw new Error("request failed");
      return response.json();
    }}

    async function refresh() {{
      state.textContent = "刷新中";
      const response = await fetch(snapshotUrl, {{credentials: "same-origin"}});
      if (!response.ok) {{
        state.textContent = "无法获取画面";
        return;
      }}
      const payload = await response.json();
      image.src = `data:${{payload.imageMimeType}};base64,${{payload.imageBase64}}`;
      state.textContent = `${{payload.status}} · ${{payload.pageOrigin}}`;
    }}

    image.addEventListener("click", async (event) => {{
      const rect = image.getBoundingClientRect();
      const x = ((event.clientX - rect.left) * image.naturalWidth) / rect.width;
      const y = ((event.clientY - rect.top) * image.naturalHeight) / rect.height;
      await postJson(inputUrl, {{action: "click", x, y}});
      await refresh();
    }});

    document.getElementById("type").addEventListener("click", async () => {{
      if (!text.value) return;
      await postJson(inputUrl, {{action: "type", text: text.value}});
      text.value = "";
      await refresh();
    }});

    document.getElementById("enter").addEventListener("click", async () => {{
      await postJson(inputUrl, {{action: "key", key: "Enter"}});
      await refresh();
    }});

    document.getElementById("refresh").addEventListener("click", refresh);
    document.getElementById("done").addEventListener("click", async () => {{
      state.textContent = "确认中";
      const response = await fetch(completeUrl, {{
        method: "POST",
        headers: {{"X-CSRF-Token": csrfHeader()}},
      }});
      state.textContent = response.ok ? "已连接，可以返回工作台" : "确认失败";
    }});

    void refresh();
  </script>
</body>
</html>"""


def _runtime_source_state_response(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session: WorkbenchSession,
    runtime_source_count_projection: Mapping[Literal["cts", "liepin"], RuntimeSourceCountProjection] | None = None,
) -> WorkbenchRuntimeSourceStateResponse:
    latest_states = store.list_runtime_source_lane_latest_state(user=user, session_id=session.session_id)
    latest_by_source = {state.source_kind: state for state in latest_states}
    if runtime_source_count_projection is None:
        runtime_source_count_projection = store.latest_runtime_source_count_projection(
            user=user,
            session_id=session.session_id,
        )
    sources = [
        _runtime_source_lane_state_response(
            source_run,
            latest_by_source.get(source_run.source_kind),
            runtime_source_count_projection.get(source_run.source_kind),
        )
        for source_run in session.source_runs
    ]
    coverage_status, revision, reason_code = _runtime_source_coverage_fields(session, latest_states, sources)
    return WorkbenchRuntimeSourceStateResponse(
        selectedSourceKinds=[source_run.source_kind for source_run in session.source_runs],
        coverageStatus=coverage_status,
        finalizationRevision=revision,
        finalizationReasonCode=reason_code,
        identityMergeCount=_runtime_source_merge_count(latest_states, "identity_merge_count"),
        ambiguousDuplicateCount=_runtime_source_merge_count(latest_states, "ambiguous_duplicate_count"),
        canonicalResumeSelectedCount=_runtime_source_merge_count(latest_states, "canonical_resume_selected_count"),
        sources=sources,
    )


def _runtime_source_lane_state_response(
    source_run: WorkbenchSourceRun,
    latest_state: WorkbenchRuntimeSourceLaneLatestState | None,
    runtime_count_projection: RuntimeSourceCountProjection | None = None,
) -> WorkbenchRuntimeSourceLaneStateResponse:
    payload = latest_state.payload if latest_state is not None else {}
    safe_counts = payload.get("safe_counts")
    if not isinstance(safe_counts, dict):
        safe_counts = {}
    typed_safe_counts = cast(dict[str, object], safe_counts)
    use_runtime_projection = (
        runtime_count_projection is not None
        and runtime_count_projection.status is not None
        and (latest_state is None or runtime_count_projection.event_seq >= latest_state.event_seq)
    )
    status_source = (
        runtime_count_projection.status
        if use_runtime_projection and runtime_count_projection is not None
        else latest_state.status if latest_state is not None else source_run.status
    )
    status = str(status_source or "pending")
    if status == "queued":
        status = "pending"
    if status not in {"pending", "running", "completed", "partial", "blocked", "failed", "cancelled"}:
        status = "pending"
    display_status = cast(RuntimeSourceDisplayStatus, status)
    if latest_state is not None and not use_runtime_projection:
        source_run_reason_fallback = (
            source_run.warning_code if latest_state.status in {"blocked", "failed", "cancelled"} else None
        )
        reason_code = _runtime_source_reason_code(
            payload.get("safe_reason_code"),
            payload.get("blocked_reason_code"),
            payload.get("stop_reason_code"),
            source_run_reason_fallback,
        )
        event_type = latest_state.event_type
        event_seq = latest_state.event_seq
    elif runtime_count_projection is not None and runtime_count_projection.status is not None:
        reason_code = _runtime_source_reason_code(runtime_count_projection.warning_code)
        event_type = "source_result"
        event_seq = runtime_count_projection.event_seq
    else:
        reason_code = _runtime_source_reason_code(source_run.warning_code)
        event_type = None
        event_seq = None
    cards_scanned_fallback = (
        runtime_count_projection.cards_scanned_count
        if runtime_count_projection is not None and runtime_count_projection.cards_scanned_count is not None
        else source_run.cards_scanned_count
    )
    unique_candidates_fallback = (
        runtime_count_projection.unique_candidates_count
        if runtime_count_projection is not None and runtime_count_projection.unique_candidates_count is not None
        else source_run.unique_candidates_count
    )
    return WorkbenchRuntimeSourceLaneStateResponse(
        sourceKind=source_run.source_kind,
        status=display_status,
        reasonCode=reason_code,
        eventType=event_type,
        eventSeq=event_seq,
        cardsSeenCount=_safe_count(typed_safe_counts.get("cards_seen"), fallback=cards_scanned_fallback),
        cardsFilteredCount=_safe_count(typed_safe_counts.get("cards_filtered"), fallback=0),
        candidatesCount=_safe_count(typed_safe_counts.get("candidates"), fallback=unique_candidates_fallback),
        detailRecommendationsCount=_safe_count(typed_safe_counts.get("detail_recommendations"), fallback=0),
        detailState=_runtime_source_detail_state(latest_state, typed_safe_counts=typed_safe_counts),
        latestWorkflowStep=_latest_workflow_step_response(latest_state),
    )


def _runtime_source_reason_code(*values: object) -> str | None:
    for value in values:
        text = str(value).strip() if value is not None else ""
        public_code = public_runtime_source_reason_code(text)
        if public_code is not None:
            return public_code
    return None


def _runtime_source_coverage_fields(
    session: WorkbenchSession,
    latest_states: list[WorkbenchRuntimeSourceLaneLatestState],
    sources: list[WorkbenchRuntimeSourceLaneStateResponse],
) -> tuple[RuntimeSourceCoverageStatus, int | None, str | None]:
    for state in sorted(latest_states, key=lambda item: item.event_seq, reverse=True):
        coverage = state.payload.get("source_coverage_summary")
        finalization = state.payload.get("finalization_revision")
        if isinstance(coverage, dict):
            typed_coverage = cast(dict[str, object], coverage)
            status = str(typed_coverage.get("status") or "")
            if status in {"complete", "degraded", "empty"}:
                typed_finalization = cast(dict[str, object], finalization) if isinstance(finalization, dict) else None
                revision = _safe_int(typed_finalization.get("revision")) if typed_finalization is not None else None
                reason = str(typed_finalization.get("reason_code")) if typed_finalization is not None else None
                return cast(RuntimeSourceCoverageStatus, status), revision, reason

    source_statuses = {source.status for source in sources}
    if source_statuses.intersection({"running", "pending"}) or any(run.status == "queued" for run in session.source_runs):
        return "pending", None, None
    if all(source.status == "completed" for source in sources):
        if any(source.candidatesCount for source in sources):
            return "complete", None, None
        return "empty", None, None
    if source_statuses.intersection({"partial", "blocked", "failed", "cancelled"}):
        return "degraded", None, None
    return "pending", None, None


def _runtime_source_detail_state(
    latest_state: WorkbenchRuntimeSourceLaneLatestState | None,
    *,
    typed_safe_counts: Mapping[str, object] | None = None,
) -> RuntimeSourceDetailState | None:
    if latest_state is None or latest_state.source_kind != "liepin":
        return None
    if _safe_count((typed_safe_counts or {}).get("detail_recommendations"), fallback=0) > 0:
        return "detail_recommended"
    payload_value = latest_state.payload.get("detail_state")
    if isinstance(payload_value, str) and payload_value in {
        "detail_recommended",
        "pending_approval",
        "leased",
        "completed",
        "blocked",
    }:
        return cast(RuntimeSourceDetailState, payload_value)
    if latest_state.event_type == "detail_recommended":
        return "detail_recommended"
    if latest_state.event_type == "detail_leased":
        return "leased"
    if latest_state.event_type == "detail_completed":
        return "completed"
    if latest_state.event_type == "detail_blocked":
        return "blocked"
    return None


def _latest_workflow_step_response(
    latest_state: WorkbenchRuntimeSourceLaneLatestState | None,
) -> WorkbenchRuntimeSourceWorkflowStepResponse | None:
    if latest_state is None or not latest_state.event_type.startswith("source_workflow_step_"):
        return None
    payload = latest_state.payload
    step_name = payload.get("step_name")
    if not isinstance(step_name, str) or not step_name.strip():
        return None
    safe_counts = payload.get("safe_counts")
    typed_counts = cast(dict[str, object], safe_counts) if isinstance(safe_counts, dict) else {}
    status = str(payload.get("status") or latest_state.status or "")
    return WorkbenchRuntimeSourceWorkflowStepResponse(
        eventType=latest_state.event_type,
        stepName=step_name.strip(),
        status=cast(RuntimeSourceDisplayStatus, status)
        if status in {"pending", "running", "completed", "partial", "blocked", "failed", "cancelled"}
        else None,
        safeCounts={
            str(key): value
            for key, value in typed_counts.items()
            if isinstance(value, int) and not isinstance(value, bool)
        },
        safeReasonCode=_runtime_source_reason_code(payload.get("safe_reason_code")),
    )


def _runtime_source_merge_count(
    latest_states: list[WorkbenchRuntimeSourceLaneLatestState],
    key: str,
) -> int:
    for state in sorted(latest_states, key=lambda item: item.event_seq, reverse=True):
        merge_summary = state.payload.get("merge_summary")
        if not isinstance(merge_summary, dict):
            continue
        typed_merge_summary = cast(dict[str, object], merge_summary)
        value = _safe_int(typed_merge_summary.get(key))
        if value is not None:
            return max(value, 0)
    if key == "canonical_resume_selected_count":
        for state in sorted(latest_states, key=lambda item: item.event_seq, reverse=True):
            finalization = state.payload.get("finalization_revision")
            if not isinstance(finalization, dict):
                continue
            typed_finalization = cast(dict[str, object], finalization)
            candidate_ids = typed_finalization.get("candidate_identity_ids")
            if isinstance(candidate_ids, list):
                return len(candidate_ids)
    return 0


def _safe_count(value: object, *, fallback: int = 0) -> int:
    parsed = _safe_int(value)
    if parsed is None:
        return fallback
    return max(parsed, 0)


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


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
