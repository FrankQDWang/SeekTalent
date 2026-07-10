from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast
import re

from seektalent.core.retrieval.provider_contract import ProviderPayloadKind, ProviderSnapshot
from seektalent.models import ResumeCandidate
from seektalent.providers.liepin.detail_payload_text import (
    sanitize_liepin_provider_payload,
    structured_liepin_detail_text,
)
from seektalent.providers.liepin.models import LiepinScoreEvidenceSource
from seektalent.providers.liepin.worker_contracts import (
    LiepinSafeCardSummary,
    LiepinWorkerCandidateCard,
    LiepinWorkerCandidateDetail,
    find_liepin_card_payload_text_tail_alias_paths,
)
from seektalent.storage.json import sha256_json


@dataclass(frozen=True)
class LiepinMappedCandidate:
    candidate: ResumeCandidate
    provider_snapshot: ProviderSnapshot


LiepinWorkerCandidate = LiepinWorkerCandidateCard | LiepinWorkerCandidateDetail
STRUCTURED_CARD_SEARCH_TEXT_MAX_CHARS = 4000


def _safe_raw(
    worker_candidate: LiepinWorkerCandidate,
    *,
    provider_payload: dict[str, object],
    raw_payload_artifact_ref: str | None,
    score_evidence_source: LiepinScoreEvidenceSource,
) -> dict[str, object]:
    raw: dict[str, object] = {
        "provider": "liepin",
        "identity_confidence": worker_candidate.identity_confidence,
        "extraction_source": worker_candidate.extraction_source,
        "extractor_version": worker_candidate.extractor_version,
        "pii_classification": worker_candidate.pii_classification,
        "retention_policy": worker_candidate.retention_policy,
        "access_scope": worker_candidate.access_scope,
        "redaction_state": worker_candidate.redaction_state,
        "raw_payload_artifact_ref": raw_payload_artifact_ref,
        "score_evidence_source": score_evidence_source,
    }
    if not getattr(worker_candidate, "_opencli_private_candidate_identity", False):
        raw["provider_subject_id"] = worker_candidate.provider_subject_id
        raw["provider_listing_id"] = worker_candidate.provider_listing_id
        raw["synthetic_candidate_fingerprint"] = worker_candidate.synthetic_candidate_fingerprint
    if isinstance(worker_candidate, LiepinWorkerCandidateCard):
        raw["safe_card_summary"] = _required_card_summary(worker_candidate).model_dump(mode="json")
        _copy_safe_card_payload_metadata(raw, provider_payload)
    if isinstance(worker_candidate, LiepinWorkerCandidateDetail):
        _copy_safe_detail_payload_fields(raw, provider_payload)
    return raw


def _map_candidate(
    worker_candidate: LiepinWorkerCandidate,
    *,
    payload_kind: ProviderPayloadKind,
    score_evidence_source: LiepinScoreEvidenceSource,
    raw_payload_artifact_ref: str | None,
) -> LiepinMappedCandidate:
    if isinstance(worker_candidate, LiepinWorkerCandidateCard):
        _validate_card_payload_before_mapping(worker_candidate)
    provider_payload = _sanitize_liepin_provider_payload(worker_candidate.payload)
    snapshot_hash = sha256_json(provider_payload)
    raw = _safe_raw(
        worker_candidate,
        provider_payload=provider_payload,
        raw_payload_artifact_ref=raw_payload_artifact_ref,
        score_evidence_source=score_evidence_source,
    )
    provider_subject_id = worker_candidate.provider_subject_id
    resume_id = provider_subject_id or worker_candidate.synthetic_candidate_fingerprint
    normalized_text = _mapped_normalized_text(worker_candidate, provider_payload)
    candidate = ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=provider_subject_id,
        snapshot_sha256=snapshot_hash,
        dedup_key=worker_candidate.synthetic_candidate_fingerprint,
        search_text=normalized_text,
        raw=raw,
    )
    snapshot = ProviderSnapshot(
        provider_name="liepin",
        payload_kind=payload_kind,
        raw_payload=provider_payload,
        normalized_text=normalized_text,
        provider_subject_id=provider_subject_id,
        provider_listing_id=worker_candidate.provider_listing_id,
        synthetic_candidate_fingerprint=worker_candidate.synthetic_candidate_fingerprint,
        identity_confidence=worker_candidate.identity_confidence,
        extraction_source=worker_candidate.extraction_source,
        extractor_version=worker_candidate.extractor_version,
        pii_classification=worker_candidate.pii_classification,
        retention_policy=worker_candidate.retention_policy,
        access_scope=worker_candidate.access_scope,
        redaction_state=worker_candidate.redaction_state,
        score_evidence_source=score_evidence_source,
    )
    return LiepinMappedCandidate(candidate=candidate, provider_snapshot=snapshot)


def map_liepin_worker_card(
    card: LiepinWorkerCandidateCard,
    *,
    raw_payload_artifact_ref: str | None = None,
) -> LiepinMappedCandidate:
    return _map_candidate(
        card,
        payload_kind="card",
        score_evidence_source="card_only",
        raw_payload_artifact_ref=raw_payload_artifact_ref,
    )


def map_liepin_worker_detail(
    detail: LiepinWorkerCandidateDetail,
    *,
    raw_payload_artifact_ref: str | None = None,
) -> LiepinMappedCandidate:
    return _map_candidate(
        detail,
        payload_kind="detail",
        score_evidence_source="detail_enriched",
        raw_payload_artifact_ref=raw_payload_artifact_ref,
    )


