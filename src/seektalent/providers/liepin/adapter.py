from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn, Protocol, cast

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderCapabilities, ProviderFirstPageExpansionError, ProviderFirstPageExpansionResult, ProviderSearchContinuation, ProviderSearchError
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.providers.liepin.client import EventCallback
from seektalent.providers.liepin.client import LiepinWorkerClient
from seektalent.providers.liepin.client import LiepinWorkerModeError
from seektalent.providers.liepin.client import is_detail_open_claim_capable_liepin_worker
from seektalent.providers.liepin.client import is_live_liepin_worker_mode
from seektalent.providers.liepin.connection_safety import (
    DEFAULT_SENSITIVE_MATERIAL_POLICY_ID,
    ProviderConnectionSafetyRecord,
    ProviderConnectionSafetyValidationError,
    TransportMode,
    validate_provider_connection_safety,
)
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger, DetailOpenClaimSearchContext
from seektalent.providers.liepin.models import LiepinConnectionRow
from seektalent.providers.liepin.policy import LiepinCardCandidate
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.store import has_unsafe_payload
from seektalent.providers.liepin.verified_loop import execute_liepin_detail_open_plan
from seektalent.providers.liepin.worker_contracts import OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT
from seektalent.storage.json import sha256_json


CONNECTION_SAFETY_POLICY_VERSION = "liepin-connection-safety-policy-v1"
CONNECTION_SAFETY_TTL = timedelta(hours=12)


class LiepinDetailOpenPlanRequired(LiepinWorkerModeError):
    pass


@dataclass(frozen=True)
class _LiepinLiveScope:
    tenant_id: str
    workspace_id: str
    actor_id: str
    connection_id: str
    compliance_gate_ref: str


class ProviderConnectionSafetyResolver(Protocol):
    def resolve_liepin_connection_safety(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        compliance_gate_ref: str,
        provider_account_hash: str,
        requested_transport: TransportMode,
        now: datetime,
    ) -> ProviderConnectionSafetyRecord | None: ...


