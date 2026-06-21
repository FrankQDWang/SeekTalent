from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from sse_starlette import EventSourceResponse

from seektalent.config import AppSettings
from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.models import SubjectType
from seektalent.providers.liepin.security import issue_stream_token, read_stream_token_payload
from seektalent.providers.liepin.store import LiepinStore
from seektalent_ui.models import (
    LiepinComplianceGateActionResponse,
    LiepinComplianceGateConnectionRequest,
    LiepinComplianceGateCreateRequest,
    LiepinComplianceGateResponse,
    LiepinConnectionCreateRequest,
    LiepinConnectionResponse,
    LiepinLoginUrlResponse,
)
from seektalent_ui.workbench_paths import liepin_db_path


@dataclass(frozen=True)
class LiepinScope:
    tenant_id: str
    workspace_id: str
    actor_id: str


def create_liepin_router(*, settings: AppSettings) -> APIRouter:
    store = LiepinStore(liepin_db_path(settings))
    router = APIRouter()

    def require_liepin_scope(
        x_seektalent_api_key: Annotated[str | None, Header(alias="X-SeekTalent-API-Key")] = None,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
        x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-ID")] = None,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> LiepinScope:
        if x_seektalent_api_key is None:
            raise HTTPException(status_code=401, detail="Missing X-SeekTalent-API-Key header.")
        if x_seektalent_api_key != settings.liepin_api_token:
            raise HTTPException(status_code=403, detail="Invalid X-SeekTalent-API-Key header.")
        if not x_tenant_id or not x_workspace_id or not x_actor_id:
            raise HTTPException(status_code=400, detail="Missing Liepin tenant, workspace, or actor scope header.")
        return LiepinScope(tenant_id=x_tenant_id, workspace_id=x_workspace_id, actor_id=x_actor_id)

    @router.post("/api/liepin/compliance-gates", status_code=201)
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

    @router.get("/api/liepin/compliance-gates/{gate_ref}")
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

    @router.post("/api/liepin/compliance-gates/{gate_ref}/bind-account")
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
            secret=_required_liepin_account_binding_secret(settings),
        )
        if account_hash is None:
            raise HTTPException(status_code=403, detail="account binding failed")
        return LiepinComplianceGateActionResponse(gateRef=gate_ref, status="approved")

    @router.post("/api/liepin/compliance-gates/{gate_ref}/verify")
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

    @router.post("/api/liepin/connections", status_code=201)
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

    @router.get("/api/liepin/connections/{connection_id}")
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

    @router.post("/api/liepin/connections/{connection_id}/login-url")
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

    @router.post("/api/liepin/connections/{connection_id}/stream-token", status_code=204)
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
            secret=_required_liepin_stream_token_secret(settings),
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            subject_type="connection",
            subject_id=connection.connection_id,
        )
        response.set_cookie(
            "liepin_stream_token",
            token,
            max_age=60,
            httponly=True,
            samesite="lax",
            secure=_stream_cookie_secure(request),
            path="/api/liepin/connections",
        )
        response.status_code = 204
        return response

    @router.get("/api/liepin/connections/{connection_id}/events")
    async def stream_connection_events(
        connection_id: str,
        request: Request,
        liepin_stream_token: Annotated[str | None, Cookie()] = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> EventSourceResponse:
        scope = _scope_from_stream_cookie(
            token=liepin_stream_token,
            settings=settings,
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

    return router


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
    subject_type: SubjectType,
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
