from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent_ui.workbench_local_actor import (
    get_workbench_store,
    local_workbench_user,
    local_workbench_write_user,
)
from seektalent_ui.liepin_account_binding import (
    bind_observed_liepin_account,
    ensure_workbench_liepin_provider_connection,
)
from seektalent_ui.workbench_liepin_login_frame import login_frame_html
from seektalent_ui.workbench_liepin_start_probe import (
    liepin_worker_client,
    refresh_liepin_opencli_connection_if_ready,
    workbench_app_settings,
)
from seektalent_ui.models import (
    WorkbenchLiepinLoginHandoffResponse,
    WorkbenchLiepinLoginRelayInputRequest,
    WorkbenchSourceConnectionListResponse,
    WorkbenchSourceConnectionResponse,
)
from seektalent_ui.workbench_response import source_connection_response
from seektalent_ui.workbench_store import (
    DEFAULT_TENANT_ID,
    LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
    WorkbenchUser,
)


router = APIRouter()

RECOVERABLE_LIEPIN_BROWSER_CHANNEL_CODES = frozenset(
    {
        LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
        LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
        "blocked_backend_unavailable",
        "login_required",
        "liepin_opencli_command_missing",
        "liepin_opencli_daemon_not_running",
        "liepin_opencli_daemon_stale",
        "liepin_opencli_extension_disconnected",
        "liepin_opencli_login_required",
        "liepin_opencli_status_unavailable",
        "liepin_opencli_timeout",
        "source_browser_backend_unavailable",
        "source_browser_extension_disconnected",
        "source_browser_timeout",
    }
)


@router.get("/api/workbench/source-connections", response_model=WorkbenchSourceConnectionListResponse)
async def list_source_connections(
    request: Request,
    user: WorkbenchUser = Depends(local_workbench_user),
) -> WorkbenchSourceConnectionListResponse:
    store = get_workbench_store(request)
    await refresh_liepin_opencli_connection_if_ready(request=request, store=store, user=user)
    return WorkbenchSourceConnectionListResponse(
        connections=[source_connection_response(connection) for connection in store.list_source_connections(user=user)]
    )


@router.post(
    "/api/workbench/source-connections/liepin",
    response_model=WorkbenchSourceConnectionResponse,
    status_code=201,
)
async def create_liepin_source_connection(
    request: Request,
    response: Response,
    user: WorkbenchUser = Depends(local_workbench_write_user),
) -> WorkbenchSourceConnectionResponse:
    store = get_workbench_store(request)
    connection, created = store.get_or_create_liepin_source_connection(user=user)
    if not created:
        response.status_code = 200
    refreshed = await refresh_liepin_opencli_connection_if_ready(request=request, store=store, user=user)
    return source_connection_response(refreshed or connection)


@router.get(
    "/api/workbench/source-connections/{connection_id}",
    response_model=WorkbenchSourceConnectionResponse,
)
def get_source_connection(
    connection_id: str,
    request: Request,
    user: WorkbenchUser = Depends(local_workbench_user),
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
    user: WorkbenchUser = Depends(local_workbench_write_user),
) -> WorkbenchLiepinLoginHandoffResponse:
    require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    existing = store.get_source_connection(user=user, connection_id=connection_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Not found.")
    compliance_gate_ref = ensure_workbench_liepin_provider_connection(
        settings=workbench_app_settings(request),
        user=user,
        connection=existing,
    )
    safe_frame_url = f"/api/workbench/source-connections/{connection_id}/login/frame"
    warning_code: str | None = None
    warning_message: str | None = None
    handoff_state = "safe_frame_available"
    try:
        worker_client = liepin_worker_client(request)
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
    user: WorkbenchUser = Depends(local_workbench_user),
) -> HTMLResponse:
    require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    connection = store.get_source_connection(user=user, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return HTMLResponse(login_frame_html(connection_id))


@router.get("/api/workbench/source-connections/{connection_id}/login/snapshot")
async def liepin_connection_login_snapshot(
    connection_id: str,
    request: Request,
    user: WorkbenchUser = Depends(local_workbench_user),
) -> dict[str, object]:
    require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    connection = store.get_source_connection(user=user, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        snapshot = await liepin_worker_client(request).login_relay_snapshot(connection_id=connection_id)
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
    user: WorkbenchUser = Depends(local_workbench_write_user),
) -> dict[str, object]:
    require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    connection = store.get_source_connection(user=user, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        result = await liepin_worker_client(request).submit_login_relay_input(
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
    user: WorkbenchUser = Depends(local_workbench_write_user),
) -> WorkbenchSourceConnectionResponse:
    require_legacy_liepin_login_relay_enabled(request)
    store = get_workbench_store(request)
    connection = store.get_source_connection(user=user, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        result = await liepin_worker_client(request).complete_login_relay(connection_id=connection_id)
    except LiepinWorkerModeError as exc:
        if exc.setup_status == "login_not_verified":
            raise HTTPException(status_code=409, detail="Liepin login has not been verified.") from exc
        raise HTTPException(status_code=409, detail="Liepin login relay is not available.") from exc
    observed_subject = result.provider_account_hash
    if observed_subject is None or connection.compliance_gate_ref is None:
        raise HTTPException(status_code=409, detail="Liepin account identity could not be verified.")
    provider_account_hash = bind_observed_liepin_account(
        settings=workbench_app_settings(request),
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


def require_legacy_liepin_login_relay_enabled(request: Request) -> None:
    if not workbench_app_settings(request).workbench_legacy_liepin_login_relay_enabled:
        raise HTTPException(status_code=410, detail="liepin_legacy_login_relay_disabled")
