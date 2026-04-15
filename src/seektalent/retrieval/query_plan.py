from __future__ import annotations

from seektalent.models import (
    LocationExecutionPlan,
    QueryTermCandidate,
    RoundRetrievalPlan,
    unique_strings,
)


def normalize_term(term: str) -> str:
    return " ".join(term.strip().split())


def canonicalize_controller_query_terms(
    proposed_terms: list[str],
    *,
    round_no: int,
    title_anchor_term: str,
    query_term_pool: list[QueryTermCandidate],
) -> list[str]:
    terms = [normalize_term(item) for item in proposed_terms if normalize_term(item)]
    unique_terms = unique_strings(terms)
    if len(terms) != len(unique_terms):
        raise ValueError("proposed_query_terms must not contain duplicates.")
    normalized_anchor = normalize_term(title_anchor_term)
    if sum(1 for term in unique_terms if term.casefold() == normalized_anchor.casefold()) != 1:
        raise ValueError("proposed_query_terms must contain the fixed title anchor exactly once.")
    if len(unique_terms) < 2:
        raise ValueError("proposed_query_terms must contain at least 2 terms.")
    if len(unique_terms) > 3:
        raise ValueError("proposed_query_terms must not exceed 3 terms.")
    non_anchor_terms = [term for term in unique_terms if term.casefold() != normalized_anchor.casefold()]
    if round_no == 1 and len(non_anchor_terms) != 1:
        raise ValueError("round 1 requires exactly 1 non-anchor JD term.")
    if round_no > 1 and len(non_anchor_terms) not in {1, 2}:
        raise ValueError("rounds after 1 require 1 or 2 non-anchor JD terms.")
    active_non_anchor_terms = {
        item.term.casefold()
        for item in query_term_pool
        if item.active and item.term.casefold() != normalized_anchor.casefold()
    }
    invalid_terms = [term for term in non_anchor_terms if term.casefold() not in active_non_anchor_terms]
    if invalid_terms:
        raise ValueError(f"non-anchor query terms must come from the active JD pool: {', '.join(invalid_terms)}")
    return [normalized_anchor, *non_anchor_terms]


def serialize_keyword_query(terms: list[str]) -> str:
    serialized: list[str] = []
    for term in terms:
        clean = normalize_term(term)
        if " " in clean or "\t" in clean:
            clean = clean.replace("\\", "\\\\").replace('"', '\\"')
            serialized.append(f'"{clean}"')
            continue
        serialized.append(clean)
    return " ".join(serialized)


def select_query_terms(
    query_term_pool: list[QueryTermCandidate],
    *,
    round_no: int,
    title_anchor_term: str,
) -> list[str]:
    normalized_anchor = normalize_term(title_anchor_term)
    ordered = sorted(
        [
            item
            for item in query_term_pool
            if item.active and item.term.casefold() != normalized_anchor.casefold()
        ],
        key=lambda item: (item.priority, item.first_added_round, item.term.casefold()),
    )
    non_anchor_budget = 1 if round_no == 1 else min(2, len(ordered))
    terms = [normalized_anchor, *[item.term for item in ordered[:non_anchor_budget]]]
    return canonicalize_controller_query_terms(
        terms,
        round_no=round_no,
        title_anchor_term=title_anchor_term,
        query_term_pool=query_term_pool,
    )


def build_location_execution_plan(
    *,
    allowed_locations: list[str],
    preferred_locations: list[str],
    round_no: int,
    target_new: int,
) -> LocationExecutionPlan:
    if not allowed_locations:
        return LocationExecutionPlan(
            mode="none",
            allowed_locations=[],
            preferred_locations=[],
            priority_order=[],
            balanced_order=[],
            rotation_offset=0,
            target_new=target_new,
        )
    if len(allowed_locations) == 1:
        return LocationExecutionPlan(
            mode="single",
            allowed_locations=list(allowed_locations),
            preferred_locations=[],
            priority_order=[],
            balanced_order=list(allowed_locations),
            rotation_offset=0,
            target_new=target_new,
        )
    normalized_preferred = [city for city in preferred_locations if city in allowed_locations]
    if normalized_preferred:
        fallback_locations = [city for city in allowed_locations if city not in normalized_preferred]
        rotation_offset = _rotation_offset(round_no, len(fallback_locations))
        return LocationExecutionPlan(
            mode="priority_then_fallback",
            allowed_locations=list(allowed_locations),
            preferred_locations=list(normalized_preferred),
            priority_order=list(normalized_preferred),
            balanced_order=rotate_locations(fallback_locations, rotation_offset),
            rotation_offset=rotation_offset,
            target_new=target_new,
        )
    rotation_offset = _rotation_offset(round_no, len(allowed_locations))
    return LocationExecutionPlan(
        mode="balanced_all",
        allowed_locations=list(allowed_locations),
        preferred_locations=[],
        priority_order=[],
        balanced_order=rotate_locations(allowed_locations, rotation_offset),
        rotation_offset=rotation_offset,
        target_new=target_new,
    )


def rotate_locations(locations: list[str], offset: int) -> list[str]:
    if not locations:
        return []
    normalized_offset = offset % len(locations)
    return locations[normalized_offset:] + locations[:normalized_offset]


def allocate_balanced_city_targets(*, ordered_cities: list[str], target_new: int) -> list[tuple[str, int]]:
    if not ordered_cities or target_new <= 0:
        return []
    base_share, remainder = divmod(target_new, len(ordered_cities))
    allocations: list[tuple[str, int]] = []
    for index, city in enumerate(ordered_cities):
        requested_count = base_share + (1 if index < remainder else 0)
        if requested_count <= 0:
            continue
        allocations.append((city, requested_count))
    return allocations


def build_round_retrieval_plan(
    *,
    plan_version: int,
    round_no: int,
    query_terms: list[str],
    title_anchor_term: str,
    query_term_pool: list[QueryTermCandidate],
    projected_cts_filters: dict[str, str | int | list[str]],
    runtime_only_constraints,
    location_execution_plan: LocationExecutionPlan,
    target_new: int,
    rationale: str,
) -> RoundRetrievalPlan:
    canonical_terms = canonicalize_controller_query_terms(
        query_terms,
        round_no=round_no,
        title_anchor_term=title_anchor_term,
        query_term_pool=query_term_pool,
    )
    return RoundRetrievalPlan(
        plan_version=plan_version,
        round_no=round_no,
        query_terms=canonical_terms,
        keyword_query=serialize_keyword_query(canonical_terms),
        projected_cts_filters=projected_cts_filters,
        runtime_only_constraints=list(runtime_only_constraints),
        location_execution_plan=location_execution_plan,
        target_new=target_new,
        rationale=rationale,
    )


def _rotation_offset(round_no: int, city_count: int) -> int:
    if city_count <= 0:
        return 0
    return (round_no - 1) % city_count
