from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import cast

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderCapabilities
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.providers.liepin.client import EventCallback
from seektalent.providers.liepin.client import LiepinWorkerClient
from seektalent.providers.liepin.client import LiepinWorkerModeError
from seektalent.providers.liepin.models import LiepinConnectionRow
from seektalent.providers.liepin.store import UNSAFE_PAYLOAD_KEYS
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.store import _normalize_payload_key
from seektalent.storage.json import sha256_json


UNSAFE_CANDIDATE_RAW_KEYS = {_normalize_payload_key(key) for key in UNSAFE_PAYLOAD_KEYS}


class LiepinDetailOpenPlanRequired(LiepinWorkerModeError):
    pass


@dataclass(frozen=True)
class _LiepinLiveScope:
    tenant_id: str
    workspace_id: str
    actor_id: str
    connection_id: str
    compliance_gate_ref: str


class LiepinProviderAdapter:
    name = "liepin"

    def __init__(
        self,
        settings: AppSettings,
        *,
        worker_client: LiepinWorkerClient | None = None,
        worker_event_callback: EventCallback | None = None,
        store: LiepinStore | None = None,
    ) -> None:
        self.settings = settings
        self.worker_client = worker_client
        self.worker_event_callback = worker_event_callback
        self.store = store

    def describe_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_structured_filters=True,
            supports_detail_fetch=True,
            supports_fetch_mode_summary=True,
            supports_fetch_mode_detail=True,
            paging_mode="cursor",
            recommended_max_concurrency=1,
            has_stable_external_id=True,
            has_stable_dedup_key=True,
        )

    async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
        if self.worker_client is None:
            raise LiepinWorkerModeError("Liepin provider search requires an explicit worker client.")
        if request.fetch_mode == "detail" and _string_context(request, "liepin_detail_open_plan_ref") is None:
            raise LiepinDetailOpenPlanRequired("Liepin detail fetch requires a detail-open plan before worker dispatch.")
        if self.settings.liepin_worker_mode in {"managed_local", "external_http"}:
            scope = _live_scope_from_request(request)
            connection = self._enforce_live_compliance(scope)
            await self.worker_client.ensure_ready(on_event=self.worker_event_callback)
            await self._require_ready_session(
                scope=scope,
                provider_account_hash=connection.provider_account_hash,
            )
        else:
            await self.worker_client.ensure_ready(on_event=self.worker_event_callback)
        result = await self.worker_client.search(request, round_no=round_no, trace_id=trace_id)
        _validate_liepin_search_result(result)
        return result

    def _enforce_live_compliance(self, scope: _LiepinLiveScope) -> LiepinConnectionRow:
        if self.store is None:
            raise LiepinWorkerModeError("Liepin live provider search requires a compliance store.")
        gate = self.store.get_compliance_gate(
            gate_ref=scope.compliance_gate_ref,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
        )
        if gate is None:
            raise LiepinWorkerModeError("Liepin compliance gate is missing.")
        connection = self.store.get_connection(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=scope.connection_id,
        )
        if connection is None:
            raise LiepinWorkerModeError("Liepin connection is missing.")
        if connection.compliance_gate_ref != scope.compliance_gate_ref:
            raise LiepinWorkerModeError("Liepin connection does not match the compliance gate.")
        denial_reason = gate.denial_reason(provider_account_hash=connection.provider_account_hash, purpose="search")
        if denial_reason is not None:
            raise LiepinWorkerModeError(f"Liepin compliance gate denied live search: {denial_reason}.")
        return connection

    async def _require_ready_session(self, *, scope: _LiepinLiveScope, provider_account_hash: str | None) -> None:
        session_status = await cast(Any, self.worker_client).session_status(
            connection_id=scope.connection_id,
            tenant=scope.tenant_id,
            workspace=scope.workspace_id,
            provider_account_hash=provider_account_hash,
        )
        if session_status.status != "ready":
            raise LiepinWorkerModeError(f"Liepin worker session is not ready: {session_status.status}.")
        if session_status.fixture_only:
            raise LiepinWorkerModeError("Liepin live provider search cannot use a fixture-only session.")
        if session_status.provider_account_hash != provider_account_hash:
            raise LiepinWorkerModeError("Liepin worker session provider account hash does not match the connection.")


def _live_scope_from_request(request: SearchRequest) -> _LiepinLiveScope:
    tenant_id = _required_string_context(request, "liepin_tenant_id")
    workspace_id = _required_string_context(request, "liepin_workspace_id")
    actor_id = _required_string_context(request, "liepin_actor_id")
    connection_id = _required_string_context(request, "liepin_connection_id")
    compliance_gate_ref = _required_string_context(request, "liepin_compliance_gate_ref")
    return _LiepinLiveScope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        connection_id=connection_id,
        compliance_gate_ref=compliance_gate_ref,
    )


def _required_string_context(request: SearchRequest, key: str) -> str:
    value = _string_context(request, key)
    if value is None:
        raise LiepinWorkerModeError(f"Liepin live provider search requires {key}.")
    return value


def _string_context(request: SearchRequest, key: str) -> str | None:
    value = request.provider_context.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _validate_liepin_search_result(result: SearchResult) -> None:
    if len(result.provider_snapshots) != len(result.candidates):
        raise LiepinWorkerModeError(
            "Liepin provider snapshot count mismatch: "
            f"candidates={len(result.candidates)}, snapshots={len(result.provider_snapshots)}"
        )
    for candidate, snapshot in zip(result.candidates, result.provider_snapshots, strict=True):
        if snapshot.provider_name != "liepin":
            raise LiepinWorkerModeError(
                "Liepin provider snapshot provider mismatch: "
                f"expected=liepin, snapshot={snapshot.provider_name}"
            )
        if snapshot.synthetic_candidate_fingerprint != candidate.dedup_key:
            raise LiepinWorkerModeError(
                "Liepin provider snapshot fingerprint mismatch: "
                f"candidate={candidate.dedup_key}, snapshot={snapshot.synthetic_candidate_fingerprint}"
            )
        snapshot_payload_hash = sha256_json(snapshot.raw_payload)
        if candidate.snapshot_sha256 != snapshot_payload_hash:
            raise LiepinWorkerModeError(
                "Liepin provider snapshot payload hash mismatch: "
                f"candidate={candidate.snapshot_sha256}, snapshot={snapshot_payload_hash}"
            )
        unsafe_key = _unsafe_candidate_raw_key(candidate.raw)
        if unsafe_key is not None:
            raise LiepinWorkerModeError(f"Liepin unsafe candidate raw key rejected: {unsafe_key}.")


def _unsafe_candidate_raw_key(value: object) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and _normalize_payload_key(key) in UNSAFE_CANDIDATE_RAW_KEYS:
                return key
            unsafe_key = _unsafe_candidate_raw_key(nested)
            if unsafe_key is not None:
                return unsafe_key
    if isinstance(value, list):
        for nested in value:
            unsafe_key = _unsafe_candidate_raw_key(nested)
            if unsafe_key is not None:
                return unsafe_key
    return None
