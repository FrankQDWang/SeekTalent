from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, cast

import uvicorn
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sse_starlette import EventSourceResponse

from seektalent.config import AppSettings, load_process_env
from seektalent.dev_mode import DevModeStatus, build_dev_mode_env_diagnostics
from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.models import SubjectType
from seektalent.providers.liepin.security import issue_stream_token, read_stream_token_payload
from seektalent.providers.liepin.store import LiepinStore
from seektalent.runtime import WorkflowRuntime
from seektalent.runtime.lifecycle import cleanup_runtime_artifacts
from seektalent_ui import event_routes, workbench_routes
from seektalent_ui.job_runner import WorkbenchJobRunner
from seektalent_ui.models import (
    LiepinComplianceGateActionResponse,
    LiepinComplianceGateConnectionRequest,
    LiepinComplianceGateCreateRequest,
    LiepinComplianceGateResponse,
    LiepinConnectionCreateRequest,
    LiepinConnectionResponse,
    LiepinLoginUrlResponse,
)
from seektalent_ui.network_guard import (
    NetworkGuard,
    build_network_guard,
    host_allowed,
    is_guarded_workbench_path,
    origin_allowed,
    render_startup_diagnostics,
    require_allowed_bind,
)
from seektalent_ui.resources import frontend_available, package_frontend_dir
from seektalent_ui.workbench_store import WorkbenchStore


@dataclass(frozen=True)
class LiepinScope:
    tenant_id: str
    workspace_id: str
    actor_id: str


