from __future__ import annotations

from dataclasses import dataclass

from seektalent.models import CTSQuery, ConstraintValue, QueryRole, unique_strings


@dataclass(frozen=True)
class CTSQueryBuildInput:
    query_role: QueryRole
    query_terms: list[str]
    keyword_query: str
    base_filters: dict[str, ConstraintValue]
    adapter_notes: list[str]
    page: int
    page_size: int
    rationale: str
    city: str | None = None


def build_cts_query(input: CTSQueryBuildInput) -> CTSQuery:
    native_filters = dict(input.base_filters)
    adapter_notes = list(input.adapter_notes)
    if input.city is not None:
        native_filters["location"] = [input.city]
        adapter_notes = unique_strings([*adapter_notes, f"runtime location dispatch: {input.city}"])
    return CTSQuery(
        query_role=input.query_role,
        query_terms=input.query_terms,
        keyword_query=input.keyword_query,
        native_filters=native_filters,
        page=input.page,
        page_size=input.page_size,
        rationale=input.rationale,
        adapter_notes=adapter_notes,
    )
