from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from seektalent.config import AppSettings
from seektalent.dev_mode import build_dev_mode_status
from seektalent.providers.liepin.client import build_liepin_worker_client
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError, SessionStatus
from seektalent_ui.liepin_account_binding import (
    bind_observed_liepin_account,
    ensure_workbench_liepin_provider_connection,
    refresh_workbench_liepin_provider_session_safety,
)
from seektalent_ui.workbench_response import liepin_start_probe_warning_message
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


@dataclass(frozen=True)
class LiepinStartProbeResult:
    ready: bool
    reason_code: str | None = None
    warning_message: str | None = None


def workbench_app_settings(request: Request) -> AppSettings:
    app_settings = getattr(request.app.state, "settings", None)
    if app_settings is None:
        raise HTTPException(status_code=500, detail="Workbench settings are not available.")
    return app_settings


def liepin_worker_client(request: Request):
    client = getattr(request.app.state, "liepin_worker_client", None)
    if client is not None:
        return client
    runner = getattr(request.app.state, "workbench_job_runner", None)
    runner_client = getattr(runner, "liepin_worker_client", None)
    if runner_client is not None:
        return runner_client
    return build_liepin_worker_client(workbench_app_settings(request))


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


async def refresh_liepin_opencli_connection_if_ready(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
    bind_unbound_account: bool = True,
) -> WorkbenchSourceConnection | None:
    settings = workbench_app_settings(request)
    if settings.liepin_browser_action_backend != "opencli":
        return None
    if bind_unbound_account:
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
    else:
        connection = next(
            (candidate for candidate in store.list_source_connections(user=user) if candidate.source_kind == "liepin"),
            None,
        )
    if connection is None or connection.status == "connected":
        return connection
    try:
        worker_client = liepin_worker_client(request)
        await worker_client.ensure_ready()
        if not connection.provider_account_hash:
            if not bind_unbound_account:
                return connection
            status: SessionStatus = await worker_client.session_status(
                connection_id=connection.connection_id,
                tenant=DEFAULT_TENANT_ID,
                workspace=user.workspace_id,
                provider_account_hash=None,
            )
            if status.status != "ready" or not status.provider_account_hash:
                return connection
            return (
                _bind_ready_liepin_connection(
                    settings=settings,
                    store=store,
                    user=user,
                    connection=connection,
                    observed_provider_account_subject=status.provider_account_hash,
                )
                or connection
            )
        compliance_gate_ref = ensure_workbench_liepin_provider_connection(
            settings=settings,
            user=user,
            connection=connection,
        )
        updated_connection = store.mark_liepin_connection_connected_without_source_runs(
            user=user,
            connection_id=connection.connection_id,
            provider_account_hash=connection.provider_account_hash,
            compliance_gate_ref=compliance_gate_ref,
        )
        if updated_connection is not None:
            refresh_workbench_liepin_provider_session_safety(
                settings=settings,
                user=user,
                connection=updated_connection,
            )
        return updated_connection
    except (LiepinWorkerModeError, OSError, RuntimeError, ValueError):
        return connection


def _bind_ready_liepin_connection(
    *,
    settings: AppSettings,
    store: WorkbenchStore,
    user: WorkbenchUser,
    connection: WorkbenchSourceConnection,
    observed_provider_account_subject: str,
    session_id: str | None = None,
    source_run_id: str | None = None,
) -> WorkbenchSourceConnection | None:
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
        observed_provider_account_subject=observed_provider_account_subject,
    )
    if provider_account_hash is None:
        return None
    if session_id is not None and source_run_id is not None:
        updated_connection = store.mark_liepin_connection_connected_for_source_run(
            user=user,
            connection_id=connection.connection_id,
            session_id=session_id,
            source_run_id=source_run_id,
            provider_account_hash=provider_account_hash,
            compliance_gate_ref=compliance_gate_ref,
        )
    else:
        updated_connection = store.mark_liepin_connection_connected_without_source_runs(
            user=user,
            connection_id=connection.connection_id,
            provider_account_hash=provider_account_hash,
            compliance_gate_ref=compliance_gate_ref,
        )
    if updated_connection is not None:
        refresh_workbench_liepin_provider_session_safety(
            settings=settings,
            user=user,
            connection=updated_connection,
        )
    return updated_connection


def _mark_login_required(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    connection_id: str,
    session_id: str,
    source_run_id: str,
    warning_code: str,
    warning_message: str,
) -> bool:
    return (
        store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection_id,
            warning_code=warning_code,
            warning_message=warning_message,
            session_id=session_id,
            source_run_id=source_run_id,
        )
        is not None
    )


def _block_start_probe(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    source_run_id: str,
) -> LiepinStartProbeResult:
    store.block_source_run_for_start_probe(
        user=user,
        session_id=session_id,
        source_run_id=source_run_id,
        warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
        warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
    )
    return liepin_probe_unavailable_result()