def create_app(
    settings: AppSettings | None = None,
    *,
    runtime_factory=WorkflowRuntime,
    network_guard: NetworkGuard | None = None,
    dev_mode_env_diagnostics: DevModeStatus | None = None,
    serve_frontend: bool = False,
) -> FastAPI:
    app_settings = settings or AppSettings()
    if serve_frontend and app_settings.runtime_mode == "prod":
        cleanup_runtime_artifacts(app_settings)
    store = LiepinStore(_liepin_db_path(app_settings))
    app = FastAPI(title="SeekTalent UI API")
    app.state.settings = app_settings
    app.state.dev_mode_env_diagnostics = dev_mode_env_diagnostics
    app.state.workbench_graph_secret = secrets.token_urlsafe(32)
    app.state.workbench_store = WorkbenchStore(_workbench_db_path(app_settings))
    app.state.workbench_job_runner = WorkbenchJobRunner(
        store=app.state.workbench_store,
        settings=app_settings,
        runtime_factory=runtime_factory,
    )
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
        elif not app_settings.workbench_enabled:
            response = JSONResponse(status_code=503, content={"detail": "Workbench is disabled by feature gate."})
        else:
            response = await call_next(request)
        if origin is not None:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
            response.headers["Access-Control-Expose-Headers"] = "X-CSRF-Token"
            response.headers["Vary"] = "Origin"
        return response

    app.include_router(workbench_routes.router)
    app.include_router(event_routes.router)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": exc.errors()})

    def require_liepin_scope(
        x_seektalent_api_key: Annotated[str | None, Header(alias="X-SeekTalent-API-Key")] = None,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
        x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-ID")] = None,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> LiepinScope:
        if x_seektalent_api_key is None:
            raise HTTPException(status_code=401, detail="Missing X-SeekTalent-API-Key header.")
        if x_seektalent_api_key != app_settings.liepin_api_token:
            raise HTTPException(status_code=403, detail="Invalid X-SeekTalent-API-Key header.")
        if not x_tenant_id or not x_workspace_id or not x_actor_id:
            raise HTTPException(status_code=400, detail="Missing Liepin tenant, workspace, or actor scope header.")
        return LiepinScope(tenant_id=x_tenant_id, workspace_id=x_workspace_id, actor_id=x_actor_id)

    @app.post("/api/liepin/compliance-gates", status_code=201)
    def create_compliance_gate(
        request: LiepinComplianceGateCreateRequest,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> LiepinComplianceGateResponse:
        gate = ComplianceGate(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            provider_account_hash=None,
            status="pending_account_binding",
            candidate_personal_info_processing_basis=request.candidatePersonalInfoProcessingBasis,
            personal_information_processor=request.personalInformationProcessor,
            operator_audit_owner=request.operatorAuditOwner,
            account_holder_authorized=request.accountHolderAuthorized,
            human_initiated_recruiting=request.humanInitiatedRecruiting,
            allowed_purposes=request.allowedPurposes,
            retention_policy=request.retentionPolicy,
            deletion_sla_days=request.deletionSlaDays,
            deletion_path=request.deletionPath,
            raw_payload_access_scope=request.rawPayloadAccessScope,
            raw_detail_retention_allowed_after_debug=request.rawDetailRetentionAllowedAfterDebug,
            fixture_export_allowed=request.fixtureExportAllowed,
            policy_ref=request.policyRef,
        )
        if not gate.allows_connection_handoff(purpose="search"):
            raise HTTPException(status_code=403, detail="Liepin compliance gate does not satisfy live-search policy.")
        gate_ref = store.create_compliance_gate(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            gate=gate,
            purpose="search",
        )
        return _gate_response(gate_ref, gate, scope)

    @app.get("/api/liepin/compliance-gates/{gate_ref}")
    def get_compliance_gate(
        gate_ref: str,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> LiepinComplianceGateResponse:
        gate = store.get_compliance_gate(
            gate_ref=gate_ref,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
        )
        if gate is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return _gate_response(gate_ref, gate, scope)

    @app.post("/api/liepin/compliance-gates/{gate_ref}/bind-account")
    def bind_compliance_gate_account(
        gate_ref: str,
        request: LiepinComplianceGateConnectionRequest,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> LiepinComplianceGateActionResponse:
        gate = store.get_compliance_gate(
            gate_ref=gate_ref,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
        )
        if gate is None:
            raise HTTPException(status_code=404, detail="Compliance gate not found.")
        connection = store.get_connection(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=request.connectionId,
        )
        if connection is None or connection.compliance_gate_ref != gate_ref:
            raise HTTPException(status_code=404, detail="Connection not found.")
        account_hash = store.bind_connection_account(
            gate_ref=gate_ref,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=request.connectionId,
            secret=_required_liepin_account_binding_secret(app_settings),
        )
        if account_hash is None:
            raise HTTPException(status_code=403, detail="account binding failed")
        return LiepinComplianceGateActionResponse(gateRef=gate_ref, status="approved")

    @app.post("/api/liepin/compliance-gates/{gate_ref}/verify")
    def verify_compliance_gate(
        gate_ref: str,
        request: LiepinComplianceGateConnectionRequest,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> LiepinComplianceGateActionResponse:
        gate = store.get_compliance_gate(
            gate_ref=gate_ref,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
        )
        if gate is None:
            raise HTTPException(status_code=404, detail="Compliance gate not found.")
        connection = store.get_connection(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=request.connectionId,
        )
        if connection is None or connection.compliance_gate_ref != gate_ref:
            raise HTTPException(status_code=404, detail="Connection not found.")
        if connection.status != "connected":
            raise HTTPException(status_code=403, detail="connection_not_bound")
        reason = gate.denial_reason(provider_account_hash=connection.provider_account_hash, purpose="search")
        if reason is not None:
            raise HTTPException(status_code=403, detail=reason)
        return LiepinComplianceGateActionResponse(gateRef=gate_ref, status="approved")

    @app.post("/api/liepin/connections", status_code=201)
    def create_connection(
        request: LiepinConnectionCreateRequest,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> LiepinConnectionResponse:
        gate = store.get_compliance_gate(
            gate_ref=request.complianceGateRef,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
        )
        if gate is None:
            raise HTTPException(status_code=404, detail="Compliance gate not found.")
        if not gate.allows_connection_handoff(purpose="search"):
            raise HTTPException(status_code=403, detail="Compliance gate does not allow connection handoff.")
        connection_id = store.create_connection(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            compliance_gate_ref=request.complianceGateRef,
        )
        connection = store.get_connection(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=connection_id,
        )
        assert connection is not None
        return _connection_response(connection)

    @app.get("/api/liepin/connections/{connection_id}")
    def get_connection(
        connection_id: str,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> LiepinConnectionResponse:
        connection = store.get_connection(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=connection_id,
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return _connection_response(connection)

    @app.post("/api/liepin/connections/{connection_id}/login-url")
    def get_login_url(
        connection_id: str,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> LiepinLoginUrlResponse:
        connection = store.get_connection(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=connection_id,
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return LiepinLoginUrlResponse(
            connectionId=connection.connection_id,
            loginUrl="https://www.liepin.com/",
            handoffState="ready_for_browser_login",
        )

    @app.post("/api/liepin/connections/{connection_id}/stream-token", status_code=204)
    def create_connection_stream_token(
        connection_id: str,
        request: Request,
        response: Response,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> Response:
        connection = store.get_connection(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=connection_id,
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="Not found.")
        token = issue_stream_token(
            secret=_required_liepin_stream_token_secret(app_settings),
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            subject_type="connection",
            subject_id=connection_id,
        )
        response.set_cookie(
            "liepin_stream_token",
            token,
            max_age=60,
            httponly=True,
            samesite="lax",
            secure=_stream_cookie_secure(request),
            path=f"/api/liepin/connections/{connection_id}/events",
        )
        response.status_code = 204
        return response

    @app.get("/api/liepin/connections/{connection_id}/events")
    async def stream_connection_events(
        connection_id: str,
        request: Request,
        liepin_stream_token: Annotated[str | None, Cookie()] = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> EventSourceResponse:
        scope = _scope_from_stream_cookie(
            token=liepin_stream_token,
            settings=app_settings,
            subject_type="connection",
            subject_id=connection_id,
            request=request,
        )
        return EventSourceResponse(
            _event_generator(
                request=request,
                store=store,
                scope=scope,
                subject_type="connection",
                subject_id=connection_id,
                after_sequence=_sequence_from_header(last_event_id),
            ),
            ping=15,
            send_timeout=5,
        )

    if serve_frontend:
        mount_packaged_frontend(app)

    return app


def mount_packaged_frontend(app: FastAPI) -> None:
    frontend_root = package_frontend_dir()
    if not frontend_available(frontend_root):
        return
    app.mount("/_app", StaticFiles(directory=frontend_root / "_app"), name="workbench_static")

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def packaged_frontend(path: str = "") -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")
        candidate = (frontend_root / path).resolve(strict=False)
        resolved_root = frontend_root.resolve(strict=False)
        if candidate.is_file() and (candidate == resolved_root or resolved_root in candidate.parents):
            return FileResponse(candidate)
        return FileResponse(frontend_root / "200.html")


def _liepin_db_path(settings: AppSettings) -> Path:
    path = Path(settings.liepin_connector_db_path)
    if path.is_absolute():
        return path
    if settings.workspace_root:
        return Path(settings.workspace_root) / path
    return path


def _workbench_db_path(settings: AppSettings) -> Path:
    if settings.workspace_root:
        return Path(settings.workspace_root) / ".seektalent" / "workbench.sqlite3"
    return Path(".seektalent") / "workbench.sqlite3"


def _gate_response(gate_ref: str, gate: ComplianceGate, scope: LiepinScope) -> LiepinComplianceGateResponse:
    return LiepinComplianceGateResponse(
        gateRef=gate_ref,
        tenantId=scope.tenant_id,
        workspaceId=scope.workspace_id,
        actorId=scope.actor_id,
        status=gate.status,
        allowedPurposes=gate.allowed_purposes,
        retentionPolicy=gate.retention_policy,
        policyRef=gate.policy_ref,
    )


def _connection_response(connection) -> LiepinConnectionResponse:
    return LiepinConnectionResponse(
        connectionId=connection.connection_id,
        tenantId=connection.tenant_id,
        workspaceId=connection.workspace_id,
        actorId=connection.actor_id,
        complianceGateRef=connection.compliance_gate_ref,
        status=connection.status,
    )


def _scope_from_stream_cookie(
    *,
    token: str | None,
    settings: AppSettings,
    subject_type: str,
    subject_id: str,
    request: Request,
) -> LiepinScope:
    if any("token" in name.lower() for name in request.query_params):
        raise HTTPException(status_code=400, detail="Stream tokens are not accepted in URL query parameters.")
    if token is None:
        raise HTTPException(status_code=401, detail="Missing stream token cookie.")
    payload = read_stream_token_payload(token, secret=_required_liepin_stream_token_secret(settings))
    if payload is None or payload.get("subject_type") != subject_type or payload.get("subject_id") != subject_id:
        raise HTTPException(status_code=403, detail="Invalid stream token.")
    return LiepinScope(
        tenant_id=str(payload["tenant_id"]),
        workspace_id=str(payload["workspace_id"]),
        actor_id=str(payload["actor_id"]),
    )


async def _event_generator(
    *,
    request: Request,
    store: LiepinStore,
    scope: LiepinScope,
    subject_type: str,
    subject_id: str,
    after_sequence: int,
):
    sequence = after_sequence
    while not await request.is_disconnected():
        rows = store.iter_events_after(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            subject_type=cast(SubjectType, subject_type),
            subject_id=subject_id,
            after_sequence=sequence,
            limit=100,
        )
        if rows:
            for row in rows:
                sequence = row.sequence
                yield {
                    "id": str(row.sequence),
                    "event": row.event_name,
                    "data": json.dumps(row.payload, sort_keys=True, separators=(",", ":")),
                }
                if row.event_name == "stream_end":
                    return
            continue
        await asyncio.sleep(0.25)


def _sequence_from_header(last_event_id: str | None) -> int:
    if last_event_id is None:
        return 0
    try:
        return max(0, int(last_event_id))
    except ValueError:
        return 0


def _stream_cookie_secure(request: Request) -> bool:
    host = (request.url.hostname or "testserver").strip("[]").lower()
    return host not in {"localhost", "127.0.0.1", "::1", "testserver"}


def _required_liepin_account_binding_secret(settings: AppSettings) -> str:
    if not settings.liepin_account_binding_secret:
        raise HTTPException(status_code=500, detail="Liepin account binding secret is not configured.")
    return settings.liepin_account_binding_secret


def _required_liepin_stream_token_secret(settings: AppSettings) -> str:
    if not settings.liepin_stream_token_secret:
        raise HTTPException(status_code=500, detail="Liepin stream token secret is not configured.")
    return settings.liepin_stream_token_secret


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
        choices=["disabled", "fake_fixture", "managed_local", "external_http", "opencli"],
        default=None,
    )
    parser.add_argument("--liepin-browser-action-backend", choices=["disabled", "opencli"], default=None)
    parser.add_argument("--liepin-opencli-command", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_process_env()
    try:
        require_allowed_bind(args.host, lan_flag=args.lan)
    except ValueError as exc:
        print(str(exc))
        return 2
    dev_mode_env_diagnostics = None
    try:
        settings = AppSettings().with_overrides(
            mock_cts=args.mock_cts,
            runtime_mode=args.runtime_mode,
            liepin_worker_mode=args.liepin_worker_mode,
            liepin_browser_action_backend=args.liepin_browser_action_backend,
            liepin_opencli_command=args.liepin_opencli_command,
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
            liepin_opencli_command=args.liepin_opencli_command,
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
                runtime_factory=WorkflowRuntime,
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
