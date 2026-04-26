from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from seektalent.models import ResumeCandidate
from seektalent.models import RuntimeConstraint


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_structured_filters: bool
    supports_detail_fetch: bool
    supports_fetch_mode_summary: bool
    supports_fetch_mode_detail: bool
    paging_mode: str
    recommended_max_concurrency: int
    has_stable_external_id: bool
    has_stable_dedup_key: bool


@dataclass(frozen=True)
class SearchRequest:
    query_terms: list[str]
    query_role: str
    runtime_constraints: list[RuntimeConstraint]
    fetch_mode: str
    page_size: int
    cursor: str | None = None


@dataclass(frozen=True)
class SearchResult:
    candidates: list[ResumeCandidate] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    exhausted: bool = False
    next_cursor: str | None = None


class ProviderAdapter(Protocol):
    name: str

    def describe_capabilities(self) -> ProviderCapabilities: ...

    async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult: ...