class LiepinStoreConnectionSafetyResolver:
    def __init__(self, store: LiepinStore) -> None:
        self.store = store

    def resolve_liepin_connection_safety(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        compliance_gate_ref: str,
        provider_account_hash: str,
        requested_transport: TransportMode,
        now: datetime,
    ) -> ProviderConnectionSafetyRecord | None:
        connection = self.store.get_connection(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            connection_id=connection_id,
        )
        if (
            connection is None
            or connection.status != "connected"
            or connection.compliance_gate_ref != compliance_gate_ref
            or connection.provider_account_hash != provider_account_hash
        ):
            return None
        gate = self.store.get_compliance_gate(
            gate_ref=compliance_gate_ref,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        if (
            gate is None
            or gate.denial_reason(provider_account_hash=provider_account_hash, purpose="search") is not None
        ):
            return None
        session = self.store.get_session_metadata(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            connection_id=connection_id,
        )
        if (
            session is None
            or session.get("status") != "connected"
            or session.get("provider_account_hash") != provider_account_hash
            or session.get("revoked_at") is not None
        ):
            return None
        verified_at = _parse_aware_datetime(session.get("session_updated_at"))
        if verified_at is None:
            return None
        expires_at = verified_at + CONNECTION_SAFETY_TTL
        return ProviderConnectionSafetyRecord(
            schema_version="provider-connection-safety-v1",
            provider="liepin",
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=actor_id,
            provider_account_hash=provider_account_hash,
            login_state="verified" if expires_at > now else "expired",
            connection_owner_verified=True,
            sensitive_material_policy_id=DEFAULT_SENSITIVE_MATERIAL_POLICY_ID,
            transport_policy="local_only",
            verified_at=verified_at,
            expires_at=expires_at,
            issued_by="workflow_runtime",
            policy_version=CONNECTION_SAFETY_POLICY_VERSION,
        )


class LiepinProviderAdapter:
    name = "liepin"

    def __init__(
        self,
        settings: AppSettings,
        *,
        worker_client: LiepinWorkerClient | None = None,
        worker_event_callback: EventCallback | None = None,
        worker_search_started_callback: Callable[[], None] | None = None,
        store: LiepinStore | None = None,
        connection_safety_resolver: ProviderConnectionSafetyResolver | None = None,
    ) -> None:
        self.settings = settings
        self.worker_client = worker_client
        self.worker_event_callback = worker_event_callback
        self.worker_search_started_callback = worker_search_started_callback
        self.store = store
        self.connection_safety_resolver = connection_safety_resolver or (
            LiepinStoreConnectionSafetyResolver(store) if store is not None else None
        )

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
        return await self._search(
            request,
            round_no=round_no,
            trace_id=trace_id,
        )

    async def search_with_detail_open_claim_ledger(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        detail_open_claim_ledger: DetailOpenClaimLedger,
        logical_round_no: int,
        query_instance_id: str,
    ) -> SearchResult:
        if not is_detail_open_claim_capable_liepin_worker(self.worker_client):
            raise ValueError("liepin_detail_open_claim_route_requires_opencli_worker")
        if request.provider_context.get("liepin_fetch_strategy") != "detail_backed_resume_search":
            raise ValueError("liepin_detail_open_claim_route_requires_detail_backed_strategy")
        DetailOpenClaimSearchContext(
            detail_open_claim_ledger=detail_open_claim_ledger,
            logical_round_no=logical_round_no,
            query_instance_id=query_instance_id,
        )
        return await self._search(
            request,
            round_no=round_no,
            trace_id=trace_id,
            detail_open_claim_ledger=detail_open_claim_ledger,
            logical_round_no=logical_round_no,
            query_instance_id=query_instance_id,
        )

    async def handle_first_page_continuation_with_detail_open_claim_ledger(self, *, action: str,
            continuation: ProviderSearchContinuation, detail_open_claim_ledger: DetailOpenClaimLedger,
            logical_round_no: int, query_instance_id: str) -> ProviderFirstPageExpansionResult:
        worker = self.worker_client
        handler = getattr(worker, "handle_first_page_continuation_with_detail_open_claim_ledger", None)
        if not callable(handler):
            raise LiepinWorkerModeError("Liepin continuation requires an OpenCLI worker.")
        if action == "discard":
            return await handler(action=action, continuation=continuation,
                detail_open_claim_ledger=detail_open_claim_ledger,
                logical_round_no=logical_round_no, query_instance_id=query_instance_id)
        result: ProviderFirstPageExpansionResult | None = None
        primary_error: ProviderSearchError | LiepinWorkerModeError | None = None
        deleted: ProviderFirstPageExpansionResult | None = None
        try:
            try:
                result = await handler(action="expand", continuation=continuation,
                    detail_open_claim_ledger=detail_open_claim_ledger,
                    logical_round_no=logical_round_no, query_instance_id=query_instance_id)
            except (ProviderSearchError, LiepinWorkerModeError) as exc:
                primary_error = exc
        finally:
            try:
                deleted = await handler(action="discard", continuation=continuation,
                    detail_open_claim_ledger=detail_open_claim_ledger,
                    logical_round_no=logical_round_no, query_instance_id=query_instance_id)
            except OSError:
                deleted = None
        continuation_deleted = deleted is not None and deleted.continuation_deleted
        cleanup_reason = None if continuation_deleted else "liepin_first_page_continuation_cleanup_failed"
        if primary_error is not None:
            if isinstance(primary_error, ProviderSearchError):
                raise ProviderFirstPageExpansionError(str(primary_error), status="failed",
                    safe_reason_code=primary_error.reason_code,
                    continuation_deleted=continuation_deleted) from primary_error
            if isinstance(primary_error, LiepinWorkerModeError):
                raise ProviderFirstPageExpansionError(str(primary_error), status="blocked",
                    safe_reason_code=str(primary_error.code or "liepin_first_page_expansion_blocked"),
                    continuation_deleted=continuation_deleted) from primary_error
            raise primary_error
        assert result is not None
        return replace(result, continuation_deleted=continuation_deleted,
            safe_reason_code=cleanup_reason or result.safe_reason_code)

    async def _search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        detail_open_claim_ledger: DetailOpenClaimLedger | None = None,
        logical_round_no: int | None = None,
        query_instance_id: str | None = None,
    ) -> SearchResult:
        if self.worker_client is None:
            raise LiepinWorkerModeError("Liepin provider search requires an explicit worker client.")
        connection: LiepinConnectionRow | None = None
        if is_live_liepin_worker_mode(self.settings.liepin_worker_mode):
            scope = _live_scope_from_request(request)
            connection = self._enforce_live_compliance(scope)
            await self.worker_client.ensure_ready(on_event=self.worker_event_callback)
            await self._require_ready_session(
                scope=scope,
                provider_account_hash=connection.provider_account_hash,
            )
            self._enforce_connection_safety(
                scope=scope,
                provider_account_hash=_required_provider_account_hash(connection.provider_account_hash),
                requested_transport=_requested_transport_from_request(request),
            )
        else:
            await self.worker_client.ensure_ready(on_event=self.worker_event_callback)
        if (
            request.fetch_mode == "detail"
            and request.provider_context.get("liepin_fetch_strategy") != "detail_backed_resume_search"
        ):
            if connection is None:
                raise LiepinWorkerModeError("Liepin detail fetch requires a live provider connection.")
            return await self._detail_search(
                request,
                connection=connection,
                round_no=round_no,
                trace_id=trace_id,
            )
        if self.worker_search_started_callback is not None:
            self.worker_search_started_callback()
        provider_account_hash = (
            connection.provider_account_hash
            if connection is not None
            else _string_context(request, "liepin_provider_account_hash")
        )
        if detail_open_claim_ledger is None:
            result = await self.worker_client.search(
                request,
                round_no=round_no,
                trace_id=trace_id,
                provider_account_hash=provider_account_hash,
            )
        else:
            if logical_round_no is None or query_instance_id is None:
                raise ValueError("liepin_detail_open_claim_route_missing_logical_provenance")
            if not is_detail_open_claim_capable_liepin_worker(self.worker_client):
                raise ValueError("liepin_detail_open_claim_route_requires_opencli_worker")
            result = await self.worker_client.search_with_detail_open_claim_ledger(
                request,
                round_no=round_no,
                trace_id=trace_id,
                provider_account_hash=provider_account_hash,
                detail_open_claim_ledger=detail_open_claim_ledger,
                logical_round_no=logical_round_no,
                query_instance_id=query_instance_id,
            )
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
        if not _session_provider_account_matches(
            session_provider_account_hash=session_status.provider_account_hash,
            connection_provider_account_hash=provider_account_hash,
            accept_opencli_local_profile=_accepts_opencli_local_profile_subject(self.settings),
        ):
            raise LiepinWorkerModeError("Liepin worker session provider account hash does not match the connection.")

    def _enforce_connection_safety(
        self,
        *,
        scope: _LiepinLiveScope,
        provider_account_hash: str,
        requested_transport: TransportMode,
    ) -> None:
        resolver = self.connection_safety_resolver
        if resolver is None:
            _raise_liepin_connection_safety_error("connection_safety_missing")
        now = datetime.now(UTC)
        record = resolver.resolve_liepin_connection_safety(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=scope.connection_id,
            compliance_gate_ref=scope.compliance_gate_ref,
            provider_account_hash=provider_account_hash,
            requested_transport=requested_transport,
            now=now,
        )
        try:
            validate_provider_connection_safety(
                record,
                provider="liepin",
                connection_id=scope.connection_id,
                workspace_id=scope.workspace_id,
                user_id=scope.actor_id,
                provider_account_hash=provider_account_hash,
                transport=requested_transport,
                now=now,
            )
        except ProviderConnectionSafetyValidationError as exc:
            _raise_liepin_connection_safety_error(exc.code)

    async def _detail_search(
        self,
        request: SearchRequest,
        *,
        connection: LiepinConnectionRow,
        round_no: int,
        trace_id: str,
    ) -> SearchResult:
        if self.store is None:
            raise LiepinWorkerModeError("Liepin detail fetch requires a compliance store.")
        if connection.provider_account_hash is None:
            raise LiepinWorkerModeError("Liepin detail fetch requires a bound provider account.")
        if self.worker_client is None:
            raise LiepinWorkerModeError("Liepin detail fetch requires an explicit worker client.")
        scope = _live_scope_from_request(request)
        detail_context = _detail_context_from_request(request)
        loop_result = await execute_liepin_detail_open_plan(
            store=self.store,
            worker_client=self.worker_client,
            card_candidates=detail_context.card_candidates,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            actor_id=scope.actor_id,
            connection_id=scope.connection_id,
            provider_account_hash=connection.provider_account_hash,
            budget_date=detail_context.budget_date,
            provider_day_key=detail_context.provider_day_key,
            timezone=detail_context.timezone,
            daily_detail_budget=detail_context.daily_budget,
            detail_open_policy_version=detail_context.open_policy_version,
            detail_open_approval_secret=_required_detail_open_approval_secret(
                self.settings.liepin_detail_open_approval_secret
            ),
            run_id=trace_id,
            query_instance_id=request.provider_context.get("query_instance_id", trace_id),
            query_fingerprint=request.provider_context.get("query_fingerprint", trace_id),
            already_opened_provider_ids=detail_context.already_opened_provider_ids,
            already_seen_weak_fingerprints=detail_context.already_seen_weak_fingerprints,
            min_card_value_score=detail_context.min_card_value_score,
            score_metadata_by_candidate_id=detail_context.score_metadata_by_candidate_id,
        )
        result = SearchResult(
            candidates=[item.candidate for item in loop_result.detail_candidates],
            provider_snapshots=[item.provider_snapshot for item in loop_result.detail_candidates],
            raw_candidate_count=len(loop_result.detail_candidates),
            request_payload={
                "liepin_detail_open_plan_ref": detail_context.open_plan_ref,
                "liepin_detail_open_policy_version": detail_context.open_policy_version,
                "round_no": round_no,
            },
        )
        _validate_liepin_search_result(result)
        return result


