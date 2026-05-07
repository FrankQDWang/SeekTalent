from __future__ import annotations

import pytest
from pydantic import ValidationError

from seektalent.providers.liepin.mapper import map_liepin_worker_card, map_liepin_worker_detail
from seektalent.providers.liepin.worker_contracts import LiepinWorkerCandidateCard, LiepinWorkerCandidateDetail


ALLOWED_RAW_KEYS = {
    "provider",
    "provider_subject_id",
    "provider_listing_id",
    "synthetic_candidate_fingerprint",
    "identity_confidence",
    "extraction_source",
    "extractor_version",
    "pii_classification",
    "retention_policy",
    "access_scope",
    "redaction_state",
    "raw_payload_artifact_ref",
    "score_evidence_source",
}

FORBIDDEN_RAW_KEYS = {
    "raw_payload",
    "payload",
    "resume_text",
    "resume_free_text",
    "phone",
    "email",
    "cookies",
    "storageState",
    "authorization",
    "auth_headers",
    "detail_body",
}


def _worker_card() -> LiepinWorkerCandidateCard:
    return LiepinWorkerCandidateCard(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "name": "Candidate One",
            "headline": "Python backend engineer",
            "resumeText": "Private card resume summary with 13800000000 and one@example.com",
            "phone": "13800000000",
            "email": "one@example.com",
            "cookies": "session=secret",
            "storageState": {"cookies": [{"name": "session", "value": "secret"}]},
            "authorization": "Bearer secret",
        },
        normalized_text="Python backend engineer card summary",
        provider_subject_id="candidate-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="fp-card-1",
        identity_confidence="provider_subject_id",
        extraction_source="network",
        extractor_version="liepin-worker-v1",
        pii_classification="direct_contact_possible",
        retention_policy="provider_snapshot_30d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def _worker_detail() -> LiepinWorkerCandidateDetail:
    return LiepinWorkerCandidateDetail(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "detailBody": "<html>Liepin private detail body</html>",
            "resumeText": "Detailed private resume text with one@example.com",
            "phone": "13800000000",
            "email": "one@example.com",
            "auth_headers": {"authorization": "Bearer secret"},
        },
        normalized_text="Python backend engineer detail summary",
        provider_subject_id="candidate-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="fp-detail-1",
        identity_confidence="provider_subject_id",
        extraction_source="dom_fallback",
        extractor_version="liepin-worker-v1",
        pii_classification="direct_contact_present",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def test_card_mapping_keeps_raw_payload_out_of_resume_candidate_raw() -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")

    assert set(mapped.candidate.raw) == ALLOWED_RAW_KEYS
    assert not (set(mapped.candidate.raw) & FORBIDDEN_RAW_KEYS)
    assert "13800000000" not in str(mapped.candidate.raw)
    assert "one@example.com" not in str(mapped.candidate.raw)
    assert "Private card resume summary" not in str(mapped.candidate.raw)
    assert mapped.candidate.raw["raw_payload_artifact_ref"] == "worker://cards/candidate-1.json"


def test_detail_mapping_keeps_raw_payload_and_detail_body_out_of_resume_candidate_raw() -> None:
    mapped = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")

    assert set(mapped.candidate.raw) == ALLOWED_RAW_KEYS
    assert not (set(mapped.candidate.raw) & FORBIDDEN_RAW_KEYS)
    assert "Liepin private detail body" not in str(mapped.candidate.raw)
    assert "Detailed private resume text" not in str(mapped.candidate.raw)
    assert "one@example.com" not in str(mapped.candidate.raw)
    assert mapped.candidate.raw["raw_payload_artifact_ref"] == "worker://details/candidate-1.json"


def test_card_mapping_returns_provider_snapshot_with_raw_payload_and_privacy_metadata() -> None:
    card = _worker_card()
    mapped = map_liepin_worker_card(card, raw_payload_artifact_ref="worker://cards/candidate-1.json")

    assert mapped.provider_snapshot.raw_payload == card.payload
    assert mapped.provider_snapshot.pii_classification == "direct_contact_possible"
    assert mapped.provider_snapshot.retention_policy == "provider_snapshot_30d"
    assert mapped.provider_snapshot.access_scope == "local_run_only"
    assert mapped.provider_snapshot.redaction_state == "raw_provider_payload"
    assert mapped.provider_snapshot.score_evidence_source == "card_only"
    assert mapped.candidate.raw["score_evidence_source"] == "card_only"


def test_detail_mapping_returns_provider_snapshot_with_raw_payload_and_privacy_metadata() -> None:
    detail = _worker_detail()
    mapped = map_liepin_worker_detail(detail, raw_payload_artifact_ref="worker://details/candidate-1.json")

    assert mapped.provider_snapshot.raw_payload == detail.payload
    assert mapped.provider_snapshot.pii_classification == "direct_contact_present"
    assert mapped.provider_snapshot.retention_policy == "provider_snapshot_7d"
    assert mapped.provider_snapshot.access_scope == "local_run_only"
    assert mapped.provider_snapshot.redaction_state == "raw_provider_payload"
    assert mapped.provider_snapshot.score_evidence_source == "detail_enriched"
    assert mapped.candidate.raw["score_evidence_source"] == "detail_enriched"


def test_worker_contracts_reject_unknown_privacy_policy_values() -> None:
    payload = _worker_card().model_dump()
    payload["pii_classification"] = "whatever_the_worker_sent"

    with pytest.raises(ValidationError):
        LiepinWorkerCandidateCard.model_validate(payload)


def test_extraction_source_tracks_origin_not_card_or_detail_kind() -> None:
    card = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    detail = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")

    assert card.provider_snapshot.extraction_source == "network"
    assert card.provider_snapshot.score_evidence_source == "card_only"
    assert detail.provider_snapshot.extraction_source == "dom_fallback"
    assert detail.provider_snapshot.score_evidence_source == "detail_enriched"
