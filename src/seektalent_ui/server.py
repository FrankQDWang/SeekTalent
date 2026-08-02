from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from seektalent.config import AppSettings, load_process_env
from seektalent.dev_mode import DevModeStatus, build_dev_mode_env_diagnostics
from seektalent.liepin_verify_session_gate import _observe_session
from seektalent.wtscli_runtime import BootstrapError
from seektalent.runtime.lifecycle import cleanup_runtime_artifacts
from seektalent.source_adapters import build_source_enabled_runtime
from seektalent.workbench_internal_secrets import ensure_workbench_internal_liepin_env
from seektalent.wtscli_lifecycle_supervisor import (
    WtsCliLifecycleError,
    WtsCliLifecycleSupervisor,
    build_wtscli_lifecycle_supervisor,
)
from seektalent_workbench_v2.agent_loop import BailianStrictWorkbenchV2AgentLoop
from seektalent_workbench_v2.runtime_runner import WorkbenchV2RuntimeQueueRunner
from seektalent_workbench_v2.runtime_service import (
    WorkbenchV2RuntimeService,
)
from seektalent_workbench_v2.service import WorkbenchV2Service
from seektalent_workbench_v2.store import WorkbenchV2Store
from seektalent_ui import (
    agent_workbench_v2_routes,
    validation_errors,
)
from seektalent_ui.liepin_routes import create_liepin_router
from seektalent_ui.network_guard import (
    NetworkGuard,
    build_network_guard,
    host_allowed,
    is_guarded_workbench_path,
    origin_allowed,
    render_startup_diagnostics,
    require_allowed_bind,
)
from seektalent_ui.problem_details import (
    no_store_json_response,
    problem_from_reason,
    regions_from_validation_errors,
)
from seektalent_ui.runtime_execution import build_runtime_execution
from seektalent_ui.liepin_security import reject_unsafe_liepin_control_plane
from seektalent_ui.static_frontend import mount_packaged_frontend
from seektalent_ui.workbench_observability import correlation_id_from_request


logger = logging.getLogger(__name__)