def _sanitize_liepin_provider_payload(payload: dict[str, object]) -> dict[str, object]:
    return sanitize_liepin_provider_payload(payload)


def _mapped_normalized_text(worker_candidate: LiepinWorkerCandidate, provider_payload: dict[str, object]) -> str:
    if isinstance(worker_candidate, LiepinWorkerCandidateDetail):
        return structured_liepin_detail_text(provider_payload)
    return _structured_card_search_text(_required_card_summary(worker_candidate).model_dump(mode="json"))


def _required_card_summary(worker_candidate: LiepinWorkerCandidateCard) -> LiepinSafeCardSummary:
    summary = getattr(worker_candidate, "safe_card_summary", None)
    if summary is None:
        raise ValueError("Liepin worker card missing required safe_card_summary")
    return summary


def _validate_card_payload_before_mapping(worker_candidate: LiepinWorkerCandidateCard) -> None:
    prohibited_paths = find_liepin_card_payload_text_tail_alias_paths(worker_candidate.payload)
    if prohibited_paths:
        paths = ", ".join(prohibited_paths)
        raise ValueError(f"Liepin card payload includes prohibited legacy card text field(s): {paths}")


def _structured_card_search_text(summary: Mapping[str, object]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for key in (
        "display_title",
        "current_or_recent_company",
        "current_or_recent_title",
        "work_years",
        "city",
        "expected_city",
        "education_level",
        "job_intention",
        "active_status",
    ):
        _append_structured_text(parts, seen, summary.get(key))
    for key in ("school_names", "major_names", "skill_tags", "badges"):
        _append_structured_sequence(parts, seen, summary.get(key))
    for item in _mapping_sequence(summary.get("experience_preview")):
        for key in ("company", "title", "date_range", "duration"):
            _append_structured_text(parts, seen, item.get(key))
    for item in _mapping_sequence(summary.get("education_preview")):
        for key in ("school", "major", "degree", "recruitment_type", "date_range"):
            _append_structured_text(parts, seen, item.get(key))
    return " ".join(parts)[:STRUCTURED_CARD_SEARCH_TEXT_MAX_CHARS]


def _append_structured_sequence(parts: list[str], seen: set[str], value: object) -> None:
    if not isinstance(value, list | tuple):
        return
    for item in value:
        _append_structured_text(parts, seen, item)


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping))


def _append_structured_text(parts: list[str], seen: set[str], value: object) -> None:
    if isinstance(value, bool) or value is None:
        return
    if not isinstance(value, str | int):
        return
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return
    key = text.casefold()
    if key in seen:
        return
    seen.add(key)
    parts.append(text)


def _copy_safe_card_payload_metadata(raw: dict[str, object], payload: dict[str, object]) -> None:
    provider_hash = _safe_identifier(payload.get("providerCandidateKeyHash"))
    if provider_hash is not None:
        raw["provider_candidate_key_hash"] = provider_hash
    safe_summary_ref = _safe_artifact_ref(payload.get("safeSummaryRef"), expected_prefix="artifact://public-summary/")
    if safe_summary_ref is not None:
        raw["safe_summary_ref"] = safe_summary_ref
    protected_snapshot_ref = _safe_artifact_ref(
        payload.get("protectedSnapshotRef"), expected_prefix="artifact://protected/"
    )
    if protected_snapshot_ref is not None:
        raw["provider_snapshot_ref"] = protected_snapshot_ref
    action_trace_ref = _safe_artifact_ref(payload.get("actionTraceRef"), expected_prefix="artifact://protected/")
    if action_trace_ref is not None:
        raw["action_trace_ref"] = action_trace_ref


def _copy_safe_detail_payload_fields(raw: dict[str, object], payload: dict[str, object]) -> None:
    for key in (
        "candidate_name",
        "candidateName",
        "activeStatus",
        "jobStatus",
        "gender",
        "age",
        "city",
        "education",
        "workYears",
        "currentTitle",
        "currentCompany",
        "jobIntention",
        "workExperienceList",
        "projectExperienceList",
        "educationList",
        "skills",
        "skillTags",
        "tags",
        "keywords",
        "locations",
        "sourceUrl",
    ):
        if key in payload:
            raw[key] = payload[key]
    provider_hash = _safe_identifier(payload.get("providerCandidateKeyHash"))
    if provider_hash is not None:
        raw["provider_candidate_key_hash"] = provider_hash
    protected_snapshot_ref = _safe_artifact_ref(
        payload.get("protectedSnapshotRef"), expected_prefix="artifact://protected/"
    )
    if protected_snapshot_ref is not None:
        raw["provider_snapshot_ref"] = protected_snapshot_ref
    action_trace_ref = _safe_artifact_ref(payload.get("actionTraceRef"), expected_prefix="artifact://protected/")
    if action_trace_ref is not None:
        raw["action_trace_ref"] = action_trace_ref


def _safe_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 256:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", text):
        return None
    lowered = text.casefold()
    if any(token in lowered for token in ("bearer", "cookie", "session", "token", "secret")):
        return None
    return text


def _safe_artifact_ref(value: object, *, expected_prefix: str) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith(expected_prefix):
        return None
    if ".." in text.split("/"):
        return None
    if not re.fullmatch(r"artifact://[A-Za-z0-9._/-]+", text):
        return None
    return text
