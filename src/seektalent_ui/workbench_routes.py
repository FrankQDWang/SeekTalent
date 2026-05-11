from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse

from seektalent.providers.liepin.client import build_liepin_worker_client
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent_ui.auth import (
    DUMMY_PASSWORD_HASH,
    clear_session_cookie,
    get_session_cookie,
    get_workbench_store,
    hash_password,
    is_loopback_client,
    require_csrf_user,
    require_current_user,
    session_token_digest,
    set_csrf_cookie,
    set_session_cookie,
    verify_password,
)
from seektalent_ui.models import (
    WorkbenchBootstrapRequest,
    WorkbenchBootstrapResponse,
    WorkbenchCandidateEvidenceResponse,
    WorkbenchCandidateReviewItemResponse,
    WorkbenchCandidateReviewItemUpdateRequest,
    WorkbenchCandidateReviewQueueResponse,
    WorkbenchDetailOpenCandidateSnapshotResponse,
    WorkbenchDetailOpenRequestStatus,
    WorkbenchDetailOpenRejectRequest,
    WorkbenchDetailOpenRequestCreateRequest,
    WorkbenchDetailOpenRequestListResponse,
    WorkbenchDetailOpenRequestResponse,
    WorkbenchDetailOpenLedgerResponse,
    WorkbenchLiepinLoginHandoffResponse,
    WorkbenchLiepinLoginRelayInputRequest,
    WorkbenchLoginRequest,
    WorkbenchMeResponse,
    WorkbenchProviderActionResponse,
    WorkbenchRequirementTriageResponse,
    WorkbenchRequirementTriageUpdateRequest,
    WorkbenchSecurityAuditEventListResponse,
    WorkbenchSecurityAuditEventResponse,
    WorkbenchSessionCreateRequest,
    WorkbenchSessionListResponse,
    WorkbenchSessionResponse,
    WorkbenchSessionStartBlockedSourceResponse,
    WorkbenchSessionStartResponse,
    WorkbenchSettingsResponse,
    WorkbenchSettingsSourceResponse,
    WorkbenchSourceCardResponse,
    WorkbenchSourceConnectionListResponse,
    WorkbenchSourceConnectionResponse,
    WorkbenchSourceRunPolicyResponse,
    WorkbenchSourceRunPolicyUpdateRequest,
    WorkbenchSourceRunJobResponse,
    WorkbenchSourceRunResponse,
    WorkbenchSourceRunStartResponse,
    WorkbenchUserResponse,
    WorkbenchWorkspaceResponse,
)
from seektalent_ui.workbench_store import (
    BootstrapAlreadyCompleteError,
    WorkbenchCandidateEvidence,
    WorkbenchCandidateReviewItem,
    WorkbenchDetailOpenCandidateSnapshot,
    WorkbenchDetailOpenLedger,
    WorkbenchDetailOpenRequest,
    WorkbenchProviderAction,
    WorkbenchRequirementTriage,
    WorkbenchSecurityAuditEvent,
    WorkbenchSession,
    WorkbenchSourceConnection,
    WorkbenchSourceRun,
    WorkbenchSourceRunJob,
    WorkbenchSourceRunPolicy,
    WorkbenchUser,
    WorkbenchWorkspace,
    DEFAULT_TENANT_ID,
)


router = APIRouter()


@router.post("/api/auth/bootstrap", response_model=WorkbenchBootstrapResponse, status_code=201)
def bootstrap_admin(request: WorkbenchBootstrapRequest, http_request: Request) -> WorkbenchBootstrapResponse:
    if not is_loopback_client(http_request):
        raise HTTPException(status_code=403, detail="Bootstrap is only available from loopback clients.")
    store = get_workbench_store(http_request)
    try:
        user, workspace = store.bootstrap_admin(
            email=request.email,
            display_name=request.displayName,
            password_hash=hash_password(request.password),
        )
    except BootstrapAlreadyCompleteError as exc:
        raise HTTPException(status_code=409, detail="Bootstrap admin already exists.") from exc
    return WorkbenchBootstrapResponse(
        user=_user_response(user),
        workspace=_workspace_response(workspace),
    )


