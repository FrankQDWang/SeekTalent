from __future__ import annotations

from itertools import combinations

from seektalent.models import (
    QueryRole,
    LocationExecutionPlan,
    QueryTermCandidate,
    RoundRetrievalPlan,
    SentQueryRecord,
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
    allow_inactive_non_anchor_terms: bool = False,
    allow_anchor_only: bool = False,
) -> list[str]:
    terms = [normalize_term(item) for item in proposed_terms if normalize_term(item)]
    unique_terms = unique_strings(terms)
    if len(terms) != len(unique_terms):
        raise ValueError("proposed_query_terms must not contain duplicates.")
    if len(unique_terms) < 2 and not allow_anchor_only:
        raise ValueError("proposed_query_terms must contain at least 2 terms.")
    if len(unique_terms) > 3:
        raise ValueError("proposed_query_terms must not exceed 3 terms.")
    del title_anchor_term
    term_index = _query_term_index(query_term_pool)
    missing_terms = [term for term in unique_terms if term.casefold() not in term_index]
    if missing_terms:
        raise ValueError(f"query terms must come from the compiled query term pool: {', '.join(missing_terms)}")
    candidates = [term_index[term.casefold()] for term in unique_terms]
    if len(candidates) == 1:
        candidate = candidates[0]
        if not (allow_anchor_only and _is_anchor_candidate(candidate)):
            raise ValueError("anchor-only query requires exactly one compiler-admitted role anchor.")
        return [candidate.term]
    not_admitted = [item.term for item in candidates if item.queryability != "admitted"]
    if not_admitted:
        raise ValueError(f"query terms must be compiler-admitted: {', '.join(not_admitted)}")
    anchors = [item for item in candidates if _is_anchor_candidate(item)]
    if len(anchors) != 1:
        raise ValueError("proposed_query_terms must contain exactly one compiler-admitted anchor.")
    anchor = anchors[0]
    non_anchor_candidates = [item for item in candidates if item.term.casefold() != anchor.term.casefold()]
    non_anchor_terms = [item.term for item in non_anchor_candidates]
    if round_no == 1 and len(non_anchor_terms) != 1:
        raise ValueError("round 1 requires exactly 1 non-anchor admitted term.")
    if round_no > 1 and len(non_anchor_terms) not in {1, 2}:
        raise ValueError("rounds after 1 require 1 or 2 non-anchor admitted terms.")
    inactive_terms = [
        item.term for item in non_anchor_candidates if not allow_inactive_non_anchor_terms and not item.active
    ]
    if inactive_terms:
        raise ValueError(f"non-anchor query terms must be active compiler-admitted terms: {', '.join(inactive_terms)}")
    duplicate_families = _duplicate_families(candidates)
    if duplicate_families:
        raise ValueError(f"query terms must not repeat compiler families: {', '.join(duplicate_families)}")
    return [anchor.term, *non_anchor_terms]


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
    del title_anchor_term
    anchors = sorted(
        [item for item in query_term_pool if item.active and _is_anchor_candidate(item)],
        key=_term_sort_key,
    )
    if not anchors:
        raise ValueError("compiled query term pool must include one active admitted anchor.")
    ordered = sorted(
        [
            item
            for item in query_term_pool
            if item.active and item.queryability == "admitted" and not _is_anchor_candidate(item)
        ],
        key=_non_anchor_sort_key,
    )
    non_anchor_budget = 1 if round_no == 1 else min(2, len(ordered))
    selected: list[QueryTermCandidate] = []
    used_families = {anchors[0].family}
    for item in ordered:
        if item.family in used_families:
            continue
        selected.append(item)
        used_families.add(item.family)
        if len(selected) >= non_anchor_budget:
            break
    if not selected:
        raise ValueError("compiled query term pool must include active admitted non-anchor terms.")
    terms = [anchors[0].term, *[item.term for item in selected]]
    return canonicalize_controller_query_terms(
        terms,
        round_no=round_no,
        title_anchor_term="",
        query_term_pool=query_term_pool,
    )


