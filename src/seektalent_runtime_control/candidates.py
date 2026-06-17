from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json

from seektalent_runtime_control.models import (
    RuntimeControlCandidateEvidence,
    RuntimeControlCandidateFinalizationRevision,
    RuntimeControlCandidateIdentity,
)


@dataclass(frozen=True)
class RuntimeControlCandidateTruth:
    identities: list[RuntimeControlCandidateIdentity]
    evidence: list[RuntimeControlCandidateEvidence]
    finalization_revisions: list[RuntimeControlCandidateFinalizationRevision]


def candidate_truth_from_run_state(
    *,
    runtime_run_id: str,
    run_state: Mapping[str, object],
    source_checkpoint_id: str | None,
    observed_at: str,
) -> RuntimeControlCandidateTruth:
    identities_payload = _mapping(run_state.get("candidate_identities"))
    identity_by_resume_id = _string_mapping(run_state.get("candidate_identity_by_resume_id"))
    canonical_by_identity = _mapping(run_state.get("canonical_resume_by_identity_id"))
    source_evidence_by_identity = _mapping(run_state.get("source_evidence_by_identity_id"))
    candidate_store = _mapping(run_state.get("candidate_store"))
    normalized_store = _mapping(run_state.get("normalized_store"))
    scorecards_by_resume_id = _mapping(run_state.get("scorecards_by_resume_id"))

    identities: list[RuntimeControlCandidateIdentity] = []
    evidence: list[RuntimeControlCandidateEvidence] = []
    seen_evidence_ids: set[str] = set()
    for identity_id in sorted(identities_payload):
        identity_payload = _mapping(identities_payload.get(identity_id))
        canonical_resume_id = _canonical_resume_id(
            identity_id=identity_id,
            identity_payload=identity_payload,
            canonical_by_identity=canonical_by_identity,
            identity_by_resume_id=identity_by_resume_id,
        )
        if canonical_resume_id is None:
            continue
        merged_resume_ids = _string_list(identity_payload.get("resume_ids")) or [canonical_resume_id]
        identity_evidence_payloads = _mapping_list(source_evidence_by_identity.get(identity_id))
        source_evidence_ids = [
            evidence_id
            for payload in identity_evidence_payloads
            if (evidence_id := _text(payload.get("evidence_id")))
        ]
        for evidence_payload in identity_evidence_payloads:
            evidence_id = _text(evidence_payload.get("evidence_id"))
            if evidence_id is None or evidence_id in seen_evidence_ids:
                continue
            seen_evidence_ids.add(evidence_id)
            resume_id = _text(evidence_payload.get("candidate_resume_id")) or canonical_resume_id
            scorecard = _mapping(scorecards_by_resume_id.get(resume_id))
            evidence.append(
                _candidate_evidence(
                    runtime_run_id=runtime_run_id,
                    identity_id=identity_id,
                    resume_id=resume_id,
                    evidence_payload=evidence_payload,
                    scorecard=scorecard,
                    observed_at=observed_at,
                )
            )
        identities.append(
            _candidate_identity(
                runtime_run_id=runtime_run_id,
                identity_id=identity_id,
                canonical_resume_id=canonical_resume_id,
                merged_resume_ids=merged_resume_ids,
                source_evidence_ids=source_evidence_ids,
                candidate=_mapping(candidate_store.get(canonical_resume_id)),
                normalized=_mapping(normalized_store.get(canonical_resume_id)),
                scorecard=_mapping(scorecards_by_resume_id.get(canonical_resume_id)),
                observed_at=observed_at,
            )
        )
    revisions = [
        _finalization_revision(
            runtime_run_id=runtime_run_id,
            revision_payload=revision_payload,
            source_checkpoint_id=source_checkpoint_id,
            observed_at=observed_at,
        )
        for revision_payload in _mapping_list(run_state.get("finalization_revisions"))
        if _int_or_none(revision_payload.get("revision")) is not None
    ]
    return RuntimeControlCandidateTruth(
        identities=identities,
        evidence=evidence,
        finalization_revisions=revisions,
    )


