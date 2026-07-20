from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from collections.abc import Callable, Mapping
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
from seektalent.product_env import MANAGED_OPENCLI_COMMAND_MARKER
from seektalent.providers.liepin.runtime_context import local_opencli_liepin_source_context
from seektalent.runtime.lifecycle import cleanup_runtime_artifacts
from seektalent.source_adapters import build_source_enabled_runtime
from seektalent.workbench_internal_secrets import ensure_workbench_internal_liepin_env
from seektalent_conversation_agent.factory import build_agent_service
from seektalent_workbench_v2.agent_loop import BailianStrictWorkbenchV2AgentLoop
from seektalent_workbench_v2.runtime_runner import WorkbenchV2RuntimeQueueRunner
from seektalent_workbench_v2.runtime_service import WorkbenchV2RuntimeService
from seektalent_workbench_v2.service import WorkbenchV2Service
from seektalent_workbench_v2.store import WorkbenchV2Store
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_ui import (
    agent_routes,
    agent_workbench_routes,
    agent_workbench_v2_routes,
    event_routes,
    validation_errors,
    workbench_routes,
)
from seektalent_ui.agent_workbench_stream_store import AgentWorkbenchStreamStore
from seektalent_ui.job_runner import WorkbenchJobRunner
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
from seektalent_ui.liepin_security import reject_unsafe_liepin_control_plane
from seektalent_ui.static_frontend import mount_packaged_frontend
from seektalent_ui.workbench_paths import agent_workbench_stream_db_path, workbench_db_path
from seektalent_ui.workbench_observability import correlation_id_from_request
from seektalent_ui.workbench_store import WorkbenchStore
from seektalent_ui.workflow_start_outbox_runner import RequirementExtractionOutboxRunner, WorkflowStartOutboxRunner


logger = logging.getLogger(__name__)