def derive_explore_query_terms(
    exploit_terms: list[str],
    *,
    title_anchor_term: str,
    query_term_pool: list[QueryTermCandidate],
    sent_query_history: list[SentQueryRecord],
) -> list[str] | None:
    exploit_terms = [normalize_term(item) for item in exploit_terms if normalize_term(item)]
    exploit_terms = canonicalize_controller_query_terms(
        exploit_terms,
        round_no=2,
        title_anchor_term=title_anchor_term,
        query_term_pool=query_term_pool,
        allow_inactive_non_anchor_terms=True,
    )
    term_index = _query_term_index(query_term_pool)
    exploit_candidates = [term_index[term.casefold()] for term in exploit_terms]
    anchor = next(item for item in exploit_candidates if _is_anchor_candidate(item))
    exploit_non_anchor_terms = [item.term for item in exploit_candidates if item.term.casefold() != anchor.term.casefold()]
    if not exploit_non_anchor_terms:
        return None

    unique_logical_queries: dict[tuple[int, tuple[str, ...]], QueryRole] = {}
    for item in sent_query_history:
        key = (item.round_no, tuple(term.casefold() for term in item.query_terms))
        unique_logical_queries.setdefault(key, item.query_role)

    term_usage: dict[str, int] = {}
    used_queries: set[tuple[str, ...]] = set()
    for (_, query_terms), _ in unique_logical_queries.items():
        used_queries.add(query_terms)
        for term in query_terms:
            if term == anchor.term.casefold():
                continue
            term_usage[term] = term_usage.get(term, 0) + 1

    exploit_term_keys = {term.casefold() for term in exploit_non_anchor_terms}
    ordered_terms = sorted(
        [
            item
            for item in query_term_pool
            if item.queryability == "admitted" and not _is_anchor_candidate(item)
        ],
        key=lambda item: (
            0 if item.active and item.term.casefold() not in exploit_term_keys else 1,
            0 if not item.active and item.term.casefold() not in exploit_term_keys else 1,
            term_usage.get(item.term.casefold(), 0),
            _signal_rank(item),
            item.priority,
            item.first_added_round,
            item.term.casefold(),
        ),
    )

    term_rank = {item.term.casefold(): index for index, item in enumerate(ordered_terms)}

    def score_combo(combo: tuple[QueryTermCandidate, ...]) -> tuple[int, int, int, int, tuple[str, ...]]:
        combo_terms = tuple(item.term.casefold() for item in combo)
        return (
            sum(1 for term in combo_terms if term in exploit_term_keys),
            sum(term_usage.get(term, 0) for term in combo_terms),
            sum(term_rank[term] for term in combo_terms),
            -len(combo_terms),
            combo_terms,
        )

    unused_candidates: list[tuple[tuple[int, int, int, int, tuple[str, ...]], list[str]]] = []
    used_candidates: list[tuple[tuple[int, int, int, int, tuple[str, ...]], list[str]]] = []
    for size in (1, 2):
        for combo in combinations(ordered_terms, size):
            candidates = [anchor, *combo]
            term_keys = [item.term.casefold() for item in candidates]
            if len(term_keys) != len(set(term_keys)) or _duplicate_families(candidates):
                continue
            terms = [item.term for item in candidates]
            candidate_terms = canonicalize_controller_query_terms(
                terms,
                round_no=2,
                title_anchor_term=title_anchor_term,
                query_term_pool=query_term_pool,
                allow_inactive_non_anchor_terms=True,
            )
            signature = tuple(term.casefold() for term in candidate_terms)
            if signature == tuple(term.casefold() for term in exploit_terms):
                continue
            bucket = used_candidates if signature in used_queries else unused_candidates
            bucket.append((score_combo(combo), candidate_terms))
    if unused_candidates:
        return min(unused_candidates, key=lambda item: item[0])[1]
    if used_candidates:
        return min(used_candidates, key=lambda item: item[0])[1]
    return None


def _query_term_index(query_term_pool: list[QueryTermCandidate]) -> dict[str, QueryTermCandidate]:
    return {normalize_term(item.term).casefold(): item for item in query_term_pool}


def _is_anchor_candidate(item: QueryTermCandidate) -> bool:
    return item.queryability == "admitted" and item.retrieval_role == "role_anchor"


def _term_sort_key(item: QueryTermCandidate) -> tuple[int, int, str]:
    return (item.priority, item.first_added_round, item.term.casefold())


def _non_anchor_sort_key(item: QueryTermCandidate) -> tuple[int, int, int, str]:
    return (_signal_rank(item), item.priority, item.first_added_round, item.term.casefold())


def _signal_rank(item: QueryTermCandidate) -> int:
    if item.retrieval_role == "core_skill":
        return 0
    if item.retrieval_role == "framework_tool":
        return 1
    if item.retrieval_role == "domain_context":
        return 2
    return 3


def _duplicate_families(candidates: list[QueryTermCandidate]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in candidates:
        if item.family in seen:
            duplicates.append(item.family)
            continue
        seen.add(item.family)
    return unique_strings(duplicates)


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
    allow_anchor_only_query: bool = False,
) -> RoundRetrievalPlan:
    canonical_terms = canonicalize_controller_query_terms(
        query_terms,
        round_no=round_no,
        title_anchor_term=title_anchor_term,
        query_term_pool=query_term_pool,
        allow_anchor_only=allow_anchor_only_query,
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
