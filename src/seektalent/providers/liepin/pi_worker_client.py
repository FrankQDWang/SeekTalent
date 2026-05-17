from __future__ import annotations

import asyncio

from seektalent.core.retrieval.provider_contract import SearchRequest, SearchResult
from seektalent.providers.liepin.client import liepin_card_search_response_to_search_result
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


class LiepinPiWorkerClient:
    def __init__(
        self,
        executor: PiLiepinExecutor,
        *,
        session_id: str,
        connection_id: str,
        provider_account_lock_key: str,
        dokobot_tool_name: str = "dokobot",
    ) -> None:
        self._executor = executor
        self._session_id = session_id
        self._connection_id = connection_id
        self._provider_account_lock_key = provider_account_lock_key
        self._dokobot_tool_name = dokobot_tool_name

    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event
        capability = await asyncio.to_thread(
            self._executor.probe_capabilities,
            expected_dokobot_tool_name=self._dokobot_tool_name,
        )
        if not capability.ready:
            raise LiepinWorkerModeError(
                "Liepin PI worker is not ready.",
                code=capability.safe_reason_code or "blocked_backend_unavailable",
            )

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        del round_no
        connection_id = _context_string(request.provider_context.get("liepin_connection_id")) or self._connection_id
        task_provider_account_hash = (
            _context_string(request.provider_context.get("liepin_provider_account_hash"))
            or provider_account_hash
        )
        result = await asyncio.to_thread(
            self._executor.search_cards,
            source_run_id=trace_id,
            keyword_query=request.keyword_query or " ".join(request.query_terms),
            query_terms=tuple(request.query_terms),
            max_pages=_positive_int(request.provider_context.get("liepin_max_pages"), default=1),
            page_size=request.page_size,
            max_cards=_positive_int(request.provider_context.get("liepin_max_cards"), default=request.page_size),
            connection_id=connection_id,
            provider_account_hash=task_provider_account_hash,
        )
        if result.status == PiLiepinResultStatus.SUCCEEDED and result.card_search is not None:
            return liepin_card_search_response_to_search_result(result.card_search)
        if result.status == PiLiepinResultStatus.PARTIAL and result.card_search is not None:
            partial_search = liepin_card_search_response_to_search_result(result.card_search)
            raise LiepinWorkerPartialSearchError(
                "Liepin PI card search returned partial cards.",
                code=result.safe_reason_code,
                partial_search_result=partial_search,
                cards_collected=len(partial_search.candidates),
            )
        raise LiepinWorkerModeError(
            "Liepin PI card search blocked.",
            code=result.safe_reason_code,
        )

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
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
        del tenant, workspace
        if status.status == "ready" and status.provider_account_hash:
            if provider_account_hash is not None and status.provider_account_hash != provider_account_hash:
                return SessionStatus(connectionId=connection_id, status="login_required", providerAccountHash=None)
            return SessionStatus(
                connectionId=connection_id,
                status="ready",
                providerAccountHash=status.provider_account_hash,
            )
        return SessionStatus(connectionId=connection_id, status="login_required", providerAccountHash=None)

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
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _context_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
