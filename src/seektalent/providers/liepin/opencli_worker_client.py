from __future__ import annotations

import asyncio
import json
import threading

from seektalent.core.retrieval.provider_contract import SearchRequest, SearchResult
from seektalent.providers.liepin.client import liepin_resume_search_response_to_search_result
from seektalent.providers.liepin.detail_open_claims import DetailOpenClaimLedger, DetailOpenClaimSearchContext
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
        return await self._search(request, trace_id=trace_id)

    async def search_with_detail_open_claim_ledger(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
        detail_open_claim_ledger: DetailOpenClaimLedger,
        logical_round_no: int,
        query_instance_id: str,
    ) -> SearchResult:
        del round_no, provider_account_hash
        detail_open_claim_context = DetailOpenClaimSearchContext(
            detail_open_claim_ledger=detail_open_claim_ledger,
            logical_round_no=logical_round_no,
            query_instance_id=query_instance_id,
        )
        return await self._search(
            request,
            trace_id=trace_id,
            detail_open_claim_context=detail_open_claim_context,
        )

    async def _search(
        self,
        request: SearchRequest,
        *,
        trace_id: str,
        detail_open_claim_context: DetailOpenClaimSearchContext | None = None,
    ) -> SearchResult:
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
                    max_cards=_positive_int(
                        request.provider_context.get("liepin_max_cards"), default=request.page_size
                    ),
                    max_pages=_positive_int(request.provider_context.get("liepin_max_pages"), default=1),
                    requirement_sheet=requirement_sheet,
                    native_filters=_native_filters_from_request(request),
                ),
                detail_open_claim_context=detail_open_claim_context,
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
            raise LiepinWorkerModeError(
                "Liepin OpenCLI resume search blocked.",
                code=str(response.request_payload.get("safeReasonCode") or "failed_provider_error"),
                partial_search_result=search_result,
                cards_collected=len(search_result.candidates),
            )
        return search_result

    def _search_resumes_sync(
        self,
        request: LiepinOpenCliResumeRequest,
        *,
        detail_open_claim_context: DetailOpenClaimSearchContext | None = None,
    ):
        with _OPENCLI_SEARCH_LOCK:
            if detail_open_claim_context is not None:
                return self._retriever._search_resumes_with_detail_open_claim_context(
                    request,
                    detail_open_claim_context=detail_open_claim_context,
                )
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
        return await asyncio.to_thread(
            self._retriever.session_status,
            connection_id=connection_id or self._connection_id,
            provider_account_hash=provider_account_hash,
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
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return default
    else:
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
