from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar, cast

from seektalent.opencli_browser.fault_isolation import isolated_call
from seektalent.core.retrieval.provider_contract import ProviderFirstPageExpansionResult, ProviderSearchContinuation
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimSearchContext
from seektalent.providers.liepin.worker_contracts import (
    LiepinResumeSearchResponse,
    SessionStatus,
)
from seektalent.providers.liepin.mapper import (
    liepin_worker_detail_from_resume_payload,
)


class LiepinResumeSearchSite(Protocol):
    def status(self): ...

    def _begin_browser_control_scope(self) -> None: ...

    def _finish_browser_control_scope(self) -> None: ...

    def session_status_probe(
        self,
        *,
        connection_id: str,
        provider_account_hash: str | None,
    ) -> SessionStatus: ...

    def search_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        target_resumes: int,
        max_pages: int,
        max_cards: int,
        native_filters: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


OpenCliResumeRunner = LiepinResumeSearchSite

_RECOVERABLE_OPENCLI_READY_REASONS = {
    "liepin_opencli_extension_disconnected",
    "liepin_opencli_daemon_stale",
    "liepin_opencli_status_unavailable",
}
_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")
class LiepinFirstPageExpansionBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True, kw_only=True)
class LiepinOpenCliResumeRequest:
    source_run_id: str
    keyword_query: str
    query_terms: Sequence[str]
    target_resumes: int
    max_cards: int
    max_pages: int
    requirement_sheet: Mapping[str, object]
    native_filters: dict[str, object] | None = None


class LiepinOpenCliResumeRetriever:
    def __init__(self, *, runner: LiepinResumeSearchSite) -> None:
        self._runner = runner

    def ensure_ready(self) -> None:
        status = self._runner.status()
        if status.ok:
            return
        reason = str(status.safe_reason_code or "liepin_opencli_status_unavailable")
        if reason == "liepin_opencli_daemon_not_running":
            raise RuntimeError(reason)
        if reason in _RECOVERABLE_OPENCLI_READY_REASONS and self._recover_connection():
            return
        raise RuntimeError(reason)

    def session_status(
        self,
        *,
        connection_id: str,
        provider_account_hash: str | None,
    ) -> SessionStatus:
        return self._runner.session_status_probe(
            connection_id=connection_id,
            provider_account_hash=provider_account_hash,
        )

    def search_resumes(self, request: LiepinOpenCliResumeRequest) -> LiepinResumeSearchResponse:
        return self._search_resumes(request, search=self._search_liepin_resumes)

    def handle_first_page_continuation_with_detail_open_claim_ledger(self, *, action: str,
            continuation: ProviderSearchContinuation, detail_open_claim_ledger: DetailOpenClaimLedger,
            logical_round_no: int, query_instance_id: str) -> ProviderFirstPageExpansionResult:
        if action == "discard":
            discard = getattr(self._runner, "_discard_liepin_first_page_continuation", None)
            exists = getattr(self._runner, "_liepin_first_page_continuation_exists", None)
            if not callable(discard) or not callable(exists):
                return _cleanup_failure_result(continuation)
            try:
                discard(continuation.opaque_ref)
                deleted = not exists(continuation.opaque_ref)
            except OSError:
                deleted = False
            from seektalent.core.retrieval.provider_contract import SearchResult
            return ProviderFirstPageExpansionResult(search_result=SearchResult(),
                first_page_visible_count=continuation.visible_candidate_count,
                first_page_eligible_count=continuation.eligible_candidate_count,
                initial_opened_count=continuation.initial_opened_count, expansion_opened_count=0,
                expansion_skipped_seen_count=0, expansion_terminal_failure_count=0,
                status="completed" if deleted else "failed",
                safe_reason_code=None if deleted else "liepin_first_page_continuation_cleanup_failed",
                continuation_deleted=deleted)
        if action != "expand":
            raise ValueError("liepin_expansion_action_invalid")
        handler = getattr(self._runner, "_handle_liepin_first_page_continuation", None)
        if not callable(handler):
            raise LiepinFirstPageExpansionBoundaryError("liepin_opencli_private_expansion_route_unavailable")
        envelope = self._run_in_browser_control_scope(
            lambda: handler(
                continuation_ref=continuation.opaque_ref,
                detail_open_claim_context=DetailOpenClaimSearchContext(
                    detail_open_claim_ledger=detail_open_claim_ledger,
                    logical_round_no=logical_round_no,
                    query_instance_id=query_instance_id,
                ),
            )
        )
        if inspect.isawaitable(envelope):
            close = getattr(envelope, "close", None)
            if callable(close):
                close()
            raise LiepinFirstPageExpansionBoundaryError("liepin_opencli_private_expansion_route_must_be_synchronous")
        response = _response_from_opencli_envelope(cast(Mapping[str, object], envelope))
        from seektalent.providers.liepin.client import liepin_resume_search_response_to_search_result
        return ProviderFirstPageExpansionResult(
            search_result=liepin_resume_search_response_to_search_result(response),
            first_page_visible_count=int(envelope.get("first_page_visible_count", 0)),
            first_page_eligible_count=int(envelope.get("first_page_eligible_count", 0)),
            initial_opened_count=int(envelope.get("initial_opened_count", 0)),
            expansion_opened_count=int(envelope.get("expansion_opened_count", 0)),
            expansion_skipped_seen_count=int(envelope.get("expansion_skipped_seen_count", 0)),
            expansion_terminal_failure_count=int(envelope.get("expansion_terminal_failure_count", 0)),
            status=cast(Literal["completed", "partial", "blocked", "failed"], envelope.get("status", "failed")),
            safe_reason_code=cast(str | None, envelope.get("safe_reason_code")))

    def _search_resumes_with_detail_open_claim_context(
        self,
        request: LiepinOpenCliResumeRequest,
        *,
        detail_open_claim_context: DetailOpenClaimSearchContext,
    ) -> LiepinResumeSearchResponse:
        return self._search_resumes(
            request,
            search=lambda resume_request: self._search_liepin_resumes_with_detail_open_claim_context(
                resume_request,
                detail_open_claim_context=detail_open_claim_context,
            ),
        )

    def _search_resumes(
        self,
        request: LiepinOpenCliResumeRequest,
        *,
        search: Callable[[LiepinOpenCliResumeRequest], dict[str, object]],
    ) -> LiepinResumeSearchResponse:
        self.ensure_ready()
        envelope = self._run_in_browser_control_scope(lambda: search(request))
        return _response_from_opencli_envelope(envelope)

    def _run_in_browser_control_scope(self, action: Callable[[], _T]) -> _T:
        try:
            self._runner._begin_browser_control_scope()
            return action()
        finally:
            isolated_call(self._runner._finish_browser_control_scope, self._report_cleanup_failure)

    def _report_cleanup_failure(self, exc: Exception) -> None:
        _LOGGER.warning("liepin_browser_scope_cleanup_failed error=%s", type(exc).__name__)

    def _search_liepin_resumes(self, request: LiepinOpenCliResumeRequest) -> dict[str, object]:
        return self._runner.search_liepin_resumes(
            source_run_id=request.source_run_id,
            query=request.keyword_query,
            target_resumes=request.target_resumes,
            max_pages=request.max_pages,
            max_cards=request.max_cards,
            native_filters=request.native_filters,
        )

    def _search_liepin_resumes_with_detail_open_claim_context(
        self,
        request: LiepinOpenCliResumeRequest,
        *,
        detail_open_claim_context: DetailOpenClaimSearchContext,
    ) -> dict[str, object]:
        search = getattr(self._runner, "_search_liepin_resumes_with_detail_open_claim_context", None)
        if not callable(search):
            raise RuntimeError("liepin_opencli_private_detail_route_unavailable")
        return cast(Callable[..., dict[str, object]], search)(
            source_run_id=request.source_run_id,
            query=request.keyword_query,
            target_resumes=request.target_resumes,
            max_pages=request.max_pages,
            max_cards=request.max_cards,
            native_filters=request.native_filters,
            detail_open_claim_context=detail_open_claim_context,
        )

    def _recover_connection(self) -> bool:
        recover = getattr(self._runner, "recover_connection", None)
        if not callable(recover):
            return False
        result = recover()
        return bool(getattr(result, "ok", False))


