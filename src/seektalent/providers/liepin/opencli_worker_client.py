from __future__ import annotations

import asyncio
import json
import threading
from typing import cast

from seektalent.core.retrieval.provider_contract import SearchRequest, SearchResult
from seektalent.providers.liepin.client import liepin_resume_search_response_to_search_result
from seektalent.providers.liepin.opencli_retriever import (
    LiepinOpenCliResumeRequest,
    LiepinOpenCliResumeRetriever,
)
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


_OPENCLI_SEARCH_LOCK = threading.Lock()


class LiepinOpenCliWorkerClient:
    def __init__(
        self,
        *,
        retriever: LiepinOpenCliResumeRetriever,
        connection_id: str,
        provider_account_hash: str,
    ) -> None:
        self._retriever = retriever
        self._connection_id = connection_id
        self._provider_account_hash = provider_account_hash

    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event
        try:
            await asyncio.to_thread(self._retriever.ensure_ready)
        except RuntimeError as exc:
            raise LiepinWorkerModeError("Liepin OpenCLI worker is not ready.", code=str(exc)) from exc

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        del round_no, provider_account_hash
        requirement_sheet = _json_object(request.provider_context.get("liepin_requirement_sheet_json"))
        if requirement_sheet is None:
            raise LiepinWorkerModeError(
                "Liepin OpenCLI resume search requires the canonical requirement sheet.",
                code="requirement_sheet_missing",
            )
        try:
            response = await asyncio.to_thread(
                self._search_resumes_sync,
                LiepinOpenCliResumeRequest(
                    source_run_id=trace_id,
                    keyword_query=request.keyword_query or " ".join(request.query_terms),
                    query_terms=tuple(request.query_terms),
                    target_resumes=request.page_size,
                    max_cards=_positive_int(request.provider_context.get("liepin_max_cards"), default=request.page_size),
                    max_pages=_positive_int(request.provider_context.get("liepin_max_pages"), default=1),
                    requirement_sheet=requirement_sheet,
                    native_filters=_native_filters_from_request(request),
                ),
            )
        except RuntimeError as exc:
            raise LiepinWorkerModeError("Liepin OpenCLI resume search blocked.", code=str(exc)) from exc
        search_result = liepin_resume_search_response_to_search_result(response)
        if response.request_payload.get("opencliStatus") == "partial":
            raise LiepinWorkerPartialSearchError(
                "Liepin OpenCLI resume search returned partial resumes.",
                code=str(response.request_payload.get("safeReasonCode") or "partial_timeout"),
                partial_search_result=search_result,
                cards_collected=len(search_result.candidates),
            )
        if response.request_payload.get("opencliStatus") in {"blocked", "failed"}:
            error = LiepinWorkerModeError(
                "Liepin OpenCLI resume search blocked.",
                code=str(response.request_payload.get("safeReasonCode") or "failed_provider_error"),
            )
            error.partial_search_result = search_result
            error.cards_collected = len(search_result.candidates)
            raise error
        return search_result

    def _search_resumes_sync(self, request: LiepinOpenCliResumeRequest):
        with _OPENCLI_SEARCH_LOCK:
            return self._retriever.search_resumes(request)

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        del tenant, workspace
        return SessionStatus(
            connectionId=connection_id or self._connection_id,
            status="ready",
            providerAccountHash=provider_account_hash or self._provider_account_hash,
        )

    async def open_details(self, request: LiepinDetailOpenRequest) -> LiepinDetailOpenResponse:
        del request
        raise LiepinWorkerModeError("Liepin OpenCLI worker performs detail-backed search directly.")

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
            "Liepin OpenCLI worker uses the user's logged-in local Chrome state.",
            code="liepin_opencli_login_required",
        )

    async def login_relay_snapshot(self, *, connection_id: str) -> LoginRelaySnapshot:
        del connection_id
        raise LiepinWorkerModeError("Liepin OpenCLI worker does not expose login relay snapshots.")

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
        raise LiepinWorkerModeError("Liepin OpenCLI worker does not accept login relay input.")

    async def complete_login_relay(self, *, connection_id: str) -> LoginRelayCompleteResult:
        del connection_id
        raise LiepinWorkerModeError("Liepin OpenCLI worker does not complete login relay.")


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(cast(object, value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else None


def _native_filters_from_request(request: SearchRequest) -> dict[str, object] | None:
    raw = request.provider_context.get("liepin_native_filters_json")
    if not isinstance(raw, str) or not raw.strip():
        return None
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else None
