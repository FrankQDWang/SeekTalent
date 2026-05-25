from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol, cast

from seektalent.core.retrieval.provider_contract import SearchRequest, SearchResult
from seektalent.providers.liepin.client import liepin_resume_search_response_to_search_result
from seektalent.providers.liepin.pi_executor import PiLiepinExecutor, PiLiepinResultStatus
from seektalent.providers.liepin.worker_contracts import (
    LiepinDetailOpenRequest,
    LiepinDetailOpenResponse,
    LiepinWorkerModeError,
    LiepinWorkerPartialSearchError,
    LoginHandoff,
    LoginRelayCompleteResult,
    LoginRelayInputResult,
    LoginRelaySnapshot,
    SessionStatus,
)


class OpenCliStatusProbe(Protocol):
    def status(self) -> Any: ...


class LiepinPiWorkerClient:
    def __init__(
        self,
        executor: PiLiepinExecutor,
        *,
        session_id: str,
        connection_id: str,
        provider_account_lock_key: str,
        dokobot_tool_name: str = "dokobot",
        expected_observed_tool_names: tuple[str, ...] = (),
        expected_opencli_observed_tool_names: tuple[str, ...] = (),
        expected_opencli_declared_tool_names: tuple[str, ...] = (),
        opencli_status_probe: OpenCliStatusProbe | None = None,
        opencli_status_probe_attempts: int = 3,
        opencli_status_retry_delay_seconds: float = 1.0,
    ) -> None:
        self._executor = executor
        self._session_id = session_id
        self._connection_id = connection_id
        self._provider_account_lock_key = provider_account_lock_key
        self._dokobot_tool_name = dokobot_tool_name
        self._expected_observed_tool_names = expected_observed_tool_names
        self._expected_opencli_observed_tool_names = expected_opencli_observed_tool_names
        self._expected_opencli_declared_tool_names = expected_opencli_declared_tool_names
        self._uses_opencli_backend = bool(
            expected_opencli_observed_tool_names or expected_opencli_declared_tool_names
        )
        self._opencli_status_probe = opencli_status_probe
        self._opencli_status_probe_attempts = max(1, opencli_status_probe_attempts)
        self._opencli_status_retry_delay_seconds = max(0.0, opencli_status_retry_delay_seconds)
        self._ready_checked = False

    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event
        if self._uses_opencli_backend:
            await self._ensure_opencli_ready()
            return
        capability = await asyncio.to_thread(
            self._executor.probe_capabilities,
            expected_dokobot_tool_name=self._dokobot_tool_name,
            expected_observed_tool_names=self._expected_observed_tool_names,
            expected_opencli_observed_tool_names=self._expected_opencli_observed_tool_names,
            expected_opencli_declared_tool_names=self._expected_opencli_declared_tool_names,
        )
        if not capability.ready:
            raise LiepinWorkerModeError(
                "Liepin PI worker is not ready.",
                code=capability.safe_reason_code or "blocked_backend_unavailable",
            )
        self._ready_checked = True

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        del round_no
        if self._uses_opencli_backend and not self._ready_checked:
            await self.ensure_ready()
        connection_id = _context_string(request.provider_context.get("liepin_connection_id")) or self._connection_id
        task_provider_account_hash = (
            _context_string(request.provider_context.get("liepin_provider_account_hash"))
            or provider_account_hash
        )
        result = await asyncio.to_thread(
            self._executor.search_resumes,
            source_run_id=trace_id,
            keyword_query=request.keyword_query or " ".join(request.query_terms),
            query_terms=tuple(request.query_terms),
            max_pages=_positive_int(request.provider_context.get("liepin_max_pages"), default=1),
            target_resumes=request.page_size,
            max_cards=_positive_int(request.provider_context.get("liepin_max_cards"), default=request.page_size),
            must_haves=_json_string_tuple(request.provider_context.get("liepin_must_haves_json")),
            nice_to_haves=_json_string_tuple(request.provider_context.get("liepin_nice_to_haves_json")),
            connection_id=connection_id,
            provider_account_hash=task_provider_account_hash,
            native_filters=_native_filters_from_request(request),
        )
        if result.status == PiLiepinResultStatus.SUCCEEDED and result.resume_search is not None:
            return liepin_resume_search_response_to_search_result(result.resume_search)
        if result.status == PiLiepinResultStatus.PARTIAL and result.resume_search is not None:
            partial_search = liepin_resume_search_response_to_search_result(result.resume_search)
            raise LiepinWorkerPartialSearchError(
                "Liepin PI resume search returned partial resumes.",
                code=result.safe_reason_code,
                partial_search_result=partial_search,
                cards_collected=len(partial_search.candidates),
            )
        raise LiepinWorkerModeError(
            "Liepin PI resume search blocked.",
            code=result.safe_reason_code,
        )

    async def _ensure_opencli_ready(self) -> None:
        if self._opencli_status_probe is None:
            self._ready_checked = True
            return
        last_reason_code = "liepin_opencli_status_unavailable"
        for attempt in range(self._opencli_status_probe_attempts):
            result = await asyncio.to_thread(self._opencli_status_probe.status)
            if result.ok:
                self._ready_checked = True
                return
            last_reason_code = str(result.safe_reason_code or last_reason_code)
            if attempt + 1 < self._opencli_status_probe_attempts and self._opencli_status_retry_delay_seconds:
                await asyncio.sleep(self._opencli_status_retry_delay_seconds)
        raise LiepinWorkerModeError(
            "Liepin OpenCLI browser channel is not ready.",
            code=last_reason_code,
        )

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        if self._uses_opencli_backend:
            if not self._ready_checked:
                await self.ensure_ready()
            return SessionStatus(
                connectionId=connection_id,
                status="ready",
                provider_account_hash=provider_account_hash or self._provider_account_lock_key,
            )
        try:
            status = await asyncio.to_thread(
                self._executor.probe_session,
                connection_id=connection_id,
            )
        except Exception as exc:
            raise LiepinWorkerModeError(
                "Liepin PI worker session probe is unavailable.",
                code="blocked_backend_unavailable",
            ) from exc
        del tenant, workspace, provider_account_hash
        if status.status == "ready" and status.provider_account_hash:
            return SessionStatus(
                connectionId=connection_id,
                status="ready",
                provider_account_hash=status.provider_account_hash,
            )
        if status.status == "failed":
            raise LiepinWorkerModeError(
                "Liepin PI worker session probe failed.",
                code=status.safe_reason_code or "blocked_backend_unavailable",
            )
        return SessionStatus(connectionId=connection_id, status="login_required", provider_account_hash=None)

    async def login_handoff(
        self,
        *,
        connection_id: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        provider_account_hash: str | None = None,
    ) -> LoginHandoff:
        del connection_id, tenant_id, workspace_id, provider_account_hash
        raise LiepinWorkerModeError(
            "Liepin PI worker uses the user's already logged-in browser; login relay is not exposed.",
            code="blocked_login_required",
        )

    async def complete_login_relay(self, *, connection_id: str) -> LoginRelayCompleteResult:
        del connection_id
        raise LiepinWorkerModeError(
            "Liepin PI worker uses the user's already logged-in browser; login relay is not exposed.",
            code="blocked_login_required",
        )

    async def login_relay_snapshot(self, *, connection_id: str) -> LoginRelaySnapshot:
        del connection_id
        raise LiepinWorkerModeError("Liepin PI worker client does not expose frame login snapshots.")

    async def submit_login_relay_input(
        self,
        *,
        connection_id: str,
        action: str,
        x: float | None = None,
        y: float | None = None,
        text: str | None = None,
        key: str | None = None,
    ) -> LoginRelayInputResult:
        del connection_id, action, x, y, text, key
        raise LiepinWorkerModeError("Liepin PI worker client does not accept frame login input.")

    async def open_details(self, request: LiepinDetailOpenRequest) -> LiepinDetailOpenResponse:
        del request
        raise LiepinWorkerModeError("Liepin PI worker client does not open detail pages through card search.")


def _positive_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _context_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _native_filters_from_request(request: SearchRequest) -> dict[str, object] | None:
    raw = request.provider_context.get("liepin_native_filters_json")
    if not isinstance(raw, str) or not raw.strip():
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return None
    return parsed


def _json_string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item.strip() for item in parsed if isinstance(item, str) and item.strip())
