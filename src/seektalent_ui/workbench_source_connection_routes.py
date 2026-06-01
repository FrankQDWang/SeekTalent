from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from seektalent.config import AppSettings
from seektalent.dev_mode import build_dev_mode_status
from seektalent.providers.liepin.client import build_liepin_worker_client
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError, SessionStatus
from seektalent_ui.auth import (
    get_workbench_store,
    require_csrf_user,
    require_current_user,
)
from seektalent_ui.liepin_account_binding import (
    bind_observed_liepin_account,
    ensure_workbench_liepin_provider_connection,
)
from seektalent_ui.workbench_liepin_login_frame import login_frame_html
from seektalent_ui.models import (
    WorkbenchLiepinLoginHandoffResponse,
    WorkbenchLiepinLoginRelayInputRequest,
    WorkbenchSourceConnectionListResponse,
    WorkbenchSourceConnectionResponse,
)
from seektalent_ui.workbench_response import (
    liepin_start_probe_warning_message,
    source_connection_response,
)
from seektalent_ui.workbench_store import (
    DEFAULT_TENANT_ID,
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
    WorkbenchSourceConnection,
    WorkbenchStore,
    WorkbenchUser,
)


router = APIRouter()

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

RECOVERABLE_LIEPIN_BROWSER_CHANNEL_CODES = frozenset(
    {
        LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
        "blocked_backend_unavailable",
        "liepin_opencli_command_missing",
        "liepin_opencli_daemon_not_running",
        "liepin_opencli_daemon_stale",
        "liepin_opencli_extension_disconnected",
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
    user: WorkbenchUser = Depends(require_current_user),
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
    user: WorkbenchUser = Depends(require_current_user),
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
    user: WorkbenchUser = Depends(require_current_user),
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
    user: WorkbenchUser = Depends(require_csrf_user),
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
    user: WorkbenchUser = Depends(require_csrf_user),
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


def liepin_worker_client(request: Request):
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
class LiepinStartProbeResult:
    ready: bool
    reason_code: str | None = None
    warning_message: str | None = None


def liepin_probe_unavailable_result() -> LiepinStartProbeResult:
    return LiepinStartProbeResult(
        ready=False,
        reason_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
        warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
    )


def liepin_probe_login_required_result() -> LiepinStartProbeResult:
    return LiepinStartProbeResult(
        ready=False,
        reason_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
        warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    )


def liepin_probe_account_mismatch_result() -> LiepinStartProbeResult:
    return LiepinStartProbeResult(
        ready=False,
        reason_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
        warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
    )


def workbench_app_settings(request: Request) -> AppSettings:
    app_settings = getattr(request.app.state, "settings", None)
    if app_settings is None:
        raise HTTPException(status_code=500, detail="Workbench settings are not available.")
    return app_settings


def require_legacy_liepin_login_relay_enabled(request: Request) -> None:
    if not workbench_app_settings(request).workbench_legacy_liepin_login_relay_enabled:
        raise HTTPException(status_code=410, detail="liepin_legacy_login_relay_disabled")


async def refresh_liepin_opencli_connection_if_ready(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
) -> WorkbenchSourceConnection | None:
    settings = workbench_app_settings(request)
    if settings.liepin_browser_action_backend != "opencli":
        return None
    connection = next(
        (candidate for candidate in store.list_source_connections(user=user) if candidate.source_kind == "liepin"),
        None,
    )
    if connection is None or connection.status == "connected":
        return connection
    try:
        await liepin_worker_client(request).ensure_ready()
        if not connection.provider_account_hash:
            return connection
        return store.mark_liepin_connection_connected_without_source_runs(
            user=user,
            connection_id=connection.connection_id,
            provider_account_hash=connection.provider_account_hash,
            compliance_gate_ref=connection.compliance_gate_ref,
        )
    except (LiepinWorkerModeError, OSError, RuntimeError, ValueError):
        return connection


async def ensure_liepin_browser_session_ready_for_start(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    source_run_id: str,
) -> LiepinStartProbeResult:
    connection, _created = store.get_or_create_liepin_source_connection(user=user)
    settings = workbench_app_settings(request)
    try:
        worker_client = liepin_worker_client(request)
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
                    return LiepinStartProbeResult(ready=True)
                return liepin_probe_unavailable_result()
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
                    return LiepinStartProbeResult(ready=True)
                return liepin_probe_login_required_result()
            if status.provider_account_hash != connection.provider_account_hash:
                if store.mark_liepin_connection_login_required(
                    user=user,
                    connection_id=connection.connection_id,
                    warning_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
                    warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
                    session_id=session_id,
                    source_run_id=source_run_id,
                ) is None:
                    return LiepinStartProbeResult(ready=True)
                return liepin_probe_account_mismatch_result()
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
                return liepin_probe_unavailable_result()
            return LiepinStartProbeResult(ready=True)
        status: SessionStatus = await worker_client.session_status(
            connection_id=connection.connection_id,
            tenant=DEFAULT_TENANT_ID,
            workspace=user.workspace_id,
            provider_account_hash=connection.provider_account_hash,
        )
    except LiepinWorkerModeError as exc:
        reason = liepin_start_probe_error_reason(exc)
        if reason == LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE:
            reason = liepin_dev_mode_setup_reason(request) or reason
        warning_message = liepin_start_probe_warning_message(reason)
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=reason,
            warning_message=warning_message,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return LiepinStartProbeResult(ready=True)
        return LiepinStartProbeResult(ready=False, reason_code=reason, warning_message=warning_message)
    except (OSError, RuntimeError, ValueError):
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return LiepinStartProbeResult(ready=True)
        return liepin_probe_unavailable_result()

    if status.status != "ready":
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
            warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return LiepinStartProbeResult(ready=True)
        return liepin_probe_login_required_result()
    if not status.provider_account_hash:
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return LiepinStartProbeResult(ready=True)
        return liepin_probe_unavailable_result()
    if connection.provider_account_hash and connection.provider_account_hash != status.provider_account_hash:
        if store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
            warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ) is None:
            return LiepinStartProbeResult(ready=True)
        return liepin_probe_account_mismatch_result()

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
        return liepin_probe_account_mismatch_result()
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
        return liepin_probe_unavailable_result()
    return LiepinStartProbeResult(ready=True)


def liepin_start_probe_error_reason(exc: LiepinWorkerModeError) -> str:
    code = str(exc.code or "").strip()
    if code in RUNTIME_SOURCE_REASON_CODES and (
        code.startswith("liepin_browser_") or code.startswith("liepin_opencli_")
    ):
        return code
    return LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE


def liepin_dev_mode_setup_reason(request: Request) -> str | None:
    diagnostics = getattr(request.app.state, "dev_mode_env_diagnostics", None)
    if diagnostics is None:
        try:
            diagnostics = build_dev_mode_status(workbench_app_settings(request))
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