@dataclass(frozen=True)
class _LiepinDetailContext:
    open_plan_ref: str
    card_candidates: list[LiepinCardCandidate]
    daily_budget: int
    budget_date: str
    provider_day_key: str
    timezone: str
    open_policy_version: str
    min_card_value_score: float
    already_opened_provider_ids: set[str]
    already_seen_weak_fingerprints: set[str]
    score_metadata_by_candidate_id: dict[str, dict[str, object]]


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


def _requested_transport_from_request(request: SearchRequest) -> TransportMode:
    value = _string_context(request, "liepin_transport_mode")
    if value is None:
        return "local_only"
    if value in {"local_only", "remote_e2e_allowed"}:
        return cast(TransportMode, value)
    _raise_liepin_connection_safety_error("connection_safety_transport_denied")


def _required_provider_account_hash(provider_account_hash: str | None) -> str:
    if provider_account_hash is None:
        _raise_liepin_connection_safety_error("connection_safety_provider_account_mismatch")
    return provider_account_hash


def _accepts_opencli_local_profile_subject(settings: AppSettings) -> bool:
    return settings.liepin_worker_mode == "opencli"


def _session_provider_account_matches(
    *,
    session_provider_account_hash: str | None,
    connection_provider_account_hash: str | None,
    accept_opencli_local_profile: bool,
) -> bool:
    if session_provider_account_hash == connection_provider_account_hash:
        return True
    return accept_opencli_local_profile and session_provider_account_hash == OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT


