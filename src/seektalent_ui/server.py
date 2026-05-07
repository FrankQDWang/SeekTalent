from __future__ import annotations

import argparse
import asyncio
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlparse

import uvicorn
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sse_starlette import EventSourceResponse

from seektalent.config import AppSettings, load_process_env
from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.security import issue_stream_token, read_stream_token_payload
from seektalent.providers.liepin.store import LiepinStore
from seektalent.runtime import WorkflowRuntime
from seektalent_ui.mapper import build_ui_payloads
from seektalent_ui.models import (
    CandidateDetailResponse,
    LiepinComplianceGateActionResponse,
    LiepinComplianceGateConnectionRequest,
    LiepinComplianceGateCreateRequest,
    LiepinComplianceGateResponse,
    LiepinConnectionCreateRequest,
    LiepinConnectionResponse,
    LiepinLoginUrlResponse,
    LiepinRunResultsResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunStatus,
    RunStatusResponse,
)


@dataclass
class UiRunRecord:
    run_id: str
    job_title: str
    jd_text: str
    sourcing_preference_text: str
    status: RunStatus = "queued"
    error_message: str | None = None
    final_shortlist: list = field(default_factory=list)
    candidate_details: dict[str, CandidateDetailResponse] = field(default_factory=dict)


class RunNotFoundError(KeyError):
    pass


class CandidateNotFoundError(KeyError):
    pass


class RunNotReadyError(RuntimeError):
    pass