def _cleanup_failure_result(continuation: ProviderSearchContinuation) -> ProviderFirstPageExpansionResult:
    from seektalent.core.retrieval.provider_contract import SearchResult
    return ProviderFirstPageExpansionResult(search_result=SearchResult(),
        first_page_visible_count=continuation.visible_candidate_count,
        first_page_eligible_count=continuation.eligible_candidate_count,
        initial_opened_count=continuation.initial_opened_count, expansion_opened_count=0,
        expansion_skipped_seen_count=0, expansion_terminal_failure_count=0,
        status="failed", safe_reason_code="liepin_first_page_continuation_cleanup_failed",
        continuation_deleted=False)


def _envelope_reason(envelope: Mapping[str, object]) -> str | None:
    if envelope.get("status") not in {"blocked", "failed"}:
        return None
    reason = envelope.get("safe_reason_code") or envelope.get("stop_reason")
    if isinstance(reason, str) and reason:
        return reason
    return None


def _response_from_opencli_envelope(envelope: Mapping[str, object]) -> LiepinResumeSearchResponse:
    private_items = envelope.get("_private_first_page_continuations", ())
    if not isinstance(private_items, (tuple, list)) or not all(
        isinstance(item, ProviderSearchContinuation) for item in private_items
    ):
        raise RuntimeError("liepin_opencli_malformed_private_continuation")
    status = envelope.get("status")
    if status not in {"succeeded", "completed", "partial", "blocked", "failed"}:
        reason = envelope.get("safe_reason_code") or envelope.get("stop_reason") or "failed_provider_error"
        raise RuntimeError(str(reason))
    raw_resumes = envelope.get("resumes")
    if not isinstance(raw_resumes, list):
        raise RuntimeError("liepin_opencli_malformed_state")
    action_trace_ref = envelope.get("action_trace_ref")
    resumes = [
        liepin_worker_detail_from_resume_payload(
            cast(Mapping[str, object], resume),
            action_trace_ref=action_trace_ref,
        )
        for resume in raw_resumes
        if isinstance(resume, Mapping)
    ]
    request_payload: dict[str, object] = {
        "source": "liepin",
        "backend": "opencli",
        "opencliStatus": status,
        "safeReasonCode": envelope.get("safe_reason_code") or envelope.get("stop_reason"),
        "actionTraceRef": action_trace_ref,
    }
    workflow_steps = envelope.get("workflow_steps")
    if isinstance(workflow_steps, list):
        request_payload["workflowSteps"] = workflow_steps
    response = LiepinResumeSearchResponse(
        resumes=resumes,
        exhausted=status in {"succeeded", "completed"},
        requestPayload=request_payload,
        raw_candidate_count=len(resumes),
    )
    response._private_first_page_continuations = tuple(
        cast(Sequence[ProviderSearchContinuation], private_items)
    )
    return response
