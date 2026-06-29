from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from seektalent.providers.liepin.worker_contracts import (
    LiepinResumeSearchResponse,
    LiepinWorkerCandidateDetail,
)


class LiepinResumeSearchSite(Protocol):
    def status(self): ...

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
        if reason in _RECOVERABLE_OPENCLI_READY_REASONS and self._recover_connection():
            return
        raise RuntimeError(reason)

    def search_resumes(self, request: LiepinOpenCliResumeRequest) -> LiepinResumeSearchResponse:
        self.ensure_ready()
        envelope = self._search_liepin_resumes(request)
        if _envelope_reason(envelope) in _RECOVERABLE_OPENCLI_READY_REASONS and self._recover_connection():
            envelope = self._search_liepin_resumes(request)
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

    def _recover_connection(self) -> bool:
        recover = getattr(self._runner, "recover_connection", None)
        if not callable(recover):
            return False
        result = recover()
        return bool(getattr(result, "ok", False))


def _envelope_reason(envelope: Mapping[str, object]) -> str | None:
    if envelope.get("status") not in {"blocked", "failed"}:
        return None
    reason = envelope.get("safe_reason_code") or envelope.get("stop_reason")
    if isinstance(reason, str) and reason:
        return reason
    return None


def _response_from_opencli_envelope(envelope: Mapping[str, object]) -> LiepinResumeSearchResponse:
    status = envelope.get("status")
    if status not in {"succeeded", "partial", "blocked", "failed"}:
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
    return LiepinResumeSearchResponse(
        resumes=resumes,
        exhausted=status == "succeeded",
        requestPayload=request_payload,
        raw_candidate_count=_positive_int(envelope.get("cards_seen"), default=len(resumes)),
    )


def _detail_from_resume_payload(
    resume: Mapping[str, object],
    *,
    action_trace_ref: object,
) -> LiepinWorkerCandidateDetail:
    provider_rank = _positive_int(resume.get("provider_rank"), default=0)
    payload = dict(cast(Mapping[str, object], resume.get("detail_payload") or {}))
    provider_candidate_hash = _provider_candidate_hash(resume)
    payload["providerCandidateKeyHash"] = provider_candidate_hash
    payload["providerRank"] = provider_rank
    payload["protectedSnapshotRef"] = resume.get("protected_snapshot_ref")
    payload["normalizedSnapshotRef"] = resume.get("normalized_snapshot_ref")
    payload["actionTraceRef"] = resume.get("action_trace_ref") or action_trace_ref
    normalized_text = str(resume.get("normalized_text") or payload.get("fullText") or "")
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


def _provider_candidate_hash(resume: Mapping[str, object]) -> str:
    material = str(
        resume.get("provider_candidate_key_material_ref")
        or resume.get("candidate_resume_id")
        or resume.get("protected_snapshot_ref")
        or ""
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
