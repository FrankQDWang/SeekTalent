from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from seektalent.models import CanonicalQuerySpec, QueryTermCandidate, is_primary_anchor_role

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


@dataclass(frozen=True)
class ResolvedQueryIdentity:
    term_group_key: str
    primary_anchor_family_id: str
    non_anchor_term_family_ids: tuple[str, ...]


def _semantic_families(
    *,
    query_terms: Sequence[str],
    query_term_pool: Sequence[QueryTermCandidate],
    explicit_family_overrides: Mapping[str, str] | None = None,
) -> list[tuple[str, QueryTermCandidate | None]]:
    overrides: dict[str, str] = {}
    for term, family_id in (explicit_family_overrides or {}).items():
        term_key = normalize_term(term)
        family_key = normalize_term(family_id)
        if not term_key or not family_key:
            raise ValueError("query_family_override_invalid")
        overrides[term_key] = family_key
    candidates = {normalize_term(item.term): item for item in query_term_pool}
    resolved: list[tuple[str, QueryTermCandidate | None]] = []
    seen: set[str] = set()
    for term in query_terms:
        term_key = normalize_term(term)
        if not term_key:
            continue
        candidate = candidates.get(term_key)
        family_id = overrides.get(term_key) or (
            normalize_term(candidate.family) if candidate is not None else f"term:{term_key}"
        )
        if not family_id:
            raise ValueError("query_family_identity_invalid")
        if family_id in seen:
            continue
        seen.add(family_id)
        resolved.append((family_id, candidate))
    if not resolved:
        raise ValueError("term_group_key_requires_terms")
    return resolved


def _term_group_hash(families: Sequence[str]) -> str:
    payload = json.dumps(
        {"version": "term-group-v1", "members": sorted(set(families))},
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:32]


def resolve_query_identity(
    *,
    query_terms: Sequence[str],
    query_term_pool: Sequence[QueryTermCandidate],
    explicit_family_overrides: Mapping[str, str] | None = None,
) -> ResolvedQueryIdentity:
    resolved = _semantic_families(
        query_terms=query_terms,
        query_term_pool=query_term_pool,
        explicit_family_overrides=explicit_family_overrides,
    )
    anchor_families = {
        family_id
        for family_id, candidate in resolved
        if candidate is not None and is_primary_anchor_role(candidate.retrieval_role)
    }
    if len(anchor_families) != 1:
        raise ValueError("query_primary_anchor_family_required")
    primary_anchor_family_id = next(iter(anchor_families))
    non_anchor_families = tuple(
        family_id for family_id, _candidate in resolved if family_id != primary_anchor_family_id
    )
    return ResolvedQueryIdentity(
        term_group_key=_term_group_hash([family_id for family_id, _candidate in resolved]),
        primary_anchor_family_id=primary_anchor_family_id,
        non_anchor_term_family_ids=non_anchor_families,
    )


def build_term_group_key(
    *,
    query_terms: Sequence[str],
    query_term_pool: Sequence[QueryTermCandidate],
) -> str:
    return _term_group_hash(
        [family_id for family_id, _candidate in _semantic_families(
            query_terms=query_terms,
            query_term_pool=query_term_pool,
        )]
    )


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
    job_title: str,
    must_haves: list[str],
    preferred_terms: list[str],
    hard_filters: dict[str, object] | None = None,
    location_preferences: list[str] | None = None,
    normalized_intent_hash: str | None = None,
    intent_schema_version: str,
) -> str:
    return _stable_hash(
        {
            "job_title": normalize_term(job_title),
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
