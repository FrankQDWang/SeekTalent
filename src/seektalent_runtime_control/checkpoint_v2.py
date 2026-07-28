from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from time import perf_counter
from typing import Mapping


RUNTIME_CHECKPOINT_SCHEMA_V1 = "runtime-control-checkpoint/v1"
RUNTIME_CHECKPOINT_SCHEMA_V2 = "runtime-control-checkpoint/v2"

V2_SAFE_BOUNDARIES = frozenset(
    {
        "before_source_dispatch",
        "after_source_result_commit",
        "runtime_candidate_checkpoint",
        "after_round_controller",
        "before_finalization",
        "after_finalization_commit",
        "entering_pause",
        "entering_needs_attention",
    }
)

_CONTROL_FIELDS = (
    "retrieval_state",
    "seen_resume_ids",
    "source_coverage_summary",
    "latest_canonical_intake_summary",
    "top_pool_ids",
)
_CANDIDATE_FIELDS = (
    "candidate_store",
    "normalized_store",
    "source_evidence_by_resume_id",
    "source_evidence_by_identity_id",
    "candidate_identity_by_resume_id",
    "candidate_identities",
    "identity_aliases_by_canonical_id",
    "identity_conflicts",
    "canonical_resume_by_identity_id",
    "scorecards_by_resume_id",
)
_SEPARATE_FIELDS = (
    "detail_open_claims_by_provider_key",
    "round_history",
    "runtime_source_lane_results",
    "finalization_revisions",
)


@dataclass(frozen=True)
class CheckpointProjection:
    schema_version: str
    control_state: dict[str, object]
    candidate_state: dict[str, object]
    detail_claims: dict[str, object]
    round_states: list[dict[str, object]]
    source_lane_results: list[dict[str, object]]
    finalization_revisions: list[dict[str, object]]
    control_state_hash: str
    field_bytes: dict[str, int]
    serialization_latency_ms: float
    projection_latency_ms: float
    payload_size_bytes: int


def checkpoint_projection(run_state: object) -> CheckpointProjection:
    serialization_started = perf_counter()
    payload = _run_state_payload(run_state)
    serialization_latency_ms = (perf_counter() - serialization_started) * 1000

    projection_started = perf_counter()
    control_state = {field: payload[field] for field in _CONTROL_FIELDS if field in payload}
    candidate_state = {
        field: payload.get(field, [] if field == "identity_conflicts" else {})
        for field in _CANDIDATE_FIELDS
    }
    detail_claims = _object(payload.get("detail_open_claims_by_provider_key"))
    round_states = _object_list(payload.get("round_history"))
    source_lane_results = _object_list(payload.get("runtime_source_lane_results"))
    finalization_revisions = _object_list(payload.get("finalization_revisions"))
    field_bytes = {
        field: len(_canonical_json(value).encode("utf-8"))
        for field, value in sorted(payload.items())
    }
    control_json = _canonical_json(control_state)
    projection_latency_ms = (perf_counter() - projection_started) * 1000
    return CheckpointProjection(
        schema_version=RUNTIME_CHECKPOINT_SCHEMA_V2,
        control_state=control_state,
        candidate_state=candidate_state,
        detail_claims=detail_claims,
        round_states=round_states,
        source_lane_results=source_lane_results,
        finalization_revisions=finalization_revisions,
        control_state_hash=sha256(control_json.encode("utf-8")).hexdigest(),
        field_bytes=field_bytes,
        serialization_latency_ms=serialization_latency_ms,
        projection_latency_ms=projection_latency_ms,
        payload_size_bytes=len(control_json.encode("utf-8")),
    )


def legacy_checkpoint_projection(run_state: Mapping[str, object]) -> CheckpointProjection:
    projection = checkpoint_projection(dict(run_state))
    excluded = set(_CANDIDATE_FIELDS) | set(_SEPARATE_FIELDS) | {
        "input_truth",
        "requirement_sheet",
        "scoring_policy",
    }
    control_state = {
        key: value
        for key, value in run_state.items()
        if key not in excluded
    }
    control_json = _canonical_json(control_state)
    return replace(
        projection,
        control_state=control_state,
        control_state_hash=sha256(control_json.encode("utf-8")).hexdigest(),
        payload_size_bytes=len(control_json.encode("utf-8")),
    )


def candidate_truth_hash(candidate_state: Mapping[str, object]) -> str:
    return sha256(_canonical_json(candidate_state).encode("utf-8")).hexdigest()


def detail_claim_hash(claims: Mapping[str, object]) -> str:
    return sha256(_canonical_json(claims).encode("utf-8")).hexdigest()


def compact_round_state(payload: Mapping[str, object]) -> dict[str, object]:
    compact = dict(payload)
    compact.pop("top_candidates", None)
    compact.pop("dropped_candidates", None)
    return compact


def _run_state_payload(run_state: object) -> dict[str, object]:
    if isinstance(run_state, Mapping):
        return {str(key): value for key, value in run_state.items() if isinstance(key, str)}
    model_dump = getattr(run_state, "model_dump", None)
    if not callable(model_dump):
        raise TypeError("runtime_checkpoint_run_state_invalid")
    payload = model_dump(mode="json")
    if not isinstance(payload, dict):
        raise TypeError("runtime_checkpoint_run_state_invalid")
    return {str(key): value for key, value in payload.items() if isinstance(key, str)}


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(key, str)}


def _object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [_object(item) for item in value if isinstance(item, Mapping)]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "CheckpointProjection",
    "RUNTIME_CHECKPOINT_SCHEMA_V1",
    "RUNTIME_CHECKPOINT_SCHEMA_V2",
    "V2_SAFE_BOUNDARIES",
    "candidate_truth_hash",
    "checkpoint_projection",
    "compact_round_state",
    "detail_claim_hash",
    "legacy_checkpoint_projection",
]