def _candidate_identity(
    *,
    runtime_run_id: str,
    identity_id: str,
    canonical_resume_id: str,
    merged_resume_ids: list[str],
    source_evidence_ids: list[str],
    candidate: Mapping[str, object],
    normalized: Mapping[str, object],
    scorecard: Mapping[str, object],
    observed_at: str,
) -> RuntimeControlCandidateIdentity:
    display_name = (
        _safe_text(normalized.get("candidate_name"), max_length=160)
        or _safe_text(_mapping(candidate.get("raw")).get("candidate_name"), max_length=160)
        or f"Candidate {identity_id[-8:]}"
    )
    title = (
        _safe_text(normalized.get("current_title"), max_length=240)
        or _safe_text(candidate.get("expected_job_category"), max_length=240)
        or ""
    )
    company = _safe_text(normalized.get("current_company"), max_length=240) or ""
    location = (
        _safe_text(_first(_string_list(normalized.get("locations"))), max_length=160)
        or _safe_text(candidate.get("now_location"), max_length=160)
        or ""
    )
    summary = (
        _safe_text(scorecard.get("reasoning_summary"), max_length=1000)
        or _safe_text(normalized.get("headline"), max_length=1000)
        or _safe_text(candidate.get("search_text"), max_length=1000)
        or ""
    )
    score = _int_or_none(scorecard.get("overall_score"))
    fit_bucket = _safe_text(scorecard.get("fit_bucket"), max_length=64)
    source_round = _int_or_none(scorecard.get("source_round")) or _int_or_none(candidate.get("source_round"))
    payload_hash = _hash_payload(
        {
            "identity_id": identity_id,
            "canonical_resume_id": canonical_resume_id,
            "merged_resume_ids": merged_resume_ids,
            "source_evidence_ids": source_evidence_ids,
            "display_name": display_name,
            "title": title,
            "company": company,
            "location": location,
            "summary": summary,
            "score": score,
            "fit_bucket": fit_bucket,
            "source_round": source_round,
        }
    )
    return RuntimeControlCandidateIdentity(
        runtime_run_id=runtime_run_id,
        identity_id=identity_id,
        canonical_resume_id=canonical_resume_id,
        merged_resume_ids=merged_resume_ids,
        source_evidence_ids=source_evidence_ids,
        display_name=display_name,
        title=title,
        company=company,
        location=location,
        summary=summary,
        score=score,
        fit_bucket=fit_bucket,
        source_round=source_round,
        payload_hash=payload_hash,
        updated_at=observed_at,
    )


def _candidate_evidence(
    *,
    runtime_run_id: str,
    identity_id: str,
    resume_id: str,
    evidence_payload: Mapping[str, object],
    scorecard: Mapping[str, object],
    observed_at: str,
) -> RuntimeControlCandidateEvidence:
    payload: dict[str, object] = {
        "providerRank": _int_or_none(evidence_payload.get("provider_rank")),
        "queryFingerprint": _safe_text(evidence_payload.get("query_fingerprint"), max_length=256),
        "reasonCode": _safe_text(evidence_payload.get("reason_code"), max_length=128),
        "safeReasonCodes": _string_list(evidence_payload.get("safe_reason_codes")),
    }
    payload_hash = _hash_payload(payload)
    return RuntimeControlCandidateEvidence(
        runtime_run_id=runtime_run_id,
        evidence_id=_text(evidence_payload.get("evidence_id")) or f"{identity_id}:{resume_id}",
        identity_id=identity_id,
        resume_id=resume_id,
        source_kind=_safe_text(evidence_payload.get("source"), max_length=32) or "unknown",
        evidence_level=_safe_text(evidence_payload.get("evidence_level"), max_length=64) or "unknown",
        provider_candidate_key_hash=_safe_text(evidence_payload.get("provider_candidate_key_hash"), max_length=256)
        or "",
        score=_int_or_none(scorecard.get("overall_score")),
        fit_bucket=_safe_text(scorecard.get("fit_bucket"), max_length=64),
        payload=payload,
        payload_hash=payload_hash,
        updated_at=observed_at,
    )


def _finalization_revision(
    *,
    runtime_run_id: str,
    revision_payload: Mapping[str, object],
    source_checkpoint_id: str | None,
    observed_at: str,
) -> RuntimeControlCandidateFinalizationRevision:
    coverage_summary = _mapping(revision_payload.get("coverage_summary"))
    payload = {
        "revision": _int_or_none(revision_payload.get("revision")) or 0,
        "reason_code": _safe_text(revision_payload.get("reason_code"), max_length=128) or "runtime_finalized",
        "candidate_identity_ids": _string_list(revision_payload.get("candidate_identity_ids")),
        "coverage_summary": dict(coverage_summary),
    }
    return RuntimeControlCandidateFinalizationRevision(
        runtime_run_id=runtime_run_id,
        revision=int(payload["revision"]),
        reason_code=str(payload["reason_code"]),
        candidate_identity_ids=list(payload["candidate_identity_ids"]),
        coverage_summary=dict(coverage_summary),
        source_checkpoint_id=source_checkpoint_id,
        payload_hash=_hash_payload(payload),
        created_at=_safe_text(revision_payload.get("created_at"), max_length=64) or observed_at,
    )


def _canonical_resume_id(
    *,
    identity_id: str,
    identity_payload: Mapping[str, object],
    canonical_by_identity: Mapping[str, object],
    identity_by_resume_id: Mapping[str, str],
) -> str | None:
    canonical_payload = _mapping(canonical_by_identity.get(identity_id))
    selected = _text(canonical_payload.get("canonical_resume_id"))
    if selected is not None:
        return selected
    for resume_id, mapped_identity_id in sorted(identity_by_resume_id.items()):
        if mapped_identity_id == identity_id:
            return resume_id
    resume_ids = _string_list(identity_payload.get("resume_ids"))
    return resume_ids[0] if resume_ids else None


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items() if isinstance(key, str)}
    return {}


def _string_mapping(value: object) -> dict[str, str]:
    return {
        key: item.strip()
        for key, item in _mapping(value).items()
        if isinstance(item, str) and item.strip()
    }


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _safe_text(value: object, *, max_length: int) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return text[:max_length]


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _first(values: Sequence[str]) -> str | None:
    return values[0] if values else None


def _hash_payload(payload: Mapping[str, object]) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return sha256(text.encode("utf-8")).hexdigest()
