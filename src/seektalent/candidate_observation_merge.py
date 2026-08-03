from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json

from seektalent.models import NormalizedResume, ResumeCandidate, RuntimeSourceEvidence
from seektalent.normalization import normalize_resume
from seektalent.source_references import SourceReference

_EVIDENCE_LEVEL_RANK = {"card": 1, "detail": 2, "final": 3}
_EARLIEST_CANDIDATE_FIELDS = {
    "source_round",
    "first_query_instance_id",
    "first_query_fingerprint",
    "first_round_no",
    "first_lane_type",
    "first_location_key",
    "first_location_type",
    "first_batch_no",
}


def merge_resume_candidate_observations(
    left: ResumeCandidate,
    right: ResumeCandidate,
    *,
    left_evidence: Sequence[RuntimeSourceEvidence] = (),
    right_evidence: Sequence[RuntimeSourceEvidence] = (),
) -> tuple[ResumeCandidate, NormalizedResume]:
    """Merge two observations of one resume without downgrading known content."""
    if left.resume_id != right.resume_id:
        raise ValueError("candidate_observation_resume_id_mismatch")
    _validate_provider_identity(left_evidence, right_evidence)

    left_normalized = normalize_resume(left)
    right_normalized = normalize_resume(right)
    left_rank = candidate_observation_rank(left, left_normalized, left_evidence)
    right_rank = candidate_observation_rank(right, right_normalized, right_evidence)
    winner, loser = (left, right) if left_rank >= right_rank else (right, left)
    earliest, later = min(
        ((left, right), (right, left)),
        key=lambda pair: _candidate_attribution_rank(pair[0]),
    )

    winner_payload = winner.model_dump(mode="python")
    loser_payload = loser.model_dump(mode="python")
    earliest_payload = earliest.model_dump(mode="python")
    later_payload = later.model_dump(mode="python")
    merged_payload: dict[str, object] = {}
    for field_name in winner_payload:
        if field_name in _EARLIEST_CANDIDATE_FIELDS:
            merged_payload[field_name] = _prefer_richer_value(
                earliest_payload[field_name],
                later_payload[field_name],
            )
            continue
        merged_payload[field_name] = _prefer_richer_value(
            winner_payload[field_name],
            loser_payload[field_name],
        )
    merged_payload["source_references"] = _merge_source_references(
        left.source_references,
        right.source_references,
    )
    merged = ResumeCandidate.model_validate(merged_payload)
    return merged, normalize_resume(merged)


def candidate_observation_rank(
    candidate: ResumeCandidate,
    normalized: NormalizedResume,
    evidence: Sequence[RuntimeSourceEvidence],
) -> tuple[int, int, int, int, str, str]:
    """Return an order-independent quality rank; larger values are preferred."""
    evidence_level = max(
        (_EVIDENCE_LEVEL_RANK.get(item.evidence_level, 0) for item in evidence),
        default=_EVIDENCE_LEVEL_RANK.get(normalized.score_evidence_source or "", 0),
    )
    structured_leaf_count = _nonempty_leaf_count(
        normalized.structured_evidence.model_dump(mode="python")
    )
    payload = candidate.model_dump(mode="json")
    payload["source_references"] = [
        item.model_dump(mode="json") for item in candidate.source_references
    ]
    verified_references = {
        (reference.source_kind, reference.display_label, reference.url)
        for item in evidence
        for reference in item.source_references
    }
    candidate_references = {
        (reference.source_kind, reference.display_label, reference.url)
        for reference in candidate.source_references
    }
    return (
        evidence_level,
        structured_leaf_count,
        normalized.completeness_score,
        len(verified_references | candidate_references),
        max((item.collected_at for item in evidence), default=""),
        _stable_payload_hash(payload),
    )


