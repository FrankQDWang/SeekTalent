from __future__ import annotations

from dataclasses import dataclass

from seektalent.core.retrieval.provider_contract import ProviderPayloadKind, ProviderSnapshot
from seektalent.models import ResumeCandidate
from seektalent.providers.liepin.models import LiepinScoreEvidenceSource
from seektalent.providers.liepin.worker_contracts import LiepinWorkerCandidateCard, LiepinWorkerCandidateDetail
from seektalent.storage.json import sha256_json


@dataclass(frozen=True)
class LiepinMappedCandidate:
    candidate: ResumeCandidate
    provider_snapshot: ProviderSnapshot


LiepinWorkerCandidate = LiepinWorkerCandidateCard | LiepinWorkerCandidateDetail


def _safe_raw(
    worker_candidate: LiepinWorkerCandidate,
    *,
    raw_payload_artifact_ref: str | None,
    score_evidence_source: LiepinScoreEvidenceSource,
) -> dict[str, object]:
    return {
        "provider": "liepin",
        "provider_subject_id": worker_candidate.provider_subject_id,
        "provider_listing_id": worker_candidate.provider_listing_id,
        "synthetic_candidate_fingerprint": worker_candidate.synthetic_candidate_fingerprint,
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


def _map_candidate(
    worker_candidate: LiepinWorkerCandidate,
    *,
    payload_kind: ProviderPayloadKind,
    score_evidence_source: LiepinScoreEvidenceSource,
    raw_payload_artifact_ref: str | None,
) -> LiepinMappedCandidate:
    snapshot_hash = sha256_json(worker_candidate.payload)
    raw = _safe_raw(
        worker_candidate,
        raw_payload_artifact_ref=raw_payload_artifact_ref,
        score_evidence_source=score_evidence_source,
    )
    provider_subject_id = worker_candidate.provider_subject_id
    resume_id = provider_subject_id or worker_candidate.synthetic_candidate_fingerprint
    candidate = ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=provider_subject_id,
        snapshot_sha256=snapshot_hash,
        dedup_key=worker_candidate.synthetic_candidate_fingerprint,
        search_text=worker_candidate.normalized_text,
        raw=raw,
    )
    snapshot = ProviderSnapshot(
        provider_name="liepin",
        payload_kind=payload_kind,
        raw_payload=worker_candidate.payload,
        normalized_text=worker_candidate.normalized_text,
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