@router.post("/api/auth/login", status_code=204)
def login(request: WorkbenchLoginRequest, http_request: Request, response: Response) -> Response:
    store = get_workbench_store(http_request)
    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")
    login_row = store.get_user_for_login(email=request.email)
    if store.is_login_locked(email=request.email, ip_address=ip_address):
        password_hash = login_row[1] if login_row is not None else DUMMY_PASSWORD_HASH
        verify_password(request.password, password_hash)
        store.record_login_attempt(
            email=request.email,
            success=False,
            reason="locked_out",
            user_id=login_row[0].user_id if login_row is not None else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if login_row is None:
        verify_password(request.password, DUMMY_PASSWORD_HASH)
        store.record_login_attempt(
            email=request.email,
            success=False,
            reason="invalid_credentials",
            user_id=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    user, password_hash, disabled = login_row
    if disabled:
        verify_password(request.password, password_hash)
        store.record_login_attempt(
            email=request.email,
            success=False,
            reason="disabled_user",
            user_id=user.user_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not verify_password(request.password, password_hash):
        store.record_login_attempt(
            email=request.email,
            success=False,
            reason="invalid_credentials",
            user_id=user.user_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    session_tokens = store.create_user_session(user_id=user.user_id, workspace_id=user.workspace_id)
    store.record_login_attempt(
        email=request.email,
        success=True,
        reason="success",
        user_id=user.user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    set_session_cookie(response, request=http_request, session_id=session_tokens.session_token)
    set_csrf_cookie(response, request=http_request, csrf_token=session_tokens.csrf_token)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/api/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: WorkbenchUser = Depends(require_csrf_user),
    session_id: str | None = Depends(get_session_cookie),
) -> Response:
    store = get_workbench_store(request)
    store.revoke_user_session(
        session_digest=session_token_digest(session_id) if session_id is not None else None,
        user=user,
    )
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/api/auth/me", response_model=WorkbenchMeResponse)
def me(
    http_request: Request,
    response: Response,
    user: WorkbenchUser = Depends(require_current_user),
    session_id: str | None = Depends(get_session_cookie),
) -> WorkbenchMeResponse:
    if session_id is not None:
        store = get_workbench_store(http_request)
        csrf_token = store.rotate_session_csrf(session_digest=session_token_digest(session_id))
        set_csrf_cookie(response, request=http_request, csrf_token=csrf_token)
    return WorkbenchMeResponse(user=_user_response(user))


@router.get("/api/workbench/sessions", response_model=WorkbenchSessionListResponse)
def list_sessions(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSessionListResponse:
    store = get_workbench_store(request)
    connections: dict[str, WorkbenchSourceConnection] = {
        connection.source_kind: connection for connection in store.list_source_connections(user=user)
    }
    return WorkbenchSessionListResponse(
        sessions=[_session_response(session, connections) for session in store.list_workbench_sessions(user=user)]
    )


@router.post("/api/workbench/sessions", response_model=WorkbenchSessionResponse, status_code=201)
def create_session(
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
    return _session_response(session, connections)


@router.get("/api/workbench/sessions/{session_id}", response_model=WorkbenchSessionResponse)
def get_session(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSessionResponse:
    store = get_workbench_store(request)
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found.")
    connections: dict[str, WorkbenchSourceConnection] = {
        connection.source_kind: connection for connection in store.list_source_connections(user=user)
    }
    return _session_response(session, connections)


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
    return WorkbenchCandidateReviewQueueResponse(items=[_candidate_review_item_response(item) for item in items])


@router.get("/api/workbench/source-connections", response_model=WorkbenchSourceConnectionListResponse)
def list_source_connections(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSourceConnectionListResponse:
    store = get_workbench_store(request)
    return WorkbenchSourceConnectionListResponse(
        connections=[_source_connection_response(connection) for connection in store.list_source_connections(user=user)]
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
    return _source_connection_response(connection)


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
    return _source_connection_response(connection)


@router.post(
    "/api/workbench/source-connections/{connection_id}/login",
    response_model=WorkbenchLiepinLoginHandoffResponse,
)
async def start_liepin_connection_login(
    connection_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchLiepinLoginHandoffResponse:
    store = get_workbench_store(request)
    existing = store.get_source_connection(user=user, connection_id=connection_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Not found.")
    provider_account_hash = _workbench_provider_account_hash(user=user, connection_id=connection_id)
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
            provider_account_hash=provider_account_hash,
        )
    except LiepinWorkerModeError:
        safe_frame_url = None
        warning_code = "relay_pending_worker"
        warning_message = "Managed browser login relay is not configured or not ready."
        handoff_state = "relay_pending_worker"
    connection = store.start_liepin_login_handoff(
        user=user,
        connection_id=connection_id,
        provider_account_hash=provider_account_hash,
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
    updated = store.mark_liepin_connection_connected(
        user=user,
        connection_id=connection_id,
        provider_account_hash=result.provider_account_hash,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _source_connection_response(updated)


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
    return _candidate_review_item_response(item)


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
    return _provider_action_response(action)


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
    return _detail_open_request_response(detail_request)


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
            _detail_open_request_response(detail_request)
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
    return _detail_open_request_response(detail_request)


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
    return _detail_open_request_response(detail_request)


@router.get(
    "/api/workbench/sessions/{session_id}/triage",
    response_model=WorkbenchRequirementTriageResponse,
)
def get_requirement_triage(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchRequirementTriageResponse:
    store = get_workbench_store(request)
    triage = store.get_requirement_triage(user=user, session_id=session_id)
    if triage is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _triage_response(triage)


@router.put(
    "/api/workbench/sessions/{session_id}/triage",
    response_model=WorkbenchRequirementTriageResponse,
)
def update_requirement_triage(
    session_id: str,
    triage_update: WorkbenchRequirementTriageUpdateRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchRequirementTriageResponse:
    store = get_workbench_store(request)
    triage = store.update_requirement_triage(
        user=user,
        session_id=session_id,
        must_haves=triage_update.mustHaves,
        nice_to_haves=triage_update.niceToHaves,
        synonyms=triage_update.synonyms,
        seniority_filters=triage_update.seniorityFilters,
        exclusions=triage_update.exclusions,
        generated_query_hints=triage_update.generatedQueryHints,
    )
    if triage is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _triage_response(triage)


@router.post(
    "/api/workbench/sessions/{session_id}/triage/approve",
    response_model=WorkbenchRequirementTriageResponse,
)
def approve_requirement_triage(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchRequirementTriageResponse:
    store = get_workbench_store(request)
    triage = store.approve_requirement_triage(user=user, session_id=session_id)
    if triage is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _triage_response(triage)


@router.post(
    "/api/workbench/sessions/{session_id}/start",
    response_model=WorkbenchSessionStartResponse,
    status_code=202,
)
def start_session_source_runs(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> WorkbenchSessionStartResponse:
    store = get_workbench_store(request)
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if session.requirement_triage.status != "approved":
        raise HTTPException(status_code=409, detail="requirement_triage_not_approved")
    started: list[WorkbenchSourceRunStartResponse] = []
    blocked: list[WorkbenchSessionStartBlockedSourceResponse] = []
    for source_run in session.source_runs:
        if source_run.status in {"completed", "failed"}:
            continue
        try:
            result = store.start_source_run_job(
                user=user,
                session_id=session_id,
                source_run_id=source_run.source_run_id,
            )
        except PermissionError as exc:
            blocked.append(
                WorkbenchSessionStartBlockedSourceResponse(
                    sourceRunId=source_run.source_run_id,
                    sourceKind=source_run.source_kind,
                    reason=str(exc),
                )
            )
            continue
        except ValueError as exc:
            if str(exc) == "source_not_implemented":
                raise HTTPException(status_code=501, detail="Source run is not implemented in this slice.") from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            blocked.append(
                WorkbenchSessionStartBlockedSourceResponse(
                    sourceRunId=source_run.source_run_id,
                    sourceKind=source_run.source_kind,
                    reason=str(exc),
                )
            )
            continue
        if result is None:
            continue
        updated_source_run, job, _created = result
        started.append(
            WorkbenchSourceRunStartResponse(
                sessionId=session_id,
                sourceRunId=updated_source_run.source_run_id,
                sourceKind=updated_source_run.source_kind,
                status=updated_source_run.status,
                job=_job_response(job),
            )
        )
    runner = getattr(request.app.state, "workbench_job_runner", None)
    if runner is not None and started:
        runner.wake()
    return WorkbenchSessionStartResponse(
        sessionId=session_id,
        sourceRuns=started,
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
    return _source_run_policy_response(policy)


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
    return _source_run_policy_response(policy)


@router.get("/api/workbench/security-audit-events", response_model=WorkbenchSecurityAuditEventListResponse)
def list_security_audit_events(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchSecurityAuditEventListResponse:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    store = get_workbench_store(request)
    return WorkbenchSecurityAuditEventListResponse(
        events=[_security_audit_event_response(event) for event in store.list_security_audit_events_for_user(user=user)]
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
    app_settings = getattr(request.app.state, "settings", None)
    if app_settings is None:
        raise HTTPException(status_code=500, detail="Liepin worker settings are not available.")
    return build_liepin_worker_client(app_settings)


def _workbench_provider_account_hash(*, user: WorkbenchUser, connection_id: str) -> str:
    subject = f"{DEFAULT_TENANT_ID}:{user.workspace_id}:{user.user_id}:{connection_id}"
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
    return f"wb_{digest[:32]}"


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


def _user_response(user: WorkbenchUser) -> WorkbenchUserResponse:
    return WorkbenchUserResponse(
        userId=user.user_id,
        email=user.email,
        displayName=user.display_name,
        role=user.role,
        workspaceId=user.workspace_id,
    )


def _workspace_response(workspace: WorkbenchWorkspace) -> WorkbenchWorkspaceResponse:
    return WorkbenchWorkspaceResponse(id=workspace.workspace_id, name=workspace.name)


def _session_response(
    session: WorkbenchSession,
    connections: dict[str, WorkbenchSourceConnection] | None = None,
) -> WorkbenchSessionResponse:
    connections = connections or {}
    source_runs = [_source_run_response(source_run) for source_run in session.source_runs]
    source_cards = [_source_card_response(source_run, connections.get(source_run.source_kind)) for source_run in session.source_runs]
    return WorkbenchSessionResponse(
        sessionId=session.session_id,
        workspaceId=session.workspace_id,
        ownerUserId=session.owner_user_id,
        jobTitle=session.job_title,
        jdText=session.jd_text,
        notes=session.notes,
        status=session.status,
        requirementTriage=_triage_response(session.requirement_triage),
        sourceRuns=source_runs,
        sourceCards=source_cards,
    )


def _source_run_response(source_run: WorkbenchSourceRun) -> WorkbenchSourceRunResponse:
    return WorkbenchSourceRunResponse(
        sourceRunId=source_run.source_run_id,
        sourceKind=source_run.source_kind,
        status=source_run.status,
        authState=source_run.auth_state,
        warningCode=source_run.warning_code,
        warningMessage=source_run.warning_message,
        cardsScannedCount=source_run.cards_scanned_count,
        uniqueCandidatesCount=source_run.unique_candidates_count,
        detailOpenUsedCount=source_run.detail_open_used_count,
        detailOpenBlockedCount=source_run.detail_open_blocked_count,
    )


def _source_card_response(
    source_run: WorkbenchSourceRun,
    connection: WorkbenchSourceConnection | None = None,
) -> WorkbenchSourceCardResponse:
    return WorkbenchSourceCardResponse(
        sourceRunId=source_run.source_run_id,
        sourceKind=source_run.source_kind,
        label="CTS" if source_run.source_kind == "cts" else "Liepin",
        status=source_run.status,
        authState=source_run.auth_state,
        warningCode=source_run.warning_code,
        warningMessage=source_run.warning_message,
        cardsScannedCount=source_run.cards_scanned_count,
        uniqueCandidatesCount=source_run.unique_candidates_count,
        detailOpenUsedCount=source_run.detail_open_used_count,
        detailOpenBlockedCount=source_run.detail_open_blocked_count,
        connectionId=connection.connection_id if connection is not None else None,
        connectionStatus=connection.status if connection is not None else None,
        connectionWarningCode=connection.warning_code if connection is not None else None,
        connectionWarningMessage=connection.warning_message if connection is not None else None,
    )


def _source_connection_response(connection: WorkbenchSourceConnection) -> WorkbenchSourceConnectionResponse:
    return WorkbenchSourceConnectionResponse(
        connectionId=connection.connection_id,
        sourceKind=connection.source_kind,
        label="Liepin",
        status=connection.status,
        warningCode=connection.warning_code,
        warningMessage=connection.warning_message,
        createdAt=connection.created_at,
        updatedAt=connection.updated_at,
        connectedAt=connection.connected_at,
    )


def _source_run_policy_response(policy: WorkbenchSourceRunPolicy) -> WorkbenchSourceRunPolicyResponse:
    return WorkbenchSourceRunPolicyResponse(
        sessionId=policy.session_id,
        sourceKind=policy.source_kind,
        detailOpenMode=policy.detail_open_mode,
        updatedAt=policy.updated_at,
    )


def _detail_open_request_response(
    detail_request: WorkbenchDetailOpenRequest,
) -> WorkbenchDetailOpenRequestResponse:
    return WorkbenchDetailOpenRequestResponse(
        requestId=detail_request.request_id,
        sessionId=detail_request.session_id,
        reviewItemId=detail_request.review_item_id,
        status=detail_request.status,
        detailOpenMode=detail_request.detail_open_mode,
        decisionNote=detail_request.decision_note,
        candidate=(
            _detail_open_candidate_snapshot_response(detail_request.candidate)
            if detail_request.candidate is not None
            else None
        ),
        blockedReason=detail_request.blocked_reason,
        ledger=_detail_open_ledger_response(detail_request.ledger) if detail_request.ledger is not None else None,
        providerAction=(
            _provider_action_response(detail_request.provider_action)
            if detail_request.provider_action is not None
            else None
        ),
        createdAt=detail_request.created_at,
        updatedAt=detail_request.updated_at,
    )


def _detail_open_candidate_snapshot_response(
    candidate: WorkbenchDetailOpenCandidateSnapshot,
) -> WorkbenchDetailOpenCandidateSnapshotResponse:
    return WorkbenchDetailOpenCandidateSnapshotResponse(
        reviewItemId=candidate.review_item_id,
        displayName=candidate.display_name,
        title=candidate.title,
        company=candidate.company,
        location=candidate.location,
        summary=candidate.summary,
        aggregateScore=candidate.aggregate_score,
        evidenceLevel=candidate.evidence_level,
        sourceBadges=candidate.source_badges,
        matchedMustHaves=candidate.matched_must_haves,
        matchedPreferences=candidate.matched_preferences,
        missingRisks=candidate.missing_risks,
    )


def _detail_open_ledger_response(ledger: WorkbenchDetailOpenLedger) -> WorkbenchDetailOpenLedgerResponse:
    return WorkbenchDetailOpenLedgerResponse(
        ledgerId=ledger.ledger_id,
        status=ledger.status,
        budgetDay=ledger.budget_day,
        leaseExpiresAt=ledger.lease_expires_at,
    )


def _provider_action_response(action: WorkbenchProviderAction) -> WorkbenchProviderActionResponse:
    return WorkbenchProviderActionResponse(
        actionKind=action.action_kind,
        sourceKind=action.source_kind,
        connectionId=action.connection_id,
        reviewItemId=action.review_item_id,
        budgetImpact=action.budget_impact,
        message=action.message,
    )


def _triage_response(triage: WorkbenchRequirementTriage) -> WorkbenchRequirementTriageResponse:
    return WorkbenchRequirementTriageResponse(
        sessionId=triage.session_id,
        status=triage.status,
        mustHaves=triage.must_haves,
        niceToHaves=triage.nice_to_haves,
        synonyms=triage.synonyms,
        seniorityFilters=triage.seniority_filters,
        exclusions=triage.exclusions,
        generatedQueryHints=triage.generated_query_hints,
        createdAt=triage.created_at,
        updatedAt=triage.updated_at,
        approvedAt=triage.approved_at,
    )


def _candidate_review_item_response(item: WorkbenchCandidateReviewItem) -> WorkbenchCandidateReviewItemResponse:
    return WorkbenchCandidateReviewItemResponse(
        reviewItemId=item.review_item_id,
        sessionId=item.session_id,
        status=item.status,
        note=item.note,
        displayName=item.display_name,
        title=item.title,
        company=item.company,
        location=item.location,
        summary=item.summary,
        aggregateScore=item.aggregate_score,
        fitBucket=item.fit_bucket,
        sourceBadges=item.source_badges,
        evidenceLevel=item.evidence_level,
        matchedMustHaves=item.matched_must_haves,
        matchedPreferences=item.matched_preferences,
        missingRisks=item.missing_risks,
        strengths=item.strengths,
        weaknesses=item.weaknesses,
        evidence=[_candidate_evidence_response(evidence) for evidence in item.evidence],
        createdAt=item.created_at,
        updatedAt=item.updated_at,
    )


def _candidate_evidence_response(evidence: WorkbenchCandidateEvidence) -> WorkbenchCandidateEvidenceResponse:
    return WorkbenchCandidateEvidenceResponse(
        evidenceId=evidence.evidence_id,
        sourceRunId=evidence.source_run_id,
        sourceKind=evidence.source_kind,
        evidenceLevel=evidence.evidence_level,
        score=evidence.score,
        fitBucket=evidence.fit_bucket,
        matchedMustHaves=evidence.matched_must_haves,
        matchedPreferences=evidence.matched_preferences,
        missingRisks=evidence.missing_risks,
        strengths=evidence.strengths,
        weaknesses=evidence.weaknesses,
        createdAt=evidence.created_at,
    )


def _security_audit_event_response(event: WorkbenchSecurityAuditEvent) -> WorkbenchSecurityAuditEventResponse:
    return WorkbenchSecurityAuditEventResponse(
        auditId=event.audit_id,
        actorUserId=event.actor_user_id,
        actorRole=event.actor_role,
        workspaceId=event.workspace_id,
        requestIp=event.request_ip,
        userAgent=event.user_agent,
        targetType=event.target_type,
        targetId=event.target_id,
        action=event.action,
        result=event.result,
        reasonCode=event.reason_code,
        metadata=event.metadata,
        createdAt=event.created_at,
    )


def _job_response(job: WorkbenchSourceRunJob) -> WorkbenchSourceRunJobResponse:
    return WorkbenchSourceRunJobResponse(
        jobId=job.job_id,
        sourceRunId=job.source_run_id,
        status=job.status,
        attemptCount=job.attempt_count,
        errorMessage=job.error_message,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
    )