def _raise_liepin_connection_safety_error(code: str) -> NoReturn:
    raise LiepinWorkerModeError(code, setup_status=code, code=code)


def _parse_aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _detail_context_from_request(request: SearchRequest) -> _LiepinDetailContext:
    open_plan_ref = _string_context(request, "liepin_detail_open_plan_ref")
    if open_plan_ref is None:
        raise LiepinDetailOpenPlanRequired("Liepin detail fetch requires a detail-open plan before worker dispatch.")
    return _LiepinDetailContext(
        open_plan_ref=open_plan_ref,
        card_candidates=_detail_candidates_from_context(request),
        daily_budget=_required_int_context(request, "liepin_detail_daily_budget"),
        budget_date=_required_string_context(request, "liepin_detail_budget_date"),
        provider_day_key=_required_string_context(request, "liepin_detail_provider_day_key"),
        timezone=_required_string_context(request, "liepin_detail_timezone"),
        open_policy_version=_required_string_context(request, "liepin_detail_open_policy_version"),
        min_card_value_score=_optional_float_context(request, "liepin_detail_min_card_value_score", default=0.0),
        already_opened_provider_ids=_optional_string_set_context(
            request,
            "liepin_detail_already_opened_provider_ids_json",
        ),
        already_seen_weak_fingerprints=_optional_string_set_context(
            request,
            "liepin_detail_already_seen_weak_fingerprints_json",
        ),
        score_metadata_by_candidate_id=_optional_score_metadata_context(request),
    )


def _required_detail_open_approval_secret(secret: str | None) -> str:
    if not secret:
        raise LiepinWorkerModeError("Liepin detail fetch requires a detail-open approval secret.")
    return secret