def _refresh_start_probe_safety(
    *,
    settings: AppSettings,
    store: WorkbenchStore,
    user: WorkbenchUser,
    connection: WorkbenchSourceConnection,
    session_id: str,
    source_run_id: str,
) -> LiepinStartProbeResult:
    if refresh_workbench_liepin_provider_session_safety(settings=settings, user=user, connection=connection):
        return LiepinStartProbeResult(ready=True)
    return _block_start_probe(store=store, user=user, session_id=session_id, source_run_id=source_run_id)


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
            return await _ensure_opencli_session_ready_for_start(
                worker_client=worker_client,
                settings=settings,
                store=store,
                user=user,
                connection=connection,
                session_id=session_id,
                source_run_id=source_run_id,
            )
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
        if not _mark_login_required(
            store=store,
            user=user,
            connection_id=connection.connection_id,
            warning_code=reason,
            warning_message=warning_message,
            session_id=session_id,
            source_run_id=source_run_id,
        ):
            return LiepinStartProbeResult(ready=True)
        return LiepinStartProbeResult(ready=False, reason_code=reason, warning_message=warning_message)
    except (OSError, RuntimeError, ValueError):
        if not _mark_login_required(
            store=store,
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ):
            return LiepinStartProbeResult(ready=True)
        return liepin_probe_unavailable_result()

    return _ensure_browser_session_ready_for_start(
        status=status,
        settings=settings,
        store=store,
        user=user,
        connection=connection,
        session_id=session_id,
        source_run_id=source_run_id,
    )


async def _ensure_opencli_session_ready_for_start(
    *,
    worker_client,
    settings: AppSettings,
    store: WorkbenchStore,
    user: WorkbenchUser,
    connection: WorkbenchSourceConnection,
    session_id: str,
    source_run_id: str,
) -> LiepinStartProbeResult:
    if not connection.provider_account_hash:
        status: SessionStatus = await worker_client.session_status(
            connection_id=connection.connection_id,
            tenant=DEFAULT_TENANT_ID,
            workspace=user.workspace_id,
            provider_account_hash=None,
        )
        return _ensure_browser_session_ready_for_start(
            status=status,
            settings=settings,
            store=store,
            user=user,
            connection=connection,
            session_id=session_id,
            source_run_id=source_run_id,
        )
    compliance_gate_ref = ensure_workbench_liepin_provider_connection(
        settings=settings,
        user=user,
        connection=connection,
    )

    status = await worker_client.session_status(
        connection_id=connection.connection_id,
        tenant=DEFAULT_TENANT_ID,
        workspace=user.workspace_id,
        provider_account_hash=connection.provider_account_hash,
    )
    if status.status != "ready":
        if not _mark_login_required(
            store=store,
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
            warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ):
            return LiepinStartProbeResult(ready=True)
        return liepin_probe_login_required_result()
    if status.provider_account_hash != connection.provider_account_hash:
        if not _mark_login_required(
            store=store,
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
            warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ):
            return LiepinStartProbeResult(ready=True)
        return liepin_probe_account_mismatch_result()

    updated_connection = store.mark_liepin_connection_connected_for_source_run(
        user=user,
        connection_id=connection.connection_id,
        session_id=session_id,
        source_run_id=source_run_id,
        provider_account_hash=connection.provider_account_hash,
        compliance_gate_ref=compliance_gate_ref,
    )
    if updated_connection is None:
        return _block_start_probe(store=store, user=user, session_id=session_id, source_run_id=source_run_id)
    return _refresh_start_probe_safety(
        settings=settings,
        store=store,
        user=user,
        connection=updated_connection,
        session_id=session_id,
        source_run_id=source_run_id,
    )


def _ensure_browser_session_ready_for_start(
    *,
    status: SessionStatus,
    settings: AppSettings,
    store: WorkbenchStore,
    user: WorkbenchUser,
    connection: WorkbenchSourceConnection,
    session_id: str,
    source_run_id: str,
) -> LiepinStartProbeResult:
    if status.status != "ready":
        if not _mark_login_required(
            store=store,
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
            warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ):
            return LiepinStartProbeResult(ready=True)
        return liepin_probe_login_required_result()
    if not status.provider_account_hash:
        if not _mark_login_required(
            store=store,
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ):
            return LiepinStartProbeResult(ready=True)
        return liepin_probe_unavailable_result()
    if connection.provider_account_hash and connection.provider_account_hash != status.provider_account_hash:
        if not _mark_login_required(
            store=store,
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
            warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        ):
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
        return _block_start_probe(store=store, user=user, session_id=session_id, source_run_id=source_run_id)
    return _refresh_start_probe_safety(
        settings=settings,
        store=store,
        user=user,
        connection=updated_connection,
        session_id=session_id,
        source_run_id=source_run_id,
    )


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