class RunRegistry:
    def __init__(
        self,
        settings: AppSettings,
        *,
        runtime_factory=WorkflowRuntime,
    ) -> None:
        self.settings = settings
        self.runtime_factory = runtime_factory
        self._lock = threading.Lock()
        self._runs: dict[str, UiRunRecord] = {}

    def create_run(self, *, job_title: str, jd_text: str, sourcing_preference_text: str) -> RunCreateResponse:
        job_title = job_title.strip()
        jd_text = jd_text.strip()
        sourcing_preference_text = sourcing_preference_text.strip()
        if not job_title:
            raise ValueError("jobTitle must not be empty.")
        if not jd_text:
            raise ValueError("jdText must not be empty.")
        run_id = f"web-{uuid.uuid4().hex[:8]}"
        record = UiRunRecord(
            run_id=run_id,
            job_title=job_title,
            jd_text=jd_text,
            sourcing_preference_text=sourcing_preference_text,
        )
        with self._lock:
            self._runs[run_id] = record
        worker = threading.Thread(
            target=self._run_workflow,
            args=(run_id,),
            name=f"seektalent-ui-{run_id}",
            daemon=True,
        )
        worker.start()
        return RunCreateResponse(runId=run_id, status="queued")

    def get_run_response(self, run_id: str) -> RunStatusResponse:
        record = self._get_record(run_id)
        return RunStatusResponse(
            runId=record.run_id,
            status=record.status,
            errorMessage=record.error_message,
            finalShortlist=record.final_shortlist,
        )

    def get_candidate_detail(self, run_id: str, candidate_id: str) -> CandidateDetailResponse:
        record = self._get_record(run_id)
        if record.status != "completed":
            raise RunNotReadyError(f"Run {run_id} is not completed yet.")
        detail = record.candidate_details.get(candidate_id)
        if detail is None:
            raise CandidateNotFoundError(candidate_id)
        return detail

    def _run_workflow(self, run_id: str) -> None:
        runtime = self.runtime_factory(self.settings)
        with self._lock:
            self._runs[run_id].status = "running"
        try:
            artifacts = runtime.run(
                job_title=self._runs[run_id].job_title,
                jd=self._runs[run_id].jd_text,
                notes=self._runs[run_id].sourcing_preference_text,
            )
            shortlist, details = build_ui_payloads(
                artifacts.final_result,
                artifacts.candidate_store,
                artifacts.normalized_store,
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                record = self._runs[run_id]
                record.status = "failed"
                record.error_message = str(exc) or "Run failed."
            return

        with self._lock:
            record = self._runs[run_id]
            record.status = "completed"
            record.error_message = None
            record.final_shortlist = shortlist
            record.candidate_details = details

    def _get_record(self, run_id: str) -> UiRunRecord:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            return record


@dataclass(frozen=True)
class LiepinScope:
    tenant_id: str
    workspace_id: str
    actor_id: str


def create_app(registry: RunRegistry, settings: AppSettings | None = None) -> FastAPI:
    app_settings = settings or registry.settings
    store = LiepinStore(_liepin_db_path(app_settings))
    app = FastAPI(title="SeekTalent UI API")

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
            org_name=request.orgName,
            org_domain=request.orgDomain,
            approved_purposes=request.approvedPurposes,
            search_keywords=request.searchKeywords,
            retention_days=request.retentionDays,
            pii_policy=request.piiPolicy,
            operator_id=request.operatorId,
            operator_name=request.operatorName,
            created_at=_now_iso(),
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
            secret=app_settings.liepin_session_store_key_id,
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
        reason = gate.denial_reason(account_binding_hash=connection.account_binding_hash, purpose="search")
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
            secret=app_settings.liepin_session_store_key_id,
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
            secure=False,
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

    def optional_scope(
        x_seektalent_api_key: Annotated[str | None, Header(alias="X-SeekTalent-API-Key")] = None,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
        x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-ID")] = None,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> LiepinScope | None:
        if x_seektalent_api_key is None and x_tenant_id is None and x_workspace_id is None and x_actor_id is None:
            return None
        return require_liepin_scope(x_seektalent_api_key, x_tenant_id, x_workspace_id, x_actor_id)

    @app.post("/api/runs", status_code=201)
    def create_run(request: RunCreateRequest, scope: LiepinScope | None = Depends(optional_scope)):
        if request.provider == "liepin":
            if scope is None:
                raise HTTPException(status_code=400, detail="Missing Liepin tenant, workspace, or actor scope header.")
            if not request.connectionId or not request.complianceGateRef:
                raise HTTPException(status_code=403, detail="Liepin runs require a compliance gate.")
            connection = store.get_connection(
                tenant_id=scope.tenant_id,
                workspace_id=scope.workspace_id,
                actor_id=scope.actor_id,
                connection_id=request.connectionId,
            )
            if connection is None:
                raise HTTPException(status_code=404, detail="Connection not found.")
            gate = store.get_compliance_gate(
                gate_ref=request.complianceGateRef,
                tenant_id=scope.tenant_id,
                workspace_id=scope.workspace_id,
                actor_id=scope.actor_id,
            )
            if gate is None:
                raise HTTPException(status_code=404, detail="Compliance gate not found.")
            if not gate.allows_live_search(account_binding_hash=connection.account_binding_hash, purpose="search"):
                raise HTTPException(status_code=403, detail="Liepin compliance gate does not allow live search.")
            run_id = store.create_run(
                tenant_id=scope.tenant_id,
                workspace_id=scope.workspace_id,
                actor_id=scope.actor_id,
                connection_id=request.connectionId,
                compliance_gate_ref=request.complianceGateRef,
            )
            store.append_event(
                tenant_id=scope.tenant_id,
                workspace_id=scope.workspace_id,
                actor_id=scope.actor_id,
                subject_type="run",
                subject_id=run_id,
                event_name="run_started",
                payload={"runId": run_id, "status": "queued"},
            )
            return RunCreateResponse(runId=run_id, status="queued")
        try:
            return registry.create_run(
                job_title=request.jobTitle.strip(),
                jd_text=request.jdText.strip(),
                sourcing_preference_text=request.sourcingPreferenceText.strip(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/stream-token", status_code=204)
    def create_run_stream_token(
        run_id: str,
        response: Response,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> Response:
        run = store.get_run(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Not found.")
        token = issue_stream_token(
            secret=app_settings.liepin_session_store_key_id,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            subject_type="run",
            subject_id=run_id,
        )
        response.set_cookie(
            "liepin_stream_token",
            token,
            max_age=60,
            httponly=True,
            samesite="lax",
            secure=False,
            path=f"/api/runs/{run_id}/events",
        )
        response.status_code = 204
        return response

    @app.get("/api/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        request: Request,
        liepin_stream_token: Annotated[str | None, Cookie()] = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> EventSourceResponse:
        scope = _scope_from_stream_cookie(
            token=liepin_stream_token,
            settings=app_settings,
            subject_type="run",
            subject_id=run_id,
            request=request,
        )
        return EventSourceResponse(
            _event_generator(
                request=request,
                store=store,
                scope=scope,
                subject_type="run",
                subject_id=run_id,
                after_sequence=_sequence_from_header(last_event_id),
            ),
            ping=15,
            send_timeout=5,
        )

    @app.get("/api/runs/{run_id}/results")
    def get_run_results(
        run_id: str,
        scope: LiepinScope = Depends(require_liepin_scope),
    ) -> LiepinRunResultsResponse:
        run = store.get_run(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return LiepinRunResultsResponse(runId=run_id, results=[])

    @app.get("/api/runs/{run_id}/candidates/{candidate_id}")
    def get_candidate_detail(run_id: str, candidate_id: str) -> CandidateDetailResponse:
        try:
            return registry.get_candidate_detail(run_id, candidate_id)
        except (RunNotFoundError, CandidateNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Not found.") from exc
        except RunNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> RunStatusResponse:
        try:
            return registry.get_run_response(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Not found.") from exc

    return app


def _liepin_db_path(settings: AppSettings) -> Path:
    path = Path(settings.liepin_connector_db_path)
    if path.is_absolute():
        return path
    if settings.workspace_root:
        return Path(settings.workspace_root) / path
    return path


def _gate_response(gate_ref: str, gate: ComplianceGate, scope: LiepinScope) -> LiepinComplianceGateResponse:
    return LiepinComplianceGateResponse(
        gateRef=gate_ref,
        tenantId=scope.tenant_id,
        workspaceId=scope.workspace_id,
        actorId=scope.actor_id,
        status=gate.status,
        approvedPurposes=gate.approved_purposes,
        orgName=gate.org_name,
        orgDomain=gate.org_domain,
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
    payload = read_stream_token_payload(token, secret=settings.liepin_session_store_key_id)
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
            subject_type=subject_type,
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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sequence_from_header(last_event_id: str | None) -> int:
    if last_event_id is None:
        return 0
    try:
        return max(0, int(last_event_id))
    except ValueError:
        return 0


def create_server(host: str, port: int, registry: RunRegistry) -> ThreadingHTTPServer:
    class UiApiHandler(BaseHTTPRequestHandler):
        server_version = "SeekTalentUiApi/0.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/runs":
                self._send_not_found()
                return
            try:
                payload = self._read_json()
                request = RunCreateRequest.model_validate(payload)
                if request.provider == "liepin":
                    self._send_json(
                        HTTPStatus.FORBIDDEN,
                        {"error": "Liepin runs require the FastAPI scoped API."},
                    )
                    return
                response = registry.create_run(
                    job_title=request.jobTitle.strip(),
                    jd_text=request.jdText.strip(),
                    sourcing_preference_text=request.sourcingPreferenceText.strip(),
                )
            except json.JSONDecodeError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON body: {exc.msg}"})
                return
            except ValidationError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": exc.errors()})
                return
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.CREATED, response.model_dump(mode="json"))

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            detail_match = re.fullmatch(r"/api/runs/([^/]+)/candidates/([^/]+)", path)
            if detail_match is not None:
                run_id = unquote(detail_match.group(1))
                candidate_id = unquote(detail_match.group(2))
                try:
                    detail = registry.get_candidate_detail(run_id, candidate_id)
                except RunNotFoundError:
                    self._send_not_found()
                    return
                except CandidateNotFoundError:
                    self._send_not_found()
                    return
                except RunNotReadyError as exc:
                    self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
                    return
                self._send_json(HTTPStatus.OK, detail.model_dump(mode="json"))
                return

            run_match = re.fullmatch(r"/api/runs/([^/]+)", path)
            if run_match is None:
                self._send_not_found()
                return

            run_id = unquote(run_match.group(1))
            try:
                payload = registry.get_run_response(run_id)
            except RunNotFoundError:
                self._send_not_found()
                return
            self._send_json(HTTPStatus.OK, payload.model_dump(mode="json"))

        def _read_json(self) -> dict[str, object]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Missing Content-Length header.")
            content_length = int(raw_length)
            body = self.rfile.read(content_length)
            return json.loads(body.decode("utf-8"))

        def _send_json(self, status: HTTPStatus, payload: object) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_not_found(self) -> None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    server = ThreadingHTTPServer((host, port), UiApiHandler)
    server.daemon_threads = True
    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local API server for the SeekTalent minimal web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--mock-cts", dest="mock_cts", action="store_true", default=None)
    parser.add_argument("--real-cts", dest="mock_cts", action="store_false")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_process_env()
    settings = AppSettings().with_overrides(mock_cts=args.mock_cts)
    registry = RunRegistry(settings)
    print(f"SeekTalent UI API listening on http://{args.host}:{args.port}")
    try:
        uvicorn.run(create_app(registry, settings=settings), host=args.host, port=args.port)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