def _detail_candidates_from_context(request: SearchRequest) -> list[LiepinCardCandidate]:
    raw = _required_string_context(request, "liepin_detail_candidates_json")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LiepinWorkerModeError("Liepin detail context has invalid liepin_detail_candidates_json.") from exc
    if not isinstance(payload, list) or not payload:
        raise LiepinWorkerModeError("Liepin detail context requires non-empty liepin_detail_candidates_json.")
    candidates: list[LiepinCardCandidate] = []
    for item in payload:
        if not isinstance(item, dict):
            raise LiepinWorkerModeError("Liepin detail candidate entries must be objects.")
        candidate_id = _required_payload_string(item, "candidate_id")
        candidates.append(
            LiepinCardCandidate(
                candidate_id=candidate_id,
                stable_provider_id=_optional_payload_string(item, "stable_provider_id"),
                weak_fingerprint=_optional_payload_string(item, "weak_fingerprint"),
                card_value_score=_payload_float(item, "card_value_score"),
                detail_url=_optional_payload_string(item, "detail_url"),
            )
        )
    return candidates


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


def _required_int_context(request: SearchRequest, key: str) -> int:
    raw = _required_string_context(request, key)
    try:
        value = int(raw)
    except ValueError as exc:
        raise LiepinWorkerModeError(f"Liepin live provider search requires integer {key}.") from exc
    if value < 0:
        raise LiepinWorkerModeError(f"Liepin live provider search requires non-negative {key}.")
    return value


def _optional_float_context(request: SearchRequest, key: str, *, default: float) -> float:
    raw = _string_context(request, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LiepinWorkerModeError(f"Liepin live provider search requires numeric {key}.") from exc


def _optional_string_set_context(request: SearchRequest, key: str) -> set[str]:
    raw = _string_context(request, key)
    if raw is None:
        return set()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LiepinWorkerModeError(f"Liepin detail context has invalid {key}.") from exc
    if not isinstance(payload, list):
        raise LiepinWorkerModeError(f"Liepin detail context requires list {key}.")
    return {item.strip() for item in payload if isinstance(item, str) and item.strip()}


def _optional_score_metadata_context(request: SearchRequest) -> dict[str, dict[str, object]]:
    raw = _string_context(request, "liepin_detail_score_metadata_json")
    if raw is None:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LiepinWorkerModeError("Liepin detail context has invalid liepin_detail_score_metadata_json.") from exc
    if not isinstance(payload, dict):
        raise LiepinWorkerModeError("Liepin detail context requires object liepin_detail_score_metadata_json.")
    metadata: dict[str, dict[str, object]] = {}
    for candidate_id, item in payload.items():
        if not isinstance(candidate_id, str) or not candidate_id.strip() or not isinstance(item, dict):
            continue
        safe_item: dict[str, object] = {}
        for key in ("card_scorecard_ref", "detail_scorecard_ref"):
            value = item.get(key)
            if isinstance(value, str) and value:
                safe_item[key] = value
        score_delta = item.get("score_delta")
        if isinstance(score_delta, int) and not isinstance(score_delta, bool):
            safe_item["score_delta"] = score_delta
        if safe_item:
            metadata[candidate_id.strip()] = safe_item
    return metadata


def _required_payload_string(payload: dict[str, object], key: str) -> str:
    value = _optional_payload_string(payload, key)
    if value is None:
        raise LiepinWorkerModeError(f"Liepin detail candidate requires {key}.")
    return value


def _optional_payload_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _payload_float(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, int | float):
        return float(value)
    raise LiepinWorkerModeError(f"Liepin detail candidate requires numeric {key}.")


def _validate_liepin_search_result(result: SearchResult) -> None:
    if len(result.provider_snapshots) != len(result.candidates):
        raise LiepinWorkerModeError(
            "Liepin provider snapshot count mismatch: "
            f"candidates={len(result.candidates)}, snapshots={len(result.provider_snapshots)}"
        )
    for candidate, snapshot in zip(result.candidates, result.provider_snapshots, strict=True):
        if snapshot.provider_name != "liepin":
            raise LiepinWorkerModeError(
                f"Liepin provider snapshot provider mismatch: expected=liepin, snapshot={snapshot.provider_name}"
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
        if has_unsafe_payload(candidate.raw):
            raise LiepinWorkerModeError("Liepin unsafe candidate raw value rejected.")