def create_app(
    settings: AppSettings | None = None,
    *,
    runtime_factory=build_source_enabled_runtime,
    network_guard: NetworkGuard | None = None,
    dev_mode_env_diagnostics: DevModeStatus | None = None,
    serve_frontend: bool = False,
    workbench_note_writer_agent_factory: Callable[[], object] | None = None,
) -> FastAPI:
    app_settings = settings or AppSettings()
    reject_unsafe_liepin_control_plane(app_settings)
    if serve_frontend and app_settings.runtime_mode == "prod":
        cleanup_runtime_artifacts(app_settings)
    app = FastAPI(title="SeekTalent UI API", lifespan=_lifespan)
    app.state.settings = app_settings
    app.state.dev_mode_env_diagnostics = dev_mode_env_diagnostics
    app.state.workbench_graph_secret = secrets.token_urlsafe(32)
    app.state.workbench_store = WorkbenchStore(workbench_db_path(app_settings))
    app.state.agent_workbench_stream_store = AgentWorkbenchStreamStore(agent_workbench_stream_db_path(app_settings))
    app.state.agent_memory_service = agent_routes.build_memory_service(settings=app_settings)
    app.state.agent_conversation_service = build_agent_service(
        settings=app_settings,
        runtime_factory=runtime_factory,
    )
    app.state.agent_conversation_store = app.state.agent_conversation_service.store
    runtime_control_store = app.state.agent_conversation_service.service_action_adapter.runtime_store
    if runtime_control_store is None:
        raise RuntimeError("runtime_control_store_unavailable")
    app.state.runtime_control_store = runtime_control_store
    app.state.workbench_v2_store = WorkbenchV2Store(
        app_settings.resolve_workspace_path(".seektalent/workbench_v2.sqlite3")
    )
    app.state.workbench_v2_store.initialize()
    def workbench_v2_runtime_factory() -> object:
        return runtime_factory(app_settings)

    app.state.workbench_v2_runtime_executor = WorkflowRuntimeExecutor(
        store=runtime_control_store,
        settings=app_settings,
        runtime_factory=workbench_v2_runtime_factory,
        source_context_provider=local_opencli_liepin_source_context,
    )
    app.state.workbench_v2_runtime_runner = WorkbenchV2RuntimeQueueRunner(
        store=runtime_control_store,
        executor=app.state.workbench_v2_runtime_executor,
    )
    app.state.workbench_v2_service = WorkbenchV2Service(
        store=app.state.workbench_v2_store,
        agent_loop=BailianStrictWorkbenchV2AgentLoop(settings=app_settings),
        runtime_service=WorkbenchV2RuntimeService(
            store=runtime_control_store,
            settings=app_settings,
            runtime_factory=workbench_v2_runtime_factory,
            executor=app.state.workbench_v2_runtime_executor,
            on_run_queued=app.state.workbench_v2_runtime_runner.wake,
        ),
    )
    app.state.agent_conversation_service.memory_service = app.state.agent_memory_service
    app.state.workflow_start_outbox_runner = WorkflowStartOutboxRunner(
        service=app.state.agent_conversation_service,
    )
    app.state.requirement_extraction_outbox_runner = RequirementExtractionOutboxRunner(
        service=app.state.agent_conversation_service,
    )
    app.state.workbench_job_runner = WorkbenchJobRunner(
        store=app.state.workbench_store,
        settings=app_settings,
        runtime_factory=runtime_factory,
        runtime_control_store=runtime_control_store,
        workbench_note_writer_agent_factory=workbench_note_writer_agent_factory,
    )
    app.state.agent_rate_limiter = agent_routes.LocalAgentRateLimiter()
    app.state.network_guard = network_guard

    app.state.workbench_store.record_security_audit_event(
        actor_user_id=None,
        actor_role="system",
        workspace_id="default",
        target_type="feature_gate",
        target_id="workbench",
        action="workbench_feature_gate_evaluated",
        result="enabled" if app_settings.workbench_enabled else "disabled",
        reason_code="startup",
        metadata={"workbenchEnabled": app_settings.workbench_enabled},
    )

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

    app.include_router(workbench_routes.router)
    app.include_router(agent_routes.router)
    app.include_router(agent_workbench_routes.router)
    app.include_router(agent_workbench_v2_routes.router)
    app.include_router(event_routes.router)
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
            schema_version = (
                agent_routes.AGENT_MEMORY_SCHEMA_VERSION
                if _request.url.path.startswith("/api/agent/memory")
                else agent_routes.AGENT_CONVERSATION_SCHEMA_VERSION
            )
            return JSONResponse(
                status_code=400,
                content={
                    "schemaVersion": schema_version,
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
        if _request.url.path.startswith("/api/agent") and isinstance(exc.detail, dict):
            content = dict(exc.detail)
            if _request.url.path.startswith("/api/agent/workbench") and "type" in content:
                return no_store_json_response(status_code=exc.status_code, content=content)
            return JSONResponse(status_code=exc.status_code, content=content)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    if serve_frontend:
        mount_packaged_frontend(app)

    _install_custom_openapi(app)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI):
    runner = getattr(app.state, "workflow_start_outbox_runner", None)
    extraction_runner = getattr(app.state, "requirement_extraction_outbox_runner", None)
    runtime_runner = getattr(app.state, "workbench_v2_runtime_runner", None)
    try:
        if runtime_runner is not None:
            runtime_runner.start()
        if runner is not None:
            runner.start()
            runner.wake()
        if extraction_runner is not None:
            extraction_runner.start()
        yield
    finally:
        body_error = sys.exception()
        cleanup_errors: list[Exception] = []
        for name, lifespan_runner in (
            ("requirement extraction runner", extraction_runner),
            ("workflow start runner", runner),
            ("Workbench v2 runtime runner", runtime_runner),
        ):
            if lifespan_runner is None:
                continue
            try:
                lifespan_runner.stop()
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.exception("%s failed during application lifespan cleanup", name)
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
    parser.add_argument("--liepin-opencli-command", default=None)
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
    try:
        liepin_opencli_command = (
            _managed_liepin_opencli_command_from_env(os.environ)
            if prod_frontend
            else args.liepin_opencli_command
        )
    except ValueError as exc:
        print(f"reason_code=liepin_opencli_config_invalid {exc}")
        return 1
    dev_mode_env_diagnostics = None
    try:
        base_settings = AppSettings(_env_file=None) if prod_frontend else AppSettings()
        settings = base_settings.with_overrides(
            mock_cts=args.mock_cts,
            runtime_mode=args.runtime_mode,
            liepin_worker_mode=args.liepin_worker_mode,
            liepin_browser_action_backend=args.liepin_browser_action_backend,
            liepin_opencli_command=liepin_opencli_command,
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
            liepin_opencli_command=liepin_opencli_command,
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


def _managed_liepin_opencli_command_from_env(env: Mapping[str, str]) -> str:
    command = str(env.get("SEEKTALENT_LIEPIN_OPENCLI_COMMAND") or "").strip()
    managed = str(env.get(MANAGED_OPENCLI_COMMAND_MARKER) or "").strip()
    if command and managed == "1":
        return command
    raise ValueError("managed OpenCLI command was not prepared by seektalent workbench")


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
