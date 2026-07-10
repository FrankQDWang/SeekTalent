from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from seektalent.providers.liepin.detail_payload_text import structured_liepin_detail_text
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
    del action_trace_ref
    payload = _public_detail_payload(resume.get("detail_payload"))
    claim_aware = resume.get("claim_aware") is True
    provider_candidate_hash = _provider_candidate_hash(
        resume,
        claim_aware=claim_aware,
    )
    if claim_aware:
        synthetic_candidate_fingerprint, presentation_resume_id = _claim_aware_identity_tokens(provider_candidate_hash)
        provider_subject_id = None
        identity_confidence = "synthetic_fingerprint"
    else:
        synthetic_candidate_fingerprint = hashlib.sha256(
            f"liepin-opencli:{provider_candidate_hash}".encode("utf-8")
        ).hexdigest()
        presentation_resume_id = None
        provider_subject_id = provider_candidate_hash
        identity_confidence = "provider_subject_id"
    normalized_text = structured_liepin_detail_text(payload)
    detail = LiepinWorkerCandidateDetail(
        payload=payload,
        normalized_text=normalized_text,
        provider_subject_id=provider_subject_id,
        provider_listing_id=None,
        synthetic_candidate_fingerprint=synthetic_candidate_fingerprint,
        identity_confidence=identity_confidence,
        extraction_source="dom_fallback",
        extractor_version="liepin-opencli-deterministic-v1",
        pii_classification="no_direct_contact",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )
    detail._opencli_private_candidate_identity = True
    detail._opencli_claim_aware_candidate_identity = claim_aware
    detail._opencli_presentation_resume_id = presentation_resume_id
    return detail


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
    carried_key_hash = resume.get("provider_candidate_key_hash")
    if _is_provider_candidate_key_hash(carried_key_hash):
        return cast(str, carried_key_hash)
    if claim_aware:
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
