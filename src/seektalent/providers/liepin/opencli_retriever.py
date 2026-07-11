from __future__ import annotations

import hashlib
import inspect
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from seektalent.providers.liepin.detail_payload_text import structured_liepin_detail_text
from seektalent.core.retrieval.provider_contract import ProviderFirstPageExpansionResult, ProviderSearchContinuation
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimSearchContext
from seektalent.providers.liepin.worker_contracts import (
    LiepinResumeSearchResponse,
    LiepinWorkerCandidateDetail,
    SessionStatus,
)


class LiepinResumeSearchSite(Protocol):
    def status(self): ...

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
_PRIVATE_DETAIL_TRANSPORT_KEYS = frozenset(
    {
        "actiontraceref",
        "detailurl",
        "normalizedsnapshotref",
        "protectedsnapshotref",
        "providercandidatekeyhash",
        "provider_candidate_key_hash",
        "provider_candidate_key_material_ref",
        "providerrank",
        "provider_rank",
        "res_id_encode",
        "source_url",
        "sourceurl",
        "url",
    }
)


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
            return
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
            discard = getattr(self._runner, "discard_liepin_first_page_continuation", None)
            exists = getattr(self._runner, "liepin_first_page_continuation_exists", None)
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
        handler = getattr(self._runner, "handle_liepin_first_page_continuation", None)
        if not callable(handler):
            raise LiepinFirstPageExpansionBoundaryError("liepin_opencli_private_expansion_route_unavailable")
        envelope = handler(continuation_ref=continuation.opaque_ref,
            detail_open_claim_context=DetailOpenClaimSearchContext(
                detail_open_claim_ledger=detail_open_claim_ledger,
                logical_round_no=logical_round_no, query_instance_id=query_instance_id))
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
        envelope = search(request)
        if _envelope_reason(envelope) in _RECOVERABLE_OPENCLI_READY_REASONS and self._recover_connection():
            envelope = search(request)
        return _response_from_opencli_envelope(envelope)

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
        _detail_from_resume_payload(cast(Mapping[str, object], resume), action_trace_ref=action_trace_ref)
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


def _detail_from_resume_payload(
    resume: Mapping[str, object],
    *,
    action_trace_ref: object,
) -> LiepinWorkerCandidateDetail:
    claim_aware = resume.get("claim_aware") is True
    if claim_aware:
        payload = _public_detail_payload(resume.get("detail_payload"))
        provider_candidate_hash = _provider_candidate_hash(resume, claim_aware=True)
        synthetic_candidate_fingerprint, presentation_resume_id = _claim_aware_identity_tokens(provider_candidate_hash)
        detail = LiepinWorkerCandidateDetail(
            payload=payload,
            normalized_text=structured_liepin_detail_text(payload),
            provider_subject_id=None,
            provider_listing_id=None,
            synthetic_candidate_fingerprint=synthetic_candidate_fingerprint,
            identity_confidence="synthetic_fingerprint",
            extraction_source="dom_fallback",
            extractor_version="liepin-opencli-deterministic-v1",
            pii_classification="no_direct_contact",
            retention_policy="provider_snapshot_7d",
            access_scope="local_run_only",
            redaction_state="raw_provider_payload",
        )
        detail._opencli_private_candidate_identity = True
        detail._opencli_claim_aware_candidate_identity = True
        detail._opencli_presentation_resume_id = presentation_resume_id
        return detail

    provider_rank = _positive_int(resume.get("provider_rank"), default=0)
    payload = dict(cast(Mapping[str, object], resume.get("detail_payload") or {}))
    provider_candidate_hash = _provider_candidate_hash(resume, claim_aware=False)
    payload["providerCandidateKeyHash"] = provider_candidate_hash
    payload["providerRank"] = provider_rank
    payload["protectedSnapshotRef"] = resume.get("protected_snapshot_ref")
    payload["normalizedSnapshotRef"] = resume.get("normalized_snapshot_ref")
    payload["actionTraceRef"] = resume.get("action_trace_ref") or action_trace_ref
    normalized_text = structured_liepin_detail_text(payload)
    fingerprint = hashlib.sha256(f"liepin-opencli:{provider_candidate_hash}".encode("utf-8")).hexdigest()
    return LiepinWorkerCandidateDetail(
        payload=payload,
        normalized_text=normalized_text,
        provider_subject_id=provider_candidate_hash,
        provider_listing_id=None,
        synthetic_candidate_fingerprint=fingerprint,
        identity_confidence="provider_subject_id",
        extraction_source="dom_fallback",
        extractor_version="liepin-opencli-deterministic-v1",
        pii_classification="no_direct_contact",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def _positive_int(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return default
        return parsed if parsed > 0 else default
    return default


def _provider_candidate_hash(resume: Mapping[str, object], *, claim_aware: bool) -> str:
    if claim_aware:
        carried_key_hash = resume.get("provider_candidate_key_hash")
        if _is_provider_candidate_key_hash(carried_key_hash):
            return cast(str, carried_key_hash)
        raise RuntimeError("liepin_opencli_candidate_identity_mismatch")
    material = str(
        resume.get("provider_candidate_key_material_ref")
        or resume.get("candidate_resume_id")
        or resume.get("protected_snapshot_ref")
        or ""
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _claim_aware_identity_tokens(carried_key_hash: str) -> tuple[str, str]:
    internal_dedup_token = hashlib.sha256(
        f"liepin:detail:dedup:v1:{carried_key_hash}".encode("utf-8")
    ).hexdigest()
    presentation_resume_id = hashlib.sha256(
        f"liepin:detail:presentation:v1:{carried_key_hash}".encode("utf-8")
    ).hexdigest()
    return internal_dedup_token, presentation_resume_id


def _is_provider_candidate_key_hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _public_detail_payload(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return _strip_private_detail_transport(cast(Mapping[str, object], value))


def _strip_private_detail_transport(value: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if key.casefold() in _PRIVATE_DETAIL_TRANSPORT_KEYS:
            continue
        sanitized = _strip_private_detail_value(raw_value)
        if sanitized is not None:
            payload[key] = sanitized
    return payload


def _strip_private_detail_value(value: object) -> object | None:
    if isinstance(value, Mapping):
        return _strip_private_detail_transport(cast(Mapping[str, object], value))
    if isinstance(value, list | tuple):
        return [item for raw_item in value if (item := _strip_private_detail_value(raw_item)) is not None]
    if isinstance(value, str):
        lower = value.casefold()
        if value.startswith("artifact://") or "res_id_encode=" in lower or lower.startswith(("http://", "https://")):
            return None
    return value
