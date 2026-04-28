from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from seektalent.models import CanonicalQuerySpec

UNORDERED_TERM_FIELDS = {
    "anchors",
    "expansion_terms",
    "generic_explore_terms",
    "required_terms",
    "optional_terms",
    "excluded_terms",
}


def _stable_hash(payload: dict[str, object]) -> str:
    blob = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(blob.encode("utf-8")).hexdigest()[:32]


def normalize_term(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _canonicalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_term(value)
    if isinstance(value, dict):
        return {key: _canonicalize_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return sorted(normalize_term(item) for item in value)
        return [_canonicalize_value(item) for item in value]
    return value


def _normalize_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _canonicalize_value(item) for key, item in sorted(value.items())}


def canonicalize_query_spec(spec: CanonicalQuerySpec) -> dict[str, object]:
    payload = spec.model_dump(mode="json")
    for field in UNORDERED_TERM_FIELDS:
        payload[field] = sorted(normalize_term(item) for item in payload[field])
    payload["provider_filters"] = _normalize_mapping(payload["provider_filters"])
    payload["rendered_provider_query"] = normalize_term(str(payload["rendered_provider_query"]))
    return payload


def build_job_intent_fingerprint(
    *,
    role_title: str,
    must_haves: list[str],
    preferred_terms: list[str],
    hard_filters: dict[str, object] | None = None,
    location_preferences: list[str] | None = None,
    normalized_intent_hash: str | None = None,
    intent_schema_version: str,
) -> str:
    return _stable_hash(
        {
            "role_title": normalize_term(role_title),
            "must_haves": sorted(normalize_term(item) for item in must_haves if item.strip()),
            "preferred_terms": sorted(normalize_term(item) for item in preferred_terms if item.strip()),
            "hard_filters": _normalize_mapping(hard_filters or {}),
            "location_preferences": sorted(
                normalize_term(item) for item in (location_preferences or []) if item.strip()
            ),
            "normalized_intent_hash": normalized_intent_hash,
            "intent_schema_version": intent_schema_version,
        }
    )


def build_query_fingerprint(
    *,
    job_intent_fingerprint: str,
    lane_type: str,
    canonical_query_spec: CanonicalQuerySpec,
    policy_version: str,
) -> str:
    if lane_type != canonical_query_spec.lane_type:
        raise ValueError("lane_type must match canonical_query_spec.lane_type")
    return _stable_hash(
        {
            "job_intent_fingerprint": job_intent_fingerprint,
            "lane_type": lane_type,
            "canonical_query_spec": canonicalize_query_spec(canonical_query_spec),
            "policy_version": policy_version,
        }
    )


def build_query_instance_id(
    *,
    run_id: str,
    round_no: int,
    lane_type: str,
    query_fingerprint: str,
    source_plan_version: str,
) -> str:
    return _stable_hash(
        {
            "run_id": run_id,
            "round_no": round_no,
            "lane_type": lane_type,
            "query_fingerprint": query_fingerprint,
            "source_plan_version": source_plan_version,
        }
    )