def create_app(
    settings: AppSettings | None = None,
    *,
    runtime_factory=build_source_enabled_runtime,
    network_guard: NetworkGuard | None = None,
    dev_mode_env_diagnostics: DevModeStatus | None = None,
    serve_frontend: bool = False,
) -> FastAPI:
    app_settings = settings or AppSettings()
    reject_unsafe_liepin_control_plane(app_settings)
    if serve_frontend and app_settings.runtime_mode == "prod":
        cleanup_runtime_artifacts(app_settings)
    app = FastAPI(title="SeekTalent UI API", lifespan=_lifespan)
    app.state.settings = app_settings
    app.state.wtscli_lifecycle_supervisor = (
        build_wtscli_lifecycle_supervisor(app_settings)
        if app_settings.liepin_worker_mode == "opencli"
        else None
    )
    app.state.dev_mode_env_diagnostics = dev_mode_env_diagnostics
    app.state.workbench_graph_secret = secrets.token_urlsafe(32)
    execution = build_runtime_execution(
        app_settings,
        runtime_factory=runtime_factory,
        wtscli_lifecycle_supervisor=app.state.wtscli_lifecycle_supervisor,
    )
    runtime_control_store = execution.store
    runtime_executor = execution.executor
    command_service = execution.command_service
    app.state.runtime_control_store = runtime_control_store
    app.state.workbench_v2_store = WorkbenchV2Store(
        app_settings.resolve_workspace_path(".seektalent/workbench_v2.sqlite3")
    )
    app.state.workbench_v2_store.initialize()
    app.state.workbench_v2_requirement_extractor = execution.requirement_extractor
    app.state.runtime_command_service = command_service
    app.state.workbench_v2_runtime_executor = runtime_executor
    app.state.workbench_v2_runtime_runner = WorkbenchV2RuntimeQueueRunner(
        store=runtime_control_store,
        executor=app.state.workbench_v2_runtime_executor,
        prepare_readiness_probe=(
            (lambda: _observe_session(app_settings))
            if app_settings.liepin_worker_mode == "opencli"
            else None
        ),
    )
    app.state.workbench_v2_service = WorkbenchV2Service(
        store=app.state.workbench_v2_store,
        agent_loop=BailianStrictWorkbenchV2AgentLoop(settings=app_settings),
        runtime_service=WorkbenchV2RuntimeService(
            store=runtime_control_store,
            requirement_extractor=app.state.workbench_v2_requirement_extractor,
            settings=app_settings,
            executor=runtime_executor,
            command_service=command_service,
            on_run_queued=app.state.workbench_v2_runtime_runner.wake,
        ),
    )
    app.state.network_guard = network_guard

    @app.middleware("http")
    async def workbench_host_guard(request: Request, call_next):
        if not is_guarded_workbench_path(request.url.path, serve_frontend=serve_frontend):
            return await call_next(request)
        origin = request.headers.get("origin")
        if not host_allowed(request.headers.get("host"), network_guard):
            return JSONResponse(status_code=403, content={"detail": "Host header is not allowed."})
        if not origin_allowed(origin, request.headers.get("host"), request.url.scheme, network_guard):
            return JSONResponse(status_code=403, content={"detail": "Origin is not allowed."})
        if request.method == "OPTIONS":
            response = Response(status_code=204)
        elif (
            app_settings.runtime_mode == "prod"
            and request.url.path.startswith("/api/workbench")
            and request.method not in {"GET", "HEAD"}
        ):
            response = JSONResponse(
                status_code=410,
                content={
                    "reasonCode": "legacy_workbench_execution_removed",
                    "detail": "Legacy Workbench execution has been removed.",
                },
            )
        elif not app_settings.workbench_enabled and request.url.path.startswith(("/api/workbench", "/api/agent")):
            if request.url.path.startswith("/api/agent/workbench"):
                problem = problem_from_reason(
                    reason_code="workbench_feature_gate_disabled",
                    status=503,
                    instance=request.url.path,
                    correlation_id=correlation_id_from_request(request),
                    detail="Workbench is disabled by feature gate.",
                )
                response = no_store_json_response(
                    status_code=503,
                    content=problem.model_dump(mode="json", exclude_none=True),
                )
            else:
                response = JSONResponse(status_code=503, content={"detail": "Workbench is disabled by feature gate."})
        else:
            response = await call_next(request)
        if origin is not None:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, PUT, OPTIONS"
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/api/health/execution-ready", include_in_schema=False)
    def execution_ready() -> JSONResponse:
        from datetime import UTC, datetime

        observed_at = datetime.now(UTC)
        components = [app.state.workbench_v2_runtime_runner.health_snapshot()]
        component_payloads = []
        for component in components:
            heartbeat = (
                datetime.fromisoformat(
                    component.last_heartbeat_at.replace(
                        "Z",
                        "+00:00",
                    )
                )
                if component.last_heartbeat_at is not None
                else None
            )
            stale = (
                heartbeat is None
                or (observed_at - heartbeat).total_seconds() > 10
            )
            item = component.as_dict()
            item["stale"] = stale
            item["status"] = (
                "ready"
                if component.alive and not stale
                else "not_ready"
            )
            component_payloads.append(item)
        browser_lane = runtime_control_store.get_browser_lane()
        lane_expired = bool(
            browser_lane is not None
            and browser_lane.status == "active"
            and browser_lane.lease_expires_at is not None
            and datetime.fromisoformat(
                browser_lane.lease_expires_at.replace(
                    "Z",
                    "+00:00",
                )
            )
            <= observed_at
        )
        lane_unresolved = bool(
            browser_lane is not None
            and browser_lane.status == "active"
            and browser_lane.last_failure_code is not None
        )
        expired_executor_leases = [
            lease
            for lease in runtime_control_store.list_active_executor_leases()
            if datetime.fromisoformat(
                lease.lease_expires_at.replace("Z", "+00:00")
            )
            <= observed_at
        ]
        ready = (
            all(item["status"] == "ready" for item in component_payloads)
            and (
                app.state.wtscli_lifecycle_supervisor is None
                or app.state.wtscli_lifecycle_supervisor.health_snapshot()["status"] == "ready"
            )
            and not lane_expired
            and not lane_unresolved
            and not expired_executor_leases
        )
        content: dict[str, object] = {
            "schemaVersion": "seektalent.execution-readiness.v1",
            "status": "ready" if ready else "not_ready",
            "components": component_payloads,
            "wtscli": (
                None
                if app.state.wtscli_lifecycle_supervisor is None
                else app.state.wtscli_lifecycle_supervisor.health_snapshot()
            ),
            "expiredExecutorLeaseCount": len(
                expired_executor_leases
            ),
            "browserLane": (
                None
                if browser_lane is None
                else {
                    "laneKey": browser_lane.lane_key,
                    "status": browser_lane.status,
                    "operationKind": browser_lane.operation_kind,
                    "fencingToken": browser_lane.fencing_token,
                    "lastFailureCode": browser_lane.last_failure_code,
                    "expired": lane_expired,
                }
            ),
        }
        return JSONResponse(status_code=200 if ready else 503, content=content)

    app.include_router(agent_workbench_v2_routes.router)
    app.include_router(create_liepin_router(settings=app_settings))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        if _request.url.path.startswith("/api/agent/workbench"):
            public_errors = validation_errors.public_validation_errors(exc)
            problem = problem_from_reason(
                reason_code="agent_request_invalid",
                status=400,
                instance=_request.url.path,
                correlation_id=correlation_id_from_request(_request),
                regions=regions_from_validation_errors(public_errors),
            )
            return no_store_json_response(
                status_code=400,
                content=problem.model_dump(mode="json", exclude_none=True),
            )
        if _request.url.path.startswith("/api/agent"):
            return JSONResponse(
                status_code=400,
                content={
                    "schemaVersion": "agent.workbench.v2",
                    "reasonCode": "agent_request_invalid",
                    "errors": validation_errors.public_validation_errors(exc),
                },
            )
        return JSONResponse(status_code=400, content={"error": validation_errors.public_validation_errors(exc)})

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if _request.url.path.startswith("/api/agent/workbench/v2") and isinstance(exc.detail, dict):
            content = dict(exc.detail)
            if "type" in content:
                return no_store_json_response(status_code=exc.status_code, content=content)
            return JSONResponse(status_code=exc.status_code, content={"detail": content})
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    if serve_frontend:
        mount_packaged_frontend(app)

    _install_custom_openapi(app)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI):
    runtime_runner = getattr(app.state, "workbench_v2_runtime_runner", None)
    wtscli_supervisor: WtsCliLifecycleSupervisor | None = getattr(
        app.state,
        "wtscli_lifecycle_supervisor",
        None,
    )
    try:
        if wtscli_supervisor is not None:
            try:
                wtscli_supervisor.start()
            except (BootstrapError, WtsCliLifecycleError) as exc:
                wtscli_supervisor.record_startup_failure(exc)
                logger.warning(
                    "WTSCLI lifecycle supervisor is not ready: %s",
                    type(exc).__name__,
                )
        if runtime_runner is not None:
            runtime_runner.start()
        yield
    finally:
        body_error = sys.exception()
        cleanup_errors: list[Exception] = []
        for name, lifespan_runner in (("Workbench v2 runtime runner", runtime_runner),):
            if lifespan_runner is None:
                continue
            try:
                lifespan_runner.stop()
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.exception("%s failed during application lifespan cleanup", name)
                cleanup_errors.append(exc)
        if wtscli_supervisor is not None:
            try:
                wtscli_supervisor.shutdown()
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.exception("WTSCLI lifecycle supervisor cleanup failed")
                cleanup_errors.append(exc)
        if cleanup_errors and body_error is None:
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            raise ExceptionGroup("application lifespan cleanup failed", cleanup_errors)