def merge_runtime_source_evidence(
    left: RuntimeSourceEvidence,
    right: RuntimeSourceEvidence,
) -> RuntimeSourceEvidence:
    """Monotonically upsert two observations represented by one evidence ID."""
    identity_fields = (
        "evidence_id",
        "source",
        "provider",
        "candidate_resume_id",
        "provider_candidate_key_hash",
    )
    if any(getattr(left, field) != getattr(right, field) for field in identity_fields):
        raise ValueError("runtime_source_evidence_identity_mismatch")

    left_rank = _runtime_source_evidence_rank(left)
    right_rank = _runtime_source_evidence_rank(right)
    winner, loser = (left, right) if left_rank >= right_rank else (right, left)
    payload = winner.model_dump(mode="python")
    loser_payload = loser.model_dump(mode="python")
    for field_name, value in payload.items():
        payload[field_name] = _prefer_richer_value(value, loser_payload[field_name])
    payload["protected_contact_hashes"] = tuple(
        sorted(set(left.protected_contact_hashes) | set(right.protected_contact_hashes))
    )
    payload["safe_reason_codes"] = tuple(
        sorted(set(left.safe_reason_codes) | set(right.safe_reason_codes))
    )
    payload["source_references"] = _merge_source_references(
        left.source_references,
        right.source_references,
    )
    return RuntimeSourceEvidence.model_validate(payload)


def merge_runtime_source_evidence_updates(
    *groups: Sequence[RuntimeSourceEvidence],
) -> tuple[RuntimeSourceEvidence, ...]:
    merged: dict[tuple[str, str], RuntimeSourceEvidence] = {}
    for evidence in (item for group in groups for item in group):
        key = (evidence.candidate_resume_id, evidence.evidence_id)
        existing = merged.get(key)
        merged[key] = (
            evidence
            if existing is None
            else merge_runtime_source_evidence(existing, evidence)
        )
    return tuple(merged[key] for key in sorted(merged))


def _validate_provider_identity(
    left_evidence: Sequence[RuntimeSourceEvidence],
    right_evidence: Sequence[RuntimeSourceEvidence],
) -> None:
    for source_provider in {
        (item.source, item.provider)
        for item in (*left_evidence, *right_evidence)
    }:
        left_hashes = {
            item.provider_candidate_key_hash
            for item in left_evidence
            if (item.source, item.provider) == source_provider and item.provider_candidate_key_hash
        }
        right_hashes = {
            item.provider_candidate_key_hash
            for item in right_evidence
            if (item.source, item.provider) == source_provider and item.provider_candidate_key_hash
        }
        if left_hashes and right_hashes and left_hashes.isdisjoint(right_hashes):
            raise ValueError("candidate_observation_provider_mismatch")


def _runtime_source_evidence_rank(evidence: RuntimeSourceEvidence) -> tuple[int, int, str, str]:
    payload = evidence.model_dump(mode="json")
    return (
        _EVIDENCE_LEVEL_RANK.get(evidence.evidence_level, 0),
        _nonempty_leaf_count(payload),
        evidence.collected_at,
        _stable_payload_hash(payload),
    )


def _prefer_richer_value(preferred: object, fallback: object) -> object:
    if isinstance(preferred, Mapping) and isinstance(fallback, Mapping):
        preferred_mapping = {str(key): value for key, value in preferred.items()}
        fallback_mapping = {str(key): value for key, value in fallback.items()}
        keys = list(preferred_mapping)
        keys.extend(key for key in fallback_mapping if key not in preferred_mapping)
        return {
            key: _prefer_richer_value(preferred_mapping.get(key), fallback_mapping.get(key))
            for key in keys
        }
    return fallback if _is_empty(preferred) and not _is_empty(fallback) else preferred


def _candidate_attribution_rank(candidate: ResumeCandidate) -> tuple[int, int, int, str, str, str]:
    last = 2**31 - 1
    return (
        candidate.source_round if candidate.source_round is not None else last,
        candidate.first_round_no if candidate.first_round_no is not None else last,
        candidate.first_batch_no if candidate.first_batch_no is not None else last,
        candidate.first_query_instance_id or "\uffff",
        candidate.resume_id,
        _stable_payload_hash(
            {
                field_name: getattr(candidate, field_name)
                for field_name in _EARLIEST_CANDIDATE_FIELDS
            }
        ),
    )


def _merge_source_references(
    left: Sequence[SourceReference],
    right: Sequence[SourceReference],
) -> tuple[SourceReference, ...]:
    references = {
        (item.source_kind, item.display_label, item.url): item
        for item in (*left, *right)
    }
    return tuple(references[key] for key in sorted(references))


def _nonempty_leaf_count(value: object) -> int:
    if isinstance(value, Mapping):
        return sum(_nonempty_leaf_count(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return sum(_nonempty_leaf_count(item) for item in value)
    return 0 if _is_empty(value) else 1


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == () or value == [] or value == {}


def _stable_payload_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