def _install_custom_openapi(app: FastAPI) -> None:
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    _patch_agent_workbench_openapi(schema)
    app.openapi_schema = schema


def _patch_agent_workbench_openapi(schema: dict[str, object]) -> None:
    paths = _string_keyed_dict(schema.get("paths"))
    if paths is None:
        return
    for path, path_item_value in paths.items():
        if not path.startswith("/api/agent/workbench"):
            continue
        path_item = _string_keyed_dict(path_item_value)
        if path_item is None:
            continue
        for method, operation_value in path_item.items():
            operation = _string_keyed_dict(operation_value)
            if operation is None:
                continue
            responses = _string_keyed_dict(operation.get("responses"))
            if responses is not None:
                responses.pop("422", None)
                operation["responses"] = responses
                path_item[method] = operation
        paths[path] = path_item
    schema["paths"] = paths


def _string_keyed_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            result[key] = item
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local API server for the SeekTalent minimal web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--lan", action="store_true", help="Allow non-loopback UI bind for trusted LAN use.")
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Allowed Host header for workbench routes; repeat for each LAN hostname or IP.",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help="Allowed Origin for credentialed workbench CORS; repeat for each browser origin.",
    )
    parser.add_argument("--mock-cts", dest="mock_cts", action="store_true", default=None)
    parser.add_argument("--real-cts", dest="mock_cts", action="store_false")
    parser.add_argument("--disable-workbench", action="store_true", help="Disable workbench/auth routes for rollback.")
    parser.add_argument("--serve-frontend", action="store_true", help="Serve packaged Workbench static frontend.")
    parser.add_argument("--runtime-mode", choices=["dev", "prod"], default=None)
    parser.add_argument(
        "--liepin-worker-mode",
        choices=["disabled", "fake_fixture", "external_http", "opencli"],
        default=None,
    )
    parser.add_argument("--liepin-browser-action-backend", choices=["disabled", "opencli"], default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    prod_frontend = args.runtime_mode == "prod" and args.serve_frontend
    if prod_frontend:
        ensure_workbench_internal_liepin_env(os.environ)
    load_process_env()
    try:
        require_allowed_bind(args.host, lan_flag=args.lan)
    except ValueError as exc:
        print(str(exc))
        return 2
    dev_mode_env_diagnostics = None
    try:
        base_settings = AppSettings(_env_file=None) if prod_frontend else AppSettings()
        settings = base_settings.with_overrides(
            mock_cts=args.mock_cts,
            runtime_mode=args.runtime_mode,
            liepin_worker_mode=args.liepin_worker_mode,
            liepin_browser_action_backend=args.liepin_browser_action_backend,
            liepin_opencli_session="" if prod_frontend else None,
            workbench_enabled=False if args.disable_workbench else None,
        )
    except ValidationError as exc:
        if not _can_recover_with_dev_mode_env_diagnostics(exc, os.environ):
            raise
        dev_mode_env_diagnostics = build_dev_mode_env_diagnostics(os.environ, workspace_root=Path.cwd())
        settings = AppSettings(_env_file=None, liepin_worker_mode="disabled").with_overrides(
            mock_cts=args.mock_cts,
            runtime_mode=args.runtime_mode,
            liepin_worker_mode=args.liepin_worker_mode,
            liepin_browser_action_backend=args.liepin_browser_action_backend,
            liepin_opencli_session="" if prod_frontend else None,
            workbench_enabled=False if args.disable_workbench else None,
        )
    network_guard = build_network_guard(
        bind_host=args.host,
        port=args.port,
        lan_enabled=args.lan,
        allowed_hosts=args.allowed_host,
        allowed_origins=args.allowed_origin,
    )
    print(render_startup_diagnostics(network_guard))
    try:
        uvicorn.run(
            create_app(
                settings=settings,
                runtime_factory=build_source_enabled_runtime,
                network_guard=network_guard,
                dev_mode_env_diagnostics=dev_mode_env_diagnostics,
                serve_frontend=args.serve_frontend,
            ),
            host=args.host,
            port=args.port,
        )
    except KeyboardInterrupt:
        return 0
    return 0


def _can_recover_with_dev_mode_env_diagnostics(exc: ValidationError, env: Mapping[str, str]) -> bool:
    worker_mode = env.get("SEEKTALENT_LIEPIN_WORKER_MODE", "").strip()
    browser_backend = env.get("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", "").strip()
    if worker_mode != "opencli" and browser_backend != "opencli":
        return False
    message = str(exc)
    return any(
        token in message
        for token in (
            "liepin_browser_action_backend",
            "liepin_opencli_",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
